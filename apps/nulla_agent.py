from __future__ import annotations

import argparse
import json
import logging
import os

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import sys as _bootstrap_sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)

from core import audit_logger, feedback_engine, policy_engine
from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import hive_followups as agent_hive_followups
from core.agent_runtime import hive_runtime as agent_hive_runtime
from core.agent_runtime import memory_runtime as agent_memory_runtime
from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import presence as agent_presence_runtime
from core.agent_runtime import turn_dispatch as agent_turn_dispatch
from core.agent_runtime import turn_frontdoor as agent_turn_frontdoor
from core.agent_runtime import turn_reasoning as agent_turn_reasoning
from core.agent_runtime.builder_facade import BuilderFacadeMixin
from core.agent_runtime.chat_surface_facade import ChatSurfaceFacadeMixin
from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
from core.agent_runtime.hive_review_runtime import HiveReviewRuntimeMixin
from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
from core.agent_runtime.nullabook_runtime import NullaBookRuntimeMixin
from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin
from core.agent_runtime.public_hive_support import PublicHiveSupportMixin
from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin
from core.agent_runtime.runtime_checkpoint_support import RuntimeCheckpointSupportMixin
from core.agent_runtime.task_persistence_support import TaskPersistenceSupportMixin
from core.agent_runtime.tool_result_surface import ToolResultSurfaceMixin
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.channel_actions import dispatch_outbound_post_intent, parse_channel_post_intent
from core.credit_ledger import (
    escrow_credits_for_task,
    get_credit_balance,
    transfer_credits,
)
from core.curiosity_roamer import CuriosityRoamer
from core.hive_activity_tracker import (
    HiveActivityTracker,
    clear_hive_interaction_state,
    prune_stale_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
    update_session_hive_state,
)
from core.human_input_adapter import adapt_user_input, runtime_session_id
from core.identity_manager import load_active_persona
from core.knowledge_fetcher import request_relevant_holders
from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
from core.logging_config import setup_logging
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import build_media_context_snippets, ingest_media_evidence
from core.memory_first_router import MemoryFirstRouter
from core.onboarding import get_agent_display_name
from core.parent_orchestrator import orchestrate_parent_task
from core.persistent_memory import (
    append_conversation_event,
    augment_history_from_session_log,
    ensure_memory_files,
    maybe_handle_memory_command,
    search_user_heuristics,
)
from core.public_hive_bridge import PublicHiveBridge
from core.reasoning_engine import (
    Plan,
    build_plan,
    explicit_planner_style_requested,
    render_response,
    should_use_planner_renderer,
)
from core.runtime_continuity import (
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_resumable_checkpoint,
    mark_stale_runtime_checkpoints_interrupted,
    record_runtime_tool_progress,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
)
from core.runtime_task_events import emit_runtime_event
from core.shard_synthesizer import build_generalized_query, from_task_result
from core.task_router import (
    classify,
    create_task_record,
    load_task_record,
    looks_like_semantic_hive_request,
)
from core.tiered_context_loader import TieredContextLoader
from core.tool_intent_executor import (
    _looks_like_workspace_bootstrap_request,
    execute_tool_intent,
    plan_tool_workflow,
    render_capability_truth_response,
    should_attempt_tool_intent,
)
from core.user_preferences import load_preferences, maybe_handle_preference_command
from network import signer as signer_mod
from retrieval.swarm_query import dispatch_query_shard
from retrieval.web_adapter import WebAdapter

_log = logging.getLogger(__name__)


