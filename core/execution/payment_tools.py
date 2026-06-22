"""Gated agent-to-agent x402 payment tools.

Two intents close the compute payment loop:

- ``pay.x402`` — BUY external x402-gated compute. SAFE BY DEFAULT: it never
  spends USDC on its own. Without an explicit per-call opt-in (``allow_spend``
  + ``approve``) it returns a ``user_action_required`` execution carrying the
  live quote so the caller can confirm. Only when the caller has both opted in
  *and* approved, supplied a hard ``max_spend_usdc`` cap, and a real wallet is
  present does it back the call with the protocol-correct
  :func:`core.web0_tools.dna_pay_and_unlock` (which itself re-checks the cap and
  the wallet balance and signs only the server-built transaction).

- ``sell.quote`` — QUOTE NULLA's own compute for another agent. Read-only: it
  builds and returns the :class:`~core.x402.client.X402Quote` that
  :func:`core.null_protocol.resolve_null_request` already derives. No spend, no
  signing, no wallet. A ``.null`` target can be resolved to its x402 endpoint
  with :func:`core.null_resolver.resolve_x402_endpoint`.
"""
from __future__ import annotations

import contextlib
from typing import Any

from core.execution.models import ToolIntentExecution, _tool_observation
from core.null_protocol import NullProtocolError, resolve_null_request
from core.null_resolver import resolve_x402_endpoint
from core.web0_tools import dna_get_quote, dna_pay_and_unlock

# Hard ceiling for a single agent-initiated buy. The per-call ``max_spend_usdc``
# cap is clamped to this, so even an over-large opt-in can never exceed it.
_MAX_SPEND_CEILING_USDC = 1.0
_DEFAULT_MAX_SPEND_USDC = 0.05


def execute_payment_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
    dna_pay_and_unlock_fn=dna_pay_and_unlock,
    dna_get_quote_fn=dna_get_quote,
    resolve_null_request_fn=resolve_null_request,
    resolve_x402_endpoint_fn=resolve_x402_endpoint,
) -> ToolIntentExecution:
    if intent == "sell.quote":
        return _execute_sell_quote(
            arguments,
            resolve_null_request_fn=resolve_null_request_fn,
            resolve_x402_endpoint_fn=resolve_x402_endpoint_fn,
        )
    if intent == "pay.x402":
        return _execute_pay_x402(
            arguments,
            source_context=source_context,
            dna_pay_and_unlock_fn=dna_pay_and_unlock_fn,
            dna_get_quote_fn=dna_get_quote_fn,
            resolve_x402_endpoint_fn=resolve_x402_endpoint_fn,
        )
    return _failed(
        intent,
        status="unsupported",
        response_text=f"`{intent}` is not a wired payment tool on this runtime.",
    )


def _execute_sell_quote(
    arguments: dict[str, Any],
    *,
    resolve_null_request_fn,
    resolve_x402_endpoint_fn,
) -> ToolIntentExecution:
    resource = _text(arguments.get("resource") or arguments.get("resource_url") or arguments.get("uri"))
    null_target = _text(arguments.get("null_name") or arguments.get("target"))
    resolved_endpoint = ""
    if null_target:
        try:
            resolved_endpoint = _text(resolve_x402_endpoint_fn(null_target))
        except Exception:
            resolved_endpoint = ""
    if not resource:
        # Default to a metered "task" service quote; an explicit ?price= on the
        # URI still wins inside resolve_null_request.
        resource = "null://task/quote"
    try:
        request = resolve_null_request_fn(resource)
    except NullProtocolError as exc:
        return _failed(
            "sell.quote",
            status="invalid_request",
            response_text=f"Could not build a quote for `{resource}`: {exc}",
        )
    quote = request.quote
    if quote is None:
        return _failed(
            "sell.quote",
            status="no_quote",
            response_text=f"No quote could be derived for `{resource}`.",
        )
    quote_payload = {
        "amount_usdc": float(quote.amount_usdc),
        "recipient_wallet": str(quote.recipient_wallet),
        "facilitator_url": str(quote.facilitator_url),
        "usdc_mint": str(quote.usdc_mint),
        "quote_hash": str(quote.quote_hash),
        "expires_at": float(quote.expires_at),
        "service": str(request.uri.service),
        "path": str(request.uri.path),
    }
    if resolved_endpoint:
        quote_payload["resolved_x402_endpoint"] = resolved_endpoint
    response_text = (
        f"Quote for `{request.uri.service}` compute: {quote_payload['amount_usdc']:.6f} USDC "
        f"to {quote_payload['recipient_wallet']} (quote {quote_payload['quote_hash'][:12]}…). "
        "Read-only — no payment was made."
    )
    return ToolIntentExecution(
        handled=True,
        ok=True,
        status="quoted",
        response_text=response_text,
        mode="tool_executed",
        tool_name="sell.quote",
        details={
            "quote": quote_payload,
            "resource": resource,
            "observation": _tool_observation(
                intent="sell.quote",
                tool_surface="x402_market",
                ok=True,
                status="quoted",
                resource=resource,
                quote=quote_payload,
            ),
        },
    )


