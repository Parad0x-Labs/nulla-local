from __future__ import annotations

from typing import Any


def handle_turn_frontdoor(
    agent: Any,
    *,
    raw_user_input: str,
    effective_input: str,
    normalized_input: str,
    source_surface: str,
    session_id: str,
    source_context: dict[str, object] | None,
    persona: Any,
    interpreted: Any,
    maybe_handle_preference_command_fn: Any,
    set_hive_interaction_state_fn: Any,
) -> dict[str, Any]:
    startup_message = agent._startup_sequence_fast_path(effective_input)
    if startup_message:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=startup_message,
                confidence=0.97,
                source_context=source_context,
                reason="startup_sequence_fast_path",
            )
        }

    heavy_model_block = agent._explicit_heavy_model_block_response(effective_input)
    if heavy_model_block:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=heavy_model_block,
                confidence=0.99,
                source_context=source_context,
                reason="explicit_heavy_model_blocked",
            )
        }

    heartbeat_reply = agent._heartbeat_poll_fast_path(
        raw_user_input,
        source_context=source_context,
    )
    if heartbeat_reply:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=heartbeat_reply,
                confidence=0.99,
                source_context=source_context,
                reason="heartbeat_poll_fast_path",
            )
        }

    handled, response = maybe_handle_preference_command_fn(effective_input)
    if handled:
        agent._sync_public_presence(
            status=agent._idle_public_presence_status(),
            source_context=source_context,
        )
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.92,
                source_context=source_context,
                reason="user_preference_command",
            )
        }

    credit_result = agent._maybe_handle_credit_command(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if credit_result is not None:
        return {"result": credit_result}

    hive_frontdoor_result, effective_hive_create_draft, _pending_hive_create_confirmation = agent._maybe_handle_hive_frontdoor(
        raw_user_input=raw_user_input,
        effective_input=effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if hive_frontdoor_result is not None:
        return {"result": hive_frontdoor_result}

    memory_result = agent._maybe_handle_memory_fast_path(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if memory_result is not None:
        return {"result": memory_result}

    web0_builder_result = agent._maybe_handle_web0_builder_fast_path(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if web0_builder_result is not None:
        return {"result": web0_builder_result}

    ui_command = agent._ui_command_fast_path(normalized_input, source_surface=source_surface)
    if ui_command:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=ui_command,
                confidence=0.97,
                source_context=source_context,
                reason="ui_command_fast_path",
            )
        }

    credit_status = None
    if effective_hive_create_draft is None:
        credit_status = agent._credit_status_fast_path(normalized_input, source_surface=source_surface)
    if credit_status:
        receipt_like_credit_query = any(
            marker in str(normalized_input or "").lower()
            for marker in (
                "receipt",
                "receipts",
                "ledger",
                "payout",
                "payouts",
                "recent credits",
            )
        )
        if agent._is_chat_truth_surface(source_context) and not receipt_like_credit_query:
            return {
                "result": agent._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=effective_input,
                    source_context=source_context,
                    persona=persona,
                    interpretation=interpreted,
                    task_class="unknown",
                    response_class=agent.ResponseClass.UTILITY_ANSWER,
                    reason="credit_status_model_wording",
                    model_input=agent._chat_surface_credit_status_model_input(
                        user_input=effective_input,
                        credit_snapshot=credit_status,
                    ),
                    fallback_response=credit_status,
                    allow_provider_inference=False,
                )
            }
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=credit_status,
                confidence=0.95,
                source_context=source_context,
                reason="credit_status_fast_path",
            )
        }

    date_time_status = agent._date_time_fast_path(
        normalized_input,
        source_surface=source_surface,
        session_id=session_id,
        source_context=source_context,
    )
    if date_time_status:
        cleaned_date_time_input = str(normalized_input or "").strip().lower().strip(" \t\r\n?!.,")
        requested_timezone, requested_label = agent._extract_utility_timezone(cleaned_date_time_input)
        if not requested_timezone:
            recent_utility_context = agent._recent_utility_context(
                session_id=session_id,
                source_context=source_context,
            )
            requested_timezone, requested_label = agent._contextual_time_followup_timezone(
                cleaned_date_time_input,
                recent_utility_context=recent_utility_context,
            )
        utility_payload: dict[str, Any] = {}
        if "current time" in str(date_time_status or "").lower():
            utility_payload = {
                "utility_kind": "time",
                "timezone": requested_timezone,
                "label": requested_label,
            }
        set_hive_interaction_state_fn(session_id, mode="utility", payload=utility_payload)
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=date_time_status,
                confidence=0.97,
                source_context=source_context,
                reason="date_time_fast_path",
            )
        }

    direct_math = agent._direct_math_fast_path(
        normalized_input,
        source_surface=source_surface,
    )
    if direct_math:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=direct_math,
                confidence=0.99,
                source_context=source_context,
                reason="direct_math_fast_path",
            )
        }

    machine_download = agent._maybe_handle_direct_machine_download_request(
        raw_user_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if machine_download is not None:
        return {"result": machine_download}

    machine_write = agent._maybe_handle_direct_machine_write_request(
        raw_user_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if machine_write is not None:
        return {"result": machine_write}

    machine_write_guard = agent._maybe_handle_safe_machine_write_guard(
        raw_user_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if machine_write_guard is not None:
        return {"result": machine_write_guard}

    machine_read = agent._maybe_handle_direct_machine_read_request(
        raw_user_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if machine_read is not None:
        return {"result": machine_read}

    workspace_runtime = agent._maybe_handle_direct_workspace_runtime_request(
        effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if workspace_runtime is not None:
        return {"result": workspace_runtime}

    capability_truth = agent._maybe_handle_capability_truth_request(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if capability_truth is not None:
        return {"result": capability_truth}

    nullabook_fast = agent._maybe_handle_nullabook_fast_path(
        effective_input,
        raw_user_input=raw_user_input,
        session_id=session_id,
        source_context=source_context,
    )
    if nullabook_fast is not None:
        return {"result": nullabook_fast}

    live_info_status = agent._maybe_handle_live_info_fast_path(
        effective_input,
        session_id=session_id,
        source_context=source_context,
        interpretation=interpreted,
    )
    if live_info_status is not None:
        return {"result": live_info_status}

    evaluative = agent._evaluative_conversation_fast_path(normalized_input, source_surface=source_surface)
    if evaluative:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=evaluative,
                confidence=0.88,
                source_context=source_context,
                reason="evaluative_conversation_fast_path",
            )
        }

    smalltalk = None
    for candidate in (normalized_input, effective_input, raw_user_input):
        smalltalk = agent._smalltalk_fast_path(
            candidate,
            source_surface=source_surface,
            session_id=session_id,
        )
        if smalltalk:
            break
    if smalltalk:
        smalltalk_phrase = " ".join(str(raw_user_input or normalized_input).lower().split()).strip(" \t\r\n?!.,")
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=smalltalk,
                confidence=0.90,
                source_context=source_context,
                reason="help_fast_path" if smalltalk_phrase in {"what can you do", "help"} else "smalltalk_fast_path",
            )
        }

    return {"result": None}
