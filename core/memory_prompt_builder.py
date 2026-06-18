from __future__ import annotations

from typing import Any

from core.fact_extractor import stable_text_embedding
from core.nulla_memory import NullaMemory

DEFAULT_BLOCKS = [
    "user_profile",
    "project_context",
    "preferences",
    "constraints",
]
MAX_MEMORY_CHARS = 2000


class MemoryPromptBuilder:
    def __init__(self, memory: NullaMemory, *, max_chars: int = MAX_MEMORY_CHARS) -> None:
        self._memory = memory
        self._max_chars = max(256, int(max_chars))

    def build_prefix(
        self,
        query: str | None = None,
        query_embedding: list[float] | None = None,
        extra_blocks: list[str] | None = None,
        top_k_nodes: int = 3,
    ) -> str:
        parts: list[str] = []
        block_names = [*DEFAULT_BLOCKS, *list(extra_blocks or [])]
        block_text = self._memory.blocks_for_prompt(block_names)
        if block_text:
            parts.append(
                "## NULLA Memory\n"
                "Private persistent facts for this direct user session. "
                "When the user asks about their name, preferences, constraints, or projects, answer from these blocks before guessing.\n\n"
                + block_text
            )

        embedding = query_embedding
        if embedding is None and query:
            embedding = stable_text_embedding(query)
        if embedding and self._memory.node_count() > 0:
            snippets = []
            for node, score in self._memory.node_search(embedding, top_k=top_k_nodes, min_score=0.70):
                tags = ", ".join(node.tags) if node.tags else "memory"
                snippets.append(f"- [{tags}; score={score:.2f}] {node.context_description}")
            if snippets:
                parts.append("## Relevant Past Context\n" + "\n".join(snippets))

        full = "\n\n".join(part for part in parts if part.strip()).strip()
        if len(full) > self._max_chars:
            return full[: self._max_chars].rstrip() + "\n[memory truncated]"
        return full


def build_memory_prefix_for_request(request: Any) -> str:
    metadata = dict(getattr(request, "metadata", None) or {})
    config = dict(metadata.get("memory_prompt") or {})
    if not bool(config.get("enabled")):
        return ""
    runtime_home = str(config.get("runtime_home") or "").strip() or None
    agent_id = str(config.get("agent_id") or "nulla").strip() or "nulla"
    extra_blocks = [str(item).strip() for item in list(config.get("extra_blocks") or []) if str(item).strip()]
    max_chars = int(config.get("max_chars") or MAX_MEMORY_CHARS)
    query = str(getattr(request, "prompt", "") or "").strip()
    try:
        memory = NullaMemory(runtime_home=runtime_home, agent_id=agent_id)
        try:
            return MemoryPromptBuilder(memory, max_chars=max_chars).build_prefix(query=query, extra_blocks=extra_blocks)
        finally:
            memory.close()
    except Exception:
        return ""


def apply_memory_prefix_to_messages(messages: list[dict[str, Any]], request: Any) -> list[dict[str, Any]]:
    prefix = build_memory_prefix_for_request(request)
    if not prefix:
        return messages
    augmented = [dict(message) for message in list(messages or [])]
    for message in augmented:
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        original = str(message.get("content") or "").strip()
        message["content"] = f"{prefix}\n\n---\n\n{original}" if original else prefix
        return augmented
    return [{"role": "system", "content": prefix}, *augmented]


__all__ = [
    "DEFAULT_BLOCKS",
    "MAX_MEMORY_CHARS",
    "MemoryPromptBuilder",
    "apply_memory_prefix_to_messages",
    "build_memory_prefix_for_request",
]
