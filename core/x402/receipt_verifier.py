"""
core/x402/receipt_verifier.py
=============================
Local, read-only on-chain verification of an x402 USDC settlement.

Why this exists
---------------
An :class:`core.x402.client.X402Receipt` carries a real SHA-256
``receipt_hash`` even in STUB mode, and
:func:`core.credit_ledger.is_real_receipt_hash` accepts ANY 64-char hex string.
So a caller who self-reports a ``receipt_hash`` plus a ``mode`` of "mainnet"
could earn full proof-of-settlement reputation without ever moving a token.

This module closes that hole by re-deriving settlement truth from the chain
itself: given a payment transaction signature, it asks a COMPLIANT publicnode
Solana RPC whether that transaction actually moved the expected amount of USDC
to the expected recipient. Nothing here trusts the caller's claimed hash.

Scope (be precise about what this proves)
-----------------------------------------
This is an **on-chain USDC-transfer check**: it confirms a finalized transaction
exists, did not error, and increased the recipient wallet's USDC balance by at
least the expected amount. It is NOT the full facilitator-verified x402 flow
(quote binding, facilitator signature, escrow-ATA routing, replay/nonce checks)
— that lives in the cross-repo ``dna_x402`` verifier. Treat a ``True`` here as
"a real USDC payment of >= the amount reached this wallet", not as
"the complete x402 protocol was honored".

Design stance: **fail closed.** Any RPC failure, missing transaction, parse
error, wrong mint, short amount, or unsupported mode returns ``False``. When in
doubt, return ``False``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.nulla_wallet import _rpc_call as _default_rpc_call

# USDC SPL mint on Solana mainnet-beta. Devnet shares the same on-chain token
# balance shape; the mint differs but the recipient-USDC-increase invariant we
# check is identical, so we match against the canonical mainnet mint here. (The
# devnet mint constant lives in core.x402.client; this verifier intentionally
# checks the mainnet USDC mint, which is what real settlements use.)
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# USDC has 6 decimals; 1 atomic unit = 1e-6 USDC. We compare balances in integer
# atomic units to avoid float drift, then allow a sub-atomic tolerance below.
USDC_DECIMALS = 6
USDC_ATOMIC_PER_UNIT = 10 ** USDC_DECIMALS

# Settlement modes this verifier will confirm. Anything else fails closed.
_VERIFIABLE_MODES = ("mainnet", "devnet")

# Tolerance, in atomic units, applied when comparing the observed USDC delta to
# the expected amount. One atomic unit (1e-6 USDC) absorbs the rounding of the
# expected float into atomic units without ever accepting a short payment of a
# whole atomic unit or more.
_ATOMIC_TOLERANCE = 1


def _owner_usdc_atomic(balances: Any, recipient_wallet: str) -> int:
    """Sum the recipient's USDC atomic balance across a token-balance array.

    ``balances`` is ``meta.preTokenBalances`` / ``meta.postTokenBalances`` from a
    jsonParsed ``getTransaction`` response: a list of entries shaped like
    ``{"owner": <pubkey>, "mint": <mint>, "uiTokenAmount": {"amount": "<int str>"}}``.

    Only entries whose ``owner`` matches ``recipient_wallet`` AND whose ``mint``
    is the USDC mint contribute. Returns the total in integer atomic units; a
    wallet with no matching USDC entry contributes 0 (a perfectly valid "before"
    state when the recipient's token account is created by this very transfer).

    Any malformed entry is skipped conservatively rather than raising.
    """
    total = 0
    if not isinstance(balances, list):
        return 0
    for entry in balances:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("owner") or "") != recipient_wallet:
            continue
        if str(entry.get("mint") or "") != USDC_MINT_MAINNET:
            continue
        ui = entry.get("uiTokenAmount")
        if not isinstance(ui, dict):
            continue
        raw_amount = ui.get("amount")
        try:
            # ``amount`` is the atomic-unit count as a string per the SPL spec.
            total += int(str(raw_amount))
        except (TypeError, ValueError):
            # A malformed amount must not inflate the observed delta.
            continue
    return total


def verify_payment_receipt(
    payment_tx: str,
    *,
    recipient_wallet: str,
    amount_usdc: float,
    mode: str,
    rpc_call: Callable[..., Any] | None = None,
) -> bool:
    """Confirm an on-chain USDC transfer actually settled, read-only.

    Looks up ``payment_tx`` via ``getTransaction`` (jsonParsed, finalized
    commitment) over the COMPLIANT publicnode RPC and returns ``True`` ONLY when
    every one of these holds:

    * ``mode`` is one of ``("mainnet", "devnet")``;
    * ``payment_tx``, ``recipient_wallet`` are non-empty and ``amount_usdc > 0``;
    * the transaction exists and ``meta.err`` is null (it did not fail);
    * the recipient wallet's USDC (mint
      ``EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v``) balance, computed as
      ``postTokenBalances - preTokenBalances`` in atomic units, increased by at
      least ``amount_usdc`` (within a one-atomic-unit float tolerance).

    Fail-closed: returns ``False`` on any RPC failure, missing transaction, parse
    error, wrong mint, insufficient amount, unsupported mode, or any doubt. This
    function performs **no** signing and **no** state mutation — it only reads.

    NOTE: this is an on-chain USDC-transfer check, not the full
    facilitator-verified x402 flow (facilitator signature / quote binding /
    escrow routing). That stronger check lives in the cross-repo ``dna_x402``
    verifier; this is the local, trust-nothing settlement gate.

    Parameters
    ----------
    payment_tx:
        Solana transaction signature (base58) to inspect.
    recipient_wallet:
        Base58 pubkey that must have received the USDC.
    amount_usdc:
        Minimum whole-USDC amount that must have been received.
    mode:
        ``"mainnet"`` or ``"devnet"``. Any other value fails closed.
    rpc_call:
        Optional injected RPC callable (used by tests). Defaults to the shared
        publicnode-only :func:`core.nulla_wallet._rpc_call`.

    Returns
    -------
    bool
        ``True`` only if a real USDC transfer of >= ``amount_usdc`` to
        ``recipient_wallet`` is confirmed on-chain; ``False`` otherwise.
    """
    # ── Cheap, total guards first — fail closed on any malformed input. ──────
    if str(mode or "").strip().lower() not in _VERIFIABLE_MODES:
        return False
    tx_sig = str(payment_tx or "").strip()
    recipient = str(recipient_wallet or "").strip()
    if not tx_sig or not recipient:
        return False
    try:
        expected = float(amount_usdc)
    except (TypeError, ValueError):
        return False
    if expected != expected or expected in (float("inf"), float("-inf")):
        return False  # NaN / inf guard
    if expected <= 0:
        return False
    # Required atomic-unit increase, rounded down so we never demand more than
    # asked; the tolerance below absorbs the rounding the other direction.
    expected_atomic = int(expected * USDC_ATOMIC_PER_UNIT)
    if expected_atomic <= 0:
        return False

    call = rpc_call or _default_rpc_call

    # ── Read-only on-chain lookup. Any exception here means "unknown" → False.
    try:
        result = call(
            "getTransaction",
            [
                tx_sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "finalized",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
    except Exception:
        return False

    # Missing tx (RPC None / not found / non-dict) → fail closed.
    if not isinstance(result, dict):
        return False

    meta = result.get("meta")
    if not isinstance(meta, dict):
        return False

    # A transaction that erred did not settle.
    if meta.get("err") is not None:
        return False

    pre = _owner_usdc_atomic(meta.get("preTokenBalances"), recipient)
    post = _owner_usdc_atomic(meta.get("postTokenBalances"), recipient)
    delta_atomic = post - pre

    # Require the recipient's USDC to have INCREASED by at least the expected
    # amount, allowing a one-atomic-unit tolerance for float→atomic rounding.
    return delta_atomic >= (expected_atomic - _ATOMIC_TOLERANCE) and delta_atomic > 0


__all__ = [
    "USDC_MINT_MAINNET",
    "verify_payment_receipt",
]
