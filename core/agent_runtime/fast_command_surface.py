from __future__ import annotations

import re
import uuid
from typing import Any

from core.hive_activity_tracker import session_hive_state
from core.human_input_adapter import runtime_session_id
from network import signer as signer_mod

_CREDIT_SEND_RE = re.compile(
    r"(?:send|transfer|give)\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(\S+)",
    re.IGNORECASE,
)
_CREDIT_SPEND_RE = re.compile(
    r"spend\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(?:prioriti[sz]e|boost|fund)",
    re.IGNORECASE,
)


def maybe_handle_credit_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None = None,
    signer_module: Any = signer_mod,
    transfer_credits_fn: Any,
    get_credit_balance_fn: Any,
    escrow_credits_for_task_fn: Any,
    session_hive_state_fn: Any = session_hive_state,
    runtime_session_id_fn: Any = runtime_session_id,
) -> dict[str, Any] | None:
    send_match = _CREDIT_SEND_RE.search(user_input)
    if send_match:
        amount = float(send_match.group(1))
        target_peer = send_match.group(2).strip()
        peer_id = signer_module.get_local_peer_id()
        ok = transfer_credits_fn(peer_id, target_peer, amount, reason="chat_transfer")
        if ok:
            response = f"Sent {amount:.2f} credits to {target_peer}. Your new balance: {get_credit_balance_fn(peer_id):.2f}."
        else:
            balance = get_credit_balance_fn(peer_id)
            response = f"Transfer failed. Your balance is {balance:.2f} credits (need {amount:.2f})."
        session_id = runtime_session_id_fn(device=agent.device, persona_id=agent.persona_id)
        return {
            "task_id": str(uuid.uuid4()),
            "response": response,
            "response_class": "task_status",
            "confidence": 0.95,
            "mode": "fast_path",
            "model_execution": {"used_model": False, "source": "credit_ledger"},
            "session_id": session_id,
            "source_context": source_context or {},
        }

    spend_match = _CREDIT_SPEND_RE.search(user_input)
    if spend_match:
        amount = float(spend_match.group(1))
        peer_id = signer_module.get_local_peer_id()
        hive_state = session_hive_state_fn(session_id)
        interaction_payload = dict(hive_state.get("interaction_payload") or {})
        active_topic_id = str(interaction_payload.get("active_topic_id") or "").strip()
        wants_current_hive_task = any(
            marker in " ".join(str(user_input or "").strip().lower().split())
            for marker in ("current hive task", "this hive task", "active hive task")
        )
        task_id = active_topic_id or str(uuid.uuid4())
        if wants_current_hive_task and not active_topic_id:
            response = "I don't have an active Hive task selected in this session, so I can't attach credits to a real task yet."
            return {
                "task_id": str(uuid.uuid4()),
                "response": response,
                "response_class": "task_status",
                "confidence": 0.9,
                "mode": "fast_path",
                "model_execution": {"used_model": False, "source": "credit_ledger"},
                "session_id": session_id,
                "source_context": source_context or {},
            }
        ok = escrow_credits_for_task_fn(peer_id, task_id, amount)
        if ok:
            if active_topic_id:
                response = (
                    f"Reserved {amount:.2f} credits to prioritize Hive task `{active_topic_id[:8]}`. "
                    f"Remaining balance: {get_credit_balance_fn(peer_id):.2f}."
                )
            else:
                response = (
                    f"Reserved {amount:.2f} credits to prioritize your Hive task. "
                    f"Remaining balance: {get_credit_balance_fn(peer_id):.2f}."
                )
        else:
            balance = get_credit_balance_fn(peer_id)
            response = f"Could not reserve credits. Your balance is {balance:.2f} (need {amount:.2f})."
        return {
            "task_id": task_id,
            "response": response,
            "response_class": "task_status",
            "confidence": 0.95,
            "mode": "fast_path",
            "model_execution": {"used_model": False, "source": "credit_ledger"},
            "session_id": session_id,
            "source_context": source_context or {},
        }

    return None