def _augment_history_from_session_log(
    history: list[dict[str, str]] | None,
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    return augment_history_from_session_log(
        history,
        session_id=session_id,
        user_text=user_text,
        limit=limit,
    )

_PATCH_COMPAT_EXPORTS = (
    pick_autonomous_research_signal,
    research_topic_from_signal,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_resumable_checkpoint,
    record_runtime_tool_progress,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
    emit_runtime_event,
    create_task_record,
    load_task_record,
    execute_tool_intent,
    plan_tool_workflow,
    render_capability_truth_response,
    should_attempt_tool_intent,
    search_user_heuristics,
    _looks_like_workspace_bootstrap_request,
    WebAdapter,
)


@dataclass
class AgentRuntime:
    backend_name: str
    device: str
    persona_id: str
    swarm_enabled: bool


@dataclass
class GateDecision:
    mode: str
    reason: str
    requires_user_approval: bool
    allowed_actions: list[str]


class ResponseClass(str, Enum):
    SMALLTALK = "smalltalk"
    UTILITY_ANSWER = "utility_answer"
    TASK_LIST = "task_list"
    TASK_SELECTION_CLARIFICATION = "task_selection_clarification"
    TASK_STARTED = "task_started"
    TASK_STATUS = "task_status"
    TASK_FAILED_USER_SAFE = "task_failed_user_safe"
    RESEARCH_PROGRESS = "research_progress"
    APPROVAL_REQUIRED = "approval_required"
    SYSTEM_ERROR_USER_SAFE = "system_error_user_safe"
    GENERIC_CONVERSATION = "generic_conversation"


@dataclass
class ChatTurnResult:
    text: str
    response_class: ResponseClass
    workflow_summary: str = ""
    debug_origin: str | None = None
    allow_planner_style: bool = False


class NullaAgent(
    RuntimeCheckpointSupportMixin,
    NullaBookRuntimeMixin,
    ToolResultSurfaceMixin,
    HiveReviewRuntimeMixin,
    FastPathFacadeMixin,
    HiveTopicFacadeMixin,
    ChatSurfaceFacadeMixin,
    ProceedIntentSupportMixin,
    PublicHiveSupportMixin,
    TaskPersistenceSupportMixin,
    BuilderFacadeMixin,
    ResearchToolLoopFacadeMixin,
):
    ResponseClass = ResponseClass
    GateDecision = GateDecision
    ChatTurnResult = ChatTurnResult

    def __init__(self, backend_name: str, device: str, persona_id: str = "default"):
        self.backend_name = backend_name
        self.device = device
        self.persona_id = persona_id
        self.swarm_enabled = True
        self.context_loader = TieredContextLoader()
        self.memory_router = MemoryFirstRouter()
        self.curiosity = CuriosityRoamer()
        self.media_pipeline = MediaAnalysisPipeline()
        self.public_hive_bridge = PublicHiveBridge()
        self.hive_activity_tracker = HiveActivityTracker()
        self._public_presence_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._public_presence_running = False
        self._public_presence_registered = False
        self._public_presence_status = "idle"
        self._public_presence_source_context: dict[str, object] | None = None
        self._public_presence_thread: threading.Thread | None = None
        self._public_presence_sync_thread: threading.Thread | None = None
        self._public_presence_sync_inflight = False
        self._public_presence_sync_pending = False
        self._idle_commons_running = False
        self._idle_commons_thread: threading.Thread | None = None
        self._last_user_activity_ts = time.time()
        self._last_idle_commons_ts = 0.0
        self._last_idle_hive_research_ts = 0.0
        self._idle_commons_seed_index = 0
        self._hive_create_pending: dict[str, dict[str, Any]] = {}
        self._nullabook_pending: dict[str, dict[str, str]] = {}

    def start(self) -> AgentRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        mark_stale_runtime_checkpoints_interrupted()
        ensure_memory_files()
        _ = load_active_persona(self.persona_id)
        self._sync_public_presence(status=self._idle_public_presence_status())
        if self._background_runtime_threads_enabled():
            self._start_public_presence_heartbeat()
            self._start_idle_commons_loop()

        return AgentRuntime(
            backend_name=self.backend_name,
            device=self.device,
            persona_id=self.persona_id,
            swarm_enabled=self.swarm_enabled,
        )

    def _background_runtime_threads_enabled(self) -> bool:
        if str(self.backend_name or "").strip().lower().startswith("test-"):
            return False
        if str(self.device or "").strip().lower().endswith("-test"):
            return False
        return not os.environ.get("PYTEST_CURRENT_TEST")

    def run_once(
        self,
        user_input: str,
        *,
        session_id_override: str | None = None,
        source_context: dict[str, object] | None = None,
    ) -> dict:
        # Keep frontdoor precedence explicit: safety/resume -> utility/direct fast paths ->
        # task-bound follow-ups -> Hive/task interactions -> grounded answer -> generic fallback.
        persona = load_active_persona(self.persona_id)
        session_id = session_id_override or runtime_session_id(device=self.device, persona_id=self.persona_id)
        self._mark_user_activity()
        runtime_source_context = dict(source_context or {})
        runtime_source_context["conversation_history"] = _augment_history_from_session_log(
            runtime_source_context.get("conversation_history"),
            session_id=session_id,
            user_text=user_input,
        )
        interpreted = adapt_user_input(user_input, session_id=session_id)
        effective_input = interpreted.reconstructed_text or interpreted.normalized_text or user_input
        normalized_input = str(interpreted.normalized_text or "").strip()
        checkpoint_bundle = self._prepare_runtime_checkpoint(
            session_id=session_id,
            raw_user_input=user_input,
            effective_input=effective_input,
            source_context=runtime_source_context,
            allow_followup_resume=not self._blocks_runtime_followup_resume(
                session_id=session_id,
                source_context=runtime_source_context,
            ),
        )
        runtime_source_context = dict(checkpoint_bundle.get("source_context") or runtime_source_context)
        checkpoint_state = str(checkpoint_bundle.get("state") or "created")
        if checkpoint_state == "missing_resume":
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=(
                    "No interrupted runtime task is available to resume in this session. "
                    "If you were retrying a Discord post: set DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN+DISCORD_CHANNEL_ID. "
                    "For Telegram: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Then retry your original message."
                ),
                confidence=0.78,
                source_context=runtime_source_context,
                reason="runtime_resume_missing",
            )
        effective_input = str(checkpoint_bundle.get("effective_input") or effective_input)
        if checkpoint_state == "resumed":
            interpreted = adapt_user_input(effective_input, session_id=session_id)
            normalized_input = str(interpreted.normalized_text or "").strip()
        source_context = runtime_source_context
        source_surface = str((source_context or {}).get("surface", "cli")).lower()
        prune_stale_hive_interaction_state(session_id)
        self._emit_runtime_event(
            source_context,
            event_type="task_resumed" if checkpoint_state == "resumed" else "task_received",
            message=(
                f"Resuming interrupted task: {self._runtime_preview(effective_input)}"
                if checkpoint_state == "resumed"
                else f"Received request: {self._runtime_preview(effective_input)}"
            ),
            request_preview=self._runtime_preview(effective_input, limit=160),
            resume_available=checkpoint_state == "resumed",
        )

        frontdoor_bundle = self._handle_turn_frontdoor(
            raw_user_input=user_input,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
            persona=persona,
            interpreted=interpreted,
        )
        frontdoor_result = frontdoor_bundle.get("result")
        if frontdoor_result is not None:
            return frontdoor_result

        self._sync_public_presence(status="busy", source_context=source_context)
        try:
            turn_bundle = self._prepare_turn_task_bundle(
                effective_input=effective_input,
                user_input=user_input,
                session_id=session_id,
                source_context=source_context,
                interpreted=interpreted,
            )
            frontdoor_result = turn_bundle.get("result")
            if frontdoor_result is not None:
                return frontdoor_result
            task = turn_bundle["task"]
            classification = dict(turn_bundle.get("classification") or {})

            return self._execute_grounded_turn(
                task=task,
                effective_input=effective_input,
                classification=classification,
                interpreted=interpreted,
                persona=persona,
                session_id=session_id,
                source_context=source_context,
            )
        except Exception as exc:
            self._finalize_runtime_checkpoint(
                source_context,
                status="interrupted",
                failure_text=str(exc),
            )
            self._emit_runtime_event(
                source_context,
                event_type="task_interrupted",
                message=f"Task failed: {self._runtime_preview(str(exc), limit=200)}",
            )
            raise
        finally:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context=source_context,
            )

    def _maybe_handle_memory_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_memory_runtime.maybe_handle_memory_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            maybe_handle_memory_command_fn=maybe_handle_memory_command,
        )

    def _model_final_response_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.model_final_response_text(model_execution)

    def _prepare_turn_task_bundle(
        self,
        *,
        effective_input: str,
        user_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
        interpreted: Any,
    ) -> dict[str, Any]:
        return agent_turn_dispatch.prepare_turn_task_bundle(
            self,
            effective_input=effective_input,
            user_input=user_input,
            session_id=session_id,
            source_context=source_context,
            interpreted=interpreted,
            classify_fn=classify,
            parse_channel_post_intent_fn=parse_channel_post_intent,
            dispatch_outbound_post_intent_fn=dispatch_outbound_post_intent,
            parse_operator_action_intent_fn=parse_operator_action_intent,
            dispatch_operator_action_fn=dispatch_operator_action,
        )

    def _handle_turn_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        normalized_input: str,
        source_surface: str,
        session_id: str,
        source_context: dict[str, object] | None,
        persona: Any,
        interpreted: Any,
    ) -> dict[str, Any]:
        return agent_turn_frontdoor.handle_turn_frontdoor(
            self,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
            persona=persona,
            interpreted=interpreted,
            maybe_handle_preference_command_fn=maybe_handle_preference_command,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _execute_grounded_turn(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpreted: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_turn_reasoning.execute_grounded_turn(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id=session_id,
            source_context=source_context,
            adapt_user_input_fn=adapt_user_input,
            ingest_media_evidence_fn=ingest_media_evidence,
            build_media_context_snippets_fn=build_media_context_snippets,
            orchestrate_parent_task_fn=orchestrate_parent_task,
            build_plan_fn=build_plan,
            render_response_fn=render_response,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
            should_use_planner_renderer_fn=should_use_planner_renderer,
            request_relevant_holders_fn=request_relevant_holders,
            dispatch_query_shard_fn=dispatch_query_shard,
            build_generalized_query_fn=build_generalized_query,
            feedback_engine_module=feedback_engine,
            policy_engine_module=policy_engine,
            from_task_result_fn=from_task_result,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
        )

    def _chat_surface_cache_or_memory_source(self, model_execution: Any) -> bool:
        return agent_memory_runtime.chat_surface_cache_or_memory_source(model_execution)

    def _chat_surface_model_final_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.chat_surface_model_final_text(model_execution)

    def _chat_surface_honest_degraded_response(
        self,
        model_execution: Any,
        *,
        user_input: str = "",
        interpretation: Any | None = None,
    ) -> str:
        return agent_memory_runtime.chat_surface_honest_degraded_response(
            self,
            model_execution,
            user_input=user_input,
            interpretation=interpretation,
        )

    def _maybe_handle_credit_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None = None,
    ) -> dict | None:
        return agent_fast_command_surface.maybe_handle_credit_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
            transfer_credits_fn=transfer_credits,
            get_credit_balance_fn=get_credit_balance,
            escrow_credits_for_task_fn=escrow_credits_for_task,
            session_hive_state_fn=session_hive_state,
            runtime_session_id_fn=runtime_session_id,
        )

    def _fast_path_result(
        self,
        *,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
        classification_details: dict[str, Any] | None = None,
        runtime_event_details: dict[str, Any] | None = None,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "user_input": user_input,
            "response": response,
            "confidence": confidence,
            "source_context": source_context,
            "reason": reason,
            "append_conversation_event_fn": append_conversation_event,
            "audit_logger_module": audit_logger,
        }
        if classification_details is not None:
            kwargs["classification_details"] = classification_details
        if runtime_event_details is not None:
            kwargs["runtime_event_details"] = runtime_event_details
        return agent_fast_command_surface.fast_path_result(
            self,
            **kwargs,
        )

    def _action_fast_path_result(
        self,
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
        learned_plan: Plan | None = None,
        workflow_summary: str = "",
    ) -> dict:
        return agent_fast_command_surface.action_fast_path_result(
            self,
            task_id=task_id,
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=confidence,
            source_context=source_context,
            reason=reason,
            success=success,
            details=details,
            mode_override=mode_override,
            task_outcome=task_outcome,
            learned_plan=learned_plan,
            workflow_summary=workflow_summary,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
        )

    def _apply_interaction_transition(self, session_id: str, result: ChatTurnResult) -> None:
        agent_orchestrator_runtime.apply_interaction_transition(
            self,
            session_id,
            result,
            session_hive_state_fn=session_hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _maybe_handle_hive_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
        return agent_hive_followups.maybe_handle_hive_frontdoor(
            self,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_runtime_command(
        self,
        user_input: str,
        *,
        session_id: str,
    ) -> tuple[bool, str, bool, dict[str, Any] | None]:
        return agent_hive_runtime.maybe_handle_hive_runtime_command(
            self,
            user_input,
            session_id=session_id,
        )

    def _recover_hive_runtime_command_input(self, user_input: str) -> str:
        return agent_hive_runtime.recover_hive_runtime_command_input(
            self,
            user_input,
            looks_like_semantic_hive_request_fn=looks_like_semantic_hive_request,
        )

    def _hive_tracker_needs_bridge_fallback(self, response: str) -> bool:
        return agent_hive_runtime.hive_tracker_needs_bridge_fallback(response)

    def _looks_like_hive_prompt_control_command(self, user_input: str) -> bool:
        return agent_hive_runtime.looks_like_hive_prompt_control_command(user_input)

    def _maybe_handle_hive_bridge_fallback(
        self,
        user_input: str,
        *,
        session_id: str,
        tracker_response: str,
    ) -> dict[str, Any] | None:
        return agent_hive_runtime.maybe_handle_hive_bridge_fallback(
            self,
            user_input,
            session_id=session_id,
            tracker_response=tracker_response,
        )

    def _store_hive_topic_selection_state(
        self,
        session_id: str,
        topics: list[dict[str, Any]],
    ) -> None:
        agent_hive_runtime.store_hive_topic_selection_state(
            session_id,
            topics,
            session_hive_state_fn=session_hive_state,
            update_session_hive_state_fn=update_session_hive_state,
        )

    def _sync_public_presence(
        self,
        *,
        status: str,
        source_context: dict[str, object] | None = None,
    ) -> None:
        agent_presence_runtime.sync_public_presence(
            self,
            status=status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name,
            audit_log_fn=audit_logger.log,
        )

    def _start_public_presence_heartbeat(self) -> None:
        agent_presence_runtime.start_public_presence_heartbeat(
            self,
            thread_factory=threading.Thread,
        )

    def _start_idle_commons_loop(self) -> None:
        agent_presence_runtime.start_idle_commons_loop(
            self,
            thread_factory=threading.Thread,
        )

    def _public_presence_heartbeat_loop(self) -> None:
        agent_presence_runtime.public_presence_heartbeat_loop(
            self,
            sleep_fn=time.sleep,
        )

    def _idle_commons_loop(self) -> None:
        agent_presence_runtime.idle_commons_loop(
            self,
            sleep_fn=time.sleep,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_run_idle_commons_once(self) -> None:
        agent_presence_runtime.maybe_run_idle_commons_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_run_autonomous_hive_research_once(self) -> None:
        agent_presence_runtime.maybe_run_autonomous_hive_research_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            pick_signal_fn=pick_autonomous_research_signal,
            research_topic_fn=research_topic_from_signal,
            audit_log_fn=audit_logger.log,
        )

    def _mark_user_activity(self) -> None:
        with self._activity_lock:
            self._last_user_activity_ts = time.time()

    def _idle_commons_session_id(self) -> str:
        return agent_presence_runtime.idle_commons_session_id(
            get_local_peer_id_fn=signer_mod.get_local_peer_id,
        )

    def _normalize_public_presence_status(self, status: str) -> str:
        return agent_presence_runtime.normalize_public_presence_status(self, status)

    def _idle_public_presence_status(self) -> str:
        return agent_presence_runtime.idle_public_presence_status(
            load_preferences_fn=load_preferences,
        )

    def _maybe_handle_hive_research_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_research_followup(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            session_hive_state_fn=session_hive_state,
            clear_hive_interaction_state_fn=clear_hive_interaction_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _maybe_resume_active_hive_task(
        self,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_resume_active_hive_task(
            self,
            lowered,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _extract_hive_topic_hint(self, text: str) -> str:
        return agent_hive_followups.extract_hive_topic_hint(text)

def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-agent")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--input", default="")
    parser.add_argument("--json", action="store_true", help="Print full response payload as JSON.")
    args = parser.parse_args()

    from core.runtime_bootstrap import bootstrap_runtime_mode

    backend_name = str(args.backend)
    device = str(args.device)
    boot = bootstrap_runtime_mode(
        mode="agent",
        force_policy_reload=True,
        resolve_backend=backend_name == "auto" or device == "auto",
    )

    if backend_name == "auto" or device == "auto":
        selection = boot.backend_selection
        if selection is None:
            raise RuntimeError("Runtime bootstrap did not resolve a backend selection.")
        backend_name = backend_name if backend_name != "auto" else selection.backend_name
        device = device if device != "auto" else selection.device

    agent = NullaAgent(
        backend_name=backend_name,
        device=device,
        persona_id=str(args.persona),
    )
    agent.start()
    if not str(args.input or "").strip():
        print("Nulla agent started. Provide --input for one-shot execution.")
        return 0

    result = agent.run_once(str(args.input))
    if bool(args.json):
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(str(result.get("response") or "").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
