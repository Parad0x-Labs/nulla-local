from __future__ import annotations

from typing import Any

from core import audit_logger, policy_engine
from core.autonomous_topic_research import research_topic_from_signal
from core.curiosity_roamer import CuriosityRoamer
from core.execution import capabilities as execution_capabilities
from core.execution.constants import (
    _HIVE_TOOL_INTENTS,
    _MUTATING_OPERATOR_INTENTS,
    _PAYMENT_TOOL_INTENTS,
    _READ_ONLY_OPERATOR_INTENTS,
    _WEB_TOOL_INTENTS,
)
from core.execution.hive_tools import execute_hive_list_available as _execute_hive_list_available_impl
from core.execution.hive_tools import execute_hive_tool as _execute_hive_tool_impl
from core.execution.hive_tools import failed_hive_execution as _failed_hive_execution_impl
from core.execution.models import ToolIntentExecution, _tool_observation
from core.execution.operator_tools import (
    build_operator_action_intent as _build_operator_action_intent_impl,
)
from core.execution.operator_tools import execute_operator_tool as _execute_operator_tool_impl
from core.execution.payment_tools import execute_payment_tool as _execute_payment_tool_impl
from core.execution.planner import (
    _looks_like_workspace_bootstrap_request,
    plan_tool_workflow,
    should_attempt_tool_intent,
)
from core.execution.receipts import (
    execution_from_receipt as _execution_from_receipt_impl,
)
from core.execution.receipts import (
    execution_to_receipt as _execution_to_receipt_impl,
)
from core.execution.receipts import inject_idempotency_key as _inject_idempotency_key_impl
from core.execution.receipts import normalize_payload as _normalize_payload_impl
from core.execution.web_tools import execute_web_tool as _execute_web_tool_impl
from core.execution.web_tools import normalize_item as _normalize_item_impl
from core.hive_activity_tracker import HiveActivityTracker, load_hive_activity_tracker_config
from core.local_operator_actions import (
    OperatorActionIntent,
    dispatch_operator_action,
    list_operator_tools,
    operator_capability_ledger,
)
from core.public_hive_bridge import PublicHiveBridge, load_public_hive_bridge_config, public_hive_write_enabled
from core.runtime_continuity import (
    build_tool_receipt_key,
    is_mutating_tool_intent,
    load_tool_receipt,
    store_tool_receipt,
)
from core.runtime_execution_tools import (
    execute_runtime_tool,
    runtime_execution_capability_ledger,
    runtime_execution_tool_specs,
)
from retrieval.web_adapter import WebAdapter
from tools.registry import call_tool, load_builtin_tools

__all__ = [
    "ToolIntentExecution",
    "_looks_like_workspace_bootstrap_request",
    "execute_tool_intent",
    "plan_tool_workflow",
    "runtime_capability_ledger",
    "runtime_tool_specs",
    "should_attempt_tool_intent",
]


def runtime_capability_ledger() -> list[dict[str, Any]]:
    return execution_capabilities.runtime_capability_ledger(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        runtime_execution_capability_ledger_fn=runtime_execution_capability_ledger,
        operator_capability_ledger_fn=operator_capability_ledger,
    )