def fast_path_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    response: str,
    confidence: float,
    source_context: dict[str, object] | None,
    reason: str,
    classification_details: dict[str, Any] | None = None,
    runtime_event_details: dict[str, Any] | None = None,
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
) -> dict[str, Any]:
    pseudo_task_id = f"fast-{uuid.uuid4().hex[:12]}"
    turn_result = agent._turn_result(
        response,
        agent._fast_path_response_class(reason=reason, response=response, details=classification_details),
        debug_origin=reason,
    )
    agent._apply_interaction_transition(session_id, turn_result)
    decorated_response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    append_conversation_event_fn(
        session_id=session_id,
        user_input=user_input,
        assistant_output=decorated_response,
        source_context=source_context,
        response_class=turn_result.response_class.value,
    )
    audit_logger_module.log(
        "agent_fast_path_response",
        target_id=pseudo_task_id,
        target_type="task",
        details={"reason": reason, "source_surface": (source_context or {}).get("surface")},
    )
    is_explicit_heavy_block = reason == "explicit_heavy_model_blocked"
    lane = _fast_path_lane(reason)
    phase = "blocked" if is_explicit_heavy_block else "completed"
    planned_model_id = "qwen3.5:35b-a3b" if is_explicit_heavy_block else ""
    fallback_reason = "explicit_heavy_lane_unavailable" if is_explicit_heavy_block else "model_not_used"
    verifier_status = "blocked_no_primary" if is_explicit_heavy_block else "not_required"
    agent._emit_chat_truth_metrics(
        task_id=pseudo_task_id,
        reason=reason,
        response_text=decorated_response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via="fast_path",
        fast_path_hit=True,
        model_inference_used=False,
        model_final_answer_hit=False,
        model_execution_source="fast_path",
        tool_backing_sources=agent._chat_truth_fast_path_backing_sources(reason),
    )
    agent._emit_runtime_event(
        source_context,
        event_type="model_lane_proof",
        message="Fast-path response completed without model inference.",
        schema="nulla.model_lane_proof.v1",
        turn_id=pseudo_task_id,
        session_id=session_id,
        task_class=reason,
        task_kind="fast_path",
        output_mode="plain_text",
        complexity=_fast_path_complexity(reason),
        lane=lane,
        lane_type=lane,
        phase=phase,
        provider_role="drone",
        role="drone",
        planned_provider_id="runtime-fast-path",
        planned_model_id=planned_model_id,
        provider_id="runtime-fast-path",
        model_id="",
        actual_adapter_provider_id="",
        actual_adapter_model_id="",
        backend="fast_path",
        tokens_per_second=0.0,
        measurement_source="deterministic_fast_path",
        queue_depth=0,
        fallback_reason=fallback_reason,
        verifier_status=verifier_status,
        kv_cache_status="fast_path=not_applicable",
        speculative_status="inactive",
        attempted=[],
        failover_used=False,
        mismatch=False,
    )
    agent._emit_runtime_event(
        source_context,
        event_type="task_completed",
        message=f"Fast-path response ready: {agent._runtime_preview(decorated_response)}",
        task_id=pseudo_task_id,
        status=reason,
        **_builder_runtime_event_details(runtime_event_details),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status="completed",
        final_response=decorated_response,
    )
    return {
        "task_id": pseudo_task_id,
        "response": str(decorated_response or ""),
        "mode": "advice_only",
        "confidence": float(confidence),
        "understanding_confidence": 1.0,
        "interpreted_input": user_input,
        "topic_hints": [],
        "prompt_assembly_report": {},
        "model_execution": {"source": "fast_path", "used_model": False},
        "media_analysis": {"used_provider": False, "reason": "fast_path"},
        "curiosity": {"mode": "skipped", "reason": "fast_path"},
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": "",
        "response_class": turn_result.response_class.value,
    }


