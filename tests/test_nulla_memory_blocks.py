from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.fact_extractor import FactExtractor, stable_text_embedding
from core.memory_prompt_builder import MemoryPromptBuilder, apply_memory_prefix_to_messages
from core.nulla_memory import NullaMemory
from core.prompt_normalizer import normalize_prompt
from core.tiered_context_loader import _source_allows_private_context
from core.web.api.runtime import RuntimeServices, _memory_recall_response, schedule_memory_extraction


def test_nulla_memory_persists_blocks_and_nodes(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path, agent_id="nulla")
    memory.block_write("user_profile", "Name: Loop")
    memory.block_append("preferences", "Prefers direct answers")
    memory.block_append("preferences", "Prefers direct answers")
    embedding = stable_text_embedding("Loop prefers direct local-first answers")
    node = memory.node_store(
        content="Loop prefers direct local-first answers",
        keywords=["loop", "direct"],
        tags=["preferences"],
        context_description="User prefers direct local-first answers.",
        embedding=embedding,
    )
    memory.close()

    reopened = NullaMemory(runtime_home=tmp_path, agent_id="nulla")
    try:
        assert reopened.block_read("user_profile") == "Name: Loop"
        assert reopened.block_read("preferences") == "Prefers direct answers"
        assert reopened.node_get(node.node_id) is not None
        hits = reopened.node_search(stable_text_embedding("direct local answers"), top_k=3, min_score=0.1)
        assert hits
        assert hits[0][0].node_id == node.node_id
    finally:
        reopened.close()


def test_fact_extractor_applies_safe_facts_and_rejects_secret_like_content(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: """
        {
          "facts": [
            {"action": "ADD", "block": "user_profile", "content": "Name: Loop"},
            {"action": "ADD", "block": "preferences", "content": "Prefers blunt concise answers"},
            {"action": "ADD", "block": "preferences", "content": "api_key: sk-proj-should-not-store-123456789"},
            {"action": "ADD", "block": "unknown_block", "content": "Should not land"}
          ]
        }
        """,
    )

    applied = extractor.run_sync(
        [
            {"role": "user", "content": "My name is Loop and I prefer blunt concise answers."},
            {"role": "assistant", "content": "Stored."},
        ]
    )

    assert ("user_profile", "Name: Loop") in {(fact.block, fact.content) for fact in applied}
    assert ("preferences", "Prefers blunt concise answers") in {(fact.block, fact.content) for fact in applied}
    assert memory.block_read("user_profile") == "Name: Loop"
    preference_block = str(memory.block_read("preferences") or "")
    assert "Prefers blunt concise answers" in preference_block
    assert "Answer style: blunt concise" in preference_block
    assert memory.node_count() >= 2
    memory.close()


def test_fact_extractor_rule_guard_catches_stable_preferences_when_model_misses(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: '{"facts":[{"action":"NOOP"}]}',
    )

    applied = extractor.run_sync(
        [
            {
                "role": "user",
                "content": (
                    "Stable memory facts: my name is Loop. "
                    "My answer-style preference is concise and direct. "
                    "My active project codename is GOLDEN_LOOP_616."
                ),
            },
            {"role": "assistant", "content": "Stored."},
        ]
    )

    assert {(fact.block, fact.content) for fact in applied} >= {
        ("user_profile", "Name: Loop"),
        ("preferences", "Answer style: concise and direct"),
        ("project_context", "Active project codename: GOLDEN_LOOP_616"),
    }
    assert "Name: Loop" in str(memory.block_read("user_profile"))
    assert "Answer style: concise and direct" in str(memory.block_read("preferences"))
    assert "Active project codename: GOLDEN_LOOP_616" in str(memory.block_read("project_context"))
    memory.close()


def test_fact_extractor_prioritizes_deterministic_facts_over_noisy_model_budget(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: """
        {"facts":[
          {"action":"ADD","block":"recent_context","content":"Transient task note one"},
          {"action":"ADD","block":"recent_context","content":"Transient task note two"},
          {"action":"ADD","block":"recent_context","content":"Transient task note three"},
          {"action":"ADD","block":"recent_context","content":"Transient task note four"},
          {"action":"ADD","block":"recent_context","content":"Transient task note five"}
        ]}
        """,
    )

    applied = extractor.run_sync(
        [
            {
                "role": "user",
                "content": (
                    "Stable memory facts: my name is Loop. "
                    "My answer-style preference is concise and direct. "
                    "My active project codename is GOLDEN_LOOP_616."
                ),
            },
        ]
    )

    assert [(fact.block, fact.content) for fact in applied[:3]] == [
        ("user_profile", "Name: Loop"),
        ("preferences", "Answer style: concise and direct"),
        ("project_context", "Active project codename: GOLDEN_LOOP_616"),
    ]
    assert memory.block_read("user_profile") == "Name: Loop"
    assert memory.block_read("project_context") == "Active project codename: GOLDEN_LOOP_616"
    assert "Answer style: concise and direct" in str(memory.block_read("preferences"))
    memory.close()


