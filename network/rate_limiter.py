from __future__ import annotations

import hashlib
import os
from collections import defaultdict, deque
from threading import RLock
from time import time

from core import audit_logger, policy_engine
from network import quarantine

_EVENTS: dict[str, deque[float]] = defaultdict(deque)
_BREACHES: dict[str, int] = defaultdict(int)
_LOCK = RLock()
_WINDOW_SECONDS = 60.0

# Per-process salt for the anonymous nullifier. Generated once at import; it
# never leaves the process, so a nullifier cannot be reproduced or correlated
# across restarts/peers. Pure-local: no crate, no network, no on-chain call.
_ANON_SALT = os.urandom(32)
_ANON_KEY_PREFIX = "anon:"


def anon_nullifier(client_token: str) -> str:
    """Local sha256 nullifier for an anonymous client.

    sha256(per-process salt || client_token) -> hex. The same token yields the
    same nullifier within a process (so the window throttles it) while the raw
    token is never stored and is not recoverable from the nullifier.
    """
    digest = hashlib.sha256(_ANON_SALT + str(client_token).encode("utf-8")).hexdigest()
    return _ANON_KEY_PREFIX + digest


def allow(peer_id: str, *, allow_anon: bool = False) -> bool:
    """
    Sliding-window rate limiter.
    Returns True if the peer is allowed to continue, False if throttled.

    With ``allow_anon=True`` the window is re-keyed off an in-process sha256
    nullifier of ``peer_id`` (treated as an opaque client token) instead of the
    raw id. This lets an anonymous caller be throttled without the limiter
    holding or correlating its identity, the peer-quarantine subsystem (which is
    identity-keyed) is bypassed for these keys. Default behaviour is unchanged.
    """
    if not allow_anon:
        if quarantine.is_peer_quarantined(peer_id):
            return False
        return _consume(peer_id, target_type="peer", quarantine_on_abuse=True)

    return _consume(anon_nullifier(peer_id), target_type="nullifier", quarantine_on_abuse=False)


def allow_anonymous(client_token: str) -> bool:
    """Throttle an anonymous client by its locally derived nullifier.

    Convenience wrapper over ``allow(..., allow_anon=True)``.
    """
    return allow(client_token, allow_anon=True)


def _consume(key: str, *, target_type: str, quarantine_on_abuse: bool) -> bool:
    limit = int(policy_engine.get("network.max_requests_per_minute_per_peer", 30))
    strike_limit = int(policy_engine.get("network.max_failed_messages_before_quarantine", 3))
    now = time()

    with _LOCK:
        dq = _EVENTS[key]

        while dq and (now - dq[0]) > _WINDOW_SECONDS:
            dq.popleft()

        if len(dq) >= limit:
            _BREACHES[key] += 1
            audit_logger.log(
                "rate_limit_exceeded",
                target_id=key,
                target_type=target_type,
                details={"breaches": _BREACHES[key], "limit": limit},
            )

            if quarantine_on_abuse and _BREACHES[key] >= strike_limit:
                quarantine.quarantine_peer(key, "rate_limit_abuse")

            return False

        dq.append(now)
        return True


def reset_peer(peer_id: str) -> None:
    with _LOCK:
        _EVENTS.pop(peer_id, None)
        _BREACHES.pop(peer_id, None)


def reset_anonymous(client_token: str) -> None:
    """Clear the sliding window for an anonymous client's nullifier."""
    key = anon_nullifier(client_token)
    with _LOCK:
        _EVENTS.pop(key, None)
        _BREACHES.pop(key, None)
