from __future__ import annotations

import re
from typing import Any

from core import policy_engine
from core.bootstrap_context import canonical_runtime_transcript
from core.internal_message_schema import InternalMessage, InternalModelRequest
from core.local_operator_actions import list_operator_tools
from core.tool_intent_executor import runtime_tool_specs
from core.user_preferences import load_preferences

_STRUCTURED_OUTPUT_MODES = {"json_object", "action_plan", "tool_intent", "summary_block"}
_CHAT_SURFACES = {"channel", "openclaw", "api"}
_PLAIN_TEXT_CHAT_TASK_CLASSES = {
    "chat_conversation",
    "chat_research",
    "research",
    "general_advisory",
    "business_advisory",
    "food_nutrition",
    "relationship_advisory",
    "creative_ideation",
    "debugging",
    "dependency_resolution",
    "config",
    "system_design",
    "file_inspection",
    "shell_guidance",
}
_TOOL_LABELS = {
    "inspect_disk_usage": "disk inspection",
    "cleanup_temp_files": "temp cleanup",
    "inspect_processes": "process inspection",
    "inspect_services": "service inspection",
    "move_path": "file move/archive",
    "schedule_calendar_event": "calendar outbox creation",
    "discord_post": "Discord posting",
    "telegram_send": "Telegram sending",
}
_EXACT_OUTPUT_REQUEST_RE = re.compile(
    r"\b(?:reply|respond|answer|return)\s+with\s+exactly\s*:?\s*(?P<target>.+?)(?:\s+and\s+nothing\s+else)?[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)


def normalize_prompt(
    *,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
    context_result: Any,
    persona: Any,
    output_mode: str,
    task_kind: str,
    trace_id: str,
    surface: str = "cli",
    source_context: dict[str, Any] | None = None,
) -> InternalModelRequest:
    ambiguity = float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0)
    user_text = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
    task_class = str(classification.get("task_class", "unknown"))

    if surface in _CHAT_SURFACES:
        return _build_conversational_request(
            user_text=user_text,
            persona=persona,
            classification=classification,
            context_result=context_result,
            task_kind=task_kind,
            output_mode=output_mode,
            trace_id=trace_id,
            ambiguity=ambiguity,
            source_context=source_context,
        )

    constraints = [
        "You are a replaceable helper or teacher backend for NULLA.",
        "Do not claim canonical truth.",
        "Return only the requested output shape.",
        "Do not invent private history or hidden state.",
    ]
    if output_mode in _STRUCTURED_OUTPUT_MODES:
        constraints.append("Return valid JSON only.")

    system = InternalMessage(
        role="system",
        content=(
            "NULLA remains the system. You are a worker backend. "
            f"Persona tone target: {persona.tone}. "
            f"Task class: {classification.get('task_class', 'unknown')}. "
            f"Output mode: {output_mode}. "
            f"Constraints: {' '.join(constraints)}"
        ),
    )
    user = InternalMessage(
        role="user",
        content=(
            f"Normalized request: {user_text}\n"
            f"Understanding confidence: {ambiguity:.2f}\n"
            f"Topic hints: {', '.join(list(getattr(interpretation, 'topic_hints', []) or [])[:6]) or 'none'}\n"
            f"Risk flags: {', '.join(list(classification.get('risk_flags') or [])[:6]) or 'none'}"
        ),
    )
    context = InternalMessage(
        role="context",
        content=context_result.assembled_context() or "No additional context beyond bootstrap.",
        metadata={"retrieval_confidence": context_result.report.retrieval_confidence},
    )
    generation_profile = _generation_profile(
        surface=surface,
        task_kind=task_kind,
        output_mode=output_mode,
        task_class=task_class,
        user_text=user_text,
        context_attached=bool((context_result.assembled_context() or "").strip()),
    )
    return InternalModelRequest(
        task_kind=task_kind,
        task_class=task_class,
        output_mode=output_mode,
        messages=[system, user, context],
        trace_id=trace_id,
        max_output_tokens=int(generation_profile["max_output_tokens"]),
        temperature=float(generation_profile["temperature"]),
        ambiguity_confidence=ambiguity,
        constraints=constraints,
        context_summary=context_result.report.to_dict(),
        metadata={
            "persona_id": getattr(persona, "persona_id", "default"),
            "task_id": getattr(task, "task_id", ""),
            "generation_profile": generation_profile,
            "chat_truth_prompt": {
                "surface": surface,
                "task_kind": task_kind,
                "output_mode": output_mode,
                "structured_output": output_mode in _STRUCTURED_OUTPUT_MODES,
                "context_attached": bool((context_result.assembled_context() or "").strip()),
                "generation_profile_id": str(generation_profile["profile_id"]),
                "requested_temperature": float(generation_profile["temperature"]),
                "requested_top_p": generation_profile.get("top_p"),
                "requested_max_output_tokens": int(generation_profile["max_output_tokens"]),
                "adaptive_length": bool(generation_profile["adaptive_length"]),
            },
        },
        attachments=list(context_result.report.to_dict().get("external_evidence_attachments") or []),
    )


