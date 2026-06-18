from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core import audit_logger, policy_engine
from core.backend_acceleration_truth import backend_acceleration_proof
from core.cache_freshness_policy import default_ttl_seconds, freshness_score, should_revalidate
from core.candidate_knowledge_lane import build_task_hash, get_exact_candidate, record_candidate_output
from core.compute_mode import get_active_compute_budget
from core.local_inference_autopilot import build_local_inference_autopilot_plan
from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
from core.local_model_bundles import model_parameter_billions
from core.model_health import circuit_is_open, record_provider_failure, record_provider_success
from core.model_registry import ModelRegistry
from core.model_selection_policy import provider_cost_class
from core.model_trust import output_trust_score
from core.output_validator import validate_provider_output
from core.prompt_normalizer import normalize_prompt
from core.provider_routing import ProviderRole, provider_capability_truth_for_manifest, rank_provider_candidates
from core.runtime_task_events import emit_runtime_event
from core.task_router import model_execution_profile

_STRUCTURED_OUTPUT_MODES = {"json_object", "action_plan", "tool_intent", "summary_block"}
_CHAT_TRUTH_SURFACES = {"channel", "openclaw", "api"}


@dataclass
class ModelExecutionDecision:
    source: str
    task_hash: str
    provider_id: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    output_text: str | None = None
    structured_output: Any = None
    confidence: float = 0.0
    trust_score: float = 0.0
    used_model: bool = False
    cache_hit: bool = False
    candidate_id: str | None = None
    failover_used: bool = False
    validation_state: str = "not_run"
    details: dict[str, Any] = field(default_factory=dict)

    def as_plan_candidate(self) -> dict[str, Any] | None:
        if not self.output_text:
            return None
        summary = self.output_text.strip().splitlines()[0][:220] if self.output_text.strip() else "Model-generated candidate"
        steps = []
        if isinstance(self.structured_output, dict):
            raw_steps = self.structured_output.get("steps") or []
            if isinstance(raw_steps, list):
                steps = [str(step) for step in raw_steps[:8]]
        return {
            "summary": summary,
            "resolution_pattern": steps,
            "score": self.trust_score or self.confidence,
            "source_type": "model_candidate",
            "source_node_id": self.provider_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "candidate_id": self.candidate_id,
            "structured_output": self.structured_output,
            "validation_state": self.validation_state,
        }


