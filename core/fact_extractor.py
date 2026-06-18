from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import requests

from core.nulla_memory import NullaMemory

FactAction = Literal["ADD", "UPDATE", "DELETE", "NOOP"]

FACT_EXTRACT_SYSTEM = """You are a local memory extraction agent. Given a conversation excerpt,
extract stable facts about the user, projects, preferences, and constraints. Return JSON only.

Output format:
{
  "facts": [
    {"action": "ADD", "block": "user_profile", "content": "Name: Loop"},
    {"action": "UPDATE", "block": "preferences", "old": "old value", "new": "new value"},
    {"action": "DELETE", "block": "project_context", "content": "outdated info"},
    {"action": "NOOP"}
  ]
}

Rules:
- Only extract facts that should survive restart.
- Do not extract one-off tasks, transient emotions, raw logs, file paths, or private internal paths.
- Do not extract secrets, keys, tokens, passwords, seed phrases, cookies, or credentials.
- Prefer NOOP when uncertain.
- Max 5 facts per call."""

FACT_EXTRACT_USER = """Conversation:
{conversation}

Extract stable persistent facts. JSON only."""

ALLOWED_BLOCKS = {
    "user_profile",
    "project_context",
    "preferences",
    "constraints",
    "recent_context",
}
_SINGLETON_LABELS_BY_BLOCK = {
    "user_profile": ("Name",),
    "preferences": ("Answer style", "Response style", "Preferred answer style"),
    "project_context": ("Active project codename", "Project codename"),
}

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password|passwd|pwd)\b\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:seed phrase|mnemonic|private key)\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"/Users/[^\\s]+"),
]
_NAME_PATTERNS = [
    re.compile(r"\bmy name is\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
    re.compile(r"\bcall me\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
    re.compile(r"\bi go by\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
]
_ANSWER_STYLE_PATTERNS = [
    re.compile(r"\b(?:my )?(?:answer-style|answer style|response style)\s+preference\s+is\s+([^.\n]{3,120})", re.IGNORECASE),
    re.compile(r"\bi prefer\s+([^.\n]{3,120}?)\s+(?:answers|responses|replies)\b", re.IGNORECASE),
    re.compile(r"\b(?:keep|make)\s+(?:answers|responses|replies)\s+([^.\n]{3,120})", re.IGNORECASE),
]
_PROJECT_CODENAME_PATTERNS = [
    re.compile(r"\b(?:my )?(?:active )?project codename is\s+([A-Z0-9][A-Z0-9_-]{2,80})\b", re.IGNORECASE),
    re.compile(r"\b(?:my )?preferred project codename is\s+([A-Z0-9][A-Z0-9_-]{2,80})\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class ExtractedFact:
    action: FactAction
    block: str = ""
    content: str = ""
    old: str = ""
    new: str = ""


class FactExtractor:
    """Mem0-style post-turn extractor that updates local memory without blocking inference."""

    def __init__(
        self,
        memory: NullaMemory,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen3:0.6b",
        model_client: Callable[[str], str] | None = None,
        close_memory_on_finish: bool = False,
    ) -> None:
        self._memory = memory
        self._ollama_url = str(ollama_url or "http://localhost:11434").rstrip("/")
        self._model = str(model or "qwen3:0.6b").strip() or "qwen3:0.6b"
        self._model_client = model_client
        self._close_memory_on_finish = bool(close_memory_on_finish)

    def trigger_async(self, messages: list[dict]) -> threading.Thread:
        thread = threading.Thread(
            target=self._run_safely,
            args=(messages,),
            daemon=True,
            name="nulla-fact-extractor",
        )
        thread.start()
        return thread

    def run_sync(self, messages: list[dict]) -> list[ExtractedFact]:
        return self._run(messages)

    def _run_safely(self, messages: list[dict]) -> None:
        try:
            self._run(messages)
        except Exception:
            return
        finally:
            if self._close_memory_on_finish:
                try:
                    self._memory.close()
                except Exception:
                    pass

    def _run(self, messages: list[dict]) -> list[ExtractedFact]:
        conversation_text = self._format_conversation(messages)
        if not conversation_text:
            return []
        raw = self._call_model(conversation_text)
        # Deterministic self-disclosed facts must win the small write budget.
        # The tiny extractor is useful, but noisy output cannot be allowed to
        # crowd out explicit profile/preferences/project declarations.
        facts = _merge_facts([*_rule_based_facts(conversation_text), *self._parse_facts(raw)])
        applied: list[ExtractedFact] = []
        for fact in facts[:5]:
            if self._apply(fact):
                applied.append(fact)
        return applied

    def _format_conversation(self, messages: list[dict]) -> str:
        parts: list[str] = []
        for msg in list(messages or [])[-20:]:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "unknown").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = _message_content_text(msg.get("content", ""))
            content = _redact_sensitive_text(content)[:500].strip()
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def _call_model(self, conversation: str) -> str:
        if self._model_client is not None:
            return str(self._model_client(conversation) or "")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": FACT_EXTRACT_SYSTEM},
                {"role": "user", "content": FACT_EXTRACT_USER.format(conversation=conversation)},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 512},
        }
        try:
            response = requests.post(f"{self._ollama_url}/api/chat", json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return ""
        return str(dict(data.get("message") or {}).get("content") or "")

    def _parse_facts(self, raw: str) -> list[ExtractedFact]:
        payload = _extract_json_object(raw)
        if not payload:
            return []
        facts: list[ExtractedFact] = []
        for item in list(payload.get("facts") or []):
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "NOOP").strip().upper()
            if action not in {"ADD", "UPDATE", "DELETE", "NOOP"}:
                continue
            facts.append(
                ExtractedFact(
                    action=action,  # type: ignore[arg-type]
                    block=str(item.get("block") or "").strip().lower(),
                    content=str(item.get("content") or "").strip(),
                    old=str(item.get("old") or "").strip(),
                    new=str(item.get("new") or "").strip(),
                )
            )
        return facts

    def _apply(self, fact: ExtractedFact) -> bool:
        if fact.action == "NOOP":
            return False
        if fact.block not in ALLOWED_BLOCKS:
            return False
        if fact.action == "ADD":
            content = _safe_memory_text(fact.content)
            if not content:
                return False
            existing = self._memory.block_read(fact.block) or ""
            if _memory_line_exists(existing, content):
                return False
            singleton_line = _existing_singleton_line(existing, block=fact.block, content=content)
            if singleton_line:
                changed = self._memory.block_replace(fact.block, singleton_line, content)
                if changed:
                    self._store_fact_node(content=f"{singleton_line} -> {content}", block=fact.block, action="UPDATE")
                return changed
            self._memory.block_append(fact.block, content)
            self._store_fact_node(content=content, block=fact.block, action=fact.action)
            return True
        if fact.action == "UPDATE":
            old = _safe_memory_text(fact.old)
            new = _safe_memory_text(fact.new)
            if not old or not new:
                return False
            changed = self._memory.block_replace(fact.block, old, new)
            if changed:
                self._store_fact_node(content=f"{old} -> {new}", block=fact.block, action=fact.action)
            return changed
        if fact.action == "DELETE":
            content = _safe_memory_text(fact.content)
            if not content:
                return False
            existing = self._memory.block_read(fact.block) or ""
            if content not in existing:
                return False
            updated = "\n".join(line for line in existing.replace(content, "").splitlines() if line.strip())
            self._memory.block_write(fact.block, updated)
            self._store_fact_node(content=f"Removed: {content}", block=fact.block, action=fact.action)
            return True
        return False

    def _store_fact_node(self, *, content: str, block: str, action: str) -> None:
        embedding = stable_text_embedding(content)
        related = [node.node_id for node, _ in self._memory.node_search(embedding, top_k=3, min_score=0.78)]
        self._memory.node_store(
            content=content,
            keywords=_keywords_for_text(content),
            tags=[block, action.lower()],
            context_description=f"{block}: {content}",
            embedding=embedding,
            linked_node_ids=related,
        )


def stable_text_embedding(text: str, *, dimensions: int = 64) -> list[float]:
    vector = [0.0] * max(8, int(dimensions))
    for token in _keywords_for_text(text, limit=80):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(vector)
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    magnitude = sum(value * value for value in vector) ** 0.5
    if not magnitude:
        return vector
    return [value / magnitude for value in vector]


def _extract_json_object(raw: str) -> dict[str, object]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*"facts".*\}', text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return " ".join(part for part in parts if part.strip())
    return str(content or "")


def _safe_memory_text(text: str) -> str:
    clean = _redact_sensitive_text(str(text or "")).strip()
    if not clean or "[REDACTED]" in clean:
        return ""
    if len(clean) > 600:
        clean = clean[:600].rsplit(" ", 1)[0].strip()
    return clean


def _rule_based_facts(conversation: str) -> list[ExtractedFact]:
    text = "\n".join(_declarative_user_lines(conversation))
    facts: list[ExtractedFact] = []
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = _clean_fact_value(match.group(1), title_case=True)
            if name:
                facts.append(ExtractedFact(action="ADD", block="user_profile", content=f"Name: {name}"))
            break
    for pattern in _ANSWER_STYLE_PATTERNS:
        match = pattern.search(text)
        if match:
            style = _clean_fact_value(match.group(1))
            if style:
                facts.append(ExtractedFact(action="ADD", block="preferences", content=f"Answer style: {style}"))
            break
    for pattern in _PROJECT_CODENAME_PATTERNS:
        match = pattern.search(text)
        if match:
            codename = _clean_fact_value(match.group(1)).upper()
            if codename:
                facts.append(
                    ExtractedFact(
                        action="ADD",
                        block="project_context",
                        content=f"Active project codename: {codename}",
                    )
                )
            break
    return facts


def _declarative_user_lines(conversation: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(conversation or "").splitlines():
        line = raw_line.strip()
        if not line.lower().startswith("user:"):
            continue
        text = line.split(":", 1)[1].strip()
        lowered = text.lower().lstrip()
        if "?" in text or lowered.startswith(("what ", "who ", "which ", "where ", "when ", "why ", "how ")):
            continue
        lines.append(text)
    return lines


def _merge_facts(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    merged: list[ExtractedFact] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    seen_singleton_labels: set[tuple[str, str]] = set()
    for fact in facts:
        singleton_label = _singleton_fact_label(fact)
        if singleton_label:
            if singleton_label in seen_singleton_labels:
                continue
            seen_singleton_labels.add(singleton_label)
        key = (
            fact.action,
            fact.block,
            " ".join(fact.content.lower().split()),
            " ".join(fact.old.lower().split()),
            " ".join(fact.new.lower().split()),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(fact)
    return merged


def _singleton_fact_label(fact: ExtractedFact) -> tuple[str, str] | None:
    if fact.action != "ADD":
        return None
    label = _content_singleton_label(block=fact.block, content=fact.content)
    if not label:
        return None
    return (fact.block, label.lower())


def _content_singleton_label(*, block: str, content: str) -> str:
    text = str(content or "").strip()
    for label in _SINGLETON_LABELS_BY_BLOCK.get(str(block or "").strip().lower(), ()):
        prefix = f"{label}:"
        if text.lower().startswith(prefix.lower()):
            return label
    return ""


def _existing_singleton_line(existing: str, *, block: str, content: str) -> str:
    label = _content_singleton_label(block=block, content=content)
    if not label:
        return ""
    prefix = f"{label}:"
    for raw_line in str(existing or "").splitlines():
        line = raw_line.strip()
        if line.lower().startswith(prefix.lower()):
            return line
    return ""


def _memory_line_exists(existing: str, addition: str) -> bool:
    normalized_addition = " ".join(str(addition or "").split()).lower()
    return any(" ".join(line.split()).lower() == normalized_addition for line in str(existing or "").splitlines())


def _clean_fact_value(value: str, *, title_case: bool = False) -> str:
    text = _safe_memory_text(str(value or ""))
    text = text.strip(" .,:;\"'`")
    if title_case and text:
        return " ".join(part[:1].upper() + part[1:] for part in text.split())
    return text


def _redact_sensitive_text(text: str) -> str:
    clean = str(text or "")
    for pattern in _SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED]", clean)
    return clean


def _keywords_for_text(text: str, *, limit: int = 12) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(text or "").lower())
    stop = {"about", "that", "this", "with", "from", "have", "into", "user", "prefers"}
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in stop or token in seen:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= limit:
            break
    return unique


__all__ = ["ALLOWED_BLOCKS", "ExtractedFact", "FactExtractor", "stable_text_embedding"]
