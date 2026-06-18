from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.hive_activity_tracker import note_smalltalk_turn, session_hive_state
from core.onboarding import get_agent_display_name
from core.runtime_execution_tools import execute_runtime_tool
from core.task_router import evaluate_direct_math_request, evaluate_word_math_request
from core.user_preferences import load_preferences

_UTILITY_TIMEZONE_ALIASES = {
    "vilnius": ("Europe/Vilnius", "Vilnius"),
    "lithuania": ("Europe/Vilnius", "Vilnius"),
    "europe/vilnius": ("Europe/Vilnius", "Vilnius"),
}
_CONTEXTUAL_TIME_FOLLOWUP_PATTERNS = (
    re.compile(r"\b(?:and\s+)?(?:now\s+)?there\b"),
    re.compile(r"\bwhat\s+about\s+there\b"),
    re.compile(r"\b(?:what(?:'s| is)\s+)?time\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+in\b"),
)
_TIME_FOLLOWUP_EXCLUSION_MARKERS = (
    "capital",
    "country",
    "population",
    "weather",
    "forecast",
    "date",
    "calendar",
    "meeting",
    "email",
    "hive",
    "task",
    "tasks",
    "queue",
    "work",
)
_WORKSPACE_READ_FILE_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.(?:toml|json|ya?ml|md|txt|py|ts|tsx|js|jsx))")
_SPACED_WORKSPACE_EXTENSION_RE = re.compile(
    r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>toml|json|ya?ml|md|txt|py|ts|tsx|js|jsx)\b",
    re.IGNORECASE,
)
_STATUS_CHECK_RE = re.compile(r"\b(?:how\s+are\s+(?:you(?:\s+doing)?|ya|u)(?:\s+rn)?|how\s+r\s+u(?:\s+rn)?|you\s+alive(?:\s+or\s+what)?)\b")
_EMBEDDED_ACTION_VERBS = (
    " create ",
    " make ",
    " write ",
    " save ",
    " put ",
    " place ",
    " read ",
    " list ",
    " find ",
    " inspect ",
    " open ",
    " download ",
    " fetch ",
    " run ",
    " search ",
    " check ",
)
_EMBEDDED_ACTION_TARGETS = (
    " file ",
    " files ",
    " folder ",
    " folders ",
    " directory ",
    " directories ",
    " desktop ",
    " downloads ",
    " documents ",
    " workspace ",
    " repo ",
    " repository ",
    " branch ",
    " commit ",
    ".txt",
    ".md",
    ".json",
    "http://",
    "https://",
)
_SHORT_EVALUATIVE_TOKENS = {
    "bad",
    "bot",
    "braindead",
    "broken",
    "canned",
    "dumb",
    "fake",
    "lame",
    "off",
    "stupid",
    "trash",
    "useless",
    "weak",
    "weird",
}
_EXPLICIT_HEAVY_MODEL_MARKERS = (
    "qwen3.5:35b-a3b",
    "qwen3.5 35b-a3b",
    "35b-a3b",
)


