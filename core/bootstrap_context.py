from __future__ import annotations

from typing import Any

from core import policy_engine
from core.context_retrieval import inject_retrieved as _inject_retrieved
from core.conversation_summarizer import KEEP_RECENT, SUMMARY_THRESHOLD, compress_if_needed
from core.persistent_memory import describe_session_memory_policy, load_memory_excerpt
from core.prompt_assembly_report import ContextItem
from core.runtime_paths import project_path
from core.user_preferences import load_preferences
from storage.dialogue_memory import get_dialogue_session, recent_dialogue_turns, session_lexicon

_WEB0_NULL_FACTS = (
    "Web0/.null project facts: .null is not an ICANN DNS TLD or normal registrar purchase. "
    ".null is a wallet-owned Solana null_registrar v2 name "
    "(NXgQhepFpDCu935H1D4g34g59ZYbo1jR4tBCZWhV8Np; live/pilot=free in project docs). "
    "Resolve locally with `nulla resolve <name>.null`. Buy/register uses Web0/null-sdk or local tooling "
    "with wallet confirmation; dial/pay need opt-in and spend approval."
)

_WEB0_NULL_MARKERS = (
    ".null",
    "dot null",
    "web0",
    "null registrar",
    "null_registrar",
    "nulldomain",
    "nullpay",
    "nullpass",
    "null://",
    "x402 endpoint",
    "x402 payment",
)


def _compact_join(items: list[str], *, limit: int) -> str:
    picked = [item.strip() for item in items if item and item.strip()][:limit]
    return ", ".join(picked)


def _continuity_lines(session_state: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    current_user_goal = str(session_state.get("current_user_goal") or "").strip()
    assistant_commitments = [str(item).strip() for item in list(session_state.get("assistant_commitments") or []) if str(item or "").strip()]
    unresolved_followups = [str(item).strip() for item in list(session_state.get("unresolved_followups") or []) if str(item or "").strip()]
    user_stance = str(session_state.get("user_stance") or "").strip()
    emotional_tone = str(session_state.get("emotional_tone") or "").strip()

    if current_user_goal:
        lines.append(f"Current user goal: {current_user_goal}.")
    if session_state.get("last_intent_mode"):
        lines.append(f"User intent mode: {session_state.get('last_intent_mode')}.")
    if user_stance or emotional_tone:
        lines.append(
            "Continuity tone: "
            f"stance={user_stance or 'none'}, emotion={emotional_tone or 'none'}."
        )
    if assistant_commitments:
        lines.append(f"Assistant commitments: {_compact_join(assistant_commitments, limit=3)}.")
    if unresolved_followups:
        lines.append(f"Unresolved followups: {_compact_join(unresolved_followups, limit=3)}.")
    return lines


def _conversation_preference_text() -> str:
    try:
        prefs = load_preferences()
    except Exception:
        return ""
    fragments = [
        f"humor={prefs.humor_percent}/100",
        f"boundaries={prefs.boundaries_mode}",
        f"profanity={prefs.profanity_level}/100",
    ]
    if getattr(prefs, "character_mode", ""):
        fragments.append(f"character_mode={prefs.character_mode}")
    if getattr(prefs, "style_notes", ""):
        fragments.append(f"style_notes={prefs.style_notes}")
    return "; ".join(fragment for fragment in fragments if str(fragment or "").strip())


def _execution_preference_text() -> str:
    try:
        prefs = load_preferences()
    except Exception:
        return ""
    fragments = [
        f"autonomy={prefs.autonomy_mode}",
        f"show_workflow={'on' if prefs.show_workflow else 'off'}",
        f"hive_followups={'on' if prefs.hive_followups else 'off'}",
        f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}",
        f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}",
        f"social_commons={'on' if prefs.social_commons else 'off'}",
    ]
    return "; ".join(fragment for fragment in fragments if str(fragment or "").strip())


def _web0_null_signal_text(
    *,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
) -> str:
    values: list[str] = [
        getattr(task, "task_summary", ""),
        getattr(interpretation, "raw_text", ""),
        getattr(interpretation, "normalized_text", ""),
        getattr(interpretation, "reconstructed_text", ""),
        str(classification.get("task_class") or ""),
    ]
    values.extend(str(item) for item in list(classification.get("topic_hints") or []))
    values.extend(str(item) for item in list(getattr(interpretation, "topic_hints", []) or []))
    values.extend(str(item) for item in list(getattr(interpretation, "reference_targets", []) or []))
    return " ".join(item for item in values if str(item or "").strip()).lower()


