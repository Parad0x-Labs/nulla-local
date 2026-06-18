from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import active_nulla_home

_BLOCK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class MemoryNode:
    node_id: str
    content: str
    timestamp: float
    keywords: list[str]
    tags: list[str]
    context_description: str
    embedding: list[float]
    linked_node_ids: list[str]
    agent_id: str

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["keywords"] = json.dumps(self.keywords, ensure_ascii=False)
        row["tags"] = json.dumps(self.tags, ensure_ascii=False)
        row["embedding"] = json.dumps(self.embedding, separators=(",", ":"))
        row["linked_node_ids"] = json.dumps(self.linked_node_ids, ensure_ascii=False)
        return row


@dataclass(frozen=True)
class MemoryBlock:
    block_name: str
    content: str
    agent_id: str
    updated_at: float


class NullaMemory:
    """SQLite-backed durable memory for named prompt blocks and episodic nodes."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS memory_blocks (
        block_name TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        content TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (block_name, agent_id)
    );

    CREATE TABLE IF NOT EXISTS memory_nodes (
        node_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL,
        keywords TEXT NOT NULL,
        tags TEXT NOT NULL,
        context_description TEXT NOT NULL,
        embedding TEXT NOT NULL,
        linked_node_ids TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_memory_blocks_agent_updated
        ON memory_blocks(agent_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_agent
        ON memory_nodes(agent_id);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_agent_ts
        ON memory_nodes(agent_id, timestamp DESC);
    """

    def __init__(
        self,
        runtime_home: str | Path | None = None,
        agent_id: str = "nulla",
        db_path: str | Path | None = None,
    ) -> None:
        self._agent_id = str(agent_id or "nulla").strip() or "nulla"
        self._db_path = _resolve_db_path(runtime_home=runtime_home, db_path=db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def block_read(self, block_name: str) -> str | None:
        name = _normalize_block_name(block_name)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT content
                FROM memory_blocks
                WHERE block_name = ? AND agent_id = ?
                LIMIT 1
                """,
                (name, self._agent_id),
            ).fetchone()
        return str(row["content"]) if row else None

    def block_write(self, block_name: str, content: str) -> None:
        name = _normalize_block_name(block_name)
        text = str(content or "").strip()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_blocks (block_name, agent_id, content, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(block_name, agent_id)
                DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
                """,
                (name, self._agent_id, text, time.time()),
            )
            self._conn.commit()

    def block_append(self, block_name: str, content: str, *, dedupe: bool = True) -> None:
        addition = str(content or "").strip()
        if not addition:
            return
        existing = self.block_read(block_name) or ""
        if dedupe and _line_exists(existing, addition):
            return
        separator = "\n" if existing and not existing.endswith("\n") else ""
        self.block_write(block_name, f"{existing}{separator}{addition}")

    def block_replace(self, block_name: str, old: str, new: str) -> bool:
        old_text = str(old or "")
        if not old_text:
            return False
        existing = self.block_read(block_name)
        if existing is None or old_text not in existing:
            return False
        self.block_write(block_name, existing.replace(old_text, str(new or "").strip(), 1))
        return True

    def block_delete(self, block_name: str) -> None:
        name = _normalize_block_name(block_name)
        with self._lock:
            self._conn.execute(
                "DELETE FROM memory_blocks WHERE block_name = ? AND agent_id = ?",
                (name, self._agent_id),
            )
            self._conn.commit()

    def blocks_for_prompt(self, block_names: list[str]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_name in block_names:
            name = _normalize_block_name(raw_name)
            if name in seen:
                continue
            seen.add(name)
            content = self.block_read(name)
            if content:
                parts.append(f"[{name}]\n{content.strip()}")
        return "\n\n".join(parts)

    def all_block_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT block_name
                FROM memory_blocks
                WHERE agent_id = ?
                ORDER BY updated_at DESC, block_name ASC
                """,
                (self._agent_id,),
            ).fetchall()
        return [str(row["block_name"]) for row in rows]

    def node_store(
        self,
        content: str,
        keywords: list[str],
        tags: list[str],
        context_description: str,
        embedding: list[float],
        linked_node_ids: list[str] | None = None,
        *,
        timestamp: float | None = None,
    ) -> MemoryNode:
        text = str(content or "").strip()
        if not text:
            raise ValueError("memory node content is required")
        ts = float(timestamp if timestamp is not None else time.time())
        node_id = hashlib.sha256(f"{self._agent_id}:{ts:.9f}:{text}".encode()).hexdigest()[:20]
        node = MemoryNode(
            node_id=node_id,
            content=text,
            timestamp=ts,
            keywords=[str(item).strip() for item in list(keywords or []) if str(item).strip()],
            tags=[str(item).strip() for item in list(tags or []) if str(item).strip()],
            context_description=str(context_description or "").strip(),
            embedding=[float(value) for value in list(embedding or [])],
            linked_node_ids=[str(item).strip() for item in list(linked_node_ids or []) if str(item).strip()],
            agent_id=self._agent_id,
        )
        row = node.to_row()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_nodes (
                    node_id, agent_id, content, timestamp, keywords, tags,
                    context_description, embedding, linked_node_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["node_id"],
                    row["agent_id"],
                    row["content"],
                    row["timestamp"],
                    row["keywords"],
                    row["tags"],
                    row["context_description"],
                    row["embedding"],
                    row["linked_node_ids"],
                ),
            )
            self._conn.commit()
        return node

    def node_get(self, node_id: str) -> MemoryNode | None:
        normalized = str(node_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE node_id = ? AND agent_id = ?
                LIMIT 1
                """,
                (normalized, self._agent_id),
            ).fetchone()
        return _row_to_node(row) if row else None

    def node_update_links(self, node_id: str, linked_ids: list[str]) -> None:
        normalized = str(node_id or "").strip()
        if not normalized:
            return
        links = [str(item).strip() for item in list(linked_ids or []) if str(item).strip()]
        with self._lock:
            self._conn.execute(
                """
                UPDATE memory_nodes
                SET linked_node_ids = ?
                WHERE node_id = ? AND agent_id = ?
                """,
                (json.dumps(links, ensure_ascii=False), normalized, self._agent_id),
            )
            self._conn.commit()

    def node_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.6,
    ) -> list[tuple[MemoryNode, float]]:
        query = [float(value) for value in list(query_embedding or [])]
        if not query:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_nodes WHERE agent_id = ?",
                (self._agent_id,),
            ).fetchall()
        scored: list[tuple[MemoryNode, float]] = []
        for row in rows:
            node = _row_to_node(row)
            score = _cosine_similarity(query, node.embedding)
            if score >= float(min_score):
                scored.append((node, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(1, int(top_k))]

    def recent_nodes(self, limit: int = 20) -> list[MemoryNode]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE agent_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (self._agent_id, max(1, int(limit))),
            ).fetchall()
        return [_row_to_node(row) for row in rows]

    def node_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM memory_nodes WHERE agent_id = ?",
                (self._agent_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> NullaMemory:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _resolve_db_path(*, runtime_home: str | Path | None, db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser().resolve()
    home = Path(runtime_home).expanduser().resolve() if runtime_home is not None else active_nulla_home()
    return (home / "data" / "memory" / "nulla_memory.db").resolve()


def _normalize_block_name(block_name: str) -> str:
    name = str(block_name or "").strip().lower()
    if not _BLOCK_NAME_RE.match(name):
        raise ValueError(f"invalid memory block name: {block_name!r}")
    return name


def _line_exists(existing: str, addition: str) -> bool:
    normalized_addition = " ".join(str(addition or "").split()).lower()
    return any(" ".join(line.split()).lower() == normalized_addition for line in str(existing or "").splitlines())


def _row_to_node(row: sqlite3.Row) -> MemoryNode:
    return MemoryNode(
        node_id=str(row["node_id"]),
        content=str(row["content"]),
        timestamp=float(row["timestamp"]),
        keywords=_json_list(row["keywords"]),
        tags=_json_list(row["tags"]),
        context_description=str(row["context_description"]),
        embedding=[float(value) for value in _json_list(row["embedding"])],
        linked_node_ids=_json_list(row["linked_node_ids"]),
        agent_id=str(row["agent_id"]),
    )


def _json_list(raw: Any) -> list[Any]:
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:
        return []
    return list(data) if isinstance(data, list) else []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(y * y for y in b) ** 0.5
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


__all__ = ["MemoryBlock", "MemoryNode", "NullaMemory"]
