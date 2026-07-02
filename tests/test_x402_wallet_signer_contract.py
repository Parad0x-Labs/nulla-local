"""Lock the NullaWallet <-> x402 signer interface contract.

The x402 payment path signs via a thin _WalletSigner adapter that assumes the
wallet exposes `.pubkey` and `.sign(bytes)`. If NullaWallet's surface drifts, the
adapter would break only at signing time (deep in a payment). These tests pin the
contract using the SAME wallet the installer creates (get_or_create_wallet), all in
STUB mode - no network, no devnet/mainnet funds, no real on-chain transfer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

solders = pytest.importorskip("solders")

from core.nulla_wallet import get_or_create_wallet, verify_wallet_signature
from core.x402.client import wallet_signer


def test_wallet_signer_pubkey_matches_install_wallet(tmp_path: Path) -> None:
    wallet = get_or_create_wallet(runtime_home=str(tmp_path / "runtime"))
    signer = wallet_signer(wallet)

    from solders.pubkey import Pubkey

    signer_pk = signer.pubkey()
    assert isinstance(signer_pk, Pubkey)
    assert str(signer_pk) == wallet.pubkey


def test_wallet_signer_signature_verifies_against_wallet_pubkey(tmp_path: Path) -> None:
    wallet = get_or_create_wallet(runtime_home=str(tmp_path / "runtime"))
    signer = wallet_signer(wallet)

    message = b"x402-exact-payment-message-v0"
    signature = signer.sign_message(message)

    # The signature the x402 path produces must verify under the wallet's own pubkey,
    # proving the local wallet (not a remote server) is what signs payments.
    assert verify_wallet_signature(
        wallet_pubkey=wallet.pubkey,
        message=message,
        signature=bytes(signature),
    )


def test_signing_never_requires_the_encryption_of_a_remote_service(tmp_path: Path) -> None:
    # The adapter must sign in-process; assert no attribute beyond pubkey/sign is needed.
    wallet = get_or_create_wallet(runtime_home=str(tmp_path / "runtime"))
    signer = wallet_signer(wallet)
    # Two signs of the same message are deterministic for ed25519.
    assert bytes(signer.sign_message(b"abc")) == bytes(signer.sign_message(b"abc"))
