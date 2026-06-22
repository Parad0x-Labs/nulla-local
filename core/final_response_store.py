from __future__ import annotations

from typing import Any

from storage.db import get_connection


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_anchored_signature_column(conn: Any) -> None:
    """Additive, idempotent migration for the on-chain anchor tx signature.

    The base schema (storage/migrations.py) predates anchor capture, so older
    databases lack this column. We add it lazily and only when missing, leaving
    every existing row + value untouched (nullable, no default).
    """
    if "anchored_signature" not in _table_columns(conn, "finalized_responses"):
        conn.execute("ALTER TABLE finalized_responses ADD COLUMN anchored_signature TEXT")


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