def capability_entry_for_intent(intent: str) -> dict[str, Any] | None:
    return execution_capabilities.capability_entry_for_intent(
        intent,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_gap_for_intent(
    intent: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return execution_capabilities.capability_gap_for_intent(
        intent,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_truth_for_request(
    user_text: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    return execution_capabilities.capability_truth_for_request(
        user_text,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def render_capability_truth_response(report: dict[str, Any] | None) -> str:
    return execution_capabilities.render_capability_truth_response(report)


def supported_public_capability_tags(*, limit: int = 16) -> list[str]:
    return execution_capabilities.supported_public_capability_tags(
        limit=limit,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def _unsupported_execution_for_intent(
    intent: str,
    *,
    status: str,
    user_safe_override: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    gap = capability_gap_for_intent(intent)
    if status in {"disabled", "not_configured", "missing_auth"}:
        gap["gap_kind"] = status
    response = render_capability_truth_response(gap)
    user_safe = str(user_safe_override or response).strip()
    details = {
        "capability_gap": gap,
        **dict(extra_details or {}),
        "observation": _tool_observation(
            intent=intent,
            tool_surface="tool_intent",
            ok=False,
            status=status,
            capability_gap=gap,
        ),
    }
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response,
        user_safe_response_text=user_safe,
        mode="tool_failed",
        tool_name=intent,
        details=details,
    )


def runtime_tool_specs() -> list[dict[str, Any]]:
    return execution_capabilities.runtime_tool_specs(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        runtime_execution_tool_specs_fn=runtime_execution_tool_specs,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        list_operator_tools_fn=list_operator_tools,
    )


def execute_tool_intent(
    payload: Any,
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None = None,
    checkpoint_id: str | None = None,
    step_index: int = 0,
) -> ToolIntentExecution:
    normalized = _normalize_payload(payload)
    intent = str(normalized.get("intent") or "").strip()
    arguments = dict(normalized.get("arguments") or {})
    if not intent:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_intent",
            response_text="I won't fake it: the model returned an invalid tool payload with no intent name.",
            user_safe_response_text="I couldn't map that cleanly to a real action.",
            mode="tool_failed",
            tool_name="unknown",
            details={"payload": normalized},
        )
    if intent in {"respond.direct", "none", "no_tool"}:
        return ToolIntentExecution(handled=False, ok=True, status="direct_response")

    receipt_key = ""
    idempotency_key = ""
    if checkpoint_id and is_mutating_tool_intent(intent):
        receipt_key = build_tool_receipt_key(
            checkpoint_id=str(checkpoint_id),
            step_index=max(0, int(step_index)),
            intent=intent,
            arguments=arguments,
        )
        cached = load_tool_receipt(receipt_key)
        if cached:
            cached_execution = _execution_from_receipt(cached)
            if cached_execution is not None:
                return cached_execution
        idempotency_key = receipt_key
        arguments = _inject_idempotency_key(intent, arguments, idempotency_key=idempotency_key)

    if intent in _WEB_TOOL_INTENTS:
        execution = _execute_web_tool(intent, arguments, task_id=task_id, source_context=source_context)
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    runtime_execution = execute_runtime_tool(intent, arguments, source_context=source_context)
    if runtime_execution is not None:
        runtime_details = dict(runtime_execution.details or {})
        runtime_details.setdefault(
            "observation",
            _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=runtime_execution.ok,
                status=runtime_execution.status,
                response_preview=str(runtime_execution.response_text or "")[:280],
            ),
        )
        runtime_mode = (
            "tool_preview"
            if runtime_execution.status in {"user_action_required", "simulate_only"}
            else "tool_executed"
            if runtime_execution.ok
            else "tool_failed"
        )
        execution = ToolIntentExecution(
            handled=runtime_execution.handled,
            ok=runtime_execution.ok,
            status=runtime_execution.status,
            response_text=runtime_execution.response_text,
            mode=runtime_mode,
            tool_name=intent,
            details=runtime_details,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    if intent in _HIVE_TOOL_INTENTS:
        execution = _execute_hive_tool(
            intent,
            arguments,
            hive_activity_tracker=hive_activity_tracker,
            public_hive_bridge=public_hive_bridge,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    if intent in _READ_ONLY_OPERATOR_INTENTS | _MUTATING_OPERATOR_INTENTS:
        execution = _execute_operator_tool(
            intent,
            arguments,
            task_id=task_id,
            session_id=session_id,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    if intent in _PAYMENT_TOOL_INTENTS:
        execution = _execute_payment_tool(intent, arguments, source_context=source_context)
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution

    audit_logger.log(
        "tool_intent_unsupported",
        target_id=task_id,
        target_type="task",
        details={"intent": intent, "arguments": arguments, "source_context": dict(source_context or {})},
    )
    return _unsupported_execution_for_intent(
        intent,
        status="unsupported",
        extra_details={
            "intent": intent,
            "arguments": arguments,
        },
        user_safe_override="That action is not wired on this runtime yet.",
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    return _normalize_payload_impl(payload)


def _inject_idempotency_key(intent: str, arguments: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
    return _inject_idempotency_key_impl(intent, arguments, idempotency_key=idempotency_key)


def _maybe_store_tool_receipt(
    execution: ToolIntentExecution,
    *,
    receipt_key: str,
    session_id: str,
    checkpoint_id: str | None,
    intent: str,
    arguments: dict[str, Any],
    idempotency_key: str,
) -> None:
    if not receipt_key or not checkpoint_id:
        return
    store_tool_receipt(
        receipt_key=receipt_key,
        session_id=session_id,
        checkpoint_id=str(checkpoint_id),
        tool_name=intent,
        idempotency_key=idempotency_key,
        arguments=arguments,
        execution=_execution_to_receipt(execution),
    )


def _execution_to_receipt(execution: ToolIntentExecution) -> dict[str, Any]:
    return _execution_to_receipt_impl(execution)


def _execution_from_receipt(receipt: dict[str, Any]) -> ToolIntentExecution | None:
    return _execution_from_receipt_impl(receipt)


def _execute_web_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> ToolIntentExecution:
    return _execute_web_tool_impl(
        intent,
        arguments,
        task_id=task_id,
        source_context=source_context,
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        load_builtin_tools_fn=load_builtin_tools,
        planned_search_query_fn=WebAdapter.planned_search_query,
        call_tool_fn=call_tool,
        adaptive_research_fn=CuriosityRoamer().adaptive_research,
        unsupported_execution_for_intent_fn=_unsupported_execution_for_intent,
        tool_observation_fn=_tool_observation,
        audit_log_fn=audit_logger.log,
    )


def _execute_hive_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    from core.nullabook_identity import get_profile, update_profile
    from network.signer import get_local_peer_id

    return _execute_hive_tool_impl(
        intent,
        arguments,
        hive_activity_tracker=hive_activity_tracker,
        public_hive_bridge=public_hive_bridge,
        unsupported_execution_for_intent_fn=_unsupported_execution_for_intent,
        capability_gap_for_intent_fn=capability_gap_for_intent,
        render_capability_truth_response_fn=render_capability_truth_response,
        research_topic_from_signal_fn=research_topic_from_signal,
        audit_log_fn=audit_logger.log,
        get_local_peer_id_fn=get_local_peer_id,
        get_profile_fn=get_profile,
        update_profile_fn=update_profile,
    )


def _execute_hive_list_available(
    hive_activity_tracker: HiveActivityTracker,
    arguments: dict[str, Any],
    *,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    return _execute_hive_list_available_impl(
        hive_activity_tracker,
        arguments,
        public_hive_bridge=public_hive_bridge,
        capability_gap_for_intent_fn=capability_gap_for_intent,
        render_capability_truth_response_fn=render_capability_truth_response,
    )


def _failed_hive_execution(intent: str, result: dict[str, Any], fallback: str) -> ToolIntentExecution:
    return _failed_hive_execution_impl(intent, result, fallback)


def _execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
) -> ToolIntentExecution:
    return _execute_operator_tool_impl(
        intent,
        arguments,
        task_id=task_id,
        session_id=session_id,
        dispatch_operator_action_fn=dispatch_operator_action,
    )


def _execute_payment_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
) -> ToolIntentExecution:
    return _execute_payment_tool_impl(intent, arguments, source_context=source_context)


def _build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    return _build_operator_action_intent_impl(operator_kind, arguments)


def _normalize_item(item: Any) -> dict[str, Any]:
    return _normalize_item_impl(item)
