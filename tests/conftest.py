from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest

from apps.nulla_agent import NullaAgent
from core.memory_first_router import ModelExecutionDecision
from core.persistent_memory import (
    conversation_log_path,
    ensure_memory_files,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.public_hive import client as public_hive_client
from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
from core.user_preferences import default_preferences, save_preferences
from storage.db import active_default_db_path, configure_default_db_path, get_connection, reset_default_connection
from storage.migrations import run_migrations

# storage/db.py has no pytest-specific path override, so without this, every test in
# this session would share the SAME on-disk SQLite file a live `apps.nulla_api_server`
# process may be connected to (active_default_db_path() resolves to the real runtime
# data dir). runtime_storage_reset below DELETEs every runtime table - including wallet
# and credit ledgers - before each test, which would wipe rows out from under a running
# server; concurrent writes to the same file from two processes also produced
# intermittent Windows access-violation crashes during full-suite runs. Re-applied at
# the top of runtime_storage_reset (not just once here) so a test that resets the
# override (e.g. test_storage_db_pooling.py calling configure_default_db_path(None))
# can't leave later tests pointed at the live path for the rest of the session.
_TEST_DB_PATH = str(Path(tempfile.mkdtemp(prefix="nulla_pytest_db_")) / "nulla_web0_v2_test.db")

RUNTIME_TABLES = (
    "adaptive_lexicon",
    "audit_log",
    "compute_credit_ledger",
    "contribution_ledger",
    "dialogue_sessions",
    "dialogue_turns",
    "curiosity_runs",
    "curiosity_topics",
    "dna_wallet_ledger",
    "dna_wallet_profiles",
    "dna_wallet_security",
    "response_feedback",
    "event_log_v2",
    "hive_idempotency_keys",
    "knowledge_holders",
    "knowledge_manifests",
    "learning_shards",
    "local_tasks",
    "runtime_checkpoints",
    "runtime_session_events",
    "runtime_sessions",
    "runtime_tool_receipts",
    "session_hive_watch_state",
    "session_memory_policies",
    "shard_reuse_outcomes",
    "swarm_dispatch_budget_events",
    "web_notes",
)

MEMORY_TEMPLATE = (
    "# NULLA Persistent Memory\n\n"
    "## Identity\n\n"
    "- **My name**: NULLA\n"
    "- **Owner's name**: unknown\n\n"
    "## Privacy Pact\n\n"
    "- Not set yet.\n\n"
    "## Learned Knowledge\n\n"
)

FORBIDDEN_CHAT_LEAKS = (
    "invalid tool payload",
    "missing_intent",
    "i won't fake it",
    "traceback",
)


def normalize_response_text(text: str) -> str:
    return " ".join(str(text or "").split())


def make_stub_context(
    *,
    local_candidates: list | None = None,
    swarm_metadata: list | None = None,
    retrieval_confidence_score: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        local_candidates=list(local_candidates or []),
        swarm_metadata=list(swarm_metadata or []),
        retrieval_confidence_score=float(retrieval_confidence_score),
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=float(retrieval_confidence_score),
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )


def memory_hit_decision(*, output_text: str = "", trust_score: float = 0.82) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="memory_hit",
        task_hash="test-memory-hit",
        output_text=output_text,
        confidence=trust_score,
        trust_score=trust_score,
        used_model=False,
        validation_state="not_run",
    )


@pytest.fixture(autouse=True)
def runtime_storage_reset() -> None:
    configure_default_db_path(_TEST_DB_PATH)
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    ensure_memory_files()
    reset_runtime_continuity_state()

    conn = get_connection()
    try:
        for table in RUNTIME_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    reset_default_connection()

    memory_path().write_text(MEMORY_TEMPLATE, encoding="utf-8")
    conversation_log_path().write_text("", encoding="utf-8")
    memory_entries_path().write_text("", encoding="utf-8")
    session_summaries_path().write_text("", encoding="utf-8")
    user_heuristics_path().write_text("", encoding="utf-8")
    operator_dense_profile_path().write_text("{}", encoding="utf-8")
    save_preferences(default_preferences())


