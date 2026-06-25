from __future__ import annotations

import contextlib
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

# Lexical markers that signal a node is a value-CHANGE (supersedes a prior value
# of the same fact) rather than a second distinct fact about the same entity.
# Lowercase and >=3 chars to match _token_set tokenization.
_SUPERSEDE_MARKERS = frozenset({
    "moved", "move", "updated", "update", "changed", "change", "now",
    "instead", "replaced", "replace", "supersedes", "superseded", "revised",
    "corrected", "renamed", "switched", "switch", "longer", "rescheduled",
    "bumped", "migrated",
})


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

    -- Full-text index for hybrid (BM25 + semantic) retrieval. Kept in sync
    -- manually in node_store / invalidate (standalone, not external-content, so
    -- a corrupt sync degrades to "no BM25 leg" rather than breaking writes).
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        node_id UNINDEXED, agent_id UNINDEXED, content, keywords,
        tokenize='porter unicode61'
    );
    """

    # Retrieval ranking (hybrid). Semantic dominates so recall is preserved;
    # effective importance carries an exp-decay recency term so the LATEST value
    # of a fact outranks the stale one it replaced (fixes stale-value retrieval
    # without fragile supersession detection); BM25 adds a keyword leg.
    # Decay follows the agent-memory standard: base * (W_R*recency + W_F*freq),
    # recency = exp(-ln2/half_life * age_days), freq = log1p(access)/log1p(cap).
    _W_SEMANTIC = 1.0
    _W_EFFECTIVE = 0.30          # weight of effective importance in the final rank
    _W_BM25 = 0.20              # weight of the normalized BM25 (keyword) leg
    _HALF_LIFE_DAYS = 14.0
    _W_RECENCY = 0.7
    _W_FREQ = 0.3
    _FREQ_CAP = 50.0
    _DEDUP_SIM = 0.97          # cosine above which a near-identical write is a NOOP
    _DEDUP_OVERLAP = 0.85      # Jaccard token-overlap required alongside high cosine
                              # (strict: only true restatements NOOP, never updates)
    # 1-hop entity-graph expansion: after ranking, pull in nodes linked from the top
    # hits so directly-connected context surfaces even if it fell below min_score on
    # direct similarity. Bounded (one hop, capped fan-out + total) and dedup'd.
    _LINK_EXPANSION = True
    _LINK_FANOUT = 3           # max links followed per top hit
    _LINK_WEIGHT = 0.9         # a linked node rides in at this fraction of its linker's score
    _LINK_EXPANSION_MAX = 5    # cap on total linked nodes pulled into one search
    _COLLAPSE_SIM = 0.85       # retrieval-time: same-topic memories collapse to the
                              # NEWEST one (latest value of a fact wins) without
                              # deleting history — surfaces updates over stale values

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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Additively add ranking/temporal columns and backfill the FTS index.
        Idempotent: safe to run on every open, on fresh and existing DBs."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(memory_nodes)")}
        adds = {
            "base_importance": "REAL NOT NULL DEFAULT 0.5",
            "access_count": "INTEGER NOT NULL DEFAULT 0",
            "last_access": "REAL",
            "valid_from": "REAL",
            "valid_to": "REAL",  # NULL = live; non-NULL = invalidated/superseded
        }
        changed = False
        for col, decl in adds.items():
            if col not in have:
                self._conn.execute(f"ALTER TABLE memory_nodes ADD COLUMN {col} {decl}")
                changed = True
        if changed:
            # backfill temporal/access defaults for pre-existing rows
            self._conn.execute(
                "UPDATE memory_nodes SET valid_from = COALESCE(valid_from, timestamp), "
                "last_access = COALESCE(last_access, timestamp)"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_nodes_live "
            "ON memory_nodes(agent_id, valid_to)"
        )
        # backfill FTS for any node missing from it (e.g. DB created before
        # upgrade). Carry each node's OWN agent_id so a multi-agent DB opened by
        # one agent does not mis-attribute other agents' rows to the opener
        # (which would leak them into the opener's BM25 leg).
        try:
            missing = self._conn.execute(
                "SELECT node_id, agent_id, content, keywords FROM memory_nodes n "
                "WHERE NOT EXISTS (SELECT 1 FROM memory_fts f WHERE f.node_id = n.node_id)"
            ).fetchall()
            for row in missing:
                self._conn.execute(
                    "INSERT INTO memory_fts (node_id, agent_id, content, keywords) VALUES (?, ?, ?, ?)",
                    (row["node_id"], str(row["agent_id"]), str(row["content"]), str(row["keywords"])),
                )
        except sqlite3.Error:
            pass  # FTS is a best-effort leg; never block on it

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
        importance: float | None = None,
    ) -> MemoryNode:
        text = str(content or "").strip()
        if not text:
            raise ValueError("memory node content is required")
        ts = float(timestamp if timestamp is not None else time.time())
        vec = [float(value) for value in list(embedding or [])]
        score = 0.5 if importance is None else max(0.0, min(1.0, float(importance)))

        # Dedup-on-write: a near-identical LIVE restatement is a NOOP — bump its
        # access (recency/frequency reinforcement) instead of storing a duplicate.
        with self._lock:
            dup = self._find_near_duplicate(vec, text)
            if dup is not None:
                self._conn.execute(
                    "UPDATE memory_nodes SET access_count = access_count + 1, "
                    "last_access = ?, base_importance = MAX(base_importance, ?) "
                    "WHERE node_id = ? AND agent_id = ?",
                    (ts, score, dup.node_id, self._agent_id),
                )
                self._conn.commit()
                return dup

        node_id = hashlib.sha256(f"{self._agent_id}:{ts:.9f}:{text}".encode()).hexdigest()[:20]
        node = MemoryNode(
            node_id=node_id,
            content=text,
            timestamp=ts,
            keywords=[str(item).strip() for item in list(keywords or []) if str(item).strip()],
            tags=[str(item).strip() for item in list(tags or []) if str(item).strip()],
            context_description=str(context_description or "").strip(),
            embedding=vec,
            linked_node_ids=[str(item).strip() for item in list(linked_node_ids or []) if str(item).strip()],
            agent_id=self._agent_id,
        )
        row = node.to_row()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_nodes (
                    node_id, agent_id, content, timestamp, keywords, tags,
                    context_description, embedding, linked_node_ids,
                    base_importance, access_count, last_access, valid_from, valid_to
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
                """,
                (
                    row["node_id"], row["agent_id"], row["content"], row["timestamp"],
                    row["keywords"], row["tags"], row["context_description"],
                    row["embedding"], row["linked_node_ids"],
                    score, ts, ts,
                ),
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.execute(
                    "INSERT INTO memory_fts (node_id, agent_id, content, keywords) VALUES (?, ?, ?, ?)",
                    (node_id, self._agent_id, text, " ".join(node.keywords)),
                )
            self._conn.commit()
        return node

    def _find_near_duplicate(self, embedding: list[float], text: str) -> MemoryNode | None:
        """Find a LIVE node that is a near-identical restatement of *text* (high
        cosine AND high token overlap). Used for dedup-on-write NOOP."""
        if not embedding:
            return None
        rows = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE agent_id = ? AND valid_to IS NULL",
            (self._agent_id,),
        ).fetchall()
        best: MemoryNode | None = None
        best_sim = 0.0
        new_tokens = _token_set(text)
        for row in rows:
            node = _row_to_node(row)
            sim = _cosine_similarity(embedding, node.embedding)
            if sim < self._DEDUP_SIM:
                continue
            if _token_overlap(new_tokens, _token_set(node.content)) < self._DEDUP_OVERLAP:
                continue
            if sim > best_sim:
                best_sim, best = sim, node
        return best

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
        """Semantic + effective-importance hybrid over LIVE nodes. The recency
        term in effective importance makes the latest value of a fact outrank
        the stale one. Returned nodes get an access bump (reinforcement).
        Backward-compatible signature; min_score still filters on cosine."""
        return self._ranked_search(query_embedding, None, top_k=top_k, min_score=min_score)

    def node_search_hybrid(
        self,
        query_text: str,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[MemoryNode, float]]:
        """Like node_search but adds a BM25 (FTS5) keyword leg — better for
        exact terms, IDs, and paraphrase recall when embeddings are weak."""
        return self._ranked_search(query_embedding, query_text, top_k=top_k, min_score=min_score)

    def _ranked_search(
        self,
        query_embedding: list[float],
        query_text: str | None,
        *,
        top_k: int,
        min_score: float,
    ) -> list[tuple[MemoryNode, float]]:
        query = [float(value) for value in list(query_embedding or [])]
        if not query:
            return []
        now = time.time()
        bm25 = self._bm25_scores(query_text) if query_text else {}
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_nodes WHERE agent_id = ? AND valid_to IS NULL",
                (self._agent_id,),
            ).fetchall()
        scored: list[tuple[MemoryNode, float, str]] = []
        for row in rows:
            node = _row_to_node(row)
            sem = _cosine_similarity(query, node.embedding)
            if sem < float(min_score):
                continue
            eff = _effective_importance(
                base=_row_float(row, "base_importance", 0.5),
                access_count=_row_int(row, "access_count", 0),
                age_days=max(0.0, (now - _row_float(row, "last_access", node.timestamp)) / 86400.0),
                half_life=self._HALF_LIFE_DAYS,
                w_recency=self._W_RECENCY,
                w_freq=self._W_FREQ,
                freq_cap=self._FREQ_CAP,
            )
            final = (
                self._W_SEMANTIC * sem
                + self._W_EFFECTIVE * eff
                + self._W_BM25 * bm25.get(node.node_id, 0.0)
            )
            scored.append((node, final, node.node_id))

        collapsed = self._temporal_collapse(scored)
        collapsed.sort(key=lambda item: item[1], reverse=True)
        top = collapsed[: max(1, int(top_k))]
        merged = self._expand_one_hop(top) if (top and self._LINK_EXPANSION) else top
        if merged:
            self._bump_access([nid for (_n, _s, nid) in merged], now)
        return [(n, s) for (n, s, _nid) in merged]

    def _expand_one_hop(
        self, top: list[tuple[MemoryNode, float, str]],
    ) -> list[tuple[MemoryNode, float, str]]:
        """Pull nodes linked from the top hits into the result, downweighted.

        One hop only (links of `top` are followed, not links of the pulled nodes),
        with a capped fan-out + total and a seen-set, so cycles and result-set
        explosion are impossible. A linked node rides in at ``_LINK_WEIGHT`` of its
        linker's score, so it surfaces just below the hit that connected it even if
        its own direct similarity fell below ``min_score``.
        """
        in_result = {nid for (_n, _s, nid) in top}
        extras: list[tuple[MemoryNode, float, str]] = []
        for node, score, _nid in top:
            for linked_id in list(node.linked_node_ids or [])[: self._LINK_FANOUT]:
                if linked_id in in_result:
                    continue
                linked = self.node_get(linked_id)
                if linked is None:
                    continue
                in_result.add(linked_id)
                extras.append((linked, score * self._LINK_WEIGHT, linked_id))
                if len(extras) >= self._LINK_EXPANSION_MAX:
                    break
            if len(extras) >= self._LINK_EXPANSION_MAX:
                break
        if not extras:
            return top
        return sorted(top + extras, key=lambda item: item[1], reverse=True)

    def _temporal_collapse(
        self, scored: list[tuple[MemoryNode, float, str]]
    ) -> list[tuple[MemoryNode, float, str]]:
        """Collapse a stale value to the NEWER one that supersedes it: the latest
        value of a fact wins over the value it replaced. Walk newest-first; a node
        is a group representative unless it BOTH is near-duplicate (cosine >=
        COLLAPSE_SIM) to an already-chosen newer rep AND looks like a restatement
        of the SAME fact rather than a second distinct fact about the same entity.

        Embedding cosine alone is NOT enough: two distinct, never-superseded facts
        about the same entity sit close in embedding space (cosine ~0.9) yet must
        both survive. We require, on top of cosine, EITHER high token overlap
        (a near-restatement) OR a shared subject AND an explicit supersession
        marker on the newer node ("moved"/"updated"/"now"/"instead"/...). A bare
        shared subject is deliberately insufficient — distinct same-entity facts
        share the entity token — so those are preserved separately."""
        order = sorted(scored, key=lambda it: it[0].timestamp, reverse=True)
        reps: list[list] = []  # [node, score, node_id, token_set, subject_set]
        for node, score, nid in order:
            node_tokens = _token_set(node.content)
            node_subjects = node_tokens | {k.lower() for k in node.keywords if k}
            marks_update = bool(node_subjects & _SUPERSEDE_MARKERS)
            merged = False
            for rep in reps:
                if _cosine_similarity(node.embedding, rep[0].embedding) < self._COLLAPSE_SIM:
                    continue
                supersedes = _token_overlap(node_tokens, rep[3]) >= self._DEDUP_OVERLAP or (
                    marks_update and bool(node_subjects & rep[4])
                )
                if supersedes:
                    rep[1] = max(rep[1], score)  # inherit the older phrasing's relevance
                    merged = True
                    break
            if not merged:
                reps.append([node, score, nid, node_tokens, node_subjects])
        return [(r[0], r[1], r[2]) for r in reps]

    def _bm25_scores(self, query_text: str) -> dict[str, float]:
        """Normalized BM25 (0..1, higher=better) per node for the query terms."""
        import re as _re
        terms = _re.findall(r"[a-zA-Z0-9_]{2,}", str(query_text or "").lower())
        if not terms:
            return {}
        match = " OR ".join(dict.fromkeys(terms))
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT node_id, bm25(memory_fts) AS rank FROM memory_fts "
                    "WHERE agent_id = ? AND memory_fts MATCH ? ORDER BY rank LIMIT 50",
                    (self._agent_id, match),
                ).fetchall()
        except sqlite3.Error:
            return {}
        # bm25() is lower=better (typically negative); map to 0..1 higher=better
        ranks = [(str(r["node_id"]), float(r["rank"])) for r in rows]
        if not ranks:
            return {}
        worst = max(r for _id, r in ranks)
        best = min(r for _id, r in ranks)
        # Single match (or all-equal ranks): every MATCH row is a genuine keyword
        # hit, so give it the full 1.0 boost rather than collapsing to 0.0 when
        # the span is empty.
        if worst == best:
            return {nid: 1.0 for nid, _ in ranks}
        span = worst - best
        return {nid: (worst - r) / span for nid, r in ranks}

    def _bump_access(self, node_ids: list[str], now: float) -> None:
        if not node_ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE memory_nodes SET access_count = access_count + 1, last_access = ? "
                "WHERE node_id = ? AND agent_id = ?",
                [(now, nid, self._agent_id) for nid in node_ids],
            )
            self._conn.commit()

    def node_invalidate(self, node_id: str, *, at: float | None = None) -> None:
        """Bi-temporal soft-delete: mark a node no longer valid (excluded from
        retrieval) without losing the historical row."""
        normalized = str(node_id or "").strip()
        if not normalized:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE memory_nodes SET valid_to = ? WHERE node_id = ? AND agent_id = ? AND valid_to IS NULL",
                (float(at if at is not None else time.time()), normalized, self._agent_id),
            )
            self._conn.commit()

    def prune(self, max_nodes: int) -> int:
        """Budgeted prune: keep the highest effective-importance LIVE nodes,
        invalidate the rest. Returns the count invalidated. Pure-Python so it
        works on any sqlite build (no SQL math functions required)."""
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, base_importance, access_count, last_access, timestamp "
                "FROM memory_nodes WHERE agent_id = ? AND valid_to IS NULL",
                (self._agent_id,),
            ).fetchall()
        if len(rows) <= max_nodes:
            return 0
        ranked = sorted(
            rows,
            key=lambda r: _effective_importance(
                base=_row_float(r, "base_importance", 0.5),
                access_count=_row_int(r, "access_count", 0),
                age_days=max(0.0, (now - _row_float(r, "last_access", _row_float(r, "timestamp", now))) / 86400.0),
                half_life=self._HALF_LIFE_DAYS, w_recency=self._W_RECENCY,
                w_freq=self._W_FREQ, freq_cap=self._FREQ_CAP,
            ),
            reverse=True,
        )
        to_drop = [str(r["node_id"]) for r in ranked[max_nodes:]]
        for nid in to_drop:
            self.node_invalidate(nid, at=now)
        return len(to_drop)

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
    # Delegate to the shared cosine so a dimension mismatch is projected-and-compared
    # (and logged once) instead of silently scoring 0.0 — which previously hid total
    # recall failure when the embedding backend changed dimension between store/query.
    from core.embedding_service import cosine_similarity as _shared_cosine
    return _shared_cosine(a, b)


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", str(text or "").lower()))


