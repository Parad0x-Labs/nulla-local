"""
storage/task_offer_store.py
===========================
Thin SQLite wrapper over the existing `task_offers` table (defined in migrations.py).
Provides the read/write operations needed by the task market routes and the
Web0 worker bid/claim/complete loop.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def list_open_task_offers(*, limit: int = 50) -> list[dict[str, Any]]:
    """Return open task offers ordered by priority + deadline."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, parent_peer_id, task_type, subtask_type, summary,
                   required_capabilities_json, reward_hint_json,
                   max_helpers, priority, deadline_ts, status, created_at
            FROM task_offers
            WHERE status = 'open'
            ORDER BY
                CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                deadline_ts ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        d = dict(row)
        for key in ("required_capabilities_json", "reward_hint_json"):
            raw = d.pop(key, None)
            parsed_key = key.removesuffix("_json")
            try:
                d[parsed_key] = json.loads(raw) if raw else {}
            except Exception:
                d[parsed_key] = {}
        result.append(d)
    return result


def claim_task_offer(task_id: str, helper_peer_id: str) -> bool:
    """
    Atomically mark an open offer as claimed by helper_peer_id.
    Returns True if the offer was successfully claimed (was 'open').
    Returns False if already claimed/completed or not found.
    """
    now = _utcnow_iso()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE task_offers
            SET status = 'claimed', updated_at = ?
            WHERE task_id = ? AND status = 'open'
            """,
            (now, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def complete_task_offer(task_id: str, result_hash: str) -> bool:
    """Mark a claimed offer as completed."""
    now = _utcnow_iso()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE task_offers
            SET status = 'completed', updated_at = ?
            WHERE task_id = ? AND status = 'claimed'
            """,
            (now, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_task_offer(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    for key in ("required_capabilities_json", "reward_hint_json"):
        raw = d.pop(key, None)
        parsed_key = key.removesuffix("_json")
        try:
            d[parsed_key] = json.loads(raw) if raw else {}
        except Exception:
            d[parsed_key] = {}
    return d


__all__ = [
    "claim_task_offer",
    "complete_task_offer",
    "get_task_offer",
    "list_open_task_offers",
]