@pytest.fixture(autouse=True)
def block_live_public_hive_network(monkeypatch):
    real_urlopen = public_hive_client.urllib.request.urlopen

    def guarded_urlopen(request, *args, **kwargs):
        target = getattr(request, "full_url", request)
        host = str(urlsplit(str(target or "")).hostname or "").strip().lower()
        if host in {"", "127.0.0.1", "localhost", "::1"}:
            return real_urlopen(request, *args, **kwargs)
        raise URLError(f"public hive live network blocked under pytest for host '{host or 'unknown'}'")

    monkeypatch.setattr(public_hive_client.urllib.request, "urlopen", guarded_urlopen)


@pytest.fixture(autouse=True)
def block_live_local_ollama_under_pytest(monkeypatch):
    if os.environ.get("NULLA_ALPHA_LIVE_SOAK") == "1" or os.environ.get("NULLA_ALLOW_LIVE_OLLAMA_TESTS") == "1":
        return

    real_socket_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def _is_blocked_ollama_target(host: object, port: object) -> bool:
        normalized_host = str(host or "").strip().lower()
        return normalized_host in {"localhost", "127.0.0.1", "::1"} and int(port or 0) == 11434

    def guarded_socket_connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2 and _is_blocked_ollama_target(address[0], address[1]):
            raise AssertionError(
                "pytest attempted to reach live local Ollama on 127.0.0.1:11434 without an explicit live-test opt-in"
            )
        return real_socket_connect(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and len(address) >= 2 and _is_blocked_ollama_target(address[0], address[1]):
            raise AssertionError(
                "pytest attempted to reach live local Ollama on 127.0.0.1:11434 without an explicit live-test opt-in"
            )
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture(autouse=True)
def default_test_policy_disables_web_fallback():
    """Force `system.allow_web_fallback` off for every test unless a test
    explicitly opts in via the `enable_web` fixture below.

    The shipped product default (config/default_policy.yaml) is now
    `allow_web_fallback: true` so real users get real DuckDuckGo-backed
    answers out of the box. Tests must stay network-isolated regardless of
    that shipped default, so this autouse fixture pins the test-session
    baseline back to False; `enable_web` (opt-in, per test) flips it back on
    for the specific tests that intentionally exercise live web lookups.
    """
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous_cache


@pytest.fixture
def enable_web():
    """Explicitly turn on the opt-in web lookup for a single test.

    Web access is forced off for tests by default (see
    default_test_policy_disables_web_fallback above); tests that exercise the
    live web tools must deliberately enable it here. This flips
    `system.allow_web_fallback` in the cached policy and restores the prior
    cache afterwards.
    """
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = True
    system["local_only_mode"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous_cache


@pytest.fixture
def context_result_factory():
    return make_stub_context


@pytest.fixture
def response_normalizer():
    return normalize_response_text


@pytest.fixture
def forbidden_chat_leaks():
    return FORBIDDEN_CHAT_LEAKS


@pytest.fixture
def make_agent(monkeypatch, context_result_factory):
    def factory(*, backend_name: str = "test-backend", device: str = "test-device", persona_id: str = "default") -> NullaAgent:
        agent = NullaAgent(backend_name=backend_name, device=device, persona_id=persona_id)
        monkeypatch.setattr(agent, "_sync_public_presence", lambda *args, **kwargs: None)
        monkeypatch.setattr(agent, "_start_public_presence_heartbeat", lambda *args, **kwargs: None)
        monkeypatch.setattr(agent, "_start_idle_commons_loop", lambda *args, **kwargs: None)
        agent.start()
        agent.context_loader.load = Mock(return_value=context_result_factory())  # type: ignore[assignment]
        return agent

    return factory