class MemoryFirstRouter:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def resolve(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        force_model: bool = False,
        allow_provider_inference: bool = True,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        effective_force_model = bool(force_model) and bool(allow_provider_inference)
        force_model = _force_model_on_chat_surface(
            force_model=effective_force_model,
            surface=surface,
            source_context=source_context,
        )
        profile = model_execution_profile(str(classification.get("task_class", "unknown")))
        task_kind = str(profile["task_kind"])
        output_mode = str(profile["output_mode"])
        normalized_input = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
        task_hash = build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode=output_mode,
        )

        if not force_model:
            cached = get_exact_candidate(task_hash, output_mode=output_mode)
            if cached and not should_revalidate(cached) and float(cached.get("trust_score") or 0.0) >= 0.56:
                return ModelExecutionDecision(
                    source="exact_cache_hit",
                    task_hash=task_hash,
                    provider_id=f"{cached['provider_name']}:{cached['model_name']}",
                    provider_name=cached["provider_name"],
                    model_name=cached["model_name"],
                    output_text=str(cached.get("normalized_output") or ""),
                    structured_output=cached.get("structured_output"),
                    confidence=float(cached.get("confidence") or 0.0),
                    trust_score=float(cached.get("trust_score") or 0.0),
                    cache_hit=True,
                    used_model=False,
                    candidate_id=cached["candidate_id"],
                    validation_state=str(cached.get("validation_state") or "cached"),
                    details={"reason": "fresh_exact_candidate_cache"},
                )

            if _memory_is_good_enough(context_result, classification):
                return ModelExecutionDecision(
                    source="memory_hit",
                    task_hash=task_hash,
                    used_model=False,
                    details={"reason": "relevant_local_memory_sufficient"},
                )

            if not allow_provider_inference:
                return ModelExecutionDecision(
                    source="no_cached_or_memory_answer",
                    task_hash=task_hash,
                    used_model=False,
                    details={"reason": "provider_inference_disabled"},
                )

        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind=task_kind,
            output_mode=output_mode,
            allow_paid_fallback=bool(profile.get("allow_paid_fallback", False)),
            provider_role=_provider_role_for_request(profile.get("provider_role")),
            surface=surface,
            source_context=source_context,
        )

    def resolve_tool_intent(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        normalized_input = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
        task_hash = build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode="tool_intent",
        )
        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind="tool_intent",
            output_mode="tool_intent",
            allow_paid_fallback=False,
            provider_role="drone",
            surface=surface,
            source_context=source_context,
        )

    def _build_request(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        output_mode: str,
        task_kind: str,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelRequest:
        internal_request = normalize_prompt(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            trace_id=str(getattr(task, "task_id", "")),
            surface=surface,
            source_context=source_context,
        )
        return ModelRequest(
            task_kind=task_kind,
            prompt=internal_request.user_prompt(),
            system_prompt=internal_request.system_prompt(),
            context=internal_request.context_summary,
            temperature=internal_request.temperature,
            max_output_tokens=internal_request.max_output_tokens,
            messages=internal_request.as_openai_messages(),
            output_mode=output_mode,
            trace_id=internal_request.trace_id,
            contract={"mode": output_mode},
            metadata={
                **dict(internal_request.metadata or {}),
                **({"task_envelope": dict((source_context or {}).get("task_envelope") or {})} if (source_context or {}).get("task_envelope") else {}),
                **({"task_role": str((source_context or {}).get("task_role") or "")} if (source_context or {}).get("task_role") else {}),
            },
            attachments=internal_request.attachments,
        )

    def _invoke_manifest(
        self,
        *,
        manifest: Any,
        request: ModelRequest,
        output_mode: str,
        task: Any,
        source_context: dict[str, Any] | None,
    ) -> tuple[ModelAdapter | None, ModelResponse | None, str | None]:
        if circuit_is_open(manifest.provider_id):
            return None, None, "circuit_open"

        adapter = self.registry.build_adapter(manifest)
        health = adapter.health_check()
        if not bool(health.get("ok")):
            record_provider_failure(manifest.provider_id, error=str(health.get("error") or "health_check_failed"))
            audit_logger.log(
                "model_provider_unhealthy",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"health": health},
            )
            return adapter, None, str(health.get("error") or "health_check_failed")

        try:
            if output_mode in _STRUCTURED_OUTPUT_MODES:
                response = adapter.run_structured_task(request)
            elif _streaming_requested(source_context, output_mode=output_mode) and adapter.supports_streaming():
                response = self._stream_response(
                    adapter=adapter,
                    manifest=manifest,
                    request=request,
                    source_context=source_context,
                )
            else:
                response = adapter.run_text_task(request)
            record_provider_success(manifest.provider_id)
            return adapter, response, None
        except Exception as exc:
            record_provider_failure(
                manifest.provider_id,
                error=str(exc),
                timeout="timeout" in str(exc).lower(),
            )
            audit_logger.log(
                "model_provider_execution_failed",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc)},
            )
            return adapter, None, str(exc)

    def _verify_primary_response(
        self,
        *,
        primary_manifest: Any,
        primary_response: ModelResponse,
        ranked_manifests: list[Any],
        autopilot_plan: dict[str, Any],
        task: Any,
        classification: dict[str, Any],
        task_kind: str,
        output_mode: str,
        source_context: dict[str, Any] | None,
        failed_provider_ids: set[str] | None = None,
    ) -> str:
        if not bool(autopilot_plan.get("verifier_required")):
            return "not_required"
        verifier_provider_id = str(autopilot_plan.get("verifier_provider_id") or "").strip()
        if not verifier_provider_id:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but no verifier lane was available.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
            )
            return "blocked"
        if verifier_provider_id == str(getattr(primary_manifest, "provider_id", "") or "").strip():
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_degraded",
                "Verifier required but selected the same model as the primary lane.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="degraded_same_model",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
                verifier_model_id=str(autopilot_plan.get("verifier_model") or "").strip(),
            )
            return "degraded_same_model"
        if verifier_provider_id in set(failed_provider_ids or set()):
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but the selected verifier lane already failed earlier in this turn.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked_failed_lane",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
            )
            return "blocked_failed_lane"
        verifier_manifest = next(
            (manifest for manifest in ranked_manifests if manifest.provider_id == verifier_provider_id),
            None,
        )
        if verifier_manifest is None:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but the selected verifier manifest was not ranked for execution.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
            )
            return "blocked"

        verifier_request = ModelRequest(
            task_kind="verification",
            prompt=(
                "Verify the primary model response for correctness, safety, and missing caveats.\n\n"
                f"Task class: {classification.get('task_class', 'unknown')}\n"
                f"Task kind: {task_kind}\n"
                f"Output mode: {output_mode}\n"
                f"Primary provider: {getattr(primary_manifest, 'provider_id', '')}\n"
                f"Primary model: {getattr(primary_manifest, 'model_name', '')}\n\n"
                "Primary response:\n"
                f"{str(primary_response.output_text or '')[:6000]}\n\n"
                "Return a concise verifier verdict with any blocking concerns."
            ),
            system_prompt="You are NULLA's verifier lane. Be strict, concise, and do not rewrite the answer.",
            context={},
            temperature=0.0,
            max_output_tokens=512,
            messages=[],
            output_mode="plain_text",
            trace_id=str(getattr(task, "task_id", "")),
            contract={"mode": "verifier"},
            metadata={
                "task_role": "verifier",
                "primary_provider_id": str(getattr(primary_manifest, "provider_id", "") or ""),
                "primary_model_id": str(getattr(primary_manifest, "model_name", "") or ""),
            },
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_verifier_started",
            f"Verifier lane started with {verifier_manifest.provider_id}.",
            lane="verifier",
            lane_type="verifier",
            phase="started",
            verifier_status="running",
            primary_provider_id=getattr(primary_manifest, "provider_id", ""),
            primary_model_id=getattr(primary_manifest, "model_name", ""),
            verifier_provider_id=verifier_manifest.provider_id,
            verifier_model_id=verifier_manifest.model_name,
        )
        adapter, verifier_response, error = self._invoke_manifest(
            manifest=verifier_manifest,
            request=verifier_request,
            output_mode="plain_text",
            task=task,
            source_context={},
        )
        if error or adapter is None or verifier_response is None:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_failed",
                f"Verifier lane failed with {verifier_manifest.provider_id}.",
                lane="verifier",
                lane_type="verifier",
                phase="failed",
                verifier_status="independent_failed",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_manifest.provider_id,
                verifier_model_id=verifier_manifest.model_name,
                fallback_reason=str(error or "no_response"),
            )
            return "independent_failed"
        _emit_model_routing_event(
            source_context,
            "model_lane_verifier_completed",
            f"Verifier lane completed with {verifier_manifest.provider_id}.",
            lane="verifier",
            lane_type="verifier",
            phase="completed",
            verifier_status="independent_completed",
            primary_provider_id=getattr(primary_manifest, "provider_id", ""),
            primary_model_id=getattr(primary_manifest, "model_name", ""),
            verifier_provider_id=verifier_manifest.provider_id,
            verifier_model_id=verifier_manifest.model_name,
            verifier_output_preview=str(verifier_response.output_text or "")[:280],
        )
        return "independent_completed"

    def _stream_response(
        self,
        *,
        adapter: ModelAdapter,
        manifest: Any,
        request: ModelRequest,
        source_context: dict[str, Any] | None,
    ) -> ModelResponse:
        emitted_chunks: list[str] = []
        raw_events: list[Any] = []
        stream_context = _ephemeral_stream_context(source_context)
        for chunk in adapter.stream_text_task(request):
            if chunk.delta_text:
                emitted_chunks.append(chunk.delta_text)
                emit_runtime_event(
                    stream_context,
                    event_type="model_output_chunk",
                    message=chunk.delta_text,
                    details={
                        "provider_id": manifest.provider_id,
                        "model_name": manifest.model_name,
                    },
                )
            if chunk.raw_event is not None:
                raw_events.append(chunk.raw_event)
        return ModelResponse(
            output_text="".join(emitted_chunks),
            confidence=float(manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=raw_events,
            provider_id=manifest.provider_id,
            model_name=manifest.model_name,
            output_mode=request.output_mode,
        )

    def _maybe_race_manifests(
        self,
        *,
        ranked_manifests: list[Any],
        request: ModelRequest,
        output_mode: str,
        allow_paid_fallback: bool,
        task: Any,
        source_context: dict[str, Any] | None,
    ) -> tuple[Any | None, ModelAdapter | None, ModelResponse | None, list[str], bool]:
        if _streaming_requested(source_context, output_mode=output_mode):
            return None, None, None, [], False
        if not allow_paid_fallback:
            return None, None, None, [], False
        if not ranked_manifests or _manifest_locality(ranked_manifests[0]) != "local":
            return None, None, None, [], False
        budget = get_active_compute_budget()
        if not source_context and str(getattr(budget, "mode", "") or "").strip() != "max_push":
            return None, None, None, [], False
        if int(budget.worker_pool_cap) < 2:
            return None, None, None, [], False
        race_pair = _local_remote_race_pair(ranked_manifests)
        if not race_pair:
            return None, None, None, [], False

        local_manifest, remote_manifest = race_pair
        attempted: list[str] = []
        result_queue: queue.Queue[tuple[Any, ModelAdapter | None, ModelResponse | None, str | None]] = queue.Queue()

        def _worker(manifest: Any) -> None:
            adapter, response, error = self._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
            )
            result_queue.put((manifest, adapter, response, error))

        for manifest in (local_manifest, remote_manifest):
            thread = threading.Thread(
                target=_worker,
                args=(manifest,),
                name=f"nulla-provider-race-{manifest.provider_name}",
                daemon=True,
            )
            thread.start()

        remaining = 2
        while remaining > 0:
            manifest, adapter, response, error = result_queue.get()
            remaining -= 1
            if error or response is None:
                attempted.append(manifest.provider_id)
                continue
            return manifest, adapter, response, attempted, True
        for manifest in (local_manifest, remote_manifest):
            if manifest.provider_id not in attempted:
                attempted.append(manifest.provider_id)
        return None, None, None, attempted, True

    def _decision_from_response(
        self,
        *,
        manifest: Any,
        adapter: ModelAdapter,
        response: ModelResponse,
        task_hash: str,
        task: Any,
        classification: dict[str, Any],
        context_result: Any,
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        ranked_manifests: list[Any],
        attempted: list[str],
        failover_used: bool,
        source: str,
        autopilot_plan: dict[str, Any] | None = None,
        lane_proof: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        validation = validate_provider_output(
            provider_id=manifest.provider_id,
            output_mode=output_mode,
            raw_text=response.output_text,
            trace_id=str(getattr(task, "task_id", "")),
        )
        freshness = freshness_score(None, None)
        trust = output_trust_score(
            manifest=manifest,
            raw_confidence=float(response.confidence or 0.5),
            contract_ok=validation.ok,
            trust_penalty=validation.trust_penalty,
            freshness_score=freshness,
            reviewed=False,
            agreement_score=min(1.0, float(context_result.retrieval_confidence_score or 0.0)),
        )
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id=str(getattr(task, "task_id", "")),
            trace_id=str(getattr(task, "task_id", "")),
            task_class=str(classification.get("task_class", "unknown")),
            task_kind=task_kind,
            output_mode=output_mode,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            raw_output=response.output_text,
            normalized_output=validation.normalized_text,
            structured_output=validation.structured_output,
            confidence=float(response.confidence or 0.5),
            trust_score=trust,
            validation_state="valid" if validation.ok else "contract_failed",
            metadata={
                "cost_class": adapter.estimate_cost_class(),
                "warnings": validation.warnings,
                "context_retrieval_confidence": context_result.report.retrieval_confidence,
            },
            provenance={
                **adapter.get_license_metadata(),
                "provider_id": manifest.provider_id,
                "output_mode": output_mode,
            },
            ttl_seconds=default_ttl_seconds(task_kind=task_kind, output_mode=output_mode),
        )
        audit_logger.log(
            "model_candidate_recorded",
            target_id=candidate_id,
            target_type="candidate_knowledge",
            trace_id=str(getattr(task, "task_id", "")),
            details={
                "provider_id": manifest.provider_id,
                "task_kind": task_kind,
                "output_mode": output_mode,
                "validation_ok": validation.ok,
                "trust_score": trust,
                "execution_source": source,
            },
        )
        return ModelExecutionDecision(
            source=source,
            task_hash=task_hash,
            provider_id=manifest.provider_id,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            output_text=validation.normalized_text or response.output_text,
            structured_output=validation.structured_output,
            confidence=float(response.confidence or 0.5),
            trust_score=trust,
            used_model=True,
            candidate_id=candidate_id,
            failover_used=failover_used,
            validation_state="valid" if validation.ok else "contract_failed",
            details={
                "warnings": validation.warnings,
                "contract_error": validation.error,
                "provider_role": provider_role,
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                "attempted": attempted,
                **({"autopilot_plan": autopilot_plan} if autopilot_plan else {}),
                **({"lane_proof": lane_proof} if lane_proof else {}),
            },
        )

    def _execute_provider_task(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        task_hash: str,
        task_kind: str,
        output_mode: str,
        allow_paid_fallback: bool,
        provider_role: ProviderRole,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelExecutionDecision:
        preferred_provider, preferred_model = self._requested_model_preferences(source_context)
        requested_manifest = self._requested_model_manifest(source_context)
        requested_paid_cloud = requested_manifest is not None and provider_cost_class(requested_manifest) == "paid_cloud"
        resolved_allow_paid = (bool(allow_paid_fallback) or requested_paid_cloud) and not policy_engine.local_only_mode()
        ranked_manifests = rank_provider_candidates(
            self.registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=provider_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=4,
            min_trust=0.45,
        )
        capability_truth = hydrate_capability_truth_with_benchmarks(
            tuple(provider_capability_truth_for_manifest(entry) for entry in ranked_manifests)
        )
        capability_by_provider = {item.provider_id: item for item in capability_truth}
        _llamacpp_provider_id = next(
            (m.provider_id for m in ranked_manifests if "llamacpp" in m.provider_id.lower()),
            "",
        )
        _accel = backend_acceleration_proof(
            provider_id=_llamacpp_provider_id,
            backend="llama.cpp" if _llamacpp_provider_id else "",
            probe=False,
        )
        _eagle3_active = _accel.eagle_status in {"active", "configured_not_proven"}
        autopilot = build_local_inference_autopilot_plan(
            user_text=str(getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")),
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            capability_truth=capability_truth,
            source_context=source_context,
            eagle3_active=_eagle3_active,
        )
        autopilot_plan = autopilot.to_dict()
        if autopilot.selected_provider_id and _can_prioritize_autopilot_selection(
            ranked_manifests,
            autopilot.selected_provider_id,
            allow_paid_fallback=resolved_allow_paid,
        ):
            ranked_manifests = _prioritize_autopilot_selection(ranked_manifests, autopilot.selected_provider_id)
        attempted: list[str] = []
        failover_used = False
        autopilot_block_reason = _autopilot_block_reason(autopilot_plan)
        planned_manifest = next(
            (manifest for manifest in ranked_manifests if manifest.provider_id == autopilot.selected_provider_id),
            None,
        )
        if (
            not autopilot_block_reason
            and autopilot.selected_provider_id
            and planned_manifest is None
            and model_parameter_billions(str(autopilot_plan.get("selected_model") or "")) >= 24.0
        ):
            autopilot_block_reason = "explicit_heavy_lane_unavailable"
        selected_manifest = None if autopilot_block_reason else (planned_manifest or (ranked_manifests[0] if ranked_manifests else None))
        _emit_model_routing_event(
            source_context,
            "model_routing_started",
            f"Autopilot routed {task_kind} through the {autopilot.lane} lane.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane=autopilot.lane,
            lane_type=autopilot.lane,
            phase="routing",
            ranked_candidates=[entry.provider_id for entry in ranked_manifests],
            autopilot_plan=autopilot_plan,
        )
        if selected_manifest is not None:
            _emit_model_routing_event(
                source_context,
                "model_lane_selected",
                f"Selected {selected_manifest.provider_id} for the {autopilot.lane} lane.",
                **_lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=selected_manifest,
                    capability=capability_by_provider.get(selected_manifest.provider_id),
                    phase="selected",
                    attempted=attempted,
                    failover_used=failover_used,
                ),
            )

        if autopilot_block_reason:
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=None,
                capability=None,
                phase="blocked",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason=autopilot_block_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_routing_failed",
                "Autopilot blocked model execution before adapter invocation.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                phase="blocked",
                ranked_candidates=[entry.provider_id for entry in ranked_manifests],
                rejection_reason=autopilot_block_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                "Autopilot blocked this lane before adapter invocation.",
                **proof,
            )
            return ModelExecutionDecision(
                source="autopilot_blocked",
                task_hash=task_hash,
                used_model=False,
                failover_used=failover_used,
                details={
                    "attempted": attempted,
                    "reason": autopilot_block_reason,
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                    "autopilot_plan": autopilot_plan,
                    "lane_proof": proof,
                },
            )

        if not ranked_manifests:
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=None,
                capability=None,
                phase="blocked",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason="no_ranked_provider",
            )
            _emit_model_routing_event(
                source_context,
                "model_routing_failed",
                "No local/provider lane is available for this request.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                phase="rejected",
                ranked_candidates=[],
                rejection_reason="no_ranked_provider",
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                "No local/provider lane was available.",
                **proof,
            )
            return ModelExecutionDecision(
                source="no_provider_available",
                task_hash=task_hash,
                used_model=False,
                failover_used=failover_used,
                details={
                    "attempted": attempted,
                    "reason": "no_ranked_provider",
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [],
                    "autopilot_plan": autopilot_plan,
                    "lane_proof": proof,
                },
            )

        request = self._build_request(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            surface=surface,
            source_context=source_context,
        )

        raced_manifest, raced_adapter, raced_response, raced_attempted, race_used = self._maybe_race_manifests(
            ranked_manifests=ranked_manifests,
            request=request,
            output_mode=output_mode,
            allow_paid_fallback=resolved_allow_paid,
            task=task,
            source_context=source_context,
        )
        if race_used:
            attempted.extend(raced_attempted)
            failover_used = True
            if raced_manifest is not None and raced_adapter is not None and raced_response is not None:
                verifier_status = self._verify_primary_response(
                    primary_manifest=raced_manifest,
                    primary_response=raced_response,
                    ranked_manifests=ranked_manifests,
                    autopilot_plan=autopilot_plan,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    source_context=source_context,
                    failed_provider_ids=set(attempted),
                )
                proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=raced_manifest,
                    capability=capability_by_provider.get(raced_manifest.provider_id),
                    phase="completed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason="provider_race_winner" if raced_manifest.provider_id != autopilot.selected_provider_id else "",
                )
                proof["verifier_status"] = verifier_status
                decision = self._decision_from_response(
                    manifest=raced_manifest,
                    adapter=raced_adapter,
                    response=raced_response,
                    task_hash=task_hash,
                    task=task,
                    classification=classification,
                    context_result=context_result,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    ranked_manifests=ranked_manifests,
                    attempted=attempted,
                    failover_used=failover_used,
                    source="provider_race_winner",
                    autopilot_plan=autopilot_plan,
                    lane_proof=proof,
                )
                _emit_model_routing_event(
                    source_context,
                    "model_lane_proof",
                    f"{raced_manifest.provider_id} completed with runtime proof.",
                    **proof,
                )
                return decision

        skipped_provider_ids = {manifest.provider_id for manifest in ranked_manifests if manifest.provider_id in attempted}
        for manifest in ranked_manifests:
            if manifest.provider_id in skipped_provider_ids:
                continue
            _emit_model_routing_event(
                source_context,
                "model_lane_started",
                f"Using {manifest.provider_id}.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                role=provider_role,
                phase="running",
                selected_provider_id=manifest.provider_id,
                provider_id=manifest.provider_id,
                selected_model=manifest.model_name,
                model_id=manifest.model_name,
                tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                attempted=attempted,
            )
            adapter, response, error = self._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
            )
            if error or adapter is None or response is None:
                attempted.append(manifest.provider_id)
                failover_used = True
                failed_proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=manifest,
                    capability=capability_by_provider.get(manifest.provider_id),
                    phase="failed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason=str(error or "no_response"),
                )
                _emit_model_routing_event(
                    source_context,
                    "model_lane_failed",
                    (
                        f"{manifest.provider_id} failed; no smaller fallback is allowed for this explicit heavy lane."
                        if _planned_heavy_manifest_failed(autopilot_plan, manifest)
                        else f"{manifest.provider_id} failed; trying fallback if available."
                    ),
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    lane=autopilot.lane,
                    lane_type=autopilot.lane,
                    role=provider_role,
                    phase="failed",
                    selected_provider_id=manifest.provider_id,
                    provider_id=manifest.provider_id,
                    selected_model=manifest.model_name,
                    model_id=manifest.model_name,
                    tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                    queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                    attempted=attempted,
                    error=str(error or "no_response"),
                    fallback_reason=str(error or "no_response"),
                    lane_proof=failed_proof,
                )
                if _planned_heavy_manifest_failed(autopilot_plan, manifest):
                    final_proof = dict(failed_proof)
                    final_proof["phase"] = "failed"
                    final_proof["fallback_reason"] = f"explicit_heavy_lane_failed:{error or 'no_response'!s}"
                    final_proof["verifier_status"] = "not_run_primary_failed"
                    _emit_model_routing_event(
                        source_context,
                        "model_lane_proof",
                        "Explicit heavy lane failed; no smaller fallback was used.",
                        **final_proof,
                    )
                    return ModelExecutionDecision(
                        source="explicit_heavy_lane_failed",
                        task_hash=task_hash,
                        used_model=False,
                        failover_used=failover_used,
                        details={
                            "attempted": attempted,
                            "reason": final_proof["fallback_reason"],
                            "provider_role": provider_role,
                            "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                            "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                            "autopilot_plan": autopilot_plan,
                            "lane_proof": final_proof,
                        },
                    )
                continue
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=manifest,
                capability=capability_by_provider.get(manifest.provider_id),
                phase="completed",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason="fallback_after_failed_lane" if failover_used else "",
            )
            proof["verifier_status"] = self._verify_primary_response(
                primary_manifest=manifest,
                primary_response=response,
                ranked_manifests=ranked_manifests,
                autopilot_plan=autopilot_plan,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                source_context=source_context,
                failed_provider_ids=set(attempted),
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_completed",
                f"{manifest.provider_id} completed.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                role=provider_role,
                phase="completed",
                selected_provider_id=manifest.provider_id,
                provider_id=manifest.provider_id,
                selected_model=manifest.model_name,
                model_id=manifest.model_name,
                tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                attempted=attempted,
                failover_used=failover_used,
                lane_proof=proof,
            )
            decision = self._decision_from_response(
                manifest=manifest,
                adapter=adapter,
                response=response,
                task_hash=task_hash,
                task=task,
                classification=classification,
                context_result=context_result,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                ranked_manifests=ranked_manifests,
                attempted=attempted,
                failover_used=failover_used,
                source="provider_execution",
                autopilot_plan=autopilot_plan,
                lane_proof=proof,
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                f"{manifest.provider_id} completed with runtime proof.",
                **proof,
            )
            return decision

        proof = _lane_proof_payload(
            source_context=source_context,
            task=task,
            classification=classification,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            autopilot_plan=autopilot_plan,
            manifest=None,
            capability=None,
            phase="failed",
            attempted=attempted,
            failover_used=failover_used,
            fallback_reason="all_ranked_providers_failed",
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_failed",
            "All ranked provider lanes failed.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane=autopilot.lane,
            lane_type=autopilot.lane,
            phase="failed",
            ranked_candidates=[entry.provider_id for entry in ranked_manifests],
            attempted=attempted,
            rejection_reason="all_ranked_providers_failed",
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_proof",
            "All ranked provider lanes failed.",
            **proof,
        )
        return ModelExecutionDecision(
            source="no_provider_available",
            task_hash=task_hash,
            used_model=False,
            failover_used=failover_used,
            details={
                "attempted": attempted,
                "reason": "all_ranked_providers_failed",
                "provider_role": provider_role,
                "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                "autopilot_plan": autopilot_plan,
                "lane_proof": proof,
            },
        )

    def _requested_model_preferences(self, source_context: dict[str, Any] | None) -> tuple[str | None, str | None]:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None, None
        lowered = requested_model.lower()
        if lowered in {"nulla", "nulla:latest"}:
            return None, None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest.provider_name, manifest.model_name
        for manifest in manifests:
            if requested_model == manifest.model_name:
                return None, manifest.model_name

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            if any(provider_hint == manifest.provider_name for manifest in manifests):
                return provider_hint, model_hint
        return None, requested_model

    def _requested_model_manifest(self, source_context: dict[str, Any] | None) -> Any | None:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None
        lowered = requested_model.lower()
        if lowered in {"nulla", "nulla:latest"}:
            return None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest

        model_matches = [manifest for manifest in manifests if requested_model == manifest.model_name]
        if len(model_matches) == 1:
            return model_matches[0]

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            for manifest in manifests:
                if provider_hint == manifest.provider_name and model_hint == manifest.model_name:
                    return manifest
        return None