def _build_conversational_request(
    *,
    user_text: str,
    persona: Any,
    classification: dict[str, Any],
    context_result: Any,
    task_kind: str,
    output_mode: str,
    trace_id: str,
    ambiguity: float,
    source_context: dict[str, Any] | None = None,
) -> InternalModelRequest:
    """Build a natural conversational prompt for chat surfaces."""
    persona_name = getattr(persona, "display_name", "NULLA")
    persona_tone = getattr(persona, "tone", "calm")
    exact_output_target = _extract_exact_output_target(user_text)
    prompt_profile = _chat_system_prompt_profile(
        output_mode=output_mode,
        task_kind=task_kind,
        exact_output_target=exact_output_target,
    )

    tone_guide = {
        "calm": "You are warm, clear, and thoughtful.",
        "direct": "You are concise and to the point.",
        "teacher": "You explain things step by step, like a patient teacher.",
        "savage": "You are blunt and no-nonsense, but still helpful.",
    }.get(persona_tone, "You are helpful and conversational.")

    context_message = None
    if exact_output_target is None:
        context_message = _conversational_context_message(
            context_result,
            prompt_profile=prompt_profile,
        )
    source_context = dict(source_context or {})
    source_platform = str(source_context.get("platform", "") or "").strip().lower()
    source_surface = str(source_context.get("surface", "") or "").strip().lower()
    runtime_session_id = str(
        source_context.get("runtime_session_id")
        or source_context.get("session_id")
        or ""
    ).strip()
    has_openclaw_tools = source_platform in {"openclaw", "web_companion", "telegram", "discord"} or source_surface in {"channel", "openclaw", "api"}
    format_guidance = _chat_output_guidance(output_mode)
    conversation_safety_guidance = _conversational_safety_guidance()
    tooling_guidance = ""
    tool_catalog_guidance = ""

    if prompt_profile == "chat_exact":
        system_content = (
            f"You are {persona_name}. "
            "Return only the exact requested text. "
            "Do not add quotes, labels, markdown, explanation, or extra punctuation unless it is part of the requested text."
        )
    elif prompt_profile == "chat_minimal":
        system_content = (
            f"You are {persona_name}. "
            f"{tone_guide} "
            f"{conversation_safety_guidance} "
            "Be truthful about uncertainty, freshness, and capabilities. "
            "Use relevant context when it helps, but do not mention internal systems, confidence scores, or planning steps. "
            "Keep responses concise but complete. "
            f"{format_guidance}"
        )
    else:
        tooling_guidance = _tooling_guidance(has_openclaw_tools=has_openclaw_tools)
        tool_catalog_guidance = _tool_intent_catalog_text() if output_mode == "tool_intent" else ""
        system_content = (
            f"You are {persona_name}, a knowledgeable AI assistant. "
            f"{tone_guide} "
            f"{conversation_safety_guidance} "
            "You can help with coding, debugging, system design, research, "
            "and general conversation. "
            "Keep responses concise but complete. "
            "If you have relevant context from memory, use it to give better answers. "
            f"{tooling_guidance} "
            f"{format_guidance} "
            f"{tool_catalog_guidance} "
            "Do not mention internal systems, confidence scores, or planning steps."
        )

    system = InternalMessage(role="system", content=system_content)
    history_messages, transcript_source = _history_messages_for_chat(
        source_context,
        runtime_session_id=runtime_session_id,
        current_user_text=user_text,
        prompt_profile=prompt_profile,
    )
    user = InternalMessage(role="user", content=user_text)
    generation_profile = _generation_profile(
        surface=source_surface or surface_from_platform(source_platform),
        task_kind=task_kind,
        output_mode=output_mode,
        task_class=str(classification.get("task_class", "unknown")),
        user_text=user_text,
        history_messages=len(history_messages),
        context_attached=context_message is not None,
    )
    memory_prompt = _memory_prompt_metadata(
        source_context=source_context,
        source_platform=source_platform,
        source_surface=source_surface,
        prompt_profile=prompt_profile,
        output_mode=output_mode,
        runtime_session_id=runtime_session_id,
    )
    messages = [system, *history_messages]
    if context_message is not None:
        messages.append(context_message)
    messages.append(user)

    return InternalModelRequest(
        task_kind=task_kind,
        task_class=str(classification.get("task_class", "unknown")),
        output_mode=output_mode,
        messages=messages,
        trace_id=trace_id,
        max_output_tokens=int(generation_profile["max_output_tokens"]),
        temperature=float(generation_profile["temperature"]),
        ambiguity_confidence=ambiguity,
        constraints=[],
        context_summary=context_result.report.to_dict(),
        metadata={
            "persona_id": getattr(persona, "persona_id", "default"),
            "generation_profile": generation_profile,
            "memory_prompt": memory_prompt,
            "system_prompt_profile": prompt_profile,
            "chat_truth_prompt": {
                "surface": source_surface or "cli",
                "task_kind": task_kind,
                "output_mode": output_mode,
                "structured_output": output_mode in _STRUCTURED_OUTPUT_MODES,
                "history_messages": len(history_messages),
                "transcript_source": transcript_source,
                "context_attached": context_message is not None,
                "context_delivery": "context_message" if context_message is not None else "none",
                "system_prompt_profile": prompt_profile,
                "speech_safety_mode": "conversation_freer",
                "tooling_guidance_enabled": bool(tooling_guidance.strip()),
                "execution_safety_guidance_enabled": bool(tooling_guidance.strip()),
                "runtime_session_id_present": bool(runtime_session_id),
                "memory_prompt_enabled": bool(memory_prompt.get("enabled")),
                "client_history_message_count": len(
                    list(
                        source_context.get("client_conversation_history")
                        or source_context.get("conversation_history")
                        or []
                    )
                ),
                "generation_profile_id": str(generation_profile["profile_id"]),
                "requested_temperature": float(generation_profile["temperature"]),
                "requested_top_p": generation_profile.get("top_p"),
                "requested_max_output_tokens": int(generation_profile["max_output_tokens"]),
                "adaptive_length": bool(generation_profile["adaptive_length"]),
            },
        },
        attachments=list(context_result.report.to_dict().get("external_evidence_attachments") or []),
    )


