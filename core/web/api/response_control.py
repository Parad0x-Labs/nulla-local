from __future__ import annotations

import re
from typing import Any

_EXACT_RESPONSE_RE = re.compile(
    r"^\s*(?:reply|respond|answer|return|say|output|print)\s+"
    r"(?:with\s+)?exactly"
    r"(?:\s+(?:this|the)\s+(?:marker|text|string|phrase|token))?"
    r"(?:\s+and\s+(?:nothing\s+else|no\s+other\s+text|no\s+extra\s+text|no\s+extra\s+characters))?"
    r"\s*:?\s*(?P<target>.+?)\s*"
    r"(?:\s+(?:and\s+)?(?:nothing\s+else|no\s+other\s+text|no\s+extra\s+text|no\s+extra\s+characters))?"
    r"[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)

_WRAPPED_TARGET_RE = re.compile(r"^[`\"'](?P<value>.*?)[`\"']$", re.DOTALL)


def exact_response_target(user_text: str) -> str:
    match = _EXACT_RESPONSE_RE.match(str(user_text or "").strip())
    if not match:
        return ""
    target = _clean_exact_target(match.group("target"))
    if not target or "\n" in target or len(target) > 240:
        return ""
    return target


def apply_exact_response_control(result: dict[str, Any], user_text: str) -> dict[str, Any]:
    target = exact_response_target(user_text)
    if not target:
        return result
    response = str(dict(result or {}).get("response") or "").strip()
    if response == target:
        return result
    controlled = dict(result or {})
    controlled["response"] = target
    controlled["response_control"] = {
        "mode": "exact_target",
        "target": target,
        "original_response_excerpt": response[:240],
    }
    return controlled


def _clean_exact_target(raw_target: str) -> str:
    target = str(raw_target or "").strip()
    target = re.sub(r"\s+", " ", target)
    wrapped = _WRAPPED_TARGET_RE.match(target)
    if wrapped:
        target = wrapped.group("value").strip()
    return target.strip()