def _memory_is_good_enough(context_result: Any, classification: dict[str, Any]) -> bool:
    if getattr(context_result, "local_candidates", None):
        top = float(context_result.local_candidates[0].get("score") or 0.0)
        if top >= 0.64:
            return True
    retrieval_confidence = float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0)
    task_class = str(classification.get("task_class", "unknown"))
    if task_class in {"shell_guidance", "file_inspection"} and retrieval_confidence >= 0.45:
        return True
    return retrieval_confidence >= 0.72


def _force_model_on_chat_surface(
    *,
    force_model: bool,
    surface: str,
    source_context: dict[str, Any] | None,
) -> bool:
    if force_model:
        return True
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface in _CHAT_TRUTH_SURFACES:
        return True
    source_surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    return source_surface in _CHAT_TRUTH_SURFACES


def _provider_role_for_request(role: object) -> ProviderRole:
    candidate = str(role or "auto").strip().lower()
    if candidate in {"drone", "queen"}:
        return candidate
    return "auto"


def _emit_model_routing_event(
    source_context: dict[str, Any] | None,
    event_type: str,
    message: str,
    **details: Any,
) -> None:
    if source_context is None:
        return
    if event_type == "model_lane_proof":
        visible_details = {key: value for key, value in details.items() if value is not None}
    else:
        visible_details = {key: value for key, value in details.items() if value is not None and value != ""}
    emit_runtime_event(
        source_context,
        event_type=event_type,
        message=message,
        details=visible_details,
    )