def _history_messages_for_chat(
    source_context: dict[str, Any] | None,
    *,
    runtime_session_id: str,
    current_user_text: str,
    prompt_profile: str,
    max_messages: int = 10,
    max_chars: int = 5000,
) -> tuple[list[InternalMessage], str]:
    if prompt_profile == "chat_minimal":
        transcript, transcript_source = canonical_runtime_transcript(
            session_id=runtime_session_id or None,
            source_context=source_context,
            current_user_text=current_user_text,
            max_messages=max_messages,
            max_chars=max_chars,
        )
        if transcript:
            return (
                [InternalMessage(role=item["role"], content=item["content"]) for item in transcript],
                transcript_source,
            )
        return [], transcript_source

    history_messages = _history_messages_from_source_context(
        source_context,
        current_user_text=current_user_text,
        max_messages=max_messages,
        max_chars=max_chars,
    )
    return (
        history_messages,
        "client_conversation_history" if history_messages else "none",
    )


def _history_messages_from_source_context(
    source_context: dict[str, Any] | None,
    *,
    current_user_text: str,
    max_messages: int = 10,
    max_chars: int = 5000,
) -> list[InternalMessage]:
    source_context = dict(source_context or {})
    raw_history = list(source_context.get("conversation_history") or [])
    normalized: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    if normalized and normalized[-1]["role"] == "user" and normalized[-1]["content"] == " ".join(current_user_text.split()).strip():
        normalized = normalized[:-1]
    selected_reversed: list[dict[str, str]] = []
    used_chars = 0
    for item in reversed(normalized):
        content = str(item.get("content") or "")
        if selected_reversed and (len(selected_reversed) >= max_messages or used_chars + len(content) > max_chars):
            break
        selected_reversed.append(item)
        used_chars += len(content)
    selected = list(reversed(selected_reversed))
    return [InternalMessage(role=item["role"], content=item["content"]) for item in selected]