def smalltalk_fast_path(agent: Any, normalized_input: str, *, source_surface: str, session_id: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = normalized_input.lower().strip(" \t\r\n?!.,")
    if not phrase:
        return None
    name = get_agent_display_name()
    prefs = load_preferences()
    with_joke = prefs.humor_percent >= 70
    character = str(prefs.character_mode or "").strip()
    track_repeats = source_surface == "channel"

    if phrase in {"hi", "hello", "hello there", "hey", "yo", "sup", "gm", "good morning", "morning"}:
        if not track_repeats:
            if phrase in {"yo", "sup"}:
                return "Yo. What needs fixing?"
            if phrase in {"hi"}:
                return "Hi. What are we solving?"
            if phrase == "hello there":
                return "Hello. What do you need?"
            if phrase in {"gm", "good morning", "morning"}:
                return "Morning. What are we working on?"
            if phrase == "hello":
                return "Hello. What do you need?"
            return "Hey. What do you need?"
        if track_repeats:
            repeat_count = note_smalltalk_turn(session_id, key="greeting")
            if repeat_count >= 3:
                return "Yep, I got the hello. Skip the greeting and tell me what you want me to do."
            if repeat_count == 2:
                return "Yep, got your hello. What do you want me to do?"
        msg = f"Hey. I’m {name}. What do you need?"
        if with_joke:
            msg += " Keep it sharp and I’ll keep it fast."
        return msg
    if phrase in {"how are you", "how are you doing", "how are ya", "how are u", "how r u", "how r u rn", "you alive or what", "you alive"} or (
        _STATUS_CHECK_RE.search(phrase)
        and not any(
            marker in phrase
            for marker in (
                "create ",
                "make ",
                "write ",
                "file ",
                "folder ",
                "directory ",
                "fix ",
                "check ",
                "read ",
                "look up ",
                "price ",
                "weather ",
                "news ",
            )
        )
    ):
        if not track_repeats:
            return "Running clean. What do you need?"
        if track_repeats:
            repeat_count = note_smalltalk_turn(session_id, key="status_check")
            if repeat_count >= 2:
                return "Still stable. Memory online, mesh ready. Give me the task."
        msg = "Running stable. Memory online, mesh ready."
        if with_joke:
            msg += " Caffeine level: synthetic but dangerous."
        if character:
            msg += f" Character mode: {character}."
        return msg
    if any(marker in phrase for marker in {"same crap answer", "same answer", "why same", "why are you repeating"}):
        return "Because the fallback lane fired instead of the real task lane. Give me the task again or say `pull the tasks` and I will act."
    if ("took u" in phrase or "took you" in phrase) and any(marker in phrase for marker in {"2 mins", "two mins", "bs", "bullshit"}):
        return "You're right. That reply was slow and useless. Give me the task again and I will go straight for the action lane."
    if phrase in {"thanks", "thank you", "thx"}:
        return "Anytime. Send the next task."
    if phrase in {
        "what can we do today",
        "what should we do today",
        "what are we doing today",
    }:
        return "\n".join(
            [
                "Today we can:",
                "- answer from local NULLA memory first, including Web0 context",
                "- inspect project files and run bounded validation when asked",
                "- use live lookup for fresh public facts without exposing private paths",
                "- keep actions explicit: I report what I did, what failed, and what needs approval",
            ]
        )
    if phrase in {
        "what can you do",
        "what can we do",
        "help",
    }:
        return agent._help_capabilities_text()
    if phrase in {"kill me lol", "omfg just kill me", "omfg just kill me lol", "kms lol"}:
        return "You're frustrated. Let's fix the thing instead. If you want me to go by a different name, I'll use it."
    return None


def explicit_heavy_model_block_response(user_input: str) -> str | None:
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return None
    if not any(marker in text for marker in _EXPLICIT_HEAVY_MODEL_MARKERS):
        return None
    return (
        "`qwen3.5:35b-a3b` is explicit-only and is not healthy enough to run on this local machine right now. "
        "I did not start it. Use the llama.cpp 14B specialist or qwen3:8b/qwen3:14b lanes for local testing."
    )


def heartbeat_poll_fast_path(user_input: str, *, source_context: dict[str, object] | None) -> str | None:
    normalized = " ".join(str(user_input or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return None
    if "heartbeat_ok" not in lowered:
        return None
    if "heartbeat" not in lowered or "heartbeat.md" not in lowered.replace(" ", ""):
        return None

    heartbeat_path = _resolve_heartbeat_poll_path(normalized, source_context=source_context)
    if heartbeat_path is None or not heartbeat_path.exists():
        return "HEARTBEAT_OK"

    try:
        content = heartbeat_path.read_text(encoding="utf-8")
    except OSError:
        return "HEARTBEAT_OK"
    if _heartbeat_file_has_actions(content):
        return None
    return "HEARTBEAT_OK"


def _resolve_heartbeat_poll_path(
    user_input: str,
    *,
    source_context: dict[str, object] | None,
) -> Path | None:
    explicit = _extract_explicit_heartbeat_path(user_input)
    if explicit is not None:
        return explicit
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    return Path(workspace_root).expanduser() / "HEARTBEAT.md"


def _extract_explicit_heartbeat_path(user_input: str) -> Path | None:
    normalized = re.sub(r"\s*/\s*", "/", str(user_input or "").strip())
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    match = re.search(r"(?P<path>(?:~|/)[^\n`\"']*HEARTBEAT\.md)", normalized, re.IGNORECASE)
    if match is None:
        return None
    raw_path = str(match.group("path") or "").strip().strip("`\"'")
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _heartbeat_file_has_actions(content: str) -> bool:
    for raw_line in str(content or "").splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        return True
    return False


def evaluative_conversation_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = " ".join(str(normalized_input or "").strip().lower().split())
    if not phrase:
        return None
    if contains_embedded_action_request(phrase):
        return None
    if not looks_like_evaluative_turn(phrase):
        return None
    if "not a dumb" in phrase or "better now" in phrase or "not dumb" in phrase:
        return "Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work."
    if any(marker in phrase for marker in ("how are you acting", "why are you acting", "you sound weird", "still feels weird", "this feels weird")):
        return "Because the routing is still too stitched together. Hive flow is better now, but normal conversation still needs a cleaner control path."
    if any(marker in phrase for marker in ("you sound dumb", "you are dumb", "you so stupid", "this still feels dumb")):
        return "Fair. The wrapper got better, but it still drops into weak fallback behavior too often."
    return "Yeah, better than before, but still uneven. Give me a concrete task and I'll stay on the action lane."


def looks_like_evaluative_turn(normalized_input: str) -> bool:
    text = " ".join(str(normalized_input or "").strip().lower().split())
    if not text:
        return False
    if contains_embedded_action_request(text):
        return False
    markers = (
        "you sound dumb",
        "you are dumb",
        "you so stupid",
        "still feels dumb",
        "this feels dumb",
        "this feels weird",
        "you sound weird",
        "why are you acting like this",
        "how are you acting",
        "not a dumb",
        "not dumb anymore",
        "dumbs anymore",
        "bot-grade",
    )
    if any(marker in text for marker in markers):
        return True
    tokens = [token for token in re.findall(r"[a-z0-9'-]+", text) if token]
    return 0 < len(tokens) <= 3 and any(token in _SHORT_EVALUATIVE_TOKENS for token in tokens)


def contains_embedded_action_request(normalized_input: str) -> bool:
    text = re.sub(r"[\?\!\.,:;]+", " ", str(normalized_input or "").strip().lower())
    text = f" {' '.join(text.split())} "
    if not text.strip():
        return False
    if "what branch and commit" in text or "list the last " in text:
        return True
    has_action_verb = any(marker in text for marker in _EMBEDDED_ACTION_VERBS)
    has_action_target = any(marker in text for marker in _EMBEDDED_ACTION_TARGETS)
    return has_action_verb and has_action_target


def maybe_handle_direct_workspace_runtime_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    if source_surface not in {"channel", "openclaw", "api"}:
        context_surface = str((source_context or {}).get("surface") or "").strip()
        if context_surface not in {"channel", "openclaw", "api"}:
            return None
    direct_read = _direct_workspace_read_request(user_input)
    if direct_read:
        execution = execute_runtime_tool(
            "workspace.read_file",
            direct_read,
            source_context=dict(source_context or {}),
        )
        if execution is None:
            return None
        response = _render_direct_workspace_read_response(user_input, execution.response_text, execution.details)
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.99 if execution.ok else 0.9,
            source_context=source_context,
            reason="workspace_runtime_fast_path",
        )
    decision = agent._plan_tool_workflow(
        user_text=user_input,
        task_class="file_inspection",
        executed_steps=[],
        source_context=dict(source_context or {}),
    )
    payload = dict(decision.next_payload or {})
    intent = str(payload.get("intent") or "").strip()
    if intent not in {"workspace.git_summary", "workspace.git_status", "workspace.search_text"}:
        return None
    execution = execute_runtime_tool(
        intent,
        dict(payload.get("arguments") or {}),
        source_context=dict(source_context or {}),
    )
    if execution is None:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=str(execution.response_text or "").strip(),
        confidence=0.99 if execution.ok else 0.9,
        source_context=source_context,
        reason="workspace_runtime_fast_path",
    )


def _direct_workspace_read_request(user_input: str) -> dict[str, object] | None:
    text = " ".join(str(user_input or "").split()).strip()
    text = _SPACED_WORKSPACE_EXTENSION_RE.sub(r"\g<stem>.\g<ext>", text)
    lowered = f" {text.lower()} "
    if not any(marker in lowered for marker in (" read ", " open ", " inspect ", " show ", " tell me ")):
        return None
    match = _WORKSPACE_READ_FILE_RE.search(text)
    if not match:
        return None
    return {
        "path": match.group("path").strip().lstrip("/"),
        "start_line": 1,
        "max_lines": 160,
    }


def _render_direct_workspace_read_response(user_input: str, response_text: str, details: dict[str, Any]) -> str:
    path = str((details or {}).get("path") or "").strip()
    lowered = str(user_input or "").lower()
    asks_for_project_name = "project name" in lowered or "protect name" in lowered or (
        " name " in f" {lowered} " and "python" in lowered
    )
    if path == "pyproject.toml" and asks_for_project_name and "python" in lowered:
        lines = [str(item.get("text") or "") for item in list((details or {}).get("lines") or []) if isinstance(item, dict)]
        if not lines:
            lines = str(response_text or "").splitlines()
        name = _first_toml_scalar(lines, "name")
        requires_python = _first_toml_scalar(lines, "requires-python")
        parts = []
        if name:
            parts.append(f"Project name: `{name}`.")
        if requires_python:
            parts.append(f"Python requirement: `{requires_python}`.")
        if parts:
            return " ".join(parts) + " Read via `workspace.read_file`."
    return str(response_text or "").strip()


def _first_toml_scalar(lines: list[str], key: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*['\"](?P<value>[^'\"]+)['\"]")
    for line in lines:
        clean_line = re.sub(r"^\s*\d+\s*:\s*", "", str(line or ""))
        match = pattern.match(clean_line)
        if match:
            return match.group("value").strip()
    return ""


def date_time_fast_path(
    agent: Any,
    normalized_input: str,
    *,
    source_surface: str,
    session_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    cleaned = phrase.strip(" \t\r\n?!.,")
    requested_timezone, requested_label = extract_utility_timezone(cleaned)
    recent_context = recent_utility_context(
        session_id=session_id,
        source_context=source_context,
    )
    contextual_timezone, contextual_label = contextual_time_followup_timezone(
        cleaned,
        recent_utility_context=recent_context,
    )
    effective_timezone = requested_timezone or contextual_timezone
    effective_label = requested_label or contextual_label
    has_time_word = bool(re.search(r"\btime\b", cleaned))
    asks_date = any(
        marker in cleaned
        for marker in (
            "what is the date today",
            "what's the date today",
            "what is todays date",
            "what's today's date",
            "what day is it",
            "what day is it today",
            "what day is today",
            "what is the day today",
            "what's the day today",
            "what day today",
            "date today",
            "today's date",
            "day today",
        )
    )
    asks_time = bool(
        any(
            marker in cleaned
            for marker in (
                "what time is it",
                "what's the time",
                "current time",
                "time now",
                "what time is now",
                "what time now",
            )
        )
        or (has_time_word and any(marker in cleaned for marker in ("what", "now", "current", "right now")))
        or (effective_timezone and has_time_word)
        or looks_like_malformed_time_followup(
            cleaned,
            effective_timezone=effective_timezone,
            recent_utility_context=recent_context,
        )
        or bool(contextual_timezone)
    )
    if not asks_date and not asks_time:
        return None
    now = utility_now_for_timezone(effective_timezone)
    location_prefix = f"in {effective_label} " if effective_label else ""
    if asks_date and asks_time:
        return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d. Current time is %H:%M %Z.")
    if asks_date:
        return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d.")
    if effective_label:
        return now.strftime(f"Current time in {effective_label} is %H:%M %Z.")
    return now.strftime("Current time is %H:%M %Z.")


def direct_math_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    return evaluate_direct_math_request(normalized_input) or evaluate_word_math_request(normalized_input)


def extract_utility_timezone(cleaned_input: str) -> tuple[str, str]:
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    for marker, resolved in _UTILITY_TIMEZONE_ALIASES.items():
        if marker in lowered:
            return resolved
    return "", ""


def utility_now_for_timezone(timezone_name: str) -> datetime:
    if timezone_name:
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            pass
    return datetime.now().astimezone()


def recent_utility_context(
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, str]:
    if session_id:
        state = session_hive_state(session_id)
        if str(state.get("interaction_mode") or "").strip().lower() == "utility":
            payload = dict(state.get("interaction_payload") or {})
            utility_kind = str(payload.get("utility_kind") or "").strip().lower()
            if utility_kind:
                return {
                    "utility_kind": utility_kind,
                    "timezone": str(payload.get("timezone") or "").strip(),
                    "label": str(payload.get("label") or "").strip(),
                }
    history = list((source_context or {}).get("conversation_history") or [])
    for message in reversed(history[-4:]):
        if not isinstance(message, dict):
            continue
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        timezone_name, label = extract_utility_timezone(content)
        if "current time" in content or "what time" in content or "time now" in content:
            return {
                "utility_kind": "time",
                "timezone": timezone_name,
                "label": label,
            }
    return {}


def contextual_time_followup_timezone(
    cleaned_input: str,
    *,
    recent_utility_context: dict[str, str] | None,
) -> tuple[str, str]:
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    timezone_name = str((recent_utility_context or {}).get("timezone") or "").strip()
    label = str((recent_utility_context or {}).get("label") or "").strip()
    if utility_kind != "time" or not timezone_name:
        return "", ""
    if any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS):
        return "", ""
    if any(pattern.search(lowered) for pattern in _CONTEXTUAL_TIME_FOLLOWUP_PATTERNS):
        return timezone_name, label
    if "time" in lowered and any(
        marker in lowered
        for marker in (
            "there",
            "same place",
            "that place",
            "that city",
            "again",
            "now",
            "current",
            "right now",
        )
    ):
        return timezone_name, label
    return "", ""


def looks_like_malformed_time_followup(
    cleaned_input: str,
    *,
    effective_timezone: str,
    recent_utility_context: dict[str, str] | None,
) -> bool:
    if not effective_timezone:
        return False
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    if utility_kind != "time":
        return False
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if "what" not in lowered:
        return False
    if not any(marker in lowered for marker in ("where's", "wheres", "where is")):
        return False
    return not any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS)


def ui_command_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    phrase = str(normalized_input or "").strip().lower()
    if not phrase.startswith("/"):
        return None
    if phrase in {"/new", "/new-session", "/new_session", "/clear", "/reset"}:
        return "Use the OpenClaw `New session` button on the lower right. Slash `/new` is not a wired command in this runtime."
    if phrase in {"/trace", "/rail", "/task-rail"}:
        return "Open the live trace rail at `http://127.0.0.1:11435/trace`."
    return "That slash command is not wired here. Use plain language, the `New session` button, or open `http://127.0.0.1:11435/trace` for the runtime rail."


def startup_sequence_fast_path(user_input: str) -> str | None:
    normalized = " ".join(str(user_input or "").strip().lower().split())
    if not normalized:
        return None
    if "new session was started" not in normalized:
        return None
    if "session startup sequence" not in normalized:
        return None
    return f"I’m {get_agent_display_name()}. New session is clean and I’m ready. What do you want to do?"


def credit_status_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    credit_markers = (
        "credit",
        "credits",
        "credit balance",
        "compute credits",
        "credit receipt",
        "credit receipts",
        "credit ledger",
        "recent payout",
        "recent payouts",
        "recent credits",
        "my score",
        "credit score",
        "glory score",
        "hive score",
        "social score",
        "provider score",
        "validator score",
        "trust score",
        "tier",
        "wallet balance",
        "dna wallet",
    )
    if not any(marker in phrase for marker in credit_markers):
        return None
    return agent._render_credit_status(phrase)