def _lane_proof_payload(
    *,
    source_context: dict[str, Any] | None,
    task: Any,
    classification: dict[str, Any],
    task_kind: str,
    output_mode: str,
    provider_role: ProviderRole,
    autopilot_plan: dict[str, Any],
    manifest: Any | None,
    capability: Any | None,
    phase: str,
    attempted: list[str],
    failover_used: bool,
    fallback_reason: str = "",
) -> dict[str, Any]:
    planned_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    planned_model = str(autopilot_plan.get("selected_model") or "").strip()
    actual_provider = str(getattr(manifest, "provider_id", "") or "").strip()
    actual_model = str(getattr(manifest, "model_name", "") or "").strip()
    mismatch = bool(actual_provider and planned_provider and actual_provider != planned_provider)
    visible_phase = "failed" if mismatch and phase == "completed" else phase
    normalized_fallback = fallback_reason or _lane_fallback_reason(
        lane=str(autopilot_plan.get("lane") or ""),
        actual_model=actual_model,
        mismatch=mismatch,
    )
    backend = _backend_for_provider(actual_provider or planned_provider)
    acceleration = backend_acceleration_proof(
        provider_id=actual_provider or planned_provider,
        model_id=actual_model or planned_model,
        backend=backend,
        probe=True,
    )
    return {
        "schema": "nulla.model_lane_proof.v1",
        "turn_id": str(getattr(task, "task_id", "") or ""),
        "session_id": str((source_context or {}).get("runtime_session_id") or (source_context or {}).get("session_id") or ""),
        "task_class": str(classification.get("task_class", "unknown") or "unknown"),
        "task_kind": str(task_kind or "unknown"),
        "output_mode": str(output_mode or "plain_text"),
        "complexity": _complexity_for_lane(str(autopilot_plan.get("lane") or "")),
        "lane": str(autopilot_plan.get("lane") or "unknown"),
        "lane_type": str(autopilot_plan.get("lane") or "unknown"),
        "phase": visible_phase,
        "provider_role": provider_role,
        "role": provider_role,
        "planned_provider_id": planned_provider,
        "planned_model_id": planned_model,
        "provider_id": actual_provider or planned_provider,
        "model_id": actual_model or planned_model,
        "actual_adapter_provider_id": actual_provider,
        "actual_adapter_model_id": actual_model,
        "backend": backend,
        "tokens_per_second": float(getattr(capability, "tokens_per_second", 0.0) or 0.0),
        "measurement_source": str(getattr(capability, "measurement_source", "") or "unknown"),
        "queue_depth": int(getattr(capability, "queue_depth", 0) or 0),
        "fallback_reason": normalized_fallback,
        "verifier_status": _verifier_status(autopilot_plan),
        "verifier_provider_id": str(autopilot_plan.get("verifier_provider_id") or "").strip(),
        "verifier_model_id": str(autopilot_plan.get("verifier_model") or "").strip(),
        "kv_cache_status": acceleration.kv_cache_status or _kv_cache_status(autopilot_plan),
        "backend_cache_proof": acceleration.backend_cache_proof,
        "speculative_status": acceleration.speculative_status,
        "speculative_proof": acceleration.speculative_proof,
        "eagle_status": acceleration.eagle_status,
        "eagle_proof": acceleration.eagle_proof,
        "attempted": list(attempted),
        "failover_used": bool(failover_used),
        "mismatch": mismatch,
        "failure_reason": "planned_adapter_mismatch" if mismatch else "",
    }