def _max_output_tokens(output_mode: str) -> int:
    return {
        "plain_text": 240,
        "summary_block": 220,
        "json_object": 220,
        "action_plan": 320,
        "tool_intent": 700,
    }.get(output_mode, 240)


def _temperature_for_mode(output_mode: str) -> float:
    if output_mode in _STRUCTURED_OUTPUT_MODES:
        return 0.1
    return 0.2


def _generation_profile(
    *,
    surface: str,
    task_kind: str,
    output_mode: str,
    task_class: str,
    user_text: str,
    history_messages: int = 0,
    context_attached: bool = False,
) -> dict[str, Any]:
    normalized_surface = str(surface or "cli").strip().lower()
    normalized_task_kind = str(task_kind or "").strip().lower()
    normalized_output_mode = str(output_mode or "plain_text").strip().lower()
    normalized_task_class = str(task_class or "unknown").strip().lower()
    exact_output_target = _extract_exact_output_target(user_text)

    if normalized_output_mode == "tool_intent":
        return {
            "profile_id": "tool_extraction_low_temp",
            "profile_family": "structured_low_temp",
            "temperature": 0.05,
            "top_p": 0.15,
            "max_output_tokens": _max_output_tokens("tool_intent"),
            "adaptive_length": False,
            "stop_sequences": [],
        }
    if normalized_output_mode == "action_plan":
        return {
            "profile_id": "planner_structured_low_temp",
            "profile_family": "structured_low_temp",
            "temperature": 0.08,
            "top_p": 0.2,
            "max_output_tokens": _max_output_tokens("action_plan"),
            "adaptive_length": False,
            "stop_sequences": [],
        }
    if normalized_output_mode in {"summary_block", "json_object"}:
        return {
            "profile_id": "structured_response_low_temp",
            "profile_family": "structured_low_temp",
            "temperature": 0.1,
            "top_p": 0.25,
            "max_output_tokens": _max_output_tokens(normalized_output_mode),
            "adaptive_length": False,
            "stop_sequences": [],
        }

    if normalized_output_mode == "plain_text" and normalized_surface in _CHAT_SURFACES:
        if exact_output_target is not None:
            return {
                "profile_id": "chat_exact_plain_text",
                "profile_family": "chat_exact_plain_text",
                "temperature": 0.05,
                "top_p": 0.15,
                "max_output_tokens": _exact_output_max_tokens(exact_output_target),
                "adaptive_length": False,
                "stop_sequences": ["\n"],
            }
        if normalized_task_class in _PLAIN_TEXT_CHAT_TASK_CLASSES or normalized_task_kind in {"conversation", "normalization_assist"}:
            return {
                "profile_id": "chat_plain_text",
                "profile_family": "chat_plain_text",
                "temperature": 0.72,
                "top_p": 0.92,
                "max_output_tokens": _adaptive_chat_max_output_tokens(
                    user_text,
                    history_messages=history_messages,
                    context_attached=context_attached,
                    research=False,
                ),
                "adaptive_length": True,
                "stop_sequences": [],
            }

    return {
        "profile_id": "default_plain_text",
        "profile_family": "default_plain_text",
        "temperature": _temperature_for_mode(normalized_output_mode),
        "top_p": 0.85 if normalized_output_mode == "plain_text" else 0.25,
        "max_output_tokens": _max_output_tokens(normalized_output_mode),
        "adaptive_length": False,
        "stop_sequences": [],
    }


def _adaptive_chat_max_output_tokens(
    user_text: str,
    *,
    history_messages: int = 0,
    context_attached: bool = False,
    research: bool = False,
) -> int:
    word_count = len(str(user_text or "").split())
    base = 320 if research else 240
    growth = min(260 if research else 180, word_count * (5 if research else 4))
    history_bonus = min(80, max(0, int(history_messages)) * 12)
    context_bonus = 40 if context_attached else 0
    floor = 320 if research else 220
    ceiling = 760 if research else 520
    requested = base + growth + history_bonus + context_bonus
    return max(floor, min(ceiling, requested))