def _execute_pay_x402(
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
    dna_pay_and_unlock_fn,
    dna_get_quote_fn,
    resolve_x402_endpoint_fn,
) -> ToolIntentExecution:
    resource = _text(arguments.get("resource") or arguments.get("resource_url") or arguments.get("url"))
    null_target = _text(arguments.get("null_name") or arguments.get("target"))
    if not resource and null_target:
        try:
            resource = _text(resolve_x402_endpoint_fn(null_target))
        except Exception:
            resource = ""
    if not resource:
        return _failed(
            "pay.x402",
            status="missing_resource",
            response_text="pay.x402 needs a `resource` URL (or a resolvable `.null` `null_name`).",
        )
    privacy_path = _text(arguments.get("privacy_path")) or "normal"
    allow_spend = bool(arguments.get("allow_spend", False))
    approved = bool(arguments.get("approve", False) or arguments.get("approved", False))
    requested_cap = _coerce_float(arguments.get("max_spend_usdc"), default=_DEFAULT_MAX_SPEND_USDC)
    max_spend_usdc = max(0.0, min(requested_cap, _MAX_SPEND_CEILING_USDC))

    wallet = (source_context or {}).get("nulla_wallet")

    # Default SAFE path: any missing opt-in / approval / wallet → no spend.
    if not allow_spend or not approved or wallet is None:
        quote_payload = _preview_quote(
            resource,
            privacy_path,
            dna_get_quote_fn=dna_get_quote_fn,
        )
        reason = _user_action_reason(
            allow_spend=allow_spend,
            approved=approved,
            wallet_present=wallet is not None,
        )
        amount_hint = ""
        if isinstance(quote_payload, dict) and quote_payload.get("amount_usdc") is not None:
            amount_hint = f" Quoted amount: {float(quote_payload['amount_usdc']):.6f} USDC."
        response_text = (
            f"Holding off on paying for `{resource}` until you confirm. {reason}"
            f"{amount_hint} Re-call pay.x402 with allow_spend=true, approve=true, and a "
            f"max_spend_usdc cap (≤ {_MAX_SPEND_CEILING_USDC:.2f}) to authorize the buy."
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="user_action_required",
            response_text=response_text,
            user_safe_response_text=response_text,
            mode="tool_preview",
            tool_name="pay.x402",
            details={
                "resource": resource,
                "privacy_path": privacy_path,
                "allow_spend": allow_spend,
                "approved": approved,
                "wallet_present": wallet is not None,
                "max_spend_usdc": max_spend_usdc,
                "quote": quote_payload if isinstance(quote_payload, dict) else None,
                "action_required": {
                    "intent": "pay.x402",
                    "confirm_arguments": {
                        "resource": resource,
                        "privacy_path": privacy_path,
                        "allow_spend": True,
                        "approve": True,
                        "max_spend_usdc": max_spend_usdc,
                    },
                },
                "observation": _tool_observation(
                    intent="pay.x402",
                    tool_surface="x402_market",
                    ok=False,
                    status="user_action_required",
                    resource=resource,
                    max_spend_usdc=max_spend_usdc,
                    reason=reason,
                ),
            },
        )

    # Authorized path: explicit opt-in + approval + a real wallet. The gated fn
    # re-checks the cap and the on-chain balance and signs only the server tx.
    result = dna_pay_and_unlock_fn(
        resource,
        wallet,
        max_spend_usdc=max_spend_usdc,
        privacy_path=privacy_path,
        allow_spend=True,
    )
    if result.get("error"):
        return _failed(
            "pay.x402",
            status=str(result.get("error") or "payment_failed"),
            response_text=f"pay.x402 did not complete: {result.get('error')}.",
            extra_details={"resource": resource, "result": result},
        )
    amount_paid = float(result.get("amount_paid_usdc") or 0.0)
    response_text = (
        f"Paid {amount_paid:.6f} USDC for `{resource}` (receipt {_text(result.get('receipt_id')) or 'n/a'})."
    )
    return ToolIntentExecution(
        handled=True,
        ok=True,
        status="paid",
        response_text=response_text,
        mode="tool_executed",
        tool_name="pay.x402",
        details={
            "resource": resource,
            "amount_paid_usdc": amount_paid,
            "receipt_id": _text(result.get("receipt_id")),
            "max_spend_usdc": max_spend_usdc,
            "result": result,
            "observation": _tool_observation(
                intent="pay.x402",
                tool_surface="x402_market",
                ok=True,
                status="paid",
                resource=resource,
                amount_paid_usdc=amount_paid,
                receipt_id=_text(result.get("receipt_id")),
            ),
        },
    )


def _preview_quote(
    resource: str,
    privacy_path: str,
    *,
    dna_get_quote_fn,
) -> dict[str, Any] | None:
    try:
        quote = dna_get_quote_fn(resource, privacy_path)
    except Exception:
        return None
    if not isinstance(quote, dict) or quote.get("error"):
        return None
    amount = quote.get("amountUsdc")
    if amount is None:
        amount = quote.get("amount")
    payload: dict[str, Any] = {"resource": resource, "privacy_path": privacy_path}
    if amount is not None:
        with contextlib.suppress(TypeError, ValueError):
            payload["amount_usdc"] = float(amount)
    return payload


def _user_action_reason(*, allow_spend: bool, approved: bool, wallet_present: bool) -> str:
    if not allow_spend:
        return "No spend opt-in was given (allow_spend is off)."
    if not approved:
        return "The buy is not yet approved (approve is off)."
    if not wallet_present:
        return "No spending wallet is wired into this runtime."
    return "Explicit confirmation is required before any USDC is spent."


def _failed(
    intent: str,
    *,
    status: str,
    response_text: str,
    extra_details: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    details: dict[str, Any] = dict(extra_details or {})
    details["observation"] = _tool_observation(
        intent=intent,
        tool_surface="x402_market",
        ok=False,
        status=status,
    )
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response_text,
        user_safe_response_text=response_text,
        mode="tool_failed",
        tool_name=intent,
        details=details,
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)