def action_fast_path_result(
    agent: Any,
    *,
    task_id: str,
    session_id: str,
    user_input: str,
    response: str,
    confidence: float,
    source_context: dict[str, object] | None,
    reason: str,
    success: bool,
    details: dict[str, object] | None = None,
    mode_override: str | None = None,
    task_outcome: str | None = None,
    learned_plan: Any | None = None,
    workflow_summary: str = "",
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
    explicit_planner_style_requested_fn: Any,
) -> dict[str, Any]:
    turn_result = agent._turn_result(
        response,
        agent._action_response_class(
            reason=reason,
            success=success,
            task_outcome=task_outcome,
            response=response,
        ),
        workflow_summary=workflow_summary,
        debug_origin=reason,
        allow_planner_style=explicit_planner_style_requested_fn(user_input),
    )
    agent._apply_interaction_transition(session_id, turn_result)
    decorated_response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    append_conversation_event_fn(
        session_id=session_id,
        user_input=user_input,
        assistant_output=decorated_response,
        source_context=source_context,
        response_class=turn_result.response_class.value,
    )
    agent._update_task_result(
        task_id,
        outcome=task_outcome or ("success" if success else "failed"),
        confidence=confidence,
    )
    if success and learned_plan is not None:
        agent._promote_verified_action_shard(task_id, learned_plan)
    audit_logger_module.log(
        "agent_channel_action",
        target_id=task_id,
        target_type="task",
        details={
            "reason": reason,
            "success": bool(success),
            "source_surface": (source_context or {}).get("surface"),
            "source_platform": (source_context or {}).get("platform"),
            **dict(details or {}),
        },
    )
    agent._emit_chat_truth_metrics(
        task_id=task_id,
        reason=reason,
        response_text=decorated_response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via="action_fast_path",
        fast_path_hit=True,
        model_inference_used=False,
        model_final_answer_hit=False,
        model_execution_source="channel_action",
        tool_backing_sources=agent._chat_truth_action_backing_sources(
            reason=reason,
            success=success,
            task_outcome=task_outcome,
        ),
    )
    checkpoint_status = "completed" if success and (task_outcome or "success") == "success" else (
        "pending_approval" if (task_outcome or "") == "pending_approval" else "failed"
    )
    event_type = (
        "task_completed"
        if checkpoint_status == "completed"
        else "task_pending_approval"
        if checkpoint_status == "pending_approval"
        else "task_failed"
    )
    if _should_emit_action_lane_proof(reason=reason, details=details):
        agent._emit_runtime_event(
            source_context,
            event_type="model_lane_proof",
            message="Runtime action path completed without model inference.",
            schema="nulla.model_lane_proof.v1",
            turn_id=task_id,
            session_id=session_id,
            task_class=reason,
            task_kind="tool_workflow",
            output_mode="action_result",
            complexity="medium",
            lane="daily",
            lane_type="daily",
            phase=checkpoint_status,
            provider_role="drone",
            role="drone",
            planned_provider_id="runtime-action",
            planned_model_id="",
            provider_id="runtime-action",
            model_id="",
            actual_adapter_provider_id="",
            actual_adapter_model_id="",
            backend="tool_workflow",
            tokens_per_second=0.0,
            measurement_source="runtime_action_path",
            queue_depth=0,
            fallback_reason="model_not_used",
            verifier_status="not_required",
            kv_cache_status="tool_workflow=not_applicable",
            speculative_status="inactive",
            attempted=[],
            failover_used=False,
            mismatch=False,
        )
    agent._emit_runtime_event(
        source_context,
        event_type=event_type,
        message=(
            f"{'Completed' if checkpoint_status == 'completed' else 'Awaiting approval for' if checkpoint_status == 'pending_approval' else 'Failed'} action response: "
            f"{agent._runtime_preview(decorated_response)}"
        ),
        task_id=task_id,
        status=reason,
        **_builder_runtime_event_details(dict(details or {})),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status=checkpoint_status,
        final_response=decorated_response if checkpoint_status == "completed" else "",
        failure_text="" if checkpoint_status != "failed" else decorated_response,
    )
    return {
        "task_id": task_id,
        "response": str(decorated_response or ""),
        "mode": mode_override or ("tool_queued" if success else "tool_failed"),
        "confidence": float(confidence),
        "understanding_confidence": 1.0,
        "interpreted_input": user_input,
        "topic_hints": [
            "discord"
            if "discord" in user_input.lower()
            else "telegram"
            if "telegram" in user_input.lower()
            else "channel"
        ],
        "prompt_assembly_report": {},
        "model_execution": {"source": "channel_action", "used_model": False},
        "media_analysis": {"used_provider": False, "reason": "channel_action"},
        "curiosity": {"mode": "skipped", "reason": "channel_action"},
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": workflow_summary,
        "response_class": turn_result.response_class.value,
    }


