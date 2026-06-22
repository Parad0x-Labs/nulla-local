from __future__ import annotations

import base64
import json
from pathlib import Path

from apps.nulla_api_server import create_app
from core.nulla_wallet import NullaWallet
from core.web.api.runtime import RuntimeServices
from core.web0_gated_html import (
    GateChallengeStore,
    NullaGateHandler,
    WalletKeyStore,
    decrypt_content_block,
    encrypt_content_block,
    make_gate_challenge,
    render_gated_block_html,
)
from core.web0_tools import web0_gate_key_store
from tests.asgi_harness import asgi_request


def _wallet(tmp_path: Path, name: str) -> NullaWallet:
    wallet = NullaWallet(runtime_home=tmp_path / name, derivation_key=(name[:1].encode("ascii") or b"x") * 32)
    wallet.generate_and_save()
    return wallet


def _signed_gate_body(wallet: NullaWallet, block_id: str) -> dict[str, str]:
    challenge = make_gate_challenge(block_id, wallet.pubkey)
    return {
        "block_id": block_id,
        "wallet_pubkey": wallet.pubkey,
        "nonce": challenge,
        "signature": base64.b64encode(wallet.sign_message(challenge)).decode("ascii"),
    }


def test_gated_block_encrypts_plaintext_and_gate_releases_key_only_after_valid_signature(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path, "alpha")
    encrypted = encrypt_content_block("private launch alpha", [wallet.pubkey], label="Alpha")
    rendered = render_gated_block_html(encrypted.block)
    store = WalletKeyStore()
    store.register_encrypted_block(encrypted)

    response = NullaGateHandler(store).handle(_signed_gate_body(wallet, encrypted.block.block_id))
    decrypted = decrypt_content_block(encrypted.block, bytes.fromhex(response["aes_key"]))

    assert "private launch alpha" not in rendered
    assert response["aes_key"] == encrypted.secret.aes_key.hex()
    assert decrypted == "private launch alpha"


def test_gated_block_denies_invalid_signature_and_non_whitelisted_wallet(tmp_path: Path) -> None:
    allowed = _wallet(tmp_path, "alpha")
    other = _wallet(tmp_path, "bravo")
    encrypted = encrypt_content_block("private", [allowed.pubkey])
    store = WalletKeyStore()
    store.register_encrypted_block(encrypted)

    invalid_signature_body = _signed_gate_body(allowed, encrypted.block.block_id)
    invalid_signature_body["signature"] = base64.b64encode(b"not-a-real-signature").decode("ascii")
    other_body = _signed_gate_body(other, encrypted.block.block_id)

    assert NullaGateHandler(store).handle(invalid_signature_body) == {"error": "invalid_signature"}
    assert NullaGateHandler(store).handle(other_body) == {"error": "not_whitelisted"}


def test_wallet_key_store_persists_encrypted_without_plaintext_key(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path, "alpha")
    encrypted = encrypt_content_block("private", [wallet.pubkey])
    store = WalletKeyStore()
    store.register_encrypted_block(encrypted)
    path = tmp_path / "gate_keys.enc"

    store.save(path=path, storage_key=b"k" * 32)
    loaded = WalletKeyStore.load(path=path, storage_key=b"k" * 32)

    assert encrypted.secret.aes_key.hex() not in path.read_text(encoding="utf-8")
    assert loaded.get_aes_key(encrypted.block.block_id, wallet.pubkey) == encrypted.secret.aes_key


def test_api_gate_unlock_route_verifies_signature_and_sets_cors_headers(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path, "alpha")
    encrypted = encrypt_content_block("private", [wallet.pubkey])
    web0_gate_key_store().register_encrypted_block(encrypted)
    app = create_app(RuntimeServices(display_name="NULLA"))

    options_status, options_headers, _ = asgi_request(app, method="OPTIONS", path="/gate/unlock")
    status, headers, body = asgi_request(
        app,
        method="POST",
        path="/gate/unlock",
        headers={"Content-Type": "application/json"},
        body=json.dumps(_signed_gate_body(wallet, encrypted.block.block_id)).encode("utf-8"),
    )
    payload = json.loads(body.decode("utf-8"))

    assert options_status == 204
    assert options_headers["access-control-allow-origin"] == "*"
    assert status == 200
    assert headers["access-control-allow-origin"] == "*"
    assert payload == {"aes_key": encrypted.secret.aes_key.hex()}


def _signed_gate_body_for_challenge(wallet: NullaWallet, block_id: str, challenge: str) -> dict[str, str]:
    return {
        "block_id": block_id,
        "wallet_pubkey": wallet.pubkey,
        "nonce": challenge,
        "signature": base64.b64encode(wallet.sign_message(challenge)).decode("ascii"),
    }


def test_challenge_store_nonce_changes_per_issue() -> None:
    store = GateChallengeStore()
    a = store.issue("block-1", "WALLET")
    b = store.issue("block-1", "WALLET")
    assert a != b  # a fresh server nonce per issue
    assert a.startswith("nulla-gate-v2:block-1:WALLET:")


def test_challenge_store_consumes_once_then_rejects_replay() -> None:
    store = GateChallengeStore()
    challenge = store.issue("block-1", "WALLET")
    assert store.consume("block-1", "WALLET", challenge) is True
    # single use: a second consume of the same nonce is rejected (replay closed)
    assert store.consume("block-1", "WALLET", challenge) is False


def test_challenge_store_rejects_expired_and_cross_binding() -> None:
    store = GateChallengeStore(ttl_seconds=0.0)
    expired = store.issue("block-1", "WALLET")
    assert store.consume("block-1", "WALLET", expired) is False  # TTL already elapsed

    fresh_store = GateChallengeStore()
    challenge = fresh_store.issue("block-1", "WALLET")
    # nonce bound to (block, wallet): cannot be replayed against another binding
    assert fresh_store.consume("block-2", "WALLET", challenge) is False
    assert fresh_store.consume("block-1", "OTHER", challenge) is False


def test_handler_with_challenge_store_requires_fresh_nonce_and_blocks_replay(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path, "alpha")
    encrypted = encrypt_content_block("private", [wallet.pubkey])
    key_store = WalletKeyStore()
    key_store.register_encrypted_block(encrypted)
    challenge_store = GateChallengeStore()
    handler = NullaGateHandler(key_store, challenge_store=challenge_store)
    block_id = encrypted.block.block_id

    # static v1 challenge is no longer accepted when a challenge store is wired
    stale = _signed_gate_body(wallet, block_id)
    assert handler.handle(stale) == {"error": "invalid_nonce"}

    # fresh server-issued challenge unlocks exactly once
    challenge = challenge_store.issue(block_id, wallet.pubkey)
    body = _signed_gate_body_for_challenge(wallet, block_id, challenge)
    assert handler.handle(body) == {"aes_key": encrypted.secret.aes_key.hex()}
    # replaying the exact same body is rejected (nonce was burned)
    assert handler.handle(body) == {"error": "invalid_nonce"}


def test_handler_without_challenge_store_keeps_v1_behavior(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path, "alpha")
    encrypted = encrypt_content_block("private", [wallet.pubkey])
    key_store = WalletKeyStore()
    key_store.register_encrypted_block(encrypted)
    handler = NullaGateHandler(key_store)  # no challenge store -> backward compatible
    body = _signed_gate_body(wallet, encrypted.block.block_id)
    assert body["nonce"] == make_gate_challenge(encrypted.block.block_id, wallet.pubkey)
    assert handler.handle(body) == {"aes_key": encrypted.secret.aes_key.hex()}