def _needs_web0_null_context(
    *,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
) -> bool:
    signal = _web0_null_signal_text(
        task=task,
        classification=classification,
        interpretation=interpretation,
    )
    if any(marker in signal for marker in _WEB0_NULL_MARKERS):
        return True
    return (
        " null " in f" {signal} "
        and any(word in signal for word in ("domain", "address", "name", "register", "buy", "resolve", "auction"))
    )


def _read_markdown_context(*parts: str, max_chars: int = 2200) -> str:
    path = project_path(*parts)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n[...]"
    return text


def _normalized_dialogue_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim_history_window(
    items: list[dict[str, str]],
    *,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    selected_reversed: list[dict[str, str]] = []
    used_chars = 0
    for item in reversed(items):
        content = str(item.get("content") or "")
        if selected_reversed and (len(selected_reversed) >= max_messages or used_chars + len(content) > max_chars):
            break
        selected_reversed.append(item)
        used_chars += len(content)
    return list(reversed(selected_reversed))


def _compress_history(
    items: list[dict[str, str]],
    *,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    """
    Smart history: LLM-compress when long, hard-trim when short.

    For short histories (≤ SUMMARY_THRESHOLD) the existing sliding-window
    behaviour is preserved. For longer ones, compress_if_needed() replaces
    older turns with a structured <context_summary> that preserves exact
    facts verbatim — instead of silently discarding them.
    """
    if len(items) > SUMMARY_THRESHOLD:
        compressed, was_compressed = compress_if_needed(
            items,
            threshold=SUMMARY_THRESHOLD,
            keep_recent=KEEP_RECENT,
        )
        if was_compressed:
            return compressed
    return _trim_history_window(items, max_messages=max_messages, max_chars=max_chars)


def _client_conversation_history(
    source_context: dict[str, Any] | None,
    *,
    current_user_text: str,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    source_context = dict(source_context or {})
    raw_history = list(
        source_context.get("client_conversation_history")
        or source_context.get("conversation_history")
        or []
    )
    normalized: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = _normalized_dialogue_text(item.get("content"))
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    current_user = _normalized_dialogue_text(current_user_text)
    if normalized and normalized[-1]["role"] == "user" and normalized[-1]["content"] == current_user:
        normalized = normalized[:-1]
    return _compress_history(
        normalized,
        max_messages=max_messages,
        max_chars=max_chars,
    )


def canonical_runtime_transcript(
    *,
    session_id: str | None,
    source_context: dict[str, Any] | None,
    current_user_text: str,
    max_messages: int = 10,
    max_chars: int = 5000,
) -> tuple[list[dict[str, str]], str]:
    client_history = _client_conversation_history(
        source_context,
        current_user_text=current_user_text,
        max_messages=max_messages,
        max_chars=max_chars,
    )
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        turns = recent_dialogue_turns(
            normalized_session_id,
            limit=max(12, int(max_messages) * 2),
            speaker_roles=("user", "assistant"),
        )
        transcript: list[dict[str, str]] = []
        for turn in reversed(turns):
            role = str(turn.get("speaker_role") or "").strip().lower()
            content = _normalized_dialogue_text(
                turn.get("reconstructed_input")
                or turn.get("normalized_input")
                or turn.get("raw_input")
            )
            if role not in {"user", "assistant"} or not content:
                continue
            transcript.append({"role": role, "content": content})
        current_user = _normalized_dialogue_text(current_user_text)
        if transcript and transcript[-1]["role"] == "user" and transcript[-1]["content"] == current_user:
            transcript = transcript[:-1]
        transcript = _compress_history(
            transcript,
            max_messages=max_messages,
            max_chars=max_chars,
        )
        if transcript:
            if client_history and len(client_history) > len(transcript):
                transcript = _inject_retrieved(normalized_session_id, current_user_text, client_history)
                return transcript, "client_conversation_history"
            transcript = _inject_retrieved(normalized_session_id, current_user_text, transcript)
            return transcript, "structured_dialogue_memory"
    if client_history:
        client_history = _inject_retrieved(normalized_session_id, current_user_text, client_history)
        return client_history, "client_conversation_history"
    return [], "none"


def build_bootstrap_context(
    *,
    persona: Any,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
    session_id: str,
    max_lexicon_items: int = 4,
    include_private_context: bool = True,
) -> list[ContextItem]:
    session_state = get_dialogue_session(session_id) if include_private_context else {}
    recent_turns = (
        recent_dialogue_turns(session_id, limit=2, speaker_roles=("user", "assistant"))
        if include_private_context
        else []
    )
    lexicon = session_lexicon(session_id) if include_private_context else {}
    quality_flags = list(getattr(interpretation, "quality_flags", []) or [])
    topic_hints = list(getattr(interpretation, "topic_hints", []) or [])
    references = list(getattr(interpretation, "reference_targets", []) or [])
    session_topics = list(session_state.get("topic_hints") or [])
    continuity_lines = _continuity_lines(session_state)

    character_mode = ""
    try:
        if include_private_context:
            _prefs = load_preferences()
            character_mode = str(getattr(_prefs, "character_mode", "") or "").strip()
    except Exception:
        pass
    persona_content = (
        f"Persona: {persona.display_name}. Tone: {persona.tone}. "
        f"Execution style: {persona.execution_style}. Spirit anchor: {persona.spirit_anchor}."
    )
    if character_mode:
        persona_content += (
            f"\n\nACTIVE ROLEPLAY: You are currently roleplaying as '{character_mode}'. "
            f"Stay fully in character at all times. Adopt the speech patterns, mannerisms, "
            f"personality, and vocabulary of '{character_mode}'. Do not break character unless "
            f"the user explicitly asks you to stop the roleplay."
        )
    items: list[ContextItem] = [
        ContextItem(
            item_id="bootstrap-persona",
            layer="bootstrap",
            source_type="persona",
            title="Agent identity",
            content=persona_content,
            priority=1.0,
            confidence=0.95,
            must_keep=True,
            include_reason="stable_identity",
        ),
        ContextItem(
            item_id="bootstrap-session",
            layer="bootstrap",
            source_type="session_state",
            title="Session topic hints",
            content=(
                f"Current topics: {_compact_join(topic_hints or session_topics, limit=4) or 'none'}. "
                f"References: {_compact_join(references or ([str(session_state.get('last_subject') or '').strip()] if session_state.get('last_subject') else []), limit=3) or 'none'}. "
                f"Last subject: {session_state.get('last_subject') or 'none'}."
            ),
            priority=0.95,
            confidence=float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0),
            must_keep=True,
            include_reason="session_grounding",
        ),
        *(
            [
                ContextItem(
                    item_id="bootstrap-continuity",
                    layer="bootstrap",
                    source_type="dialogue_continuity",
                    title="Conversation continuity",
                    content=" ".join(continuity_lines),
                    priority=0.93,
                    confidence=0.84,
                    must_keep=True,
                    include_reason="continuity_state",
                )
            ]
            if continuity_lines
            else []
        ),
        ContextItem(
            item_id="bootstrap-task",
            layer="bootstrap",
            source_type="task_constraints",
            title="Active task constraints",
            content=(
                f"Task class: {classification.get('task_class', 'unknown')}. "
                f"Summary: {getattr(task, 'task_summary', '')}. "
                f"Risk flags: {_compact_join(list(classification.get('risk_flags') or []), limit=4) or 'none'}."
            ),
            priority=0.92,
            confidence=float(classification.get("confidence_hint", 0.0) or 0.0),
            must_keep=True,
            include_reason="task_constraints",
        ),
        ContextItem(
            item_id="bootstrap-safety",
            layer="bootstrap",
            source_type="policy",
            title="Safety mode",
            content=(
                f"Execution default: {policy_engine.get('execution.default_mode', 'advice_only')}. "
                f"Persona core locked: {bool(policy_engine.get('personality.persona_core_locked', True))}. "
                f"Understanding confidence: {float(getattr(interpretation, 'understanding_confidence', 0.0) or 0.0):.2f}."
            ),
            priority=0.88,
            confidence=0.9,
            must_keep=True,
            include_reason="safety_policy",
            metadata={"exclude_from_chat_minimal_system_prompt": True},
        ),
        ContextItem(
            item_id="bootstrap-conversation-safety",
            layer="bootstrap",
            source_type="conversation_policy",
            title="Conversation policy",
            content=(
                "Sensitive conversation is allowed when the user is asking for discussion, analysis, or explanation only. "
                "Do not confuse conversation about intimate, controversial, or offensive topics with permission to take action, reveal private data, or bypass safety gates."
            ),
            priority=0.89,
            confidence=0.92,
            must_keep=True,
            include_reason="conversation_safety_split",
        ),
    ]

    working = getattr(interpretation, "working_interpretation", None)
    if working and getattr(working, "grounding_note", ""):
        items.append(
            ContextItem(
                item_id="bootstrap-short-input-grounding",
                layer="bootstrap",
                source_type="context_understanding",
                title="Short/fragmented input grounding",
                content=getattr(working, "grounding_note", ""),
                priority=0.91,
                confidence=0.9,
                must_keep=True,
                include_reason="anti_hallucination",
            )
        )

    if _needs_web0_null_context(task=task, classification=classification, interpretation=interpretation):
        items.append(
            ContextItem(
                item_id="bootstrap-web0-null",
                layer="bootstrap",
                source_type="project_knowledge",
                title="Web0 .null project facts",
                content=_WEB0_NULL_FACTS,
                priority=0.975,
                confidence=1.0,
                must_keep=True,
                include_reason="web0_null_grounding",
                metadata={"context_domain": "web0_null"},
            )
        )

    # Self-knowledge: load NULLA's self-awareness document
    sk_text = _read_markdown_context("docs", "NULLA_SELF_KNOWLEDGE.md", max_chars=2200)
    if sk_text:
        items.append(
            ContextItem(
                item_id="bootstrap-self-knowledge",
                layer="bootstrap",
                source_type="self_knowledge",
                title="Self-knowledge",
                content=sk_text,
                priority=0.97,
                confidence=1.0,
                must_keep=True,
                include_reason="agent_self_awareness",
            )
        )

    # Operational doctrine: OpenClaw integrations + live internet behavior.
    doctrine_text = _read_markdown_context("docs", "NULLA_OPENCLAW_TOOL_DOCTRINE.md", max_chars=2000)
    if doctrine_text:
        items.append(
            ContextItem(
                item_id="bootstrap-openclaw-doctrine",
                layer="bootstrap",
                source_type="operating_doctrine",
                title="OpenClaw tool doctrine",
                content=doctrine_text,
                priority=0.965,
                confidence=1.0,
                include_reason="tooling_behavior_contract",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            )
        )

    # Owner identity: display name, privacy pact, and owner authority.
    try:
        from core.onboarding import load_identity
        identity = load_identity()
        agent_name = identity.get("agent_name", "")
        privacy_pact = identity.get("privacy_pact", "")
        if agent_name:
            content = (
                f"My current display name is {agent_name}. "
                "The operator can rename me or give me a nickname at any time. "
                "Internal runtime identity and display naming are separate."
            )
            items.append(
                ContextItem(
                    item_id="bootstrap-owner-identity",
                    layer="bootstrap",
                    source_type="owner_identity",
                    title="Owner identity",
                    content=content,
                    priority=0.99,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="owner_identity_contract",
                )
            )
            if privacy_pact:
                items.append(
                    ContextItem(
                        item_id="bootstrap-owner-privacy-pact",
                        layer="bootstrap",
                        source_type="privacy_pact",
                        title="Privacy pact",
                        content=f"Privacy pact: {privacy_pact}",
                        priority=0.98,
                        confidence=1.0,
                        must_keep=True,
                        include_reason="owner_privacy_contract",
                        metadata={"exclude_from_chat_minimal_system_prompt": True},
                    )
                )
    except Exception:
        pass

    try:
        from core.nullabook_identity import get_profile
        from network.signer import get_local_peer_id
        nb_profile = get_profile(get_local_peer_id())
        if nb_profile and nb_profile.status == "active":
            nb_content = (
                f"I have a NullaBook account with handle '{nb_profile.handle}'. "
                f"NullaBook is the public web surface for agent work in the NULLA hive. "
                f"I can post research findings, claim topics, and interact in communities. "
                f"My posts are authenticated with a dedicated posting token (X-NullaBook-Token). "
                f"NullaBook etiquette: evidence-backed posts, no spam, proof-of-useful-work matters. "
                f"Stats: {nb_profile.post_count} posts, {nb_profile.claim_count} claims."
            )
            if nb_profile.bio:
                nb_content += f" Bio: {nb_profile.bio}"
            items.append(
                ContextItem(
                    item_id="bootstrap-nullabook-identity",
                    layer="bootstrap",
                    source_type="nullabook_identity",
                    title="NullaBook profile",
                    content=nb_content,
                    priority=0.90,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="nullabook_social_identity",
                )
            )
    except Exception:
        pass

    if include_private_context:
        # Runtime memory: persists under NULLA_HOME/data/MEMORY.md.
        try:
            memory_excerpt = load_memory_excerpt(max_chars=2000).strip()
            if memory_excerpt:
                items.append(
                    ContextItem(
                        item_id="bootstrap-runtime-memory",
                        layer="bootstrap",
                        source_type="runtime_memory",
                        title="Persistent memory",
                        content=memory_excerpt,
                        priority=0.94,
                        confidence=0.9,
                        include_reason="persistent_runtime_memory",
                    )
                )
        except Exception:
            pass

        try:
            policy_text = describe_session_memory_policy(session_id)
            if policy_text:
                items.append(
                    ContextItem(
                        item_id="bootstrap-session-memory-policy",
                        layer="bootstrap",
                        source_type="session_policy",
                        title="Session memory policy",
                        content=policy_text,
                        priority=0.985,
                        confidence=1.0,
                        must_keep=True,
                        include_reason="memory_sharing_scope",
                        metadata={"exclude_from_chat_minimal_system_prompt": True},
                    )
                )
        except Exception:
            pass

        conversation_pref_text = _conversation_preference_text()
        if conversation_pref_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-conversation-preferences",
                    layer="bootstrap",
                    source_type="user_preferences",
                    title="Conversation preferences",
                    content=conversation_pref_text,
                    priority=0.91,
                    confidence=1.0,
                    include_reason="persistent_user_preferences",
                )
            )

        execution_pref_text = _execution_preference_text()
        if execution_pref_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-execution-preferences",
                    layer="bootstrap",
                    source_type="execution_preferences",
                    title="Execution preferences",
                    content=execution_pref_text,
                    priority=0.9,
                    confidence=1.0,
                    include_reason="execution_policy_preferences",
                    metadata={"exclude_from_chat_minimal_system_prompt": True},
                )
            )

    if quality_flags:
        items.append(
            ContextItem(
                item_id="bootstrap-quality",
                layer="bootstrap",
                source_type="input_quality",
                title="Input quality",
                content=f"Quality flags: {_compact_join(quality_flags, limit=5)}.",
                priority=0.72,
                confidence=0.7,
                include_reason="input_quality",
            )
        )

    if recent_turns:
        recent_summary = " | ".join(
            f"{str(turn.get('speaker_role') or 'user').title()}: {str(turn.get('reconstructed_input') or '')[:80]}"
            for turn in recent_turns[:2]
        )
        items.append(
            ContextItem(
                item_id="bootstrap-dialogue",
                layer="bootstrap",
                source_type="recent_dialogue",
                title="Recent dialogue state",
                content=f"Recent turns: {recent_summary}",
                priority=0.82,
                confidence=0.72,
                include_reason="recent_dialogue",
            )
        )

    if lexicon:
        selected: list[str] = []
        input_text = (
            f"{getattr(interpretation, 'normalized_text', '')} "
            f"{getattr(interpretation, 'reconstructed_text', '')}"
        ).lower()
        for term, canonical in lexicon.items():
            if term in input_text or canonical in input_text or canonical in topic_hints:
                selected.append(f"{term}->{canonical}")
            if len(selected) >= max_lexicon_items:
                break
        if selected:
            items.append(
                ContextItem(
                    item_id="bootstrap-lexicon",
                    layer="bootstrap",
                    source_type="shorthand",
                    title="Active shorthand mappings",
                    content=f"Shorthand: {', '.join(selected[:max_lexicon_items])}.",
                    priority=0.7,
                    confidence=0.75,
                    include_reason="active_shorthand",
                )
            )

    return items
