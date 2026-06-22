from __future__ import annotations

import sqlite3
import threading
from typing import Any

from storage.db import get_connection

# Serializes the defensive column-ensure across threads so two concurrent
# background anchor writers can never both issue the ADD COLUMN at once.
_ENSURE_COLUMN_LOCK = threading.Lock()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_anchored_signature_column(conn: Any) -> None:
    """Additive, idempotent + thread-safe ensure for the anchor tx signature column.

    The current schema (storage/migrations.py) creates ``anchored_signature`` in
    the ``finalized_responses`` DDL and via the additive migration block, so on a
    migrated database this ensure finds the column already present and does
    nothing. It survives only as a defensive fallback for a connection that
    somehow predates that migration.

    Two background anchor threads can call this concurrently. Without coordination
    both could see the column missing and both run ``ALTER TABLE ADD COLUMN`` —
    the second raising ``duplicate column name``. We take a process-wide lock and
    treat that specific OperationalError as success so the add is effectively
    idempotent and never propagates as an error that would lose the signature.
    """
    with _ENSURE_COLUMN_LOCK:
        if "anchored_signature" in _table_columns(conn, "finalized_responses"):
            return
        try:
            conn.execute("ALTER TABLE finalized_responses ADD COLUMN anchored_signature TEXT")
        except sqlite3.OperationalError as exc:
            # A racing writer (another process, or a connection that bypassed the
            # lock) already added the column. Anything else is a real failure.
            if "duplicate column name" not in str(exc).lower():
                raise


def store_final_response(parent_task_id: str, raw: str, rendered: str, status: str, confidence: float) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO finalized_responses (
                parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(parent_task_id) DO UPDATE SET
                raw_synthesized_text = excluded.raw_synthesized_text,
                rendered_persona_text = excluded.rendered_persona_text,
                status_marker = excluded.status_marker,
                confidence_score = excluded.confidence_score,
                created_at = CURRENT_TIMESTAMP
            """,
            (parent_task_id, raw, rendered, status, confidence)
        )
        conn.commit()
    finally:
        conn.close()


def set_anchored_signature(parent_task_id: str, signature: str) -> bool:
    """Persist the on-chain anchor tx signature on an already-finalized row.

    Additive: only updates the new ``anchored_signature`` column, leaving the
    synthesized/rendered text, status, and confidence untouched. Returns True
    when a matching row was updated, False otherwise (e.g. unknown task or empty
    signature) so the caller never has to assume success.
    """
    if not parent_task_id or not signature:
        return False
    conn = get_connection()
    try:
        _ensure_anchored_signature_column(conn)
        cursor = conn.execute(
            "UPDATE finalized_responses SET anchored_signature = ? WHERE parent_task_id = ?",
            (signature, parent_task_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_final_response(parent_task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        _ensure_anchored_signature_column(conn)
        row = conn.execute(
            "SELECT * FROM finalized_responses WHERE parent_task_id = ?",
            (parent_task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