def _fast_path_lane(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized in {"smalltalk_fast_path", "heartbeat_poll_fast_path", "date_time_fast_path", "direct_math_fast_path", "ui_command_fast_path"}:
        return "tiny"
    if normalized == "explicit_heavy_model_blocked":
        return "deep"
    return "daily"


def _fast_path_complexity(reason: str) -> str:
    lane = _fast_path_lane(reason)
    if lane == "tiny":
        return "trivial"
    if lane == "deep":
        return "hard"
    return "medium"


def _should_emit_action_lane_proof(*, reason: str, details: dict[str, object] | None) -> bool:
    if not str(reason or "").startswith("model_tool_intent_"):
        return True
    tool_steps = [
        str(item).strip()
        for item in list((details or {}).get("tool_steps") or [])
        if str(item).strip()
    ]
    return bool(tool_steps)


def _builder_runtime_event_details(payload: dict[str, Any] | None) -> dict[str, Any]:
    builder = dict((payload or {}).get("builder_controller") or {})
    if not builder:
        return {}
    tool_steps = [
        str(item).strip()
        for item in list(builder.get("tool_steps") or [])
        if str(item).strip()
    ]
    artifacts = dict(builder.get("artifacts") or {})
    changed_paths: list[str] = []
    for item in list(artifacts.get("file_diffs") or []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    details: dict[str, Any] = {}
    if tool_steps:
        details["tool_name"] = tool_steps[-1]
        details["tool_steps"] = tool_steps
    if changed_paths:
        details["changed_paths"] = changed_paths
        details["touched_paths"] = list(changed_paths)
    return details


def maybe_handle_capability_truth_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    capability_truth_for_request_fn: Any,
    render_capability_truth_response_fn: Any,
) -> dict[str, Any] | None:
    report = capability_truth_for_request_fn(
        user_input,
        extra_entries=agent._capability_ledger_entries(),
    )
    normalized = _normalize_capability_prompt(user_input)
    if not report:
        if _looks_like_capability_inventory_prompt(normalized):
            response_text = (
                compact_capabilities_text(agent)
                if _wants_compact_capability_inventory(normalized)
                else agent._help_capabilities_text()
            )
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=response_text,
                confidence=0.96,
                source_context=source_context,
                reason="capability_truth_query",
            )
        if normalized in {
            "what can you do",
            "what can you do right now",
            "what can you do right now on this machine",
            "what can you do on this machine",
            "what are you able to do right now on this machine",
            "what actions can you take right now on this machine",
        }:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=agent._help_capabilities_text(),
                confidence=0.96,
                source_context=source_context,
                reason="capability_truth_query",
            )
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=render_capability_truth_response_fn(report),
        confidence=0.96,
        source_context=source_context,
        reason="capability_truth_query",
    )


def help_capabilities_text(agent: Any) -> str:
    lines = [
        "Wired on this runtime:",
        "- plain-language reasoning, persistent memory, and rolling chat continuity",
    ]
    supported_entries = [entry for entry in agent._capability_ledger_entries() if entry.get("supported")]
    partial_entries = [
        entry
        for entry in supported_entries
        if str(entry.get("support_level") or "").strip().lower() == "partial"
    ]
    full_entries = [
        entry
        for entry in supported_entries
        if str(entry.get("support_level") or "").strip().lower() != "partial"
    ]
    unsupported_entries = [entry for entry in agent._capability_ledger_entries() if not entry.get("supported")]
    for entry in full_entries:
        lines.append(f"- {str(entry.get('claim') or '').strip()}")
    if partial_entries:
        lines.append("")
        lines.append("Partially supported on this runtime:")
        for entry in partial_entries:
            claim = str(entry.get("claim") or "").strip()
            note = str(entry.get("partial_reason") or "").strip()
            lines.append(f"- {claim}" + (f" ({note})" if note else ""))
    lines.append("- I report real tool executions, approval previews, and failures directly instead of bluffing")
    if unsupported_entries:
        lines.append("")
        lines.append("Not wired or not enabled here:")
        for entry in unsupported_entries:
            reason = str(entry.get("unsupported_reason") or entry.get("claim") or "").strip()
            if reason:
                lines.append(f"- {reason}")
    return "\n".join(lines)


