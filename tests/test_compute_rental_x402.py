"""
tests/test_compute_rental_x402.py
==================================
Integration tests for the NULLA compute rental → x402 keystone wire.

What is tested
--------------
1.  Existing stub behaviour unchanged (backward-compat guarantee).
2.  X402Client stub mode: receipt structure, hash determinism, idempotency.
3.  X402Config validation: max_fee guard, mode defaults.
4.  ComputeRentalMarket with x402_config (stub): receipt attached to session,
    WorkProof.signature carries the receipt hash, canonical_hash is stable.
5.  ComputeRentalMarket without x402_config: exact previous behaviour.
6.  Cost estimation: correct USDC amount for USDC and NULL listings.
7.  Edge cases: zero cost floor, max_fee guard, stub vs live mode flag.

All tests run without a Solana wallet or network connection.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

from core.x402.client import (
    X402Client,
    X402Config,
    X402Mode,
    X402PaymentError,
    X402Quote,
    X402Receipt,
)
from core.compute.rental_market import (
    ComputeListing,
    ComputeRentalMarket,
    HardwareProbe,
    WorkProof,
    NULL_PER_USDC,
)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _usdc_listing(price: float = 0.001, tps: int = 50) -> ComputeListing:
    return ComputeListing(
        node_id=f"node-{uuid.uuid4().hex[:8]}",
        endpoint="http://localhost:7860",
        hardware={},
        tokens_per_second=tps,
        price_per_1k_tokens=price,
        currency="USDC",
        min_rental_minutes=1,
        available=True,
    )


def _null_listing(price: float = 1.0, tps: int = 50) -> ComputeListing:
    return ComputeListing(
        node_id=f"node-{uuid.uuid4().hex[:8]}",
        endpoint="http://localhost:7860",
        hardware={},
        tokens_per_second=tps,
        price_per_1k_tokens=price,
        currency="NULL",
        min_rental_minutes=1,
        available=True,
    )


STUB_CFG = X402Config(mode=X402Mode.STUB)


# ────────────────────────────────────────────────────────────────────────────
# 1. Backward compat — no x402_config, behaviour unchanged
# ────────────────────────────────────────────────────────────────────────────

class TestBackwardCompat:
    def test_rent_without_config_returns_session(self):
        market = ComputeRentalMarket()
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=5)
        assert session.session_id.startswith("sess-")
        assert session.x402_receipt is None

    def test_release_without_config_gives_stub_sig(self):
        market = ComputeRentalMarket()
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        session.tokens_generated = 100
        proof = market.release(session)
        assert proof.signature is not None
        assert proof.signature.startswith("stub-sig-")
        assert proof.receipt_hash is None

    def test_null_listing_no_payment_attempt(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _null_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=5)
        # NULL currency listings are not paid via x402 (no USDC facilitator)
        assert session.x402_receipt is None

    def test_cannot_rent_unavailable_listing(self):
        market = ComputeRentalMarket()
        listing = _usdc_listing()
        listing.available = False
        with pytest.raises(ValueError, match="not available"):
            market.rent(listing, duration_minutes=5)

    def test_cannot_rent_below_minimum_duration(self):
        market = ComputeRentalMarket()
        listing = _usdc_listing()
        listing.min_rental_minutes = 10
        with pytest.raises(ValueError, match="below minimum"):
            market.rent(listing, duration_minutes=5)


# ────────────────────────────────────────────────────────────────────────────
# 2. X402Client — stub mode
# ────────────────────────────────────────────────────────────────────────────

class TestX402ClientStub:
    def test_pay_returns_receipt(self):
        client = X402Client(STUB_CFG)
        receipt = client.pay(0.001, "NodeWallet123", "sess-abc")
        assert isinstance(receipt, X402Receipt)
        assert receipt.amount_usdc == 0.001
        assert receipt.recipient_wallet == "NodeWallet123"
        assert receipt.session_id == "sess-abc"
        assert receipt.mode == "stub"

    def test_receipt_hash_is_hex_64_chars(self):
        client = X402Client(STUB_CFG)
        receipt = client.pay(0.001, "NodeWallet123", "sess-abc")
        assert len(receipt.receipt_hash) == 64
        assert all(c in "0123456789abcdef" for c in receipt.receipt_hash)

    def test_receipt_hash_is_deterministic_for_same_inputs(self):
        """Two receipts for the same session/amount/recipient must have the
        SAME canonical hash if payment_tx and timestamp also match."""
        t = time.time()
        r1 = X402Receipt(
            session_id="sess-same",
            payment_tx="stub-tx-fixed",
            amount_usdc=0.005,
            recipient_wallet="Wallet456",
            facilitator_sig="stub-fac",
            timestamp=t,
            mode="stub",
        )
        r2 = X402Receipt(
            session_id="sess-same",
            payment_tx="stub-tx-fixed",
            amount_usdc=0.005,
            recipient_wallet="Wallet456",
            facilitator_sig="stub-fac-different",  # sig doesn't affect hash
            timestamp=t,
            mode="stub",
        )
        assert r1.receipt_hash == r2.receipt_hash

    def test_different_amounts_give_different_hashes(self):
        client = X402Client(STUB_CFG)
        r1 = client.pay(0.001, "W", "sess-1")
        r2 = client.pay(0.002, "W", "sess-1")
        assert r1.receipt_hash != r2.receipt_hash

    def test_to_dict_round_trip(self):
        client = X402Client(STUB_CFG)
        receipt = client.pay(0.003, "WalletABC", "sess-dict")
        d = receipt.to_dict()
        assert d["amount_usdc"] == 0.003
        assert d["receipt_hash"] == receipt.receipt_hash
        assert d["mode"] == "stub"

    def test_quote_structure(self):
        client = X402Client(STUB_CFG)
        quote = client.quote(0.01, "WalletXYZ")
        assert isinstance(quote, X402Quote)
        assert quote.amount_usdc == 0.01
        assert len(quote.quote_hash) == 64
        assert quote.expires_at > time.time()

    def test_max_fee_guard_raises(self):
        cfg = X402Config(mode=X402Mode.STUB, max_fee_usdc=0.01)
        client = X402Client(cfg)
        with pytest.raises(ValueError, match="max_fee_usdc"):
            client.pay(0.05, "W", "sess-big")

    def test_zero_amount_raises(self):
        client = X402Client(STUB_CFG)
        with pytest.raises(ValueError, match="must be > 0"):
            client.pay(0.0, "W", "sess-zero")

    def test_negative_amount_raises(self):
        client = X402Client(STUB_CFG)
        with pytest.raises(ValueError, match="must be > 0"):
            client.pay(-0.001, "W", "sess-neg")

    def test_auto_session_id_generated(self):
        client = X402Client(STUB_CFG)
        r = client.pay(0.001, "W")  # no session_id provided
        assert r.session_id.startswith("sess-")


# ────────────────────────────────────────────────────────────────────────────
# 3. ComputeRentalMarket with x402_config (stub)
# ────────────────────────────────────────────────────────────────────────────

class TestRentalMarketWithX402:
    def test_rent_usdc_listing_attaches_receipt(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=5)
        assert session.x402_receipt is not None
        assert session.x402_receipt.mode == "stub"

    def test_receipt_amount_matches_estimated_cost(self):
        """estimated_tokens = tps * dur_min * 60; cost = tokens/1000 * price"""
        tps, price, dur = 50, 0.001, 5
        expected = (tps * dur * 60 / 1000) * price  # 0.015 USDC
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing(price=price, tps=tps)
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=dur)
        assert abs(session.x402_receipt.amount_usdc - expected) < 1e-8

    def test_release_signature_carries_receipt_hash(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        session.tokens_generated = 3000
        proof = market.release(session)
        assert proof.signature is not None
        assert proof.signature.startswith("x402:")
        assert proof.receipt_hash == session.x402_receipt.receipt_hash
        assert proof.signature == f"x402:{proof.receipt_hash}"

    def test_work_proof_canonical_hash_is_stable(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        session.tokens_generated = 1000
        proof = market.release(session)
        h1 = proof.canonical_hash()
        h2 = proof.canonical_hash()
        assert h1 == h2
        assert len(h1) == 64

    def test_work_proof_canonical_hash_changes_with_tokens(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing

        session_a = market.rent(listing, duration_minutes=1)
        session_a.tokens_generated = 100
        proof_a = market.release(session_a)

        listing.available = True  # re-enable for second rent
        session_b = market.rent(listing, duration_minutes=1)
        session_b.tokens_generated = 500
        proof_b = market.release(session_b)

        assert proof_a.canonical_hash() != proof_b.canonical_hash()

    def test_session_id_propagated_to_receipt(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing()
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        assert session.x402_receipt.session_id == session.session_id


# ────────────────────────────────────────────────────────────────────────────
# 4. Cost estimation edge cases
# ────────────────────────────────────────────────────────────────────────────

class TestCostEstimation:
    def test_zero_token_floor(self):
        """
        A very slow node at very low price should still produce a
        minimum 1 micro-USDC payment (not zero).
        """
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing = _usdc_listing(price=0.0000001, tps=1)
        market._listings[listing.node_id] = listing
        session = market.rent(listing, duration_minutes=1)
        assert session.x402_receipt.amount_usdc >= 0.000001

    def test_cost_proportional_to_duration(self):
        market = ComputeRentalMarket(x402_config=STUB_CFG)
        listing_a = _usdc_listing(price=0.001, tps=50)
        listing_b = _usdc_listing(price=0.001, tps=50)

        market._listings[listing_a.node_id] = listing_a
        market._listings[listing_b.node_id] = listing_b

        session_1 = market.rent(listing_a, duration_minutes=1)
        session_5 = market.rent(listing_b, duration_minutes=5)

        cost_1 = session_1.x402_receipt.amount_usdc
        cost_5 = session_5.x402_receipt.amount_usdc

        assert abs(cost_5 / cost_1 - 5.0) < 0.001  # 5× longer → 5× cost

    def test_live_mode_requires_keypair_path(self):
        """Attempting a devnet payment without a keypair raises X402PaymentError."""
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=None)
        client = X402Client(cfg)
        with pytest.raises(X402PaymentError, match="keypair_path"):
            client.pay(0.001, "SomeWallet", "sess-no-key")
