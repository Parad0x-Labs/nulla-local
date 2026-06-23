"""Canonical x402 "exact" Solana pay path (PayAI /verify + /settle).

These exercise the rewritten live path WITHOUT touching the network: the
facilitator HTTP calls and the blockhash fetch are mocked. One devnet settle was
proven live and committed under proofs/devnet/ — these lock the wire format and
the client's verify→settle→receipt wiring so a regression fails loudly.
"""
from __future__ import annotations

import base64
import json

import pytest

from core.x402.client import (
    X402Client,
    X402Config,
    X402Mode,
    X402PaymentError,
    build_solana_x402_payment,
)

Keypair = pytest.importorskip("solders.keypair").Keypair
_BLOCKHASH = "11111111111111111111111111111111"  # valid 32-byte base58 (all-zero)


def _keypair_file(tmp_path, name="payer.json"):
    kp = Keypair.from_seed(bytes(range(32)))
    f = tmp_path / name
    f.write_text(json.dumps(list(bytes(kp))))
    return str(f), kp


def _pubkey() -> str:
    return str(Keypair().pubkey())


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _requirements(payer_pub):
    asset, pay_to, fee = _pubkey(), _pubkey(), _pubkey()
    return {
        "scheme": "exact", "network": "solana-devnet",
        "maxAmountRequired": "1000", "resource": "https://nulla.local/x402/t",
        "description": "t", "mimeType": "application/json",
        "payTo": pay_to, "maxTimeoutSeconds": 120, "asset": asset,
        "extra": {"feePayer": fee},
    }


# ── build_solana_x402_payment: structure of the partially-signed v0 tx ──────

class TestBuildPayload:
    def test_envelope_shape(self, monkeypatch, tmp_path):
        monkeypatch.setattr("core.x402.client._get_latest_blockhash", lambda _u: _BLOCKHASH)
        _, payer = _keypair_file(tmp_path)
        env = build_solana_x402_payment(payer, _requirements(str(payer.pubkey())),
                                        "https://rpc.example", decimals=6)
        assert env["x402Version"] == 1
        assert env["scheme"] == "exact"
        assert env["network"] == "solana-devnet"
        assert isinstance(env["payload"]["transaction"], str)
        # base64-decodable
        base64.b64decode(env["payload"]["transaction"])

    def test_tx_is_v0_feepayer_and_partially_signed(self, monkeypatch, tmp_path):
        from solders.signature import Signature
        from solders.transaction import VersionedTransaction
        monkeypatch.setattr("core.x402.client._get_latest_blockhash", lambda _u: _BLOCKHASH)
        _, payer = _keypair_file(tmp_path)
        req = _requirements(str(payer.pubkey()))
        env = build_solana_x402_payment(payer, req, "https://rpc.example", decimals=6)

        tx = VersionedTransaction.from_bytes(base64.b64decode(env["payload"]["transaction"]))
        msg = tx.message
        keys = list(msg.account_keys)
        # fee payer is the facilitator (account index 0), NOT the payer
        assert str(keys[0]) == req["extra"]["feePayer"]
        assert str(payer.pubkey()) in [str(k) for k in keys]
        # three instructions: compute-limit, compute-price, transfer
        assert len(msg.instructions) == 3
        # payer slot signed; fee-payer slot left empty for the facilitator
        sigs = list(tx.signatures)
        payer_idx = [str(k) for k in keys].index(str(payer.pubkey()))
        assert sigs[payer_idx] != Signature.default()
        assert sigs[0] == Signature.default()

    def test_compute_unit_limit_under_facilitator_cap(self, monkeypatch, tmp_path):
        # The facilitator rejects an over-high sponsored limit; default must be <= cap.
        monkeypatch.setattr("core.x402.client._get_latest_blockhash", lambda _u: _BLOCKHASH)
        _, payer = _keypair_file(tmp_path)
        env = build_solana_x402_payment(payer, _requirements(str(payer.pubkey())),
                                        "https://rpc.example", compute_unit_limit=50_000)
        # decoding the first compute-budget ix data: [0x02, u32 LE limit]
        from solders.transaction import VersionedTransaction
        tx = VersionedTransaction.from_bytes(base64.b64decode(env["payload"]["transaction"]))
        ix0 = tx.message.instructions[0]
        assert bytes(ix0.data)[0] == 2  # SetComputeUnitLimit discriminator
        limit = int.from_bytes(bytes(ix0.data)[1:5], "little")
        assert limit == 50_000


# ── _solana_pay: verify → settle → receipt wiring ───────────────────────────

