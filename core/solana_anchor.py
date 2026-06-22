from __future__ import annotations

import base64
import os

from core import audit_logger

# Reuse the ONE compliant RPC path (publicnode only — never api.mainnet-beta,
# which 403s with an Origin header) and the base58 codec + wallet loader.
from core.nulla_wallet import _rpc_call, b58encode, get_or_create_wallet

# solders builds the canonical transaction message; the wallet signs the exact
# serialized bytes, so no raw keypair leaves the wallet abstraction.
try:
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.pubkey import Pubkey
except ImportError:  # solders not installed -> anchoring degrades to no-op
    Hash = Instruction = AccountMeta = Message = Pubkey = None  # type: ignore[assignment]

# SPL Memo program — arbitrary on-chain note, the safe minimal anchor (no
# program-specific account layout to get wrong). Every anchored receipt becomes
# a clickable Solana transaction whose memo carries the work-receipt hash.
_MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
_ANCHOR_TAG = b"nulla-receipt:"


def anchor_enabled() -> bool:
    """True only when receipt anchoring is explicitly opted in (NULLA_ANCHOR_RECEIPTS=1).

    A real anchor broadcasts a SOL-spending memo tx, so it must never fire by
    default. Both call sites (finalizer + the API service) gate on this single
    helper so they cannot drift apart.
    """
    return os.environ.get("NULLA_ANCHOR_RECEIPTS") == "1"


def build_memo_anchor_message(payer_pubkey: str, payload_hash: str, recent_blockhash: str) -> bytes:
    """Serialize a single-signer SPL-Memo message carrying the receipt hash.

    Pure + deterministic (no network, no signing). The fee payer is the only
    signer; the memo data is ``nulla-receipt:<hash>``.
    """
    if Pubkey is None:
        raise RuntimeError("solders is not installed")
    payer = Pubkey.from_string(payer_pubkey)
    memo = Pubkey.from_string(_MEMO_PROGRAM_ID)
    ix = Instruction(
        program_id=memo,
        accounts=[AccountMeta(pubkey=payer, is_signer=True, is_writable=True)],
        data=_ANCHOR_TAG + payload_hash.encode("utf-8"),
    )
    msg = Message.new_with_blockhash([ix], payer, Hash.from_string(recent_blockhash))
    return bytes(msg)


def _latest_blockhash() -> str | None:
    result = _rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
    if isinstance(result, dict):
        value = result.get("value") or {}
        bh = value.get("blockhash")
        if bh:
            return str(bh)
    return None


def submit_memo_anchor(payload_hash: str) -> str | None:
    """Build, sign, and broadcast the memo anchor over the compliant RPC.

    Returns the transaction signature (base58) on success, None on any failure
    (missing libs, no blockhash, unfunded wallet, RPC error) — fail-closed, never
    raises into the caller's hot path. Broadcasting spends a small SOL fee.
    """
    if Pubkey is None:
        return None
    try:
        wallet = get_or_create_wallet()
        blockhash = _latest_blockhash()
        if not blockhash:
            return None
        message_bytes = build_memo_anchor_message(wallet.pubkey, payload_hash, blockhash)
        signature = wallet.sign_transaction(message_bytes)  # ed25519 over the exact message
        # Legacy wire tx: compact-array(1 signature) + message.
        wire = bytes([1]) + signature + message_bytes
        b64 = base64.b64encode(wire).decode("ascii")
        result = _rpc_call("sendTransaction", [b64, {"encoding": "base64"}])
        if isinstance(result, str) and result:
            return result
        # Fall back to the locally-computed signature id if the RPC echoed nothing.
        return b58encode(signature) if result is not None else None
    except Exception:
        return None


def anchor_vault_proof(parent_task_id: str, final_response_hash: str, confidence: float) -> str | None:
    """Anchor a proof of the finalized parent task on Solana as an SPL-Memo.

    Returns the real transaction signature, or None if anchoring is unavailable
    (no solders, no funded wallet, or network down) — fails silently so the
    finalize path is never blocked. Call sites gate this behind an env flag.
    """
    try:
        signature = submit_memo_anchor(final_response_hash)
        if not signature:
            return None
        audit_logger.log(
            "solana_proof_anchored",
            target_id=parent_task_id,
            target_type="task",
            details={"signature": signature, "confidence": confidence},
        )
        return signature
    except Exception as e:
        audit_logger.log(
            "solana_proof_failed",
            target_id=parent_task_id,
            target_type="task",
            details={"error": str(e)},
        )
        return None


__all__ = ["anchor_enabled", "anchor_vault_proof", "build_memo_anchor_message", "submit_memo_anchor"]