def _complexity_for_lane(lane: str) -> str:
    return {
        "tiny": "trivial",
        "daily": "medium",
        "deep": "hard",
        "cloud": "remote",
        "human": "blocked",
    }.get(str(lane or "").strip().lower(), "unknown")


def _backend_for_provider(provider_id: str) -> str:
    lowered = str(provider_id or "").strip().lower()
    if "llamacpp" in lowered or "llama.cpp" in lowered:
        return "llama.cpp"
    if "mlx" in lowered:
        return "mlx"
    if "vllm" in lowered:
        return "vllm"
    if "ollama" in lowered:
        return "ollama"
    return "unknown"


def _lane_fallback_reason(*, lane: str, actual_model: str, mismatch: bool) -> str:
    if mismatch:
        return "planned_adapter_mismatch"
    if str(lane or "").strip().lower() == "tiny" and model_parameter_billions(actual_model) > 4.0:
        return "tiny_lane_unavailable"
    return ""


def _autopilot_block_reason(autopilot_plan: dict[str, Any]) -> str:
    warnings = {str(item).strip() for item in list(autopilot_plan.get("warnings") or []) if str(item).strip()}
    if "explicit_heavy_lane_unavailable" in warnings:
        return "explicit_heavy_lane_unavailable"
    return ""


