from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.bootstrap_context import build_bootstrap_context
from core.cold_context_gate import ColdContextDecision, evaluate_cold_context_gate
from core.context_budgeter import BudgetedLayer, ContextBudget, budget_layer, normalize_budget
from core.context_relevance_ranker import rank_context_items, retrieval_confidence
from core.knowledge_fetcher import consult_relevant_swarm_metadata
from core.liquefy_bridge import lookup_cold_archive_candidates
from core.persistent_memory import search_relevant_memory, search_session_summaries, search_user_heuristics
from core.prompt_assembly_report import ContextItem, PromptAssemblyReport
from core.shard_matcher import find_local_candidates
from core.shard_ranker import rank
from core.task_router import context_strategy
from storage.context_access_log import record_context_access
from storage.db import get_connection
from storage.dialogue_memory import recent_dialogue_turns, session_lexicon
from storage.payment_status import list_payment_status
from storage.shard_reuse_outcomes import summarize_reuse_outcomes_for_shards
from storage.swarm_memory import search_recent_contexts


@dataclass
class TieredContextResult:
    bootstrap_items: list[ContextItem]
    relevant_items: list[ContextItem]
    cold_items: list[ContextItem]
    local_candidates: list[dict[str, Any]]
    swarm_metadata: list[dict[str, Any]]
    report: PromptAssemblyReport
    retrieval_confidence_score: float
    cold_decision: ColdContextDecision

    def assembled_context(self, *, prompt_profile: str = "default") -> str:
        sections: list[str] = []
        bootstrap_items = _filter_context_items_for_prompt_profile(self.bootstrap_items, prompt_profile=prompt_profile)
        relevant_items = _filter_context_items_for_prompt_profile(self.relevant_items, prompt_profile=prompt_profile)
        cold_items = _filter_context_items_for_prompt_profile(self.cold_items, prompt_profile=prompt_profile)
        sections.extend(_render_context_sections("Bootstrap Context", bootstrap_items))
        sections.extend(_render_context_sections("Relevant Context", relevant_items))
        sections.extend(_render_context_sections("Cold Context", cold_items))
        return "\n\n".join(sections)

    def context_snippets(self) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        for item in self.relevant_items + self.cold_items:
            payload = {
                "title": item.title,
                "source_type": item.source_type,
                "summary": item.content,
                "confidence": item.confidence,
                "priority": item.priority,
                "metadata": dict(item.metadata),
            }
            observation = _observation_payload_from_item(item)
            if observation is not None:
                payload["observation"] = observation
            citation = dict(item.metadata or {}).get("reuse_citation")
            if isinstance(citation, dict) and citation:
                payload["citation"] = dict(citation)
            snippets.append(payload)
        return snippets

    def retrieval_profile(self) -> dict[str, Any]:
        return {
            "retrieval_confidence": self.report.retrieval_confidence,
            "retrieval_confidence_score": self.retrieval_confidence_score,
            "swarm_metadata_consulted": bool(self.report.swarm_metadata_consulted),
            "cold_archive_opened": bool(self.report.cold_archive_opened),
            "local_candidate_count": len(self.local_candidates),
            "swarm_metadata_count": len(self.swarm_metadata),
        }


def _filter_context_items_for_prompt_profile(
    items: list[ContextItem],
    *,
    prompt_profile: str,
) -> list[ContextItem]:
    if prompt_profile != "chat_minimal":
        return list(items)
    return [
        item
        for item in items
        if not bool((item.metadata or {}).get("exclude_from_chat_minimal_system_prompt"))
    ]


def _observation_payload_from_item(item: ContextItem) -> dict[str, Any] | None:
    metadata = dict(item.metadata or {})
    observation = metadata.get("observation")
    if isinstance(observation, dict) and observation:
        return dict(observation)
    if str(metadata.get("context_format") or "").strip() != "structured_observation":
        return None
    try:
        loaded = json.loads(str(item.content or ""))
    except Exception:
        return None
    return dict(loaded) if isinstance(loaded, dict) else None


