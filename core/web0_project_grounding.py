from __future__ import annotations

import re
from typing import Any

NULL_REGISTRAR_V2_PROGRAM = "NXgQhepFpDCu935H1D4g34g59ZYbo1jR4tBCZWhV8Np"
_NULL_NAME_RE = re.compile(r"\b([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\.null\b", re.IGNORECASE)

_WEB0_NULL_MARKERS = (
    ".null",
    "dot null",
    "web0",
    "null registrar",
    "null_registrar",
    "nulldomain",
    "nullpay",
    "null://",
)

_BUY_REGISTER_MARKERS = (
    "address",
    "auction",
    "available",
    "availability",
    "bid",
    "buy",
    "domain",
    "get",
    "name",
    "purchase",
    "register",
    "registration",
    "resolve",
)

WEB0_NULL_REGISTRATION_RESPONSE = (
    f"- Yes, but not as a normal ICANN/DNS purchase. In this stack, `.null` is a wallet-owned Web0 name on Solana `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`).\n"
    "- Current project docs describe the registrar as live/pilot=free; registration uses Web0/null-sdk or local project tooling with wallet confirmation, not a public registrar checkout.\n"
    "- Local NULLA can check/resolve names with `nulla resolve <name>.null`; dial/x402 payment actions stay opt-in and require explicit spend approval."
)


def _extract_null_name(text: str) -> str:
    match = _NULL_NAME_RE.search(str(text or ""))
    if not match:
        return ""
    return f"{match.group(1).lower()}.null"


def _looks_like_named_registration_request(text: str, name: str) -> bool:
    if not name:
        return False
    clean = " ".join(str(text or "").lower().split())
    return any(
        marker in clean
        for marker in (
            "buy",
            "claim",
            "get",
            "help me",
            "i want",
            "mint",
            "register",
            "registration",
            "reserve",
        )
    )


def _named_registration_response(name: str) -> str:
    return (
        f"- I can help you check `{name}`, but I will not sign, spend, or submit a wallet transaction automatically.\n"
        f"- First run `nulla resolve {name}`. If it resolves, it is already owned/configured; if it returns no record, treat `{name}` as the candidate to register.\n"
        f"- Registration then goes through the local Web0/null-sdk or project registrar tooling against `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`); approve only the exact name and wallet prompt you intend."
    )


def looks_like_web0_null_registration_question(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if not any(marker in clean for marker in _WEB0_NULL_MARKERS):
        return False
    return any(marker in clean for marker in _BUY_REGISTER_MARKERS)


def web0_null_project_response(text: str) -> dict[str, Any] | None:
    if not looks_like_web0_null_registration_question(text):
        return None
    null_name = _extract_null_name(text)
    if _looks_like_named_registration_request(text, null_name):
        return {
            "response": _named_registration_response(null_name),
            "confidence": 1.0,
            "source": "web0_project_grounding",
            "deterministic": True,
            "intent": "web0_null_named_registration_workflow",
            "null_name": null_name,
        }
    return {
        "response": WEB0_NULL_REGISTRATION_RESPONSE,
        "confidence": 1.0,
        "source": "web0_project_grounding",
        "deterministic": True,
    }
