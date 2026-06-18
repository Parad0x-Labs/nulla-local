from __future__ import annotations

from typing import Any

from core import policy_engine

from .fast_live_info_runtime_results import disabled_live_info_result


def prepare_live_info_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
) -> tuple[str, str, dict[str, Any] | None]:
    live_mode = agent._live_info_mode(user_input, interpretation=interpretation)
    recovered_query = agent._recover_price_lookup_query(
        user_input,
        source_context=source_context,
    )
    if not live_mode and recovered_query:
        live_mode = "fresh_lookup"
    if not live_mode:
        return "", "", None
    if _explicit_remote_fetch_disabled(source_context):
        return "", "", None
    if not policy_engine.allow_web_fallback():
        return live_mode, "", disabled_live_info_result(
            agent,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )

    query = recovered_query or agent._normalize_live_info_query(user_input, mode=live_mode)
    if agent._requires_ultra_fresh_insufficient_evidence(user_input):
        response = agent._ultra_fresh_insufficient_evidence_response(query=query)
        return live_mode, query, agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="live_info_insufficient_evidence",
        )
    return live_mode, query, None


def _explicit_remote_fetch_disabled(source_context: dict[str, object] | None) -> bool:
    if not isinstance(source_context, dict) or "allow_remote_fetch" not in source_context:
        return False
    # Explicit false means the caller is forcing a local-only turn. Do not let
    # the live-info fast path reinterpret a trusted surface as permission to browse.
    return not bool(source_context.get("allow_remote_fetch"))
