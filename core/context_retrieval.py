"""
L3 semantic memory layer for the agent turn loop.

Two-sided integration with the runtime:

  store_turn(session_id, user_text, assistant_text)
    Called after each completed turn. Embeds and persists important content
    to NullaMemory SQLite so it survives session boundaries. Filler turns
    (importance < 0.35) are skipped to keep the store signal-dense.

  inject_retrieved(session_id, query, transcript)
    Called before building the LLM prompt. Queries NullaMemory for *query*,
    filters out nodes already in the transcript (covering L2 summary and
    recent verbatim turns), and injects the remainder as a
    <retrieved_context> system message before the last user turn.

Both functions are best-effort: any error is swallowed silently so the
main response path is never interrupted.
"""
from __future__ import annotations

import re

from core.context_window import (
    DEDUP_MIN_SUBSTRING,
    DEDUP_THRESHOLD,
    _content_covered,
    _content_covered_substring,
)
from core.embedding_service import embed
from core.nulla_memory import NullaMemory

_AGENT_ID = "nulla_chat"      # shared across sessions — enables cross-session recall
_MIN_STORE_CHARS = 30         # short facts ("port is 5433") are still worth storing
_IMPORTANCE_THRESHOLD = 0.35  # skip pure filler turns
_RETRIEVAL_TOP_K = 4
_RETRIEVAL_MIN_SCORE = 0.42
_MAX_INJECT_TOKENS = 350


# ── internal helpers ───────────────────────────────────────────────────────────


def _score_importance(content: str) -> float:
    score = 0.2
    signals = [
        (r'\b(password|secret|passphrase)\b', 0.4),
        (r'\bsk-[a-zA-Z0-9\-_]{6,}\b', 0.4),
        (r'\b(api[\s_-]?key|access[\s_-]?token)\b', 0.35),
        (r'\bport\s*[:=]?\s*\d{2,5}\b', 0.3),
        (r'\b\d{4}-\d{2}-\d{2}\b', 0.25),
        (r'\b(deadline|due\s*date|release\s*date|expires?)\b', 0.25),
        (r'\b(decided?|will\s+use|prefer[rs]?|must\b|required\b)\b', 0.2),
        (r'\b(error|exception|traceback|bug|broken|failed?)\b', 0.15),
    ]
    for pattern, weight in signals:
        if re.search(pattern, content, re.IGNORECASE):
            score = min(1.0, score + weight)
    return round(score, 2)


def _extract_keywords(text: str, max_kw: int = 8) -> list[str]:
    tokens = re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", text)
    seen: dict[str, int] = {}
    for tok in tokens:
        seen[tok.lower()] = seen.get(tok.lower(), 0) + 1
    return sorted(seen, key=lambda k: (-seen[k], -len(k)))[:max_kw]


def _open_memory() -> NullaMemory | None:
    try:
        return NullaMemory(agent_id=_AGENT_ID)
    except Exception:
        return None


# ── public API ─────────────────────────────────────────────────────────────────


def store_turn(
    session_id: str | None,
    user_text: str,
    assistant_text: str,
) -> None:
    """
    Embed and store high-importance content from this turn to NullaMemory.
    Low-importance (filler) turns are skipped to keep the store signal-dense.
    Best-effort — silently ignores all errors.
    """
    try:
        mem = _open_memory()
        if mem is None:
            return
        sid = str(session_id or "unknown").strip()[:20]
        for role, text in (("user", user_text), ("assistant", assistant_text)):
            content = str(text or "").strip()
            if len(content) < _MIN_STORE_CHARS:
                continue
            importance = _score_importance(content)
            if importance < _IMPORTANCE_THRESHOLD:
                continue
            vec = embed(content)
            mem.node_store(
                content=content,
                keywords=_extract_keywords(content),
                tags=[role, f"session:{sid}", f"importance:{importance:.1f}"],
                context_description=f"session={sid} role={role}",
                embedding=vec,
            )
        mem.close()
    except Exception:
        pass


def inject_retrieved(
    session_id: str | None,
    query: str,
    transcript: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Query NullaMemory for *query*, filter nodes already covered by *transcript*,
    and inject the remainder before the last user turn.

    Returns the original transcript unchanged if nothing new is found or on
    any error.
    """
    if not str(query or "").strip():
        return transcript
    try:
        mem = _open_memory()
        if mem is None:
            return transcript
        q_vec = embed(query)
        hits = mem.node_search(
            q_vec, top_k=_RETRIEVAL_TOP_K * 3, min_score=_RETRIEVAL_MIN_SCORE
        )
        mem.close()
    except Exception:
        return transcript

    if not hits:
        return transcript

    context_text = " ".join(m.get("content", "") for m in transcript).lower()
    budget_chars = _MAX_INJECT_TOKENS * 4
    selected = []
    for node, score in hits:
        if _content_covered(node.content, context_text, DEDUP_THRESHOLD):
            continue
        if _content_covered_substring(node.content, context_text, DEDUP_MIN_SUBSTRING):
            continue
        selected.append((node, score))
        budget_chars -= len(node.content)
        if budget_chars <= 0 or len(selected) >= _RETRIEVAL_TOP_K:
            break

    if not selected:
        return transcript

    lines = ["<retrieved_context>"]
    for node, score in selected:
        lines.append(f"[score={score:.2f}] {node.content}")
    lines.append("</retrieved_context>")
    retrieval_msg: dict[str, str] = {"role": "system", "content": "\n".join(lines)}

    result = list(transcript)
    last_user = next(
        (i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"),
        None,
    )
    if last_user is not None:
        result.insert(last_user, retrieval_msg)
    else:
        result.append(retrieval_msg)
    return result


__all__ = ["inject_retrieved", "store_turn"]
