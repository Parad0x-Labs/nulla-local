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

# Registration-specific words (used to catch a follow-up like "where do I send the
# registration fee" / "ok grab it and register" that no longer carries a .null token).
_REGISTRATION_MARKERS = (
    "register",
    "registration",
    "claim",
    "mint",
    "reserve",
)

# Fee / payment phrasing, and "you do it" execution phrasing.
_FEE_MARKERS = (
    "fee",
    "cost",
    "price",
    "how much",
    "send the",
    "where to send",
    "where do i send",
    "pay for",
    "pay the",
)
_EXEC_MARKERS = (
    "do it for me",
    "you do it",
    "you will do it",
    "you'll do it",
    "go ahead",
    "grab it",
    "buy it",
    "get it for me",
    "handle it",
)

WEB0_NULL_REGISTRATION_RESPONSE = (
    f"- Yes, but not as a normal ICANN/DNS purchase. In this stack, `.null` is a wallet-owned Web0 name on Solana `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`).\n"
    "- In the current pilot, registration is free — there is no fee and no address to send one to. Your wallet just needs a little SOL (about 0.003) for the on-chain account rent, which stays in the account you own. (That is the live on-chain config and can change, so it is read on-chain before any real registration.)\n"
    "- Local NULLA can check/resolve names now with `nulla resolve <name>.null`; it can't sign or submit a registration for you yet, and any dial/x402 payment stays opt-in and needs explicit spend approval."
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


def _looks_like_registration_fee_or_exec(text: str) -> bool:
    """Catch a registration follow-up whose .null token was in a previous turn.

    Fires only when the message combines a registration word with either fee/payment
    phrasing or an "you do it" execution phrase, e.g. "ok lets grab it, tell me where to
    send the registration fee and you will do it for me". Without this, that turn carries
    no web0/.null marker, so the grounding responder is skipped and the tool loop returns
    the "I couldn't map that cleanly to a real action" dead-end.
    """
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if not any(marker in clean for marker in _REGISTRATION_MARKERS):
        return False
    return any(marker in clean for marker in _FEE_MARKERS) or any(marker in clean for marker in _EXEC_MARKERS)


def _named_registration_response(name: str) -> str:
    return (
        f"- I can help you check `{name}`, but I will not sign, spend, or submit a wallet transaction automatically.\n"
        f"- First run `nulla resolve {name}`. If it resolves, it is already owned; if it returns no record, `{name}` is free to register.\n"
        f"- In the current pilot there is no registration fee to send anywhere — your wallet just needs about 0.003 SOL for the on-chain account rent. Automatic register-and-sign isn't wired into NULLA yet; when it is, it will ask for explicit approval and a wallet prompt against `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`)."
    )


def _registration_fee_or_exec_response(name: str) -> str:
    resolve_target = name if name else "<name>.null"
    name_phrase = f"`{name}` " if name else ""
    return (
        f"- There's no registration fee to send anywhere. In the current pilot, registering a {name_phrase}`.null` name is free — no fee, and no recipient address to send one to.\n"
        "- The only cost is a small amount of SOL (about 0.003) for the on-chain account rent, paid from and held in your own wallet. It's read live from the registrar config before any real registration, since that can change.\n"
        f"- I can't sign and submit the registration for you yet — right now I resolve/check names (`nulla resolve {resolve_target}`). Automatic register-and-sign is coming, but only behind explicit approval and a wallet confirmation, never automatic."
    )


def looks_like_web0_null_registration_question(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if not any(marker in clean for marker in _WEB0_NULL_MARKERS):
        return False
    return any(marker in clean for marker in _BUY_REGISTER_MARKERS)


def web0_null_project_response(text: str) -> dict[str, Any] | None:
    null_name = _extract_null_name(text)
    if looks_like_web0_null_registration_question(text):
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
    # Registration follow-up ("where's the fee" / "grab it and do it") whose .null token
    # lived in an earlier turn: answer it correctly instead of dead-ending in the tool loop.
    if _looks_like_registration_fee_or_exec(text):
        return {
            "response": _registration_fee_or_exec_response(null_name),
            "confidence": 1.0,
            "source": "web0_project_grounding",
            "deterministic": True,
            "intent": "web0_null_registration_fee_followup",
            "null_name": null_name,
        }
    return None