def test_fact_extractor_does_not_create_duplicate_nodes_for_existing_block_lines(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: '{"facts":[{"action":"ADD","block":"user_profile","content":"Name: Loop"}]}',
    )

    applied = extractor.run_sync([{"role": "user", "content": "My name is Loop."}])

    assert applied == []
    assert memory.block_read("user_profile") == "Name: Loop"
    assert memory.node_count() == 0
    memory.close()


def test_fact_extractor_keeps_first_singleton_fact_when_model_conflicts(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: """
        {"facts":[
          {"action":"ADD","block":"user_profile","content":"Name: Loop"},
          {"action":"ADD","block":"preferences","content":"Answer style: verbose"},
          {"action":"ADD","block":"project_context","content":"Active project codename: STALE_CODE"}
        ]}
        """,
    )

    applied = extractor.run_sync(
        [
            {
                "role": "user",
                "content": (
                    "Stable memory facts: my name is Regression Loop. "
                    "My answer-style preference is tight factual. "
                    "My active project codename is MEMORY_PROBE_616."
                ),
            }
        ]
    )

    assert {(fact.block, fact.content) for fact in applied} == {
        ("user_profile", "Name: Regression Loop"),
        ("preferences", "Answer style: tight factual"),
        ("project_context", "Active project codename: MEMORY_PROBE_616"),
    }
    assert memory.block_read("user_profile") == "Name: Regression Loop"
    assert memory.block_read("preferences") == "Answer style: tight factual"
    assert memory.block_read("project_context") == "Active project codename: MEMORY_PROBE_616"
    memory.close()


def test_fact_extractor_replaces_existing_singleton_when_user_updates_it(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: '{"facts":[{"action":"NOOP"}]}',
    )

    applied = extractor.run_sync([{"role": "user", "content": "Stable memory facts: my name is Regression Loop."}])

    assert [(fact.block, fact.content) for fact in applied] == [("user_profile", "Name: Regression Loop")]
    assert memory.block_read("user_profile") == "Name: Regression Loop"
    assert memory.node_count() == 1
    memory.close()


def test_fact_extractor_rule_guard_ignores_questions_and_assistant_claims(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: '{"facts":[{"action":"NOOP"}]}',
    )

    applied = extractor.run_sync(
        [
            {
                "role": "user",
                "content": "What active project codename is stored?",
            },
            {
                "role": "assistant",
                "content": "The active project codename is WRONG_ASSISTANT_VALUE.",
            },
        ]
    )

    assert applied == []
    assert memory.all_block_names() == []
    assert memory.node_count() == 0
    memory.close()


def test_fact_extractor_update_and_delete_modify_named_blocks(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("preferences", "Prefers long answers\nPrefers browser screenshots")
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _conversation: """
        {"facts":[
          {"action":"UPDATE","block":"preferences","old":"Prefers long answers","new":"Prefers concise answers"},
          {"action":"DELETE","block":"preferences","content":"Prefers browser screenshots"}
        ]}
        """,
    )

    applied = extractor.run_sync([{"role": "user", "content": "Update my preference."}])

    assert [fact.action for fact in applied] == ["UPDATE", "DELETE"]
    assert memory.block_read("preferences") == "Prefers concise answers"
    memory.close()


def test_memory_prompt_builder_and_message_injection(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    memory.block_write("project_context", "Building Web0 and NULLA local-first runtime")
    node = memory.node_store(
        content="Web0 is a local-first private web stack.",
        keywords=["web0", "local-first"],
        tags=["project_context"],
        context_description="Web0 is a local-first private web stack.",
        embedding=stable_text_embedding("tell me about web0"),
    )

    prefix = MemoryPromptBuilder(memory).build_prefix(query="tell me about web0")
    assert "## NULLA Memory" in prefix
    assert "Name: Loop" in prefix
    assert "Building Web0" in prefix
    assert node.context_description in prefix

    request = SimpleNamespace(
        prompt="tell me about web0",
        metadata={
            "memory_prompt": {
                "enabled": True,
                "runtime_home": str(tmp_path),
                "agent_id": "nulla",
            }
        },
    )
    messages = apply_memory_prefix_to_messages(
        [{"role": "system", "content": "You are NULLA."}, {"role": "user", "content": "tell me about web0"}],
        request,
    )
    assert messages[0]["content"].startswith("## NULLA Memory")
    assert "You are NULLA." in messages[0]["content"]
    memory.close()


def test_openai_adapter_injects_memory_prefix_into_native_ollama_payload(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    memory.close()
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            metadata={"runtime_family": "ollama", "deployment_class": "local"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "timeout_seconds": 5.0,
                "temperature": 0.4,
                "think": False,
            },
        )
    )
    request = ModelRequest(
        task_kind="conversation",
        prompt="what is my name?",
        system_prompt="You are NULLA.",
        messages=[
            {"role": "system", "content": "You are NULLA."},
            {"role": "user", "content": "what is my name?"},
        ],
        metadata={
            "memory_prompt": {
                "enabled": True,
                "runtime_home": str(tmp_path),
                "agent_id": "nulla",
            }
        },
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "Your name is Loop."}, "eval_count": 5}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        result = adapter.run_text_task(request)

    payload = post_mock.call_args.kwargs["json"]
    assert result.output_text == "Your name is Loop."
    assert payload["messages"][0]["content"].startswith("## NULLA Memory")
    assert "Name: Loop" in payload["messages"][0]["content"]
    assert payload["messages"][0]["content"].endswith("You are NULLA.")


