"""
core/web0_mesh_registry.py
==========================
In-process Web0 worker registry.

NULLA instances POST to /v1/workers/announce when they boot.
The registry tracks them with a TTL and exposes a list endpoint.
No external dependency — pure Python dict + threading lock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

_WORKER_TTL_SECONDS = 300  # 5 min — workers must re-announce to stay visible

_lock = threading.Lock()
_workers: dict[str, _WorkerEntry] = {}


@dataclass
class _WorkerEntry:
    worker_id: str
    provider_ids: list[str]
    top_tps: float
    top_tier: str
    context_window: int
    tools: list[str]
    price_per_token_usdc: float
    privacy_mode: str
    announced_at: float
    expires_at: float


def announce_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Accept a Web0CapabilityManifest payload and store it.
    Returns {ok, worker_id, expires_at}.
    """
    worker_id = str(payload.get("worker_id") or "").strip()
    if not worker_id:
        return {"ok": False, "error": "worker_id required"}

    now = time.time()
    entry = _WorkerEntry(
        worker_id=worker_id,
        provider_ids=[str(p) for p in list(payload.get("provider_ids") or [])],
        top_tps=float(payload.get("top_tps") or 0.0),
        top_tier=str(payload.get("top_tier") or "drone"),
        context_window=int(payload.get("context_window") or 32768),
        tools=[str(t) for t in list(payload.get("tools") or [])],
        price_per_token_usdc=float(payload.get("price_per_token_usdc") or 0.000001),
        privacy_mode=str(payload.get("privacy_mode") or "plain"),
        announced_at=float(payload.get("announced_at") or now),
        expires_at=now + _WORKER_TTL_SECONDS,
    )
    with _lock:
        _workers[worker_id] = entry

    return {
        "ok": True,
        "worker_id": worker_id,
        "expires_at": entry.expires_at,
        "ttl_seconds": _WORKER_TTL_SECONDS,
    }


def list_workers(*, active_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
    """Return visible workers, optionally filtered to non-expired ones."""
    now = time.time()
    with _lock:
        entries = list(_workers.values())

    if active_only:
        entries = [e for e in entries if e.expires_at > now]

    entries.sort(key=lambda e: e.top_tps, reverse=True)
    rows = []
    for e in entries[:limit]:
        d = asdict(e)
        d["active"] = e.expires_at > now
        rows.append(d)
    return rows


def get_worker(worker_id: str) -> dict[str, Any] | None:
    with _lock:
        entry = _workers.get(worker_id)
    if entry is None:
        return None
    d = asdict(entry)
    d["active"] = entry.expires_at > time.time()
    return d


def evict_expired() -> int:
    """Remove stale entries; returns count removed."""
    now = time.time()
    with _lock:
        stale = [wid for wid, e in _workers.items() if e.expires_at <= now]
        for wid in stale:
            del _workers[wid]
    return len(stale)


__all__ = [
    "announce_worker",
    "evict_expired",
    "get_worker",
    "list_workers",
]
