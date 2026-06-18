from __future__ import annotations

import json
from typing import Any

from core import audit_logger, policy_engine
from core.candidate_knowledge_lane import get_candidate_by_id
from core.curiosity_roamer import AdaptiveResearchResult
from core.task_router import looks_like_explicit_lookup_request, looks_like_public_entity_lookup_request


class ResearchToolLoopFacadeMixin:
    def _collect_live_web_notes(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> list[dict[str, Any]]:
        if not policy_engine.allow_web_fallback():
            return []
        source_context = dict(source_context or {})
        surface = str(source_context.get("surface", "") or "").lower()
        platform = str(source_context.get("platform", "") or "").lower()
        explicit_remote_policy = "allow_remote_fetch" in source_context
        allow_remote_fetch = bool(source_context.get("allow_remote_fetch", False))
        trusted_live_surface = (
            surface in {"channel", "openclaw", "api"}
            or platform in {"openclaw", "web_companion", "telegram", "discord"}
        )
        # Explicit false is an operator/privacy boundary; trusted surfaces only default
        # to live research when the caller omits the remote-fetch policy entirely.
        remote_fetch_allowed = allow_remote_fetch if explicit_remote_policy else trusted_live_surface
        if not remote_fetch_allowed:
            return []

        task_class = str(classification.get("task_class", "unknown"))
        wants_fresh_info = self._wants_fresh_info(query_text, interpretation=interpretation)
        wants_live_lookup = task_class in {"research", "system_design", "integration_orchestration"}
        if not wants_fresh_info:
            return []
        try:
            if wants_live_lookup:
                notes = self._planned_search_query(
                    query_text,
                    task_id=task_id,
                    limit=3,
                    task_class=task_class,
                    topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
                    source_label="duckduckgo.com",
                )
                if notes:
                    return notes
            return self._search_query(
                query_text,
                task_id=task_id,
                limit=3,
                source_label="duckduckgo.com",
            )
        except Exception as exc:
            audit_logger.log(
                "agent_live_web_lookup_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return []

    def _collect_adaptive_research(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> AdaptiveResearchResult:
        try:
            return self.curiosity.adaptive_research(
                task_id=task_id,
                user_input=query_text,
                classification=classification,
                interpretation=interpretation,
                source_context=dict(source_context or {}),
            )
        except Exception as exc:
            audit_logger.log(
                "adaptive_research_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return AdaptiveResearchResult(
                enabled=False,
                reason="controller_error",
                strategy="tool_gap",
                tool_gap_note="Adaptive research failed for this turn, so I should stay cautious about unsupported claims.",
                admitted_uncertainty=True,
                uncertainty_reason="Adaptive research failed for this turn.",
            )

    def _should_frontload_curiosity(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
    ) -> bool:
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design"}:
            return True
        if task_class != "integration_orchestration":
            return False
        lowered = str(query_text or "").lower()
        if any(
            marker in lowered
            for marker in (
                "build",
                "design",
                "architecture",
                "best practice",
                "best practices",
                "framework",
                "stack",
                "github",
                "repo",
                "repos",
                "docs",
                "documentation",
            )
        ):
            return True
        topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"telegram bot", "discord bot"} & topic_hints)

    def _curiosity_candidate_evidence(self, candidate_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        plan_candidates: list[dict[str, Any]] = []
        context_snippets: list[dict[str, Any]] = []
        for candidate_id in list(candidate_ids or [])[:3]:
            candidate = get_candidate_by_id(candidate_id)
            if not candidate:
                continue
            structured = dict(candidate.get("structured_output") or {})
            metadata = dict(candidate.get("metadata") or {})
            snippets = [dict(item) for item in list(structured.get("snippets") or []) if isinstance(item, dict)]
            topic = str(structured.get("topic") or metadata.get("curiosity_topic") or "technical research").strip()
            topic_kind = str(structured.get("topic_kind") or "technical").strip().lower() or "technical"
            score = self._curiosity_candidate_score(candidate=candidate, snippets=snippets)
            summary = self._curiosity_candidate_summary(
                topic=topic,
                topic_kind=topic_kind,
                snippets=snippets,
                fallback_text=str(candidate.get("normalized_output") or candidate.get("raw_output") or ""),
            )
            plan_candidates.append(
                {
                    "summary": summary,
                    "resolution_pattern": self._curiosity_candidate_steps(topic_kind=topic_kind, snippets=snippets),
                    "score": score,
                    "source_type": "curiosity_candidate",
                    "source_node_id": "curiosity_roamer",
                    "provider_name": "curiosity_roamer",
                    "model_name": str(candidate.get("model_name") or "bounded_web_research"),
                    "candidate_id": candidate_id,
                }
            )
            for index, snippet in enumerate(snippets[:4], start=1):
                snippet_summary = " ".join(str(snippet.get("summary") or "").split()).strip()
                if not snippet_summary:
                    continue
                label = str(
                    snippet.get("source_profile_label")
                    or snippet.get("origin_domain")
                    or snippet.get("source_label")
                    or "curated source"
                ).strip()
                context_snippets.append(
                    {
                        "title": f"{label} note {index}",
                        "source_type": "curiosity_research",
                        "summary": snippet_summary[:320],
                        "confidence": score,
                        "priority": score,
                        "metadata": {
                            "origin_domain": snippet.get("origin_domain"),
                            "result_url": snippet.get("result_url"),
                            "source_profile_id": snippet.get("source_profile_id"),
                            "created_at": candidate.get("created_at"),
                            "candidate_id": candidate_id,
                        },
                    }
                )
        return plan_candidates, context_snippets

    def _curiosity_candidate_summary(
        self,
        *,
        topic: str,
        topic_kind: str,
        snippets: list[dict[str, Any]],
        fallback_text: str,
    ) -> str:
        clean_topic = " ".join(str(topic or "").split()).strip() or "this topic"
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains

        lead = f"Research brief for {clean_topic}:"
        if topic_kind in {"technical", "integration"} and official_docs and repo_examples:
            lead = f"For {clean_topic}, start with official docs first and use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {clean_topic}, anchor the answer on official documentation before applying examples."
        elif repo_examples:
            lead = f"For {clean_topic}, compare a few reputable GitHub implementations before locking the design."

        highlights = [
            " ".join(str(snippet.get("summary") or "").split()).strip().rstrip(".")
            for snippet in snippets[:2]
            if str(snippet.get("summary") or "").strip()
        ]
        if highlights:
            return f"{lead} {' '.join(highlights)}"[:420]
        clean_fallback = " ".join(str(fallback_text or "").split()).strip()
        if clean_fallback:
            return f"{lead} {clean_fallback}"[:420]
        return lead[:420]

    def _curiosity_candidate_steps(self, *, topic_kind: str, snippets: list[dict[str, Any]]) -> list[str]:
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        steps: list[str] = []
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            steps.append("review_official_platform_docs")
        if "github.com" in domains or "reputable repositories" in labels:
            steps.append("compare_reputable_repo_examples")
        if topic_kind in {"technical", "integration"}:
            steps.extend(["define_minimal_architecture", "validate_auth_limits_and_deployment_constraints"])
        elif topic_kind == "design":
            steps.extend(["compare_reference_patterns", "shape_minimal_user_flow"])
        elif topic_kind == "news":
            steps.extend(["compare_multiple_reputable_sources", "separate_verified_facts_from_speculation"])
        if not steps:
            steps.append("summarize_grounded_findings")
        deduped: list[str] = []
        seen: set[str] = set()
        for step in steps:
            if step in seen:
                continue
            seen.add(step)
            deduped.append(step)
        return deduped[:4]

    def _curiosity_candidate_score(self, *, candidate: dict[str, Any], snippets: list[dict[str, Any]]) -> float:
        score = float(candidate.get("trust_score") or candidate.get("confidence") or 0.0)
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            score += 0.08
        if "github.com" in domains or "reputable repositories" in labels:
            score += 0.05
        if len(domains) >= 2:
            score += 0.03
        return max(0.50, min(0.90, score))

    def _web_note_plan_candidates(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        web_notes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        notes = [dict(note) for note in list(web_notes or []) if isinstance(note, dict)]
        if not notes:
            return []
        labels = {
            str(note.get("source_profile_label") or note.get("source_profile_id") or "").strip().lower()
            for note in notes
        }
        domains = {
            str(note.get("origin_domain") or "").strip().lower()
            for note in notes
            if str(note.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains
        topic = " ".join(str(query_text or "").split()).strip() or str(classification.get("task_class") or "research")
        lead = f"Research notes for {topic}:"
        if official_docs and repo_examples:
            lead = f"For {topic}, anchor the design on official docs first, then use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {topic}, anchor the answer on official documentation."
        elif repo_examples:
            lead = f"For {topic}, compare reputable GitHub implementations before locking the design."
        highlights = [
            " ".join(str(note.get("summary") or "").split()).strip().rstrip(".")
            for note in notes[:2]
            if str(note.get("summary") or "").strip()
        ]
        steps: list[str] = []
        if official_docs:
            steps.append("review_official_docs")
        if repo_examples:
            steps.append("compare_reputable_repo_examples")
        if str(classification.get("task_class") or "") in {"system_design", "integration_orchestration"}:
            steps.extend(["define_minimal_architecture", "validate_runtime_constraints"])
        elif str(classification.get("task_class") or "") == "research":
            steps.extend(["compare_findings", "summarize_grounded_recommendation"])
        score = max(float(note.get("confidence") or 0.0) for note in notes)
        if official_docs:
            score += 0.08
        if repo_examples:
            score += 0.05
        summary = lead if not highlights else f"{lead} {' '.join(highlights)}"
        deduped_steps: list[str] = []
        seen_steps: set[str] = set()
        for step in steps:
            if step in seen_steps:
                continue
            seen_steps.add(step)
            deduped_steps.append(step)
        return [
            {
                "summary": summary[:420],
                "resolution_pattern": deduped_steps[:4] or ["summarize_grounded_findings"],
                "score": max(0.45, min(0.86, score)),
                "source_type": "planned_web_candidate",
                "source_node_id": "web_source_planner",
                "provider_name": "web_source_planner",
                "model_name": "source_ranked_web_notes",
            }
        ]

    def _maybe_execute_model_tool_intent(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        surface: str,
    ) -> dict[str, Any] | None:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        checkpoint = self._get_runtime_checkpoint(checkpoint_id) if checkpoint_id else None
        checkpoint_state = dict((checkpoint or {}).get("state") or {})
        if self._should_keep_ai_first_chat_lane(
            user_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            checkpoint_state=checkpoint_state,
        ):
            return None
        if self._should_run_builder_controller(
            effective_input=effective_input,
            classification=classification,
            source_context=dict(source_context or {}),
        ):
            return None
        if not self._should_attempt_tool_intent(
            effective_input,
            task_class=str(classification.get("task_class", "unknown")),
            source_context=source_context,
        ):
            return None
        loop_source_context = self._merge_runtime_source_contexts(
            dict(checkpoint_state.get("loop_source_context") or {}),
            dict(source_context or {}),
        )
        executed_steps: list[dict[str, Any]] = []
        last_tool_decision = None
        seen_tool_payloads: set[str] = set()
        pending_tool_payload: dict[str, Any] | None = None
        if checkpoint_state:
            executed_steps = [dict(step) for step in list(checkpoint_state.get("executed_steps") or []) if isinstance(step, dict)]
            seen_tool_payloads = {
                str(item)
                for item in list(checkpoint_state.get("seen_tool_payloads") or [])
                if str(item).strip()
            }
            saved_pending = checkpoint_state.get("pending_tool_payload") or (checkpoint or {}).get("pending_intent") or {}
            if isinstance(saved_pending, dict) and saved_pending:
                pending_tool_payload = dict(saved_pending)
        if checkpoint and (executed_steps or pending_tool_payload):
            self._emit_runtime_event(
                loop_source_context,
                event_type="tool_loop_resumed",
                message=(
                    f"Resuming tool loop from {len(executed_steps)} completed step"
                    f"{'' if len(executed_steps) == 1 else 's'}."
                ),
                step_count=len(executed_steps),
            )
        max_steps = 5

        while len(executed_steps) < max_steps:
            tool_decision = None
            tool_payload: dict[str, Any] = {}
            provider_id = None
            validation_state = "not_run"
            confidence_hint = 0.55

            if pending_tool_payload:
                tool_payload = dict(pending_tool_payload)
                pending_tool_payload = None
                tool_name = str(tool_payload.get("intent") or "").strip()
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_selected" if tool_name else "tool_failed",
                    message=(
                        f"Resuming pending tool {tool_name}."
                        if tool_name
                        else "Resuming invalid pending tool payload with no intent name."
                    ),
                    tool_name=tool_name or "unknown",
                )
            else:
                workflow_decision = self._plan_tool_workflow(
                    user_text=effective_input,
                    task_class=str(classification.get("task_class") or "unknown"),
                    executed_steps=executed_steps,
                    source_context=loop_source_context,
                )
                if workflow_decision.handled and workflow_decision.stop_after:
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_stop",
                        message="Workflow planner gathered enough state and stopped before another tool step.",
                        status=workflow_decision.reason,
                        step_count=len(executed_steps),
                    )
                    break
                if workflow_decision.handled and workflow_decision.next_payload:
                    tool_payload = dict(workflow_decision.next_payload)
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_step",
                        message=f"Workflow planner selected {tool_name}.",
                        tool_name=tool_name or "unknown",
                        status=workflow_decision.reason,
                    )
                else:
                    tool_decision = self.memory_router.resolve_tool_intent(
                        task=task,
                        classification=classification,
                        interpretation=interpretation,
                        context_result=context_result,
                        persona=persona,
                        surface=surface,
                        source_context=loop_source_context,
                    )
                    last_tool_decision = tool_decision
                    direct_message = self._tool_intent_direct_message(tool_decision.structured_output)
                    if direct_message is not None:
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_loop_completed",
                            message=(
                                f"Returning grounded reply after {len(executed_steps)} real tool step"
                                f"{'' if len(executed_steps) == 1 else 's'}."
                            ),
                            step_count=len(executed_steps),
                        )
                        confidence = max(0.35, min(0.96, float(tool_decision.trust_score or tool_decision.confidence or 0.55)))
                        return {
                            "response": self._render_tool_loop_response(
                                final_message=direct_message,
                                executed_steps=executed_steps,
                                include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                            ),
                            "confidence": confidence,
                            "success": True,
                            "status": "direct_response_after_tools" if executed_steps else "direct_response",
                            "mode": "tool_executed" if executed_steps else "advice_only",
                            "task_outcome": "success",
                            "details": {
                                "tool_name": "respond.direct",
                                "tool_provider": tool_decision.provider_id,
                                "tool_validation": tool_decision.validation_state,
                                "tool_steps": [step["tool_name"] for step in executed_steps],
                            },
                            "learned_plan": None,
                            "workflow_summary": self._tool_intent_loop_workflow_summary(
                                executed_steps=executed_steps,
                                provider_id=tool_decision.provider_id,
                                validation_state=tool_decision.validation_state,
                            ),
                        }
                    try:
                        payload_signature = json.dumps(tool_decision.structured_output, sort_keys=True, ensure_ascii=True, default=str)
                    except Exception:
                        payload_signature = str(tool_decision.structured_output)
                    if payload_signature in seen_tool_payloads:
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_repeat_blocked",
                            message="Repeated tool request detected. Switching to grounded synthesis instead of looping.",
                        )
                        if checkpoint_id:
                            self._record_runtime_tool_progress(
                                checkpoint_id,
                                executed_steps=executed_steps,
                                loop_source_context=loop_source_context,
                                seen_tool_payloads=seen_tool_payloads,
                                pending_tool_payload=None,
                                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                                last_tool_response=checkpoint_state.get("last_tool_response"),
                                last_tool_name=str((executed_steps[-1] if executed_steps else {}).get("tool_name") or ""),
                                task_class=str(classification.get("task_class") or "unknown"),
                                status="running",
                            )
                        break
                    seen_tool_payloads.add(payload_signature)
                    tool_payload = dict(tool_decision.structured_output or {})
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    provider_id = tool_decision.provider_id
                    validation_state = tool_decision.validation_state
                    confidence_hint = float(tool_decision.trust_score or tool_decision.confidence or 0.55)
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="tool_selected" if tool_name else "tool_failed",
                        message=(
                            f"Running real tool {tool_name}."
                            if tool_name
                            else "Model returned an invalid tool payload with no intent name."
                        ),
                        tool_name=tool_name or "unknown",
                    )

            tool_name = str(tool_payload.get("intent") or "").strip() or "unknown"
            if checkpoint_id:
                self._record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=tool_payload,
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=tool_name,
                    task_class=str(classification.get("task_class") or "unknown"),
                    status="running",
                )

            execution = self._execute_tool_intent(
                tool_payload,
                task_id=task.task_id,
                session_id=session_id,
                source_context=loop_source_context,
                hive_activity_tracker=self.hive_activity_tracker,
                public_hive_bridge=self.public_hive_bridge,
                checkpoint_id=checkpoint_id,
                step_index=len(executed_steps),
            )
            if not execution.handled:
                break
            if self._should_fallback_after_tool_failure(
                execution=execution,
                effective_input=effective_input,
                classification=classification,
                interpretation=interpretation,
                executed_steps=executed_steps,
            ):
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_fallback_to_research",
                    message="Tool-intent failed before any real tool ran. Continuing with grounded research instead of returning a tooling error.",
                    tool_name=execution.tool_name or tool_name,
                    status=str(execution.status or "failed"),
                )
                checkpoint_state["last_tool_payload"] = dict(tool_payload)
                checkpoint_state["last_tool_response"] = {
                    "handled": bool(execution.handled),
                    "ok": bool(execution.ok),
                    "status": str(execution.status or ""),
                    "response_text": str(execution.response_text or ""),
                    "mode": str(execution.mode or ""),
                    "tool_name": str(execution.tool_name or tool_name),
                    "details": dict(execution.details or {}),
                }
                if checkpoint_id:
                    self._record_runtime_tool_progress(
                        checkpoint_id,
                        executed_steps=executed_steps,
                        loop_source_context=loop_source_context,
                        seen_tool_payloads=seen_tool_payloads,
                        pending_tool_payload=None,
                        last_tool_payload=checkpoint_state.get("last_tool_payload"),
                        last_tool_response=checkpoint_state.get("last_tool_response"),
                        last_tool_name=str(execution.tool_name or tool_name),
                        task_class=str(classification.get("task_class") or "unknown"),
                        status="running",
                    )
                return None

            executed_steps.append(
                {
                    "tool_name": execution.tool_name or tool_name,
                    "status": str(execution.status or "executed"),
                    "mode": execution.mode,
                    "arguments": dict(tool_payload.get("arguments") or {}),
                    "observation": dict((execution.details or {}).get("observation") or {}),
                    "details": dict(execution.details or {}),
                    "summary": self._tool_step_summary(execution.response_text, fallback=str(execution.status or "executed")),
                }
            )
            step_summary = str(executed_steps[-1]["summary"] or "").strip()
            self._emit_runtime_event(
                loop_source_context,
                event_type=str(execution.mode or "tool_failed"),
                message=(
                    f"{'Finished' if execution.mode == 'tool_executed' else 'Approval required for' if execution.mode == 'tool_preview' else 'Tool failed:'} "
                    f"{execution.tool_name or tool_name}. {step_summary}"
                ),
                tool_name=execution.tool_name or tool_name,
                status=str(execution.status or "executed"),
                mode=execution.mode,
            )
            loop_source_context = self._append_tool_result_to_source_context(
                loop_source_context,
                execution=execution,
                tool_name=execution.tool_name or tool_name,
            )
            checkpoint_state["last_tool_payload"] = dict(tool_payload)
            checkpoint_state["last_tool_response"] = {
                "handled": bool(execution.handled),
                "ok": bool(execution.ok),
                "status": str(execution.status or ""),
                "response_text": str(execution.response_text or ""),
                "mode": str(execution.mode or ""),
                "tool_name": str(execution.tool_name or tool_name),
                "details": dict(execution.details or {}),
            }
            if checkpoint_id:
                self._record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=None,
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=str(execution.tool_name or tool_name),
                    task_class=str(classification.get("task_class") or "unknown"),
                    status=(
                        "pending_approval"
                        if execution.mode == "tool_preview"
                        else "failed"
                        if execution.mode == "tool_failed"
                        else "running"
                    ),
                )
            if execution.mode != "tool_executed":
                confidence = max(0.35, min(0.96, confidence_hint))
                task_outcome = "pending_approval" if execution.mode == "tool_preview" else "failed"
                safe_response = self._tool_failure_user_message(
                    execution=execution,
                    effective_input=effective_input,
                    session_id=session_id,
                )
                return {
                    "response": self._render_tool_loop_response(
                        final_message=safe_response,
                        executed_steps=executed_steps,
                        include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                    ),
                    "confidence": confidence,
                    "success": bool(execution.ok),
                    "status": str(execution.status or "executed"),
                    "mode": execution.mode,
                    "task_outcome": task_outcome,
                    "details": {
                        "tool_name": execution.tool_name,
                        "tool_provider": provider_id,
                        "tool_validation": validation_state,
                        "tool_steps": [step["tool_name"] for step in executed_steps],
                        **dict(execution.details or {}),
                    },
                    "learned_plan": execution.learned_plan,
                    "workflow_summary": self._tool_intent_loop_workflow_summary(
                        executed_steps=executed_steps,
                        provider_id=provider_id,
                        validation_state=validation_state,
                    ),
                }

        if not executed_steps:
            return None

        self._emit_runtime_event(
            loop_source_context,
            event_type="tool_synthesizing",
            message="Synthesizing final reply from real tool results.",
            step_count=len(executed_steps),
        )
        if checkpoint_id:
            self._record_runtime_tool_progress(
                checkpoint_id,
                executed_steps=executed_steps,
                loop_source_context=loop_source_context,
                seen_tool_payloads=seen_tool_payloads,
                pending_tool_payload=None,
                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                last_tool_response=checkpoint_state.get("last_tool_response"),
                last_tool_name=str(executed_steps[-1].get("tool_name") or ""),
                task_class=str(classification.get("task_class") or "unknown"),
                status="running",
            )
        synthesis = self.memory_router.resolve(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            force_model=True,
            surface=surface,
            source_context=loop_source_context,
        )
        final_message = self._tool_loop_final_message(synthesis, executed_steps)
        final_provider_id = synthesis.provider_id if synthesis.provider_id else (
            last_tool_decision.provider_id if last_tool_decision else None
        )
        final_validation = synthesis.validation_state if synthesis.validation_state != "not_run" else (
            last_tool_decision.validation_state if last_tool_decision else "not_run"
        )
        confidence = max(
            0.35,
            min(
                0.96,
                float(
                    synthesis.trust_score
                    or synthesis.confidence
                    or (last_tool_decision.trust_score if last_tool_decision else 0.55)
                    or 0.55
                ),
            ),
        )
        return {
            "response": self._render_tool_loop_response(
                final_message=final_message,
                executed_steps=executed_steps,
                include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
            ),
            "confidence": confidence,
            "success": True,
            "status": "multi_step_executed",
            "mode": "tool_executed",
            "task_outcome": "success",
            "details": {
                "tool_name": executed_steps[-1]["tool_name"],
                "tool_provider": final_provider_id,
                "tool_validation": final_validation,
                "tool_steps": [step["tool_name"] for step in executed_steps],
                "step_count": len(executed_steps),
            },
            "learned_plan": None,
            "workflow_summary": self._tool_intent_loop_workflow_summary(
                executed_steps=executed_steps,
                provider_id=final_provider_id,
                validation_state=final_validation,
            ),
        }

    def _should_fallback_after_tool_failure(
        self,
        *,
        execution: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        executed_steps: list[dict[str, Any]],
    ) -> bool:
        if bool(getattr(execution, "ok", False)):
            return False
        if str(getattr(execution, "mode", "") or "").strip().lower() != "tool_failed":
            return False
        if executed_steps:
            return False
        status = str(getattr(execution, "status", "") or "").strip().lower()
        tool_name = str(getattr(execution, "tool_name", "") or "").strip().lower()
        if status not in {"missing_intent", "invalid_payload"} and tool_name not in {"", "unknown"}:
            return False
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design", "integration_orchestration"}:
            return True
        if self._wants_fresh_info(effective_input, interpretation=interpretation):
            return True
        return self._should_frontload_curiosity(
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
        )

    def _wants_fresh_info(self, text: str, *, interpretation: Any) -> bool:
        lowered = " ".join(str(text or "").strip().lower().split())
        if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
            return True
        for marker in (
            "latest",
            "newest",
            "today",
            "current",
            "recent",
            "fresh",
            "just released",
            "release notes",
            "status page",
            "news",
            "update",
            "version",
            "price now",
            "weather",
            "forecast",
            "temperature",
            "search online",
            "check online",
            "look up",
            "browse",
            "on x",
            "on twitter",
            "on the web",
            "on web",
            "google",
        ):
            if marker in lowered:
                return True
        hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"news", "weather", "web", "telegram", "discord", "integration"} & hints)
