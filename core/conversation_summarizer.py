"""
LLM-based conversation summarizer.

When a conversation exceeds SUMMARY_THRESHOLD messages:
  - Keep the last KEEP_RECENT messages verbatim
  - Compress all older messages into a structured <summary> assistant turn
  - Replace those messages with the summary turn

Structured output format preserves key facts verbatim, decisions, open
questions, and a narrative summary — so facts are never lost to compression.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

_OLLAMA_BASE = "http://127.0.0.1:11434"

SUMMARY_THRESHOLD = 20   # compress when messages exceed this count
KEEP_RECENT = 8           # always keep this many recent messages verbatim

_SUMMARY_SYSTEM = """\
You are a conversation compressor. Produce a structured summary preserving ALL information.

Output EXACTLY these four sections:

## Key Facts
(bullet every specific value: passwords, ports, keys, dates, names, numbers — VERBATIM, \
character-for-character exact)

## Decisions Made
(bullet every decision, preference, commitment, or constraint the user stated)

## Open Questions
(bullet unresolved items or pending work — write "None" if none)

## Context Summary
(2-3 sentences: what was discussed and any important outcomes)

Rules:
- EXACT values for passwords, keys, ports, codes, dates — word-for-word
- Do NOT drop anything that might be asked about later
- No preamble, no commentary — output ONLY the four sections above"""


def _call_ollama(model: str, messages: list[dict], timeout: int = 60) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 1024},
    }
    req = urllib.request.Request(
        f"{_OLLAMA_BASE}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["message"]["content"]


def _pick_model() -> str:
    """Prefer the smallest fast model for summarization."""
    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
        names = [m["name"] for m in data.get("models", [])]
        for preferred in ("qwen3:0.6b", "qwen3:8b", "qwen3:14b"):
            if preferred in names:
                return preferred
        if names:
            return names[0]
    except Exception:
        pass
    return "qwen3:0.6b"


def summarize_messages(
    messages: list[dict],
    *,
    model: str | None = None,
) -> str:
    """
    Ask the LLM to compress *messages* into a structured summary.
    Returns the summary text with Key Facts / Decisions / Open Questions / Context sections.
    """
    if not messages:
        return ""

    lines: list[str] = []
    for m in messages:
        role = m.get("role", "user").upper()
        content = str(m.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    transcript = "\n".join(lines)

    llm_model = model or _pick_model()
    prompt = (
        "Summarize this conversation transcript into the four required sections. "
        "Preserve every fact, number, password, key, date, and decision verbatim:\n\n"
        f"{transcript}"
    )

    try:
        return _call_ollama(llm_model, [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ])
    except Exception:
        # Fallback: extract first sentence of each message
        fallback_lines = ["## Key Facts"]
        for m in messages:
            content = str(m.get("content", "")).strip()
            first_sent = content.split(".")[0][:120] if content else ""
            if first_sent:
                role = m.get("role", "user")
                fallback_lines.append(f"- [{role}] {first_sent}")
        fallback_lines += ["## Decisions Made", "- (fallback — LLM unavailable)",
                           "## Open Questions", "- None",
                           "## Context Summary", "Conversation context preserved via fallback."]
        return "\n".join(fallback_lines)


def compress_if_needed(
    messages: list[dict],
    *,
    threshold: int = SUMMARY_THRESHOLD,
    keep_recent: int = KEEP_RECENT,
    model: str | None = None,
) -> tuple[list[dict], bool]:
    """
    Compress *messages* if they exceed *threshold*.

    Returns (new_message_list, was_compressed).

    The returned list has at most (keep_recent + 1) messages:
      - [0] is a system or assistant <context_summary> turn
      - [1..] are the most recent *keep_recent* turns verbatim
    """
    if len(messages) <= threshold:
        return messages, False

    split = len(messages) - keep_recent
    old_messages = messages[:split]
    recent_messages = messages[split:]

    system_prefix: list[dict] = []
    if old_messages and old_messages[0].get("role") == "system":
        system_prefix = [old_messages[0]]
        old_messages = old_messages[1:]

    if not old_messages:
        return messages, False

    summary_text = summarize_messages(old_messages, model=model)
    summary_turn: dict = {
        "role": "assistant",
        "content": f"<context_summary>\n{summary_text}\n</context_summary>",
    }

    return [*system_prefix, summary_turn, *recent_messages], True


def token_estimate(messages: list[dict]) -> int:
    """Approximate token count for a message list (chars / 4)."""
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return total_chars // 4


__all__ = [
    "KEEP_RECENT",
    "SUMMARY_THRESHOLD",
    "compress_if_needed",
    "summarize_messages",
    "token_estimate",
]