def _token_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard overlap (symmetric): |a∩b| / |a∪b|. Symmetric so a value-changing
    UPDATE ('deadline July 15' -> 'moved deadline to Aug 1') scores below the
    dedup gate and is NOT swallowed as a duplicate — temporal collapse at
    retrieval handles that case instead."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _effective_importance(
    *,
    base: float,
    access_count: int,
    age_days: float,
    half_life: float,
    w_recency: float,
    w_freq: float,
    freq_cap: float,
) -> float:
    """Agent-memory-standard effective importance: base * (W_R*recency + W_F*freq).
    recency = exp(-ln2/half_life * age_days); freq = log1p(access)/log1p(cap)."""
    import math
    recency = math.exp(-(math.log(2.0) / max(1e-6, half_life)) * max(0.0, age_days))
    freq = math.log1p(max(0, access_count)) / math.log1p(max(1.0, freq_cap))
    return max(0.0, min(1.0, float(base))) * (w_recency * recency + w_freq * freq)


def _row_float(row: Any, key: str, default: float) -> float:
    try:
        val = row[key]
        return float(val) if val is not None else float(default)
    except (KeyError, IndexError, TypeError, ValueError):
        return float(default)


def _row_int(row: Any, key: str, default: int) -> int:
    try:
        val = row[key]
        return int(val) if val is not None else int(default)
    except (KeyError, IndexError, TypeError, ValueError):
        return int(default)


__all__ = ["MemoryBlock", "MemoryNode", "NullaMemory"]