def _planned_heavy_manifest_failed(autopilot_plan: dict[str, Any], manifest: Any) -> bool:
    planned_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    if not planned_provider or planned_provider != str(getattr(manifest, "provider_id", "") or "").strip():
        return False
    return model_parameter_billions(str(autopilot_plan.get("selected_model") or getattr(manifest, "model_name", "") or "")) >= 24.0


def _verifier_status(autopilot_plan: dict[str, Any]) -> str:
    if not bool(autopilot_plan.get("verifier_required")):
        return "not_required"
    verifier_provider = str(autopilot_plan.get("verifier_provider_id") or "").strip()
    selected_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    if not selected_provider:
        return "blocked"
    if not verifier_provider:
        return "blocked"
    if verifier_provider == selected_provider:
        return "degraded_same_model"
    return "independent"


def _kv_cache_status(autopilot_plan: dict[str, Any]) -> str:
    prefix_cache = autopilot_plan.get("prefix_cache")
    if not isinstance(prefix_cache, dict):
        return "unsupported"
    backend = str(prefix_cache.get("backend") or "").strip()
    if backend == "ollama":
        return "ollama=not_supported_keep_alive_only"
    if bool(prefix_cache.get("supported")):
        return f"{backend}=supported_not_active"
    return f"{backend or 'unknown'}=unsupported"