def _remote_shard_citation(
    candidate: dict[str, Any],
    *,
    reuse_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    receipt = dict(candidate.get("retrieval_receipt") or {})
    if str(candidate.get("source_type") or "") != "peer_received" or not receipt:
        return None
    citation = {
        "kind": "remote_shard",
        "shard_id": str(candidate.get("shard_id") or "").strip(),
        "source_peer_id": str(receipt.get("source_peer_id") or "").strip(),
        "source_node_id": str(candidate.get("source_node_id") or "").strip(),
        "manifest_id": str(receipt.get("manifest_id") or "").strip(),
        "content_hash": str(receipt.get("content_hash") or "").strip(),
        "receipt_id": str(receipt.get("receipt_id") or "").strip(),
        "validation_state": str(receipt.get("validation_state") or "").strip(),
        "fetched_at": str(receipt.get("created_at") or "").strip(),
    }
    effective_summary = dict(reuse_summary or candidate.get("reuse_outcomes") or {})
    if effective_summary:
        citation["reuse_outcomes"] = effective_summary
    return citation


def _render_context_sections(label: str, items: list[ContextItem]) -> list[str]:
    if not items:
        return []
    narrative_items = [item for item in items if _observation_payload_from_item(item) is None]
    observation_payloads = [
        payload
        for payload in (_observation_payload_from_item(item) for item in items)
        if isinstance(payload, dict) and payload
    ]
    sections: list[str] = []
    if narrative_items:
        sections.append(label + ":\n" + "\n".join(f"- {item.title}: {item.content}" for item in narrative_items))
    if observation_payloads:
        sections.append(
            label.replace("Context", "Observations") + ":\n" +
            json.dumps(observation_payloads, indent=2, sort_keys=True, ensure_ascii=True, default=str)
        )
    return sections


def _local_candidate_items(task: Any, classification: dict[str, Any]) -> tuple[list[ContextItem], list[dict[str, Any]]]:
    task_class = str(classification.get("task_class") or "").strip()
    ranked = rank(find_local_candidates(task, classification), task)
    outcome_summaries = summarize_reuse_outcomes_for_shards(
        [
            str(candidate.get("shard_id") or "").strip()
            for candidate in ranked
            if str(candidate.get("source_type") or "") == "peer_received"
            and not dict(candidate.get("reuse_outcomes") or {})
        ],
        task_class=task_class,
    )
    items: list[ContextItem] = []
    for candidate in ranked[:8]:
        pattern = list(candidate.get("resolution_pattern") or [])[:4]
        shard_id = str(candidate.get("shard_id") or "").strip()
        reuse_summary = dict(candidate.get("reuse_outcomes") or outcome_summaries.get(shard_id) or {})
        citation = _remote_shard_citation(candidate, reuse_summary=reuse_summary)
        if citation:
            reuse_outcomes = dict(citation.get("reuse_outcomes") or {})
            reuse_note = ""
            quality_backed = int(reuse_outcomes.get("quality_backed_count") or 0)
            quality_backed_durable = int(reuse_outcomes.get("quality_backed_durable_count") or 0)
            answer_backed = int(reuse_outcomes.get("answer_backed_count") or 0)
            answer_backed_durable = int(reuse_outcomes.get("answer_backed_durable_count") or 0)
            selected = int(reuse_outcomes.get("selected_count") or 0)
            success = int(reuse_outcomes.get("success_count") or 0)
            durable = int(reuse_outcomes.get("durable_count") or 0)
            if quality_backed > 0:
                reuse_note = (
                    f" Previously improved clean answers for this task class in {quality_backed} turns "
                    f"({quality_backed_durable} durable)."
                )
            elif answer_backed > 0:
                reuse_note = (
                    f" Previously backed answers for this task class in {answer_backed} turns "
                    f"({answer_backed_durable} durable), but clean-answer proof is still weaker."
                )
            elif selected > 0:
                reuse_note = f" Previously selected during planning for this task class in {selected} turns."
            elif success > 0:
                reuse_note = (
                    f" Previously cited for this task class in {success} successful turns "
                    f"({durable} durable); answer-backed proof not established yet."
                )
            content = (
                f"Cached remote shard from {citation.get('source_peer_id', 'unknown peer')[:12]}... "
                f"with validation {citation.get('validation_state', 'unknown')}. "
                f"{candidate.get('summary', '')} "
                f"Pattern: {', '.join(str(step) for step in pattern) or 'n/a'}."
                f"{reuse_note}"
            ).strip()
            source_type = "remote_shard_cache"
            include_reason = "remote_shard_reuse"
        else:
            content = (
                f"{candidate.get('summary', '')} "
                f"Pattern: {', '.join(str(step) for step in pattern) or 'n/a'}."
            ).strip()
            source_type = "local_shard"
            include_reason = "local_shard_match"
        metadata = {
            "shard_id": candidate["shard_id"],
            "problem_class": candidate["problem_class"],
            "freshness_ts": candidate.get("freshness_ts"),
            "trust_score": candidate.get("trust_score"),
        }
        if citation:
            metadata["reuse_citation"] = citation
        items.append(
            ContextItem(
                item_id=f"local-shard-{candidate['shard_id']}",
                layer="relevant",
                source_type=source_type,
                title=f"Local shard {candidate['problem_class']}",
                content=content[:420],
                priority=float(candidate.get("score") or 0.0),
                confidence=float(candidate.get("score") or 0.0),
                include_reason=include_reason,
                metadata=metadata,
                provenance={"source_node_id": candidate.get("source_node_id")},
            )
        )
    return items, ranked


def _dialogue_items(session_id: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for turn in recent_dialogue_turns(session_id, limit=6):
        items.append(
            ContextItem(
                item_id=f"dialogue-{turn['turn_id']}",
                layer="relevant",
                source_type="dialogue_turn",
                title="Recent dialogue turn",
                content=str(turn.get("reconstructed_input") or turn.get("normalized_input") or "")[:260],
                confidence=float(turn.get("understanding_confidence") or 0.0),
                include_reason="recent_dialogue_memory",
                metadata={
                    "created_at": turn.get("created_at"),
                    "topic_hints": list(turn.get("topic_hints") or []),
                },
            )
        )
    return items


def _runtime_tool_observation_items(session_id: str) -> list[ContextItem]:
    if not str(session_id or "").strip():
        return []
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT checkpoint_id, status, state_json, updated_at
            FROM runtime_checkpoints
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    except Exception:
        return []
    finally:
        conn.close()
    if not row:
        return []
    try:
        state = json.loads(str(row["state_json"] or "{}"))
    except Exception:
        return []
    if not isinstance(state, dict):
        return []
    last_tool_response = dict(state.get("last_tool_response") or {})
    if not last_tool_response:
        return []
    details = dict(last_tool_response.get("details") or {})
    observation = details.get("observation")
    if not isinstance(observation, dict) or not observation:
        observation = {
            "schema": "tool_observation_v1",
            "intent": str(last_tool_response.get("tool_name") or "").strip(),
            "tool_surface": "runtime_tool",
            "ok": bool(last_tool_response.get("ok")),
            "status": str(last_tool_response.get("status") or "").strip(),
            "response_preview": str(last_tool_response.get("response_text") or "").strip()[:320],
        }
    executed_steps = [
        {
            "tool_name": str(step.get("tool_name") or "").strip(),
            "status": str(step.get("status") or "").strip(),
            "summary": str(step.get("summary") or "").strip(),
        }
        for step in list(state.get("executed_steps") or [])[-4:]
        if isinstance(step, dict)
    ]
    payload = dict(observation)
    if executed_steps:
        payload["recent_steps"] = executed_steps
    payload["checkpoint_status"] = str(row["status"] or "").strip()
    payload["checkpoint_updated_at"] = str(row["updated_at"] or "").strip()
    content = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    tool_name = str(payload.get("intent") or last_tool_response.get("tool_name") or "tool").strip() or "tool"
    return [
        ContextItem(
            item_id=f"runtime-tool-observation-{row['checkpoint_id']}",
            layer="relevant",
            source_type="tool_observation",
            title=f"Recent tool observation {tool_name}",
            content=content[:1800],
            priority=0.92,
            confidence=0.79,
            include_reason="recent_runtime_tool_observation",
            metadata={
                "checkpoint_id": str(row["checkpoint_id"] or "").strip(),
                "updated_at": str(row["updated_at"] or "").strip(),
                "context_format": "structured_observation",
                "observation": payload,
            },
        )
    ]


def _persistent_memory_items(query_text: str, topic_hints: list[str]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for entry in search_relevant_memory(query_text, topic_hints=topic_hints, limit=4):
        category = str(entry.get("category") or "fact")
        items.append(
            ContextItem(
                item_id=f"runtime-memory-{category}-{abs(hash(str(entry.get('text') or '')))}",
                layer="relevant",
                source_type="runtime_memory",
                title=f"Persistent memory {category}",
                content=str(entry.get("text") or "")[:320],
                priority=float(entry.get("score") or 0.0),
                confidence=float(entry.get("confidence") or 0.0),
                include_reason="relevant_persistent_memory",
                metadata={
                    "category": category,
                    "created_at": entry.get("created_at"),
                    "session_id": entry.get("session_id"),
                },
            )
        )
    return items


def _user_heuristic_items(query_text: str, topic_hints: list[str]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for entry in search_user_heuristics(query_text, topic_hints=topic_hints, limit=4):
        category = str(entry.get("category") or "heuristic")
        signal = str(entry.get("signal") or category)
        items.append(
            ContextItem(
                item_id=f"user-heuristic-{category}-{signal}",
                layer="relevant",
                source_type="user_heuristic",
                title=f"User heuristic {category}",
                content=str(entry.get("text") or "")[:320],
                priority=float(entry.get("score") or 0.0),
                confidence=float(entry.get("confidence") or 0.0),
                include_reason="inferred_user_heuristic",
                metadata={
                    "category": category,
                    "signal": signal,
                    "mentions": int(entry.get("mentions") or 0),
                    "updated_at": entry.get("updated_at"),
                },
            )
        )
    return items


def _session_summary_items(query_text: str, topic_hints: list[str], *, session_id: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for entry in search_session_summaries(
        query_text,
        topic_hints=topic_hints,
        limit=2,
        exclude_session_id=session_id,
    ):
        items.append(
            ContextItem(
                item_id=f"session-summary-{abs(hash(str(entry.get('session_id') or '')))}",
                layer="relevant",
                source_type="session_summary",
                title="Prior session continuity",
                content=str(entry.get("summary") or "")[:360],
                priority=float(entry.get("score") or 0.0),
                confidence=0.68,
                include_reason="relevant_session_summary",
                metadata={
                    "created_at": entry.get("created_at"),
                    "session_id": entry.get("session_id"),
                    "turn_count": entry.get("turn_count"),
                },
            )
        )
    return items


def _shared_swarm_context_items(query_text: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for idx, entry in enumerate(search_recent_contexts(query_text, limit=2), start=1):
        content = (
            f"Prompt: {entry.get('prompt_preview') or 'n/a'} "
            f"Result: {entry.get('result_preview') or 'n/a'}"
        ).strip()
        items.append(
            ContextItem(
                item_id=f"swarm-context-{idx}-{abs(hash(content))}",
                layer="relevant",
                source_type="swarm_context",
                title="Shared swarm memory",
                content=content[:380],
                priority=float(entry.get("score") or 0.0),
                confidence=0.62,
                include_reason="shared_swarm_context",
                metadata={
                    "created_at": entry.get("timestamp"),
                    "parent_peer_id": entry.get("parent_peer_id"),
                    "learning_value": entry.get("learning_value"),
                },
            )
        )
    return items


def _final_response_items(query_text: str) -> list[ContextItem]:
    tokens = {token for token in "".join(ch if ch.isalnum() else " " for ch in query_text.lower()).split() if len(token) >= 3}
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT parent_task_id, rendered_persona_text, status_marker, confidence_score, created_at
            FROM finalized_responses
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
    finally:
        conn.close()

    items: list[ContextItem] = []
    for row in rows:
        rendered = str(row["rendered_persona_text"] or "")
        if tokens:
            overlap = len(tokens & {token for token in "".join(ch if ch.isalnum() else " " for ch in rendered.lower()).split() if len(token) >= 3})
            if overlap == 0:
                continue
        items.append(
            ContextItem(
                item_id=f"final-response-{row['parent_task_id']}",
                layer="relevant",
                source_type="final_response",
                title=f"Final response {row['status_marker'] or 'unknown'}",
                content=rendered[:320],
                confidence=float(row["confidence_score"] or 0.0),
                include_reason="prior_final_response",
                metadata={"created_at": row["created_at"], "task_id": row["parent_task_id"]},
            )
        )
    return items


def _shorthand_items(session_id: str, query_text: str) -> list[ContextItem]:
    lexicon = session_lexicon(session_id)
    lower = query_text.lower()
    selected = [(term, canonical) for term, canonical in lexicon.items() if term in lower or canonical in lower][:4]
    if not selected:
        return []
    return [
        ContextItem(
            item_id=f"shorthand-{term}",
            layer="relevant",
            source_type="shorthand",
            title="User shorthand mapping",
            content=f"{term} maps to {canonical}.",
            confidence=0.7,
            include_reason="user_shorthand_memory",
        )
        for term, canonical in selected
    ]


def _payment_items(query_text: str) -> list[ContextItem]:
    if not any(token in query_text.lower() for token in ("credit", "payment", "receipt", "dna", "settlement")):
        return []
    items: list[ContextItem] = []
    for marker in list_payment_status(limit=5):
        items.append(
            ContextItem(
                item_id=f"payment-{marker['task_or_transfer_id']}",
                layer="relevant",
                source_type="payment_status",
                title=f"Payment marker {marker['status']}",
                content=(
                    f"Payer {marker['payer_peer_id'][:12]}..., payee {marker['payee_peer_id'][:12]}..., "
                    f"status {marker['status']}."
                ),
                confidence=0.45,
                include_reason="payment_metadata",
                metadata={"updated_at": marker.get("updated_at"), "receipt_reference": marker.get("receipt_reference")},
            )
        )
    return items


def _swarm_metadata_items(
    task: Any,
    classification: dict[str, Any],
    *,
    allow_swarm_metadata: bool,
    allow_swarm_fetch: bool,
) -> tuple[list[ContextItem], list[dict[str, Any]], bool]:
    if not allow_swarm_metadata:
        return [], [], False
    result = consult_relevant_swarm_metadata(
        classification.get("task_class", "unknown"),
        getattr(task, "task_summary", ""),
        limit=4,
        allow_fetch=allow_swarm_fetch,
    )
    items: list[ContextItem] = []
    for entry in result["items"]:
        tags = [str(tag) for tag in list(entry.get("topic_tags") or [])[:4] if str(tag).strip()]
        problem_class = str(entry.get("problem_class") or "unknown").strip() or "unknown"
        utility = float(entry.get("utility_score") or 0.0)
        trust = float(entry.get("trust_weight") or 0.0)
        quality = float(entry.get("quality_score") or 0.0)
        status_note = (
            "Live swarm fetch requested from the holder."
            if bool(entry.get("fetched"))
            else "Metadata-only remote context."
        )
        items.append(
            ContextItem(
                item_id=f"swarm-{entry['shard_id']}-{entry['holder_peer_id']}",
                layer="relevant",
                source_type="swarm_remote_context" if bool(entry.get("fetched")) else "swarm_metadata",
                title=f"Swarm shard {problem_class}",
                content=(
                    f"{status_note} Holder {entry['holder_peer_id'][:12]}..., region {entry.get('home_region', 'global')}, "
                    f"problem class {problem_class}, tags {', '.join(tags) or 'n/a'}, utility {utility:.2f}, "
                    f"quality {quality:.2f}, trust {trust:.2f}, fetch method {dict(entry.get('fetch_route') or {}).get('method', 'unknown')}."
                ),
                priority=float(entry.get("relevance_score") or 0.0),
                confidence=min(0.8, float(entry.get("trust_weight") or 0.0) + 0.15),
                include_reason="swarm_fetch_requested" if bool(entry.get("fetched")) else "swarm_metadata_only",
                metadata={
                    "freshness_ts": entry.get("freshness_ts"),
                    "home_region": entry.get("home_region"),
                    "shard_id": entry["shard_id"],
                    "problem_class": problem_class,
                    "utility_score": utility,
                    "quality_score": quality,
                    "fetched": bool(entry.get("fetched")),
                },
                provenance={"fetch_route": entry.get("fetch_route")},
            )
        )
    return items, result["items"], bool(result.get("consulted"))


def _cold_context_items(query_text: str, session_id: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    older_turns = recent_dialogue_turns(session_id, limit=10)[4:]
    for turn in older_turns[:2]:
        items.append(
            ContextItem(
                item_id=f"cold-dialogue-{turn['turn_id']}",
                layer="cold",
                source_type="cold_archive",
                title="Older dialogue archive",
                content=str(turn.get("reconstructed_input") or turn.get("normalized_input") or "")[:260],
                confidence=0.4,
                include_reason="older_dialogue_archive",
                metadata={"created_at": turn.get("created_at")},
            )
        )
    for archive in lookup_cold_archive_candidates(query_text, limit=2):
        items.append(
            ContextItem(
                item_id=f"cold-archive-{archive['archive_id']}",
                layer="cold",
                source_type="cold_archive",
                title=f"Archive candidate {archive['storage_backend']}",
                content=str(archive.get("preview") or "")[:280],
                confidence=float(archive.get("confidence_score") or 0.0),
                include_reason="archive_lookup",
                metadata={
                    "created_at": archive.get("created_at"),
                    "storage_backend": archive.get("storage_backend"),
                    "archive_id": archive.get("archive_id"),
                },
            )
        )
    return items


def _report_exclusions(report: PromptAssemblyReport, layer: BudgetedLayer) -> None:
    for item, reason in layer.excluded:
        if reason == "trimmed_to_fit":
            report.trimming_decisions.append(f"{item.title} trimmed to fit {item.layer} budget.")
            report.items_excluded.append(item.to_record(included=False, reason=reason))
        else:
            report.items_excluded.append(item.to_record(included=False, reason=reason))


def _source_allows_private_context(source_context: dict[str, Any] | None) -> bool:
    context = dict(source_context or {})
    platform = str(context.get("platform") or "").strip().lower()
    surface = str(context.get("surface") or "").strip().lower()
    group_like = (
        platform in {"discord", "telegram", "slack", "whatsapp", "group"}
        or surface in {"discord", "telegram", "slack", "whatsapp", "group"}
        or bool(context.get("is_group"))
        or bool(context.get("group_id"))
        or bool(context.get("channel_is_group"))
    )
    if group_like:
        return False
    explicit = context.get("private_context_enabled")
    if isinstance(explicit, bool):
        return explicit
    return platform in {"", "api", "cli", "openclaw", "web_companion", "local"} or surface in {
        "",
        "api",
        "cli",
        "openclaw",
        "web_companion",
    }


class TieredContextLoader:
    def load(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        persona: Any,
        session_id: str,
        total_context_budget: int | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> TieredContextResult:
        strategy = context_strategy(
            classification.get("task_class", "unknown"),
            context=getattr(interpretation, "as_context", lambda: {})(),
            user_input=getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", ""),
        )
        budget = normalize_budget(
            ContextBudget(
                total_tokens=int(total_context_budget or strategy["total_context_budget"]),
                bootstrap_tokens=int(strategy["bootstrap_budget"]),
                relevant_tokens=int(strategy["relevant_budget"]),
                cold_tokens=int(strategy["cold_budget"]),
                max_bootstrap_items=int(strategy["max_bootstrap_items"]),
                max_relevant_items=int(strategy["max_relevant_items"]),
                max_cold_items=int(strategy["max_cold_items"]),
            )
        )

        include_private_context = _source_allows_private_context(source_context)
        bootstrap_candidates = build_bootstrap_context(
            persona=persona,
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=session_id,
            include_private_context=include_private_context,
        )
        bootstrap_layer = budget_layer(
            bootstrap_candidates,
            token_budget=budget.bootstrap_tokens,
            max_items=budget.max_bootstrap_items,
        )

        local_items, local_candidates = _local_candidate_items(task, classification)
        query_text = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
        topic_hints = list(getattr(interpretation, "topic_hints", []) or [])
        relevant_candidates = list(local_items) if include_private_context else []
        if include_private_context:
            relevant_candidates.extend(_runtime_tool_observation_items(session_id))
            relevant_candidates.extend(_dialogue_items(session_id))
            relevant_candidates.extend(_user_heuristic_items(query_text, topic_hints))
            relevant_candidates.extend(_persistent_memory_items(query_text, topic_hints))
            relevant_candidates.extend(_session_summary_items(query_text, topic_hints, session_id=session_id))
            relevant_candidates.extend(_shared_swarm_context_items(query_text))
            relevant_candidates.extend(_final_response_items(getattr(task, "task_summary", "")))
            relevant_candidates.extend(
                _shorthand_items(
                    session_id,
                    getattr(interpretation, "reconstructed_text", "") or getattr(interpretation, "normalized_text", ""),
                )
            )
            relevant_candidates.extend(_payment_items(getattr(task, "task_summary", "")))
        swarm_items, swarm_metadata, swarm_consulted = _swarm_metadata_items(
            task,
            classification,
            allow_swarm_metadata=bool(strategy.get("allow_swarm_metadata", False)),
            allow_swarm_fetch=bool(strategy.get("allow_swarm_fetch", False)),
        )
        relevant_candidates.extend(swarm_items)
        ranked_relevant = rank_context_items(
            relevant_candidates,
            query_text=getattr(task, "task_summary", ""),
            topic_hints=topic_hints,
            task_class=str(classification.get("task_class", "unknown")),
        )
        confidence_label, confidence_score = retrieval_confidence(ranked_relevant)
        relevant_layer = budget_layer(
            ranked_relevant,
            token_budget=budget.relevant_tokens,
            max_items=budget.max_relevant_items,
        )

        cold_decision = evaluate_cold_context_gate(
            user_text=getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", ""),
            task_class=str(classification.get("task_class", "unknown")),
            relevant_confidence_score=confidence_score,
            strategy=strategy,
        )
        cold_candidates = _cold_context_items(getattr(task, "task_summary", ""), session_id) if cold_decision.allow else []
        ranked_cold = rank_context_items(
            cold_candidates,
            query_text=getattr(task, "task_summary", ""),
            topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
            task_class=str(classification.get("task_class", "unknown")),
        )
        cold_layer = budget_layer(
            ranked_cold,
            token_budget=budget.cold_tokens if cold_decision.allow else 0,
            max_items=budget.max_cold_items,
        )

        report = PromptAssemblyReport(
            task_id=str(getattr(task, "task_id", "")),
            trace_id=str(getattr(task, "task_id", "")),
            total_context_budget=budget.total_tokens,
            bootstrap_budget=budget.bootstrap_tokens,
            relevant_budget=budget.relevant_tokens,
            cold_budget=budget.cold_tokens if cold_decision.allow else 0,
            bootstrap_tokens_used=bootstrap_layer.used_tokens,
            relevant_tokens_used=relevant_layer.used_tokens,
            cold_tokens_used=cold_layer.used_tokens,
            bootstrap_chars_used=bootstrap_layer.used_chars,
            relevant_chars_used=relevant_layer.used_chars,
            cold_chars_used=cold_layer.used_chars,
            swarm_metadata_consulted=swarm_consulted,
            cold_archive_opened=bool(cold_decision.allow and cold_layer.included),
            retrieval_confidence=confidence_label,
        )
        for item in bootstrap_layer.included + relevant_layer.included + cold_layer.included:
            report.items_included.append(item.to_record(included=True, reason=item.include_reason or "included"))
        _report_exclusions(report, bootstrap_layer)
        _report_exclusions(report, relevant_layer)
        _report_exclusions(report, cold_layer)
        report.stayed_under_budget = report.total_tokens_used() <= budget.total_tokens

        record_context_access(report.to_dict())

        return TieredContextResult(
            bootstrap_items=bootstrap_layer.included,
            relevant_items=relevant_layer.included,
            cold_items=cold_layer.included,
            local_candidates=local_candidates,
            swarm_metadata=swarm_metadata,
            report=report,
            retrieval_confidence_score=confidence_score,
            cold_decision=cold_decision,
        )