def compact_capabilities_text(agent: Any) -> str:
    supported_entries = [entry for entry in agent._capability_ledger_entries() if entry.get("supported")]
    claims = " ".join(str(entry.get("claim") or "").lower() for entry in supported_entries)
    includes_download = "download" in claims
    includes_sandbox = "sandbox" in claims or "command" in claims
    parts = [
        "Local powers here:",
        "workspace file/folder read-write",
    ]
    if includes_download:
        parts.append("downloads")
    if includes_sandbox:
        parts.append("bounded sandbox commands")
    parts.append("runtime and machine inspection")
    return ", ".join(parts[:-1]) + ", and " + parts[-1] + "."


def _normalize_capability_prompt(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split()).strip(" \t\r\n?!.,")


def _looks_like_capability_inventory_prompt(normalized: str) -> bool:
    if not normalized:
        return False
    if "what are your actual local powers" in normalized:
        return True
    if "what can you actually do" in normalized and any(marker in normalized for marker in ("machine", "local", "locally", "right now")):
        return True
    return "what can you do" in normalized and any(
        marker in normalized
        for marker in ("machine", "local", "locally", "right now")
    )


def _wants_compact_capability_inventory(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "one short line",
            "one clean line",
            "one line only",
            "real quick",
            "short answer only",
        )
    )


def render_credit_status(normalized_input: str) -> str:
    from core.credit_ledger import list_credit_ledger_entries, reconcile_ledger
    from core.dna_wallet_manager import DNAWalletManager
    from core.scoreboard_engine import get_peer_scoreboard

    peer_id = signer_mod.get_local_peer_id()
    ledger = reconcile_ledger(peer_id)
    scoreboard = get_peer_scoreboard(peer_id)
    wallet_status = DNAWalletManager().get_status()
    mention_wallet = any(token in normalized_input for token in ("wallet", "usdc", "dna"))
    mention_rewards = any(token in normalized_input for token in ("earn", "earned", "reward", "share", "hive", "task"))
    mention_receipts = any(token in normalized_input for token in ("receipt", "receipts", "ledger", "payout", "payouts"))
    provider_score = float(getattr(scoreboard, "provider", 0.0) or 0.0)
    validator_score = float(getattr(scoreboard, "validator", 0.0) or 0.0)
    trust_score = float(getattr(scoreboard, "trust", 0.0) or 0.0)
    glory_score = float(getattr(scoreboard, "glory_score", 0.0) or 0.0)
    tier_label = str(getattr(scoreboard, "tier", "Newcomer") or "Newcomer")

    parts = [
        f"You currently have {ledger.balance:.2f} compute credits.",
        (
            f"Provider score {provider_score:.1f}, validator score {validator_score:.1f}, "
            f"trust {trust_score:.1f}, glory score {glory_score:.1f}, tier {tier_label}."
        ),
    ]
    if wallet_status is None:
        if mention_wallet:
            parts.append("DNA wallet is not configured on this runtime yet.")
    else:
        parts.append(
            f"DNA wallet: hot {wallet_status.hot_balance_usdc:.2f} USDC, cold {wallet_status.cold_balance_usdc:.2f} USDC."
        )
    if mention_rewards or "credit" in normalized_input:
        parts.append(
            "Plain public Hive posts do not mint credits by themselves. Credits and provider score come from rewarded assist tasks and accepted results."
        )
    if mention_receipts:
        entries = list_credit_ledger_entries(peer_id, limit=4)
        if not entries:
            parts.append("No credit receipts are recorded for this peer yet.")
        else:
            receipt_lines = ["Recent credit receipts:"]
            for entry in entries:
                amount = float(entry.get("amount") or 0.0)
                sign = "+" if amount >= 0 else ""
                receipt_id = str(entry.get("receipt_id") or "").strip() or "no-receipt"
                reason = str(entry.get("reason") or "").strip() or "unknown"
                timestamp = str(entry.get("timestamp") or "").strip() or "unknown time"
                receipt_lines.append(
                    f"- {sign}{amount:.2f} for `{reason}` ({receipt_id[:24]}) at {timestamp}."
                )
            parts.append("\n".join(receipt_lines))
    if ledger.mode:
        parts.append(f"Ledger mode is {ledger.mode}.")
    return " ".join(part.strip() for part in parts if part.strip())