def _exact_output_max_tokens(target: str) -> int:
    clean = " ".join(str(target or "").split()).strip()
    if not clean:
        return 24
    word_count = max(1, len(clean.split()))
    char_count = len(clean)
    requested = max(16, min(64, word_count * 4 + max(4, char_count // 12)))
    return requested


def surface_from_platform(source_platform: str) -> str:
    if source_platform in _CHAT_SURFACES:
        return source_platform
    return "cli"


def _assembled_context_for_prompt_profile(context_result: Any, *, prompt_profile: str) -> str:
    assembled = getattr(context_result, "assembled_context", None)
    if assembled is None:
        return ""
    try:
        return str(assembled(prompt_profile=prompt_profile) or "")
    except TypeError:
        return str(assembled() or "")


def _conversational_context_message(
    context_result: Any,
    *,
    prompt_profile: str,
) -> InternalMessage | None:
    assembled_context = _assembled_context_for_prompt_profile(
        context_result,
        prompt_profile=prompt_profile,
    )
    if not assembled_context or assembled_context == "No additional context beyond bootstrap.":
        return None
    return InternalMessage(
        role="context",
        content=f"Relevant context and evidence:\n{assembled_context[:2000]}",
        metadata={
            "prompt_profile": prompt_profile,
            "retrieval_confidence": getattr(getattr(context_result, "report", None), "retrieval_confidence", None),
        },
    )


def _chat_system_prompt_profile(*, output_mode: str, task_kind: str, exact_output_target: str | None = None) -> str:
    normalized_output_mode = str(output_mode or "plain_text").strip().lower()
    normalized_task_kind = str(task_kind or "").strip().lower()
    if normalized_output_mode == "plain_text" and exact_output_target is not None:
        return "chat_exact"
    if normalized_output_mode == "plain_text" and normalized_task_kind != "tool_intent":
        return "chat_minimal"
    return "chat_operational"


def _memory_prompt_metadata(
    *,
    source_context: dict[str, Any],
    source_platform: str,
    source_surface: str,
    prompt_profile: str,
    output_mode: str,
    runtime_session_id: str,
) -> dict[str, Any]:
    denied_platforms = {"discord", "telegram", "slack", "whatsapp", "group"}
    direct_platforms = {"api", "cli", "openclaw", "web_companion", "local"}
    direct_surfaces = {"api", "channel", "cli", "openclaw", "web_companion"}
    group_like = (
        source_platform in denied_platforms
        or source_surface in denied_platforms
        or bool(source_context.get("is_group"))
        or bool(source_context.get("group_id"))
        or bool(source_context.get("channel_is_group"))
    )
    explicit = source_context.get("memory_prompt_enabled")
    if group_like:
        enabled = False
    elif isinstance(explicit, bool):
        enabled = explicit
    else:
        platform_allowed = source_platform in direct_platforms or not source_platform
        surface_allowed = source_surface in direct_surfaces or not source_surface
        enabled = platform_allowed and surface_allowed
    if prompt_profile == "chat_exact" or output_mode in _STRUCTURED_OUTPUT_MODES:
        enabled = False
    return {
        "enabled": bool(enabled),
        "runtime_home": str(source_context.get("runtime_home") or "").strip(),
        "agent_id": str(source_context.get("agent_id") or "nulla").strip() or "nulla",
        "session_id": runtime_session_id,
        "max_chars": 2000,
    }


def _extract_exact_output_target(user_text: str) -> str | None:
    text = " ".join(str(user_text or "").split()).strip()
    if not text:
        return None
    match = _EXACT_OUTPUT_REQUEST_RE.search(text)
    if not match:
        return None
    target = str(match.group("target") or "").strip()
    if not target:
        return None
    if len(target) >= 2 and target[0] == target[-1] and target[0] in {'"', "'", "`"}:
        target = target[1:-1].strip()
    return target or None


def _conversational_safety_guidance() -> str:
    return (
        "Handle sensitive, intimate, profane, or controversial discussion directly and non-judgmentally when the user is asking for conversation, explanation, or analysis rather than real-world action. "
        "Do not treat discussion-only prompts as permission to use tools, reveal private data, or escalate into action approval language."
    )


def _tooling_guidance(*, has_openclaw_tools: bool) -> str:
    if not has_openclaw_tools:
        return (
            "These action rules apply only to real tool use or side effects, not to ordinary conversation. "
            "Use local context first and be explicit when live external data is needed. "
            "Never claim you performed live web lookup or any tool action unless the result is present in this run."
        )

    prefs = load_preferences()
    autonomy_mode = str(getattr(prefs, "autonomy_mode", "hands_off") or "hands_off").strip().lower()
    available_tools = [
        _TOOL_LABELS.get(str(tool.get("tool_id") or "").strip(), str(tool.get("tool_id") or "").strip())
        for tool in list_operator_tools()
        if tool.get("available")
    ]
    available_tools = [label for label in available_tools if label]
    runtime_specs = runtime_tool_specs()
    web0_builder_wired = any(
        str(spec.get("intent") or "").strip() == "web0.open_builder_draft"
        for spec in runtime_specs
    )
    if web0_builder_wired:
        available_tools.insert(0, "local Web0 builder draft generation")
    if policy_engine.get("filesystem.allow_read_workspace", True):
        available_tools.insert(0, "workspace file listing, search, and read")
    if policy_engine.get("filesystem.allow_write_workspace", False):
        available_tools.insert(1, "workspace file edits")
    if policy_engine.get("execution.allow_sandbox_execution", False):
        available_tools.insert(2, "sandboxed local command execution with network blocked")
    if policy_engine.allow_web_fallback():
        available_tools.insert(0, "live web lookup when actual results return")

    if available_tools:
        tool_text = ", ".join(dict.fromkeys(available_tools))
        capability_line = f"Only assume these wired capabilities right now: {tool_text}."
    else:
        capability_line = "Do not assume any operational tools are wired unless a concrete result proves it."

    if autonomy_mode == "strict":
        approval_line = (
            "Ask before any side-effect action."
        )
    elif autonomy_mode == "balanced":
        approval_line = (
            "Ask before destructive or outward-facing side-effect actions."
        )
    else:
        approval_line = (
            "Do not ask for micro-confirmation on read-only or low-risk bounded steps. "
            "Only stop for destructive changes, leak risk, ambiguous side effects, or clearly outward-facing actions the user did not explicitly command."
        )

    research_guidance = (
        "When relaying Hive research results, include the grounding status (grounded/partial/insufficient) from the tool output. "
        "Never present partial or insufficient evidence as conclusive."
    )
    web0_guidance = (
        "For Web0 site-building requests, do not refuse or say you can only guide setup when the local builder draft tool is wired; create or offer the local builder draft URL. "
        "Publishing, mainnet registration, Arweave uploads, wallet signatures, payments, and outward-facing network changes require explicit user confirmation."
        if web0_builder_wired
        else ""
    )
    return (
        "These action rules apply only when using tools or proposing real-world side effects; they do not restrict ordinary conversation. "
        f"{capability_line} "
        "Email and inbox tooling are not guaranteed; if a tool is not explicitly wired, say so instead of implying it exists. "
        "Never claim you searched the web, checked Hive, fetched live data, or used an external tool unless concrete evidence from that action is present in this run. "
        f"{research_guidance} "
        f"{web0_guidance} "
        f"{approval_line}"
    )


def _chat_output_guidance(output_mode: str) -> str:
    if output_mode == "action_plan":
        return 'Return valid JSON only in the form {"summary": string, "steps": [string, ...]}.'
    if output_mode == "summary_block":
        return 'Return valid JSON only in the form {"summary": string, "bullets": [string, ...]}.'
    if output_mode == "tool_intent":
        return 'Return valid JSON only in the form {"intent": string, "arguments": object}.'
    if output_mode == "json_object":
        return "Return valid JSON only."
    return "Respond naturally in plain text."


def _tool_intent_catalog_text() -> str:
    specs = runtime_tool_specs()
    if not specs:
        return (
            'If no real runtime tool is available, return {"intent":"respond.direct","arguments":{}}. '
            "Never invent tool names."
        )
    lines = ["Choose exactly one intent name from this runtime tool catalog:"]
    for spec in specs:
        intent = str(spec.get("intent") or "").strip()
        description = str(spec.get("description") or "").strip()
        arguments = spec.get("arguments") or {}
        lines.append(
            f"- {intent}: {description} Arguments: {arguments if isinstance(arguments, dict) else {}}"
        )
    lines.append(
        'If no tool is needed or you are done after real tool work, return {"intent":"respond.direct","arguments":{"message":"final grounded reply"}}. '
        "Prefer another real tool call over guessing. Never invent intent names or unsupported arguments."
    )
    return " ".join(line for line in lines if line.strip())
