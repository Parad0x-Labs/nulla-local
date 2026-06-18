"""
ContextWindow — live message history with compression and memory retrieval.

Replaces a plain list[dict] as the conversation buffer for the agent loop.

Three memory tiers:
  L1  Recent messages kept verbatim (last KEEP_RECENT turns)
  L2  Compressed summary of older turns (single <context_summary> turn)
  L3  Long-term semantic nodes in NullaMemory SQLite

Features:
  1. Auto-summarize: LLM compresses old turns into structured L2 summary
  2. Importance scoring: high-value turns (passwords, keys, dates, decisions)
     are tagged and prioritised during retrieval
  3. Smart retrieval: inject_relevant() skips nodes already covered by L2,
     respects a per-call token budget
  4. Hard token cap: never lets the context exceed MAX_TOKENS chars/4

Usage:
    ctx = ContextWindow(agent_id="my_agent")
    ctx.add_system("You are a helpful assistant.")
    ctx.add("user", "My API key is sk-abc123")
    ctx.add("assistant", "Got it.")

    messages = ctx.messages_for_llm()       # compressed, within budget
    ctx.inject_relevant("what was the key?") # pulls from L3 if not in L2
"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator

from core.conversation_summarizer import compress_if_needed, token_estimate
from core.embedding_service import embed
from core.nulla_memory import NullaMemory

# ── tunables ───────────────────────────────────────────────────────────────────

MAX_TOKENS = 6_000          # hard token cap before compression fires
SUMMARY_THRESHOLD = 20      # message count that triggers summarisation
KEEP_RECENT = 8             # messages kept verbatim after compression
RETRIEVAL_TOP_K = 3         # candidate nodes to fetch from L3
RETRIEVAL_MIN_SCORE = 0.4   # minimum cosine similarity to surface a node
MAX_INJECT_TOKENS = 300     # max tokens added by a single inject_relevant() call
STORE_MIN_CHARS = 30        # short facts ("port is 5433") are still worth storing
DEDUP_THRESHOLD = 0.60      # word-overlap ratio above which a node is "already covered"
DEDUP_MIN_SUBSTRING = 40    # min chars for literal substring dedup check


class ContextWindow:
    """
    Drop-in replacement for list[dict] with three-tier memory.

    All messages are kept in self._messages (full history).
    self.messages_for_llm() returns the L2-compressed view within token budget.
    self.inject_relevant(query) adds L3 nodes not already covered by L2.
    """

    def __init__(
        self,
        agent_id: str = "nulla",
        *,
        db_path: str | None = None,
        max_tokens: int = MAX_TOKENS,
        summary_threshold: int = SUMMARY_THRESHOLD,
        keep_recent: int = KEEP_RECENT,
        summarizer_model: str | None = None,
        persist_memory: bool = True,
    ) -> None:
        self._agent_id = agent_id
        self._max_tokens = max_tokens
        self._summary_threshold = summary_threshold
        self._keep_recent = keep_recent
        self._summarizer_model = summarizer_model
        self._messages: list[dict] = []
        self._compressed: list[dict] = []
        self._compression_count = 0
        self._memory: NullaMemory | None = None

        if persist_memory:
            with contextlib.suppress(Exception):
                self._memory = NullaMemory(agent_id=agent_id, db_path=db_path)

    # ── public message API ─────────────────────────────────────────────────────

    def add(self, role: str, content: str) -> None:
        """Append a message and optionally persist it as an L3 memory node."""
        msg = {"role": role, "content": str(content or "").strip()}
        self._messages.append(msg)
        self._compressed.append(msg)

        if self._memory and len(msg["content"]) >= STORE_MIN_CHARS:
            importance = _score_importance(msg["content"])
            try:
                vec = embed(msg["content"])
                self._memory.node_store(
                    content=msg["content"],
                    keywords=_extract_keywords(msg["content"]),
                    tags=[role, f"importance:{importance:.1f}"],
                    context_description=f"turn {len(self._messages)}",
                    embedding=vec,
                )
            except Exception:
                pass

    def add_system(self, content: str) -> None:
        self.add("system", content)

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self) -> Iterator[dict]:
        return iter(self._messages)

    # ── LLM-ready view ─────────────────────────────────────────────────────────

    def messages_for_llm(self) -> list[dict]:
        """
        Return the L1+L2 compressed message list within the token budget.

        Compression fires automatically when:
          a) token budget exceeded, OR
          b) message count exceeds summary_threshold
        """
        self._maybe_compress()
        return list(self._compressed)

    def inject_relevant(
        self,
        query: str,
        *,
        top_k: int = RETRIEVAL_TOP_K,
        max_inject_tokens: int = MAX_INJECT_TOKENS,
    ) -> int:
        """
        Embed *query*, retrieve L3 nodes, filter out anything already in L2,
        and inject the remainder as a system message before the last user turn.

        Returns the number of nodes actually injected (0 if none useful).
        """
        if not self._memory:
            return 0
        try:
            q_vec = embed(query)
            # Fetch extra candidates so filtering doesn't leave us empty
            hits = self._memory.node_search(
                q_vec, top_k=top_k * 3, min_score=RETRIEVAL_MIN_SCORE
            )
        except Exception:
            return 0

        if not hits:
            return 0

        # Build a single lowercase string of everything already in compressed context
        context_text = " ".join(m.get("content", "") for m in self._compressed).lower()

        budget_chars = max_inject_tokens * 4
        selected: list[tuple] = []
        for node, score in hits:
            if _content_covered(node.content, context_text, DEDUP_THRESHOLD):
                continue
            if _content_covered_substring(node.content, context_text, DEDUP_MIN_SUBSTRING):
                continue
            selected.append((node, score))
            budget_chars -= len(node.content)
            if budget_chars <= 0 or len(selected) >= top_k:
                break

        if not selected:
            return 0

        lines = ["<retrieved_context>"]
        for node, score in selected:
            lines.append(f"[score={score:.2f}] {node.content}")
        lines.append("</retrieved_context>")

        retrieval_msg = {"role": "system", "content": "\n".join(lines)}
        last_user = next(
            (i for i in range(len(self._compressed) - 1, -1, -1)
             if self._compressed[i]["role"] == "user"),
            None,
        )
        if last_user is not None:
            self._compressed.insert(last_user, retrieval_msg)
        else:
            self._compressed.append(retrieval_msg)

        return len(selected)

    # ── stats ──────────────────────────────────────────────────────────────────

    @property
    def raw_message_count(self) -> int:
        return len(self._messages)

    @property
    def compressed_message_count(self) -> int:
        return len(self._compressed)

    @property
    def compression_count(self) -> int:
        return self._compression_count

    @property
    def estimated_tokens(self) -> int:
        return token_estimate(self._compressed)

    @property
    def memory_node_count(self) -> int:
        return self._memory.node_count() if self._memory else 0

    # ── internals ──────────────────────────────────────────────────────────────

    def _maybe_compress(self) -> None:
        too_many_msgs = len(self._compressed) > self._summary_threshold
        too_many_tokens = token_estimate(self._compressed) > self._max_tokens

        if not (too_many_msgs or too_many_tokens):
            return

        new_compressed, was_compressed = compress_if_needed(
            self._compressed,
            threshold=self._summary_threshold,
            keep_recent=self._keep_recent,
            model=self._summarizer_model,
        )
        if was_compressed:
            self._compressed = new_compressed
            self._compression_count += 1

    def close(self) -> None:
        if self._memory:
            with contextlib.suppress(Exception):
                self._memory.close()

    def __enter__(self) -> ContextWindow:
        return self

    def __exit__(self, *_) -> bool:
        self.close()
        return False


# ── helpers ────────────────────────────────────────────────────────────────────


def _score_importance(content: str) -> float:
    """
    Score 0.0–1.0 for how important a turn is to preserve in memory.

    High: passwords, API keys, ports, dates, decisions.
    Low:  generic technical explanations, filler.
    """
    score = 0.2  # base — most things are worth remembering a little
    signals = [
        (r'\b(password|secret|passphrase)\b', 0.4),
        (r'\bsk-[a-zA-Z0-9\-_]{6,}\b', 0.4),     # API key pattern
        (r'\b(api[\s_-]?key|access[\s_-]?token)\b', 0.35),
        (r'\bport\s*[:=]?\s*\d{2,5}\b', 0.3),
        (r'\b\d{4}-\d{2}-\d{2}\b', 0.25),          # ISO date
        (r'\b(deadline|due\s*date|release\s*date|expires?)\b', 0.25),
        (r'\b(decided?|will\s+use|prefer[rs]?|must\b|required\b)\b', 0.2),
        (r'\b(error|exception|traceback|bug|broken|failed?)\b', 0.15),
        (r'\b(important|critical|urgent|priority)\b', 0.1),
    ]
    for pattern, weight in signals:
        if re.search(pattern, content, re.IGNORECASE):
            score = min(1.0, score + weight)
    return round(score, 2)


def _content_covered(content: str, context_text: str, threshold: float = DEDUP_THRESHOLD) -> bool:
    """
    Return True if *content*'s significant words are already substantially
    present in *context_text* (L2 summary or recent turns).
    """
    words = set(re.findall(r'\b[a-zA-Z0-9_\-]{4,}\b', content.lower()))
    if not words:
        return False
    covered = sum(1 for w in words if w in context_text)
    return (covered / len(words)) >= threshold


def _content_covered_substring(content: str, context_text: str, min_len: int = DEDUP_MIN_SUBSTRING) -> bool:
    """
    Return True if any contiguous chunk of *content* (at least min_len chars,
    or the whole content if shorter) appears literally in *context_text*.

    Catches verbatim repetition even when word-overlap scoring misses it due
    to stemming differences or low-frequency terms.
    """
    lower = content.lower().strip()
    if not lower:
        return False
    check_len = min(len(lower), min_len)
    if check_len < 8:
        return False
    step = max(1, check_len // 4)
    return any(lower[start:start + check_len] in context_text for start in range(0, len(lower) - check_len + 1, step))


def _extract_keywords(text: str, max_kw: int = 8) -> list[str]:
    """Simple keyword extractor: most-frequent tokens by length."""
    tokens = re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", text)
    seen: dict[str, int] = {}
    for tok in tokens:
        key = tok.lower()
        seen[key] = seen.get(key, 0) + 1
    ranked = sorted(seen, key=lambda k: (-seen[k], -len(k)))
    return ranked[:max_kw]


__all__ = [
    "KEEP_RECENT",
    "MAX_INJECT_TOKENS",
    "MAX_TOKENS",
    "SUMMARY_THRESHOLD",
    "ContextWindow",
]
