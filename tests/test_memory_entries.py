from __future__ import annotations

import pytest

from core.runtime_paths import configure_runtime_home
from core.memory.entries import (
    add_memory_fact,
    combined_memory_entries,
    forget_memory,
    keyword_tokens_filtered,
    load_memory_excerpt,
    recency_score,
    recent_conversation_events,
    replace_name_memory,
    search_relevant_memory,
    summarize_memory,
)
from core.memory.files import (
    conversation_log_path,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)


@pytest.fixture(autouse=True)
def isolated_memory_home(tmp_path):
    configure_runtime_home(tmp_path / "runtime-home")
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    for path in (
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()
    memory_path().write_text(
        "# NULLA Persistent Memory\n\n"
        "## Identity\n\n"
        "- **My name**: NULLA\n"
        "- **Owner's name**: unknown\n\n"
        "## Privacy Pact\n\n"
        "- Not set yet.\n\n"
        "## Learned Knowledge\n\n"
        "<!-- New memories append below -->\n",
        encoding="utf-8",
    )
    yield
    configure_runtime_home(None)


def test_memory_entries_roundtrip_and_forget() -> None:
    assert add_memory_fact("Operator uses Python and official docs.", category="fact", session_id="s1") is True
    assert add_memory_fact("Operator uses Python and official docs.", category="fact", session_id="s1") is False

    summary = "\n".join(summarize_memory(limit=8)).lower()
    assert "python" in summary
    hits = search_relevant_memory("python docs", topic_hints=["python"], limit=4)
    assert hits

    removed = forget_memory("python")
    assert removed == 1
    assert "python" not in "\n".join(summarize_memory(limit=8)).lower()


def test_load_memory_excerpt_and_name_replacement_contract() -> None:
    assert add_memory_fact("Operator name is Pedro.", category="name", session_id="s2") is True
    replace_name_memory("Operator name is SLS.")

    excerpt = load_memory_excerpt(max_chars=400)
    assert "SLS" in excerpt
    assert "Pedro" not in excerpt
    assert not any("pedro" in str(row.get("text") or "").lower() for row in combined_memory_entries())


def test_recent_conversation_events_and_scoring_helpers() -> None:
    conversation_log_path().write_text(
        '{"session_id":"alpha","user":"one","assistant":"a"}\n'
        '{"session_id":"beta","user":"two","assistant":"b"}\n'
        '{"session_id":"alpha","user":"three","assistant":"c"}\n',
        encoding="utf-8",
    )

    events = recent_conversation_events("alpha", limit=2)
    assert [row["user"] for row in events] == ["one", "three"]
    assert keyword_tokens_filtered("build a brutally honest python telegram bot")[:2]
    assert recency_score("") == 0.35
