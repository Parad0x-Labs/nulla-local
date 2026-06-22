from __future__ import annotations

import base64
import contextlib
import os
import threading

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
    # Anchor against a 'finalized' blockhash so the tx commits to a rooted slot
    # (no risk of building on a forked/dropped recent block).
    result = _rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
    if isinstance(result, dict):
        value = result.get("value") or {}
        bh = value.get("blockhash")
        if bh:
            return str(bh)
    return None


def parse_signature_status(result: object) -> dict[str, object] | None:
    """Pull the single per-signature status out of a getSignatureStatuses result.

    Pure parser (no network) so it is trivially testable against a fixture.
    Returns the status dict (with ``confirmationStatus``, ``err``, ``slot``) for
    the first/only signature, or None when the signature is unknown to the RPC
    (the ``value`` slot is null) or the shape is unexpected.
    """
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    return first if isinstance(first, dict) else None


def confirm_signature(signature: str, *, commitment: str = "finalized") -> dict[str, object] | None:
    """OPTIONAL, light landed-confirmation check for an anchor tx signature.

    Does a single getSignatureStatuses call (with searchTransactionHistory) and
    returns the parsed status dict, or None if unknown/unavailable. This is NOT
    wired into the broadcast hot path — broadcasting stays fire-and-forget so the
    finalize path runs at full speed; callers opt in to confirmation explicitly.
    """
    if not signature:
        return None
    try:
        result = _rpc_call(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
        )
    except Exception:
        return None
    status = parse_signature_status(result)
    if status is None:
        return None
    # Surface the requested commitment alongside the raw status for callers that
    # want to compare without re-passing it.
    return {"requested_commitment": commitment, **status}


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


def _anchor_and_persist(parent_task_id: str, final_response_hash: str, confidence: float) -> None:
    """Run the blocking anchor broadcast and persist its signature.

    Designed to run inside a background worker thread. ``anchor_vault_proof``
    already fails closed (returns None, never raises), but we wrap the whole
    body so a stray error in the persistence step can never surface as an
    unhandled-thread traceback. On a successful broadcast the real tx signature
    is written onto the finalized row so the receipt links to its on-chain proof.
    """
    try:
        # Import lazily so this module has no hard dependency on the store at
        # import time (and to keep the dependency arrow one-directional).
        from core.final_response_store import set_anchored_signature

        signature = anchor_vault_proof(parent_task_id, final_response_hash, confidence)
        if signature:
            set_anchored_signature(parent_task_id, signature)
    except Exception as exc:  # pragma: no cover - defensive; anchor already fails closed
        with contextlib.suppress(Exception):
            audit_logger.log(
                "solana_anchor_async_error",
                target_id=parent_task_id,
                target_type="task",
                details={"error": str(exc)},
            )


def dispatch_anchor_in_background(
    parent_task_id: str, final_response_hash: str, confidence: float
) -> threading.Thread | None:
    """Fire the gated anchor broadcast off the caller's hot path.

    Broadcasting does serial blocking RPC (getLatestBlockhash + sendTransaction
    over several endpoints), so it must never run inline on the finalize turn.
    When anchoring is opted in (NULLA_ANCHOR_RECEIPTS=1) this spawns a daemon
    worker that does the broadcast and persists the resulting signature, then
    returns immediately with the started thread (so callers/tests can join it).

    A strict no-op when anchoring is disabled: returns None without touching the
    network or spawning a thread.
    """
    if not anchor_enabled():
        return None
    try:
        worker = threading.Thread(
            target=_anchor_and_persist,
            args=(parent_task_id, final_response_hash, confidence),
            daemon=True,
        )
        worker.start()
        return worker
    except Exception as exc:  # pragma: no cover - thread spawn failure is rare
        with contextlib.suppress(Exception):
            audit_logger.log(
                "solana_anchor_spawn_error",
                target_id=parent_task_id,
                target_type="task",
                details={"error": str(exc)},
            )
        return None


__all__ = [
    "anchor_enabled",
    "anchor_vault_proof",
    "build_memo_anchor_message",
    "confirm_signature",
    "dispatch_anchor_in_background",
    "parse_signature_status",
    "submit_memo_anchor",
]
