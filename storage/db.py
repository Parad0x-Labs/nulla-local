from __future__ import annotations

import contextlib
import functools
import sqlite3
import threading
from pathlib import Path

from core.runtime_paths import active_data_dir

# Keep the public default path constant import-safe. The real default connection
# target is resolved via active runtime home unless an explicit override is set.
DEFAULT_DB_PATH = str(((Path(__file__).resolve().parents[1] / ".nulla_local" / "data") / "nulla_web0_v2.db").resolve())
_DEFAULT_DB_PATH_OVERRIDE: str | None = None

_thread_local = threading.local()


@functools.lru_cache(maxsize=256)
def _resolve_db_path_cached(db_path_str: str) -> str:
    path = Path(db_path_str).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _resolve_db_path(db_path: str | Path) -> str:
    # get_connection() below calls this multiple times per invocation (including on
    # its cached-connection fast path), so on Windows this was issuing a real
    # GetFinalPathNameByHandleW syscall (via Path.resolve()) thousands of times
    # across a full test run - which produced intermittent access-violation crashes
    # under sustained load. The resolution is idempotent for a given input string
    # within a process lifetime, so cache it.
    return _resolve_db_path_cached(str(db_path))


def _runtime_default_db_path() -> str:
    return str((active_data_dir() / "nulla_web0_v2.db").resolve())


def _make_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a fresh SQLite connection with WAL mode and safe defaults."""
    conn = sqlite3.connect(_resolve_db_path(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class _PooledConnection:
    """Thin wrapper that makes close() a no-op so callers cannot kill the cached connection."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def close(self) -> None:
        # Keep pooled connections alive, but never return with an open tx.
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except Exception:
            return

    def _real_close(self) -> None:
        self._conn.close()

    def __enter__(self) -> _PooledConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            try:
                if self._conn.in_transaction:
                    self._conn.rollback()
            except Exception:
                pass
            return False
        try:
            if self._conn.in_transaction:
                self._conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                self._conn.rollback()
        return False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def reset_default_connection() -> None:
    """Drop the cached default SQLite connection for the current thread."""
    cached: _PooledConnection | None = getattr(_thread_local, "default_conn", None)
    if cached is None:
        return
    with contextlib.suppress(Exception):
        if cached._conn.in_transaction:
            cached._conn.rollback()
    with contextlib.suppress(Exception):
        cached._real_close()
    _thread_local.default_conn = None


def configure_default_db_path(db_path: str | Path | None) -> None:
    global _DEFAULT_DB_PATH_OVERRIDE
    reset_default_connection()
    _DEFAULT_DB_PATH_OVERRIDE = None if db_path is None else _resolve_db_path(db_path)


def active_default_db_path() -> str:
    return _DEFAULT_DB_PATH_OVERRIDE or _resolve_db_path(_runtime_default_db_path())


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a thread-local reusable SQLite connection.

    Connections are cached per-thread for the default DB path to avoid
    the overhead of re-opening and re-running PRAGMAs on every call.
    Non-default paths always get a fresh connection.
    """
    requested_resolved = _resolve_db_path(db_path)
    base_default_resolved = _resolve_db_path(DEFAULT_DB_PATH)
    default_resolved = active_default_db_path()
    effective_db_path = (
        default_resolved
        if requested_resolved in {base_default_resolved, default_resolved}
        else requested_resolved
    )
    resolved = _resolve_db_path(effective_db_path)

    # Non-default path: always fresh (used for test isolation etc.)
    if resolved != default_resolved:
        return _make_connection(resolved)

    # Thread-local reuse for the default path
    cached: _PooledConnection | None = getattr(_thread_local, "default_conn", None)
    if cached is not None:
        try:
            cached._conn.execute("SELECT 1")
            return cached  # type: ignore[return-value]
        except Exception:
            with contextlib.suppress(Exception):
                cached._real_close()
            _thread_local.default_conn = None

    conn = _make_connection(resolved)
    pooled = _PooledConnection(conn)
    _thread_local.default_conn = pooled
    return pooled  # type: ignore[return-value]


def execute_query(query: str, params: tuple = (), db_path: str | Path = DEFAULT_DB_PATH) -> list:
    """Run a parameterized query and close the connection immediately after use."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if query.strip().upper().startswith("SELECT"):
            return [dict(row) for row in cursor.fetchall()]
        conn.commit()
        return [{"status": "success", "lastrowid": cursor.lastrowid}]
    finally:
        conn.close()


def init_schema(db_path: str | Path = DEFAULT_DB_PATH):
    """
    Initialize the exact V2 SQLite schema defined in the Reference Architecture.
    """
    from storage.migrations import SCHEMA_SQL

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        conn.commit()
        return
    finally:
        conn.close()


def healthcheck(db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    try:
        conn = get_connection(db_path)
        try:
            conn.execute("SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        return False