def _streaming_requested(source_context: dict[str, Any] | None, *, output_mode: str) -> bool:
    if output_mode != "plain_text":
        return False
    return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())


def _ephemeral_stream_context(source_context: dict[str, Any] | None) -> dict[str, Any]:
    stream_id = str((source_context or {}).get("runtime_event_stream_id") or "").strip()
    if not stream_id:
        return {}
    return {"runtime_event_stream_id": stream_id}


def _manifest_locality(manifest: Any) -> str:
    deployment_class = str(getattr(manifest, "metadata", {}).get("deployment_class") or "").strip().lower()
    if deployment_class in {"local", "remote"}:
        return deployment_class
    base_url = str(getattr(manifest, "runtime_config", {}).get("base_url") or "").strip().lower()
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        return "local"
    return "remote"


def _local_remote_race_pair(ranked_manifests: list[Any]) -> tuple[Any, Any] | None:
    local_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "local"), None)
    remote_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "remote"), None)
    if local_manifest is None or remote_manifest is None:
        return None
    if local_manifest.provider_id == remote_manifest.provider_id:
        return None
    return local_manifest, remote_manifest


def _can_prioritize_autopilot_selection(
    ranked_manifests: list[Any],
    selected_provider_id: str,
    *,
    allow_paid_fallback: bool,
) -> bool:
    selected = next((manifest for manifest in ranked_manifests if manifest.provider_id == selected_provider_id), None)
    if selected is None:
        return False
    if not ranked_manifests or not allow_paid_fallback:
        return True
    if _manifest_locality(ranked_manifests[0]) == "remote" and _manifest_locality(selected) == "local":
        return False
    if _manifest_locality(ranked_manifests[0]) != "local" or _manifest_locality(selected) != "remote":
        return True
    return _local_remote_race_pair(ranked_manifests) is None


def _prioritize_autopilot_selection(ranked_manifests: list[Any], selected_provider_id: str) -> list[Any]:
    return sorted(
        ranked_manifests,
        key=lambda manifest: 0 if manifest.provider_id == selected_provider_id else 1,
    )