def test_prompt_normalizer_enables_memory_for_direct_chat_but_not_exact_or_group() -> None:
    direct = _normalized_chat_request("what do you know about me?", source_context={"surface": "openclaw", "platform": "openclaw"})
    assert direct.metadata["memory_prompt"]["enabled"] is True
    assert direct.metadata["chat_truth_prompt"]["memory_prompt_enabled"] is True

    exact = _normalized_chat_request("Reply with exactly GREEN and nothing else.", source_context={"surface": "openclaw", "platform": "openclaw"})
    assert exact.metadata["memory_prompt"]["enabled"] is False

    group = _normalized_chat_request("what do you know about me?", source_context={"surface": "channel", "platform": "discord", "is_group": True})
    assert group.metadata["memory_prompt"]["enabled"] is False

    forced_group = _normalized_chat_request(
        "what do you know about me?",
        source_context={"surface": "channel", "platform": "discord", "is_group": True, "memory_prompt_enabled": True},
    )
    assert forced_group.metadata["memory_prompt"]["enabled"] is False


def test_source_context_private_memory_gate_blocks_group_overrides() -> None:
    assert _source_allows_private_context({"surface": "openclaw", "platform": "openclaw"}) is True
    assert _source_allows_private_context({"surface": "openclaw", "platform": "openclaw", "private_context_enabled": False}) is False
    assert (
        _source_allows_private_context(
            {
                "surface": "channel",
                "platform": "discord",
                "is_group": True,
                "private_context_enabled": True,
            }
        )
        is False
    )


def test_runtime_schedules_async_fact_extraction_only_for_direct_context(tmp_path) -> None:
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    with mock.patch("core.fact_extractor.FactExtractor") as extractor_cls:
        schedule_memory_extraction(
            runtime,
            user_text="My name is Loop.",
            assistant_output="Stored.",
            session_id="openclaw:test",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    extractor_cls.return_value.trigger_async.assert_called_once()

    with mock.patch("core.fact_extractor.FactExtractor") as extractor_cls:
        schedule_memory_extraction(
            runtime,
            user_text="My name is Loop.",
            assistant_output="Stored.",
            session_id="discord:test",
            source_context={"surface": "channel", "platform": "discord", "is_group": True},
        )

    extractor_cls.assert_not_called()

    with mock.patch("core.fact_extractor.FactExtractor") as extractor_cls:
        schedule_memory_extraction(
            runtime,
            user_text="My name is Loop.",
            assistant_output="Stored.",
            session_id="discord:test",
            source_context={
                "surface": "channel",
                "platform": "discord",
                "is_group": True,
                "memory_capture_enabled": True,
            },
        )

    extractor_cls.assert_not_called()


def test_memory_recall_response_returns_private_blocks_without_model(tmp_path) -> None:
    memory = NullaMemory(runtime_home=tmp_path)
    memory.block_write("user_profile", "Name: Loop")
    memory.block_write("preferences", "Answer style: concise and direct")
    memory.block_write("project_context", "Active project codename: GOLDEN_LOOP_616")
    memory.close()
    runtime = RuntimeServices(runtime_home=str(tmp_path))

    response = _memory_recall_response(
        runtime,
        user_text="Personal profile recall: who is the user and what project codename is stored?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert response is not None
    assert response["source"] == "local_private_memory"
    assert response["response"] == (
        "Stored local profile: user: Loop; preferred answer style: concise and direct; "
        "active project codename: GOLDEN_LOOP_616."
    )

    group_response = _memory_recall_response(
        runtime,
        user_text="Personal profile recall: who is the user?",
        source_context={"surface": "channel", "platform": "discord", "is_group": True},
    )
    assert group_response is None


def _normalized_chat_request(prompt: str, *, source_context: dict) -> SimpleNamespace:
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.35,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=0.35,
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )
    return normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary=prompt),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(reconstructed_text=prompt, topic_hints=[], understanding_confidence=0.9),
        context_result=context_result,
        persona=SimpleNamespace(persona_id="default", display_name="NULLA", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="trace-1",
        surface="openclaw",
        source_context=source_context,
    )