class TestCanonicalSettle:
    def _patch(self, monkeypatch, *, verify=None, settle=None):
        monkeypatch.setattr("core.x402.client._get_latest_blockhash", lambda _u: _BLOCKHASH)
        verify = verify or {"isValid": True, "payer": "P"}
        settle = settle or {"success": True, "network": "solana-devnet",
                            "transaction": "SETTLED_SIG_123", "payer": "P"}

        def fake_get(url, *a, **k):
            return _Resp({"kinds": [{"scheme": "exact", "network": "solana-devnet",
                                     "extra": {"feePayer": _pubkey()}}]})

        def fake_post(url, *a, **k):
            return _Resp(verify if url.endswith("/verify") else settle)

        monkeypatch.setattr("requests.get", fake_get)
        monkeypatch.setattr("requests.post", fake_post)

    def test_pay_returns_real_signature(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        kp_file, _ = _keypair_file(tmp_path)
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=kp_file,
                         asset_mint=_pubkey(), asset_decimals=6)
        receipt = X402Client(cfg).pay(0.001, _pubkey(), "sess-1")
        assert receipt.payment_tx == "SETTLED_SIG_123"
        assert receipt.mode == "devnet"
        assert receipt.facilitator_sig == ""  # canonical settle returns no sig
        assert len(receipt.receipt_hash) == 64

    def test_verify_rejection_raises(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, verify={"isValid": False,
                    "invalidReason": "insufficient_funds", "payer": ""})
        kp_file, _ = _keypair_file(tmp_path)
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=kp_file, asset_mint=_pubkey())
        with pytest.raises(X402PaymentError, match="verify rejected"):
            X402Client(cfg).pay(0.001, _pubkey(), "sess-2")

    def test_settle_failure_raises(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, settle={"success": False, "transaction": "",
                    "errorReason": "unexpected_settle_error"})
        kp_file, _ = _keypair_file(tmp_path)
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=kp_file, asset_mint=_pubkey())
        with pytest.raises(X402PaymentError, match="settle failed"):
            X402Client(cfg).pay(0.001, _pubkey(), "sess-3")

    def test_devnet_without_keypair_raises(self):
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=None)
        with pytest.raises(X402PaymentError, match="keypair_path"):
            X402Client(cfg).pay(0.001, _pubkey(), "sess-4")

    def test_pay_with_injected_signer_needs_no_keypair_path(self, monkeypatch):
        # The dial pays with a wrapped NullaWallet (a signer), not a keypair file.
        self._patch(monkeypatch)
        cfg = X402Config(mode=X402Mode.DEVNET, asset_mint=_pubkey())  # no keypair_path
        receipt = X402Client(cfg, signer=Keypair()).pay(0.001, _pubkey(), "sess-signer")
        assert receipt.payment_tx == "SETTLED_SIG_123"


# ── config: network + asset selection ───────────────────────────────────────

class TestConfig:
    def test_network_name(self):
        assert X402Config(mode=X402Mode.DEVNET).network_name == "solana-devnet"
        assert X402Config(mode=X402Mode.MAINNET).network_name == "solana"

    def test_single_facilitator_host(self):
        from core.x402.client import PAYAI_FACILITATOR
        assert X402Config(mode=X402Mode.DEVNET).effective_facilitator == PAYAI_FACILITATOR
        assert X402Config(mode=X402Mode.MAINNET).effective_facilitator == PAYAI_FACILITATOR

    def test_asset_override_else_usdc(self):
        from core.x402.client import USDC_MINT_DEVNET
        assert X402Config(mode=X402Mode.DEVNET).effective_asset == USDC_MINT_DEVNET
        assert X402Config(mode=X402Mode.DEVNET, asset_mint="MINT").effective_asset == "MINT"


class TestWalletSigner:
    """wallet_signer adapts a NullaWallet (pubkey() + sign(bytes)) to the
    solders-Keypair surface the payment builder needs."""

    def test_wraps_wallet_to_signer_surface(self):
        from core.x402.client import wallet_signer
        kp = Keypair()

        class FakeWallet:  # mirrors NullaWallet: pubkey() method + sign(bytes)
            def pubkey(self):
                return str(kp.pubkey())

            def sign(self, payload):
                return bytes(kp.sign_message(bytes(payload)))

        s = wallet_signer(FakeWallet())
        assert str(s.pubkey()) == str(kp.pubkey())
        msg = b"x402-wallet-signer"
        assert s.sign_message(msg) == kp.sign_message(msg)
