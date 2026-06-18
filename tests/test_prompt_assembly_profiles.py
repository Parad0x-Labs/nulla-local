from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core.cold_context_gate import ColdContextDecision
from core.human_input_adapter import HumanInputInterpretation, adapt_user_input
from core.identity_manager import load_active_persona
from core.persistent_memory import append_conversation_event
from core.prompt_assembly_report import ContextItem, PromptAssemblyReport
from core.prompt_normalizer import normalize_prompt
from core.runtime_continuity import create_runtime_checkpoint, update_runtime_checkpoint
from core.task_router import create_task_record
from core.tiered_context_loader import TieredContextLoader, TieredContextResult


def _build_request(
    prompt: str,
    *,
    task_class: str,
    task_kind: str,
    output_mode: str,
):
    persona = load_active_persona("default")
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=task.task_summary,
        normalized_text=task.task_summary,
        reconstructed_text=task.task_summary,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.84,
        quality_flags=[],
    )
    classification = {
        "task_class": task_class,
        "risk_flags": [],
        "confidence_hint": 0.84,
    }
    loader = TieredContextLoader()
    context_result = loader.load(
        task=task,
        classification=classification,
        interpretation=interpretation,
        persona=persona,
        session_id=f"ctx-{task.task_id}",
        total_context_budget=5000,
    )
    request = normalize_prompt(
        task=task,
        classification=classification,
        interpretation=interpretation,
        context_result=context_result,
        persona=persona,
        output_mode=output_mode,
        task_kind=task_kind,
        trace_id=task.task_id,
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    return request, context_result


def _session_id(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _synthetic_context_result() -> TieredContextResult:
    return TieredContextResult(
        bootstrap_items=[
            ContextItem(
                item_id="bootstrap-persona",
                layer="bootstrap",
                source_type="persona",
                title="Agent identity",
                content="Persona: NULLA. Tone: direct.",
            ),
            ContextItem(
                item_id="bootstrap-safety",
                layer="bootstrap",
                source_type="policy",
                title="Safety mode",
                content="Execution default: advice_only.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-conversation-safety",
                layer="bootstrap",
                source_type="conversation_policy",
                title="Conversation policy",
                content="Sensitive conversation is allowed when the user is asking for discussion only.",
            ),
            ContextItem(
                item_id="bootstrap-conversation-preferences",
                layer="bootstrap",
                source_type="user_preferences",
                title="Conversation preferences",
                content="humor=20/100; boundaries=user_defined; profanity=40/100",
            ),
            ContextItem(
                item_id="bootstrap-session-memory-policy",
                layer="bootstrap",
                source_type="session_policy",
                title="Session memory policy",
                content="Session sharing: PRIVATE VAULT.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-owner-privacy-pact",
                layer="bootstrap",
                source_type="privacy_pact",
                title="Privacy pact",
                content="Privacy pact: local only",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
            ContextItem(
                item_id="bootstrap-execution-preferences",
                layer="bootstrap",
                source_type="execution_preferences",
                title="Execution preferences",
                content="autonomy=hands_off; show_workflow=off",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
        ],
        relevant_items=[
            ContextItem(
                item_id="memory-1",
                layer="relevant",
                source_type="runtime_memory",
                title="Persistent memory",
                content="The user prefers direct, useful answers.",
            ),
            ContextItem(
                item_id="doctrine-1",
                layer="relevant",
                source_type="operating_doctrine",
                title="OpenClaw tool doctrine",
                content="Never claim you searched the web without evidence.",
                metadata={"exclude_from_chat_minimal_system_prompt": True},
            ),
        ],
        cold_items=[],
        local_candidates=[],
        swarm_metadata=[],
        report=PromptAssemblyReport(
            task_id="task-1",
            trace_id="trace-1",
            total_context_budget=1000,
            bootstrap_budget=300,
            relevant_budget=500,
            cold_budget=200,
            retrieval_confidence="medium",
        ),
        retrieval_confidence_score=0.6,
        cold_decision=ColdContextDecision(False, "cold_context_not_justified"),
    )


@pytest.mark.parametrize(
    ("task_class", "task_kind", "prompt"),
    [
        ("unknown", "normalization_assist", "Do you think boredom is useful?"),
        ("business_advisory", "normalization_assist", "How should I position my B2B analytics product?"),
        ("relationship_advisory", "normalization_assist", "My partner and I keep having the same argument. What should I do?"),
        ("research", "summarization", "Latest Telegram Bot API updates?"),
        ("integration_orchestration", "normalization_assist", "Show me the open Hive tasks."),
    ],
)
def test_plain_text_chat_system_prompt_stays_minimal(
    task_class: str,
    task_kind: str,
    prompt: str,
) -> None:
    request, context_result = _build_request(
        prompt,
        task_class=task_class,
        task_kind=task_kind,
        output_mode="plain_text",
    )

    system_prompt = request.system_prompt().lower()
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})
    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    assert chat_truth["system_prompt_profile"] == "chat_minimal"
    assert chat_truth["speech_safety_mode"] == "conversation_freer"
    assert chat_truth["tooling_guidance_enabled"] is False
    assert chat_truth["execution_safety_guidance_enabled"] is False
    assert chat_truth["context_delivery"] == "context_message"
    assert "be truthful about uncertainty, freshness, and capabilities" in system_prompt
    assert "handle sensitive, intimate, profane, or controversial discussion directly" in system_prompt
    assert "do not treat discussion-only prompts as permission to use tools" in system_prompt
    assert "do not ask for micro-confirmation" not in system_prompt
    assert "these action rules apply only when using tools" not in system_prompt
    assert "workspace file listing" not in system_prompt
    assert "sandboxed local command execution" not in system_prompt
    assert "email and inbox tooling are not guaranteed" not in system_prompt
    assert "never claim you searched the web" not in system_prompt
    assert "execution default:" not in system_prompt
    assert "openclaw tool doctrine" not in system_prompt
    assert "relevant context from your memory" not in system_prompt
    assert len(context_messages) == 1
    assert context_messages[0].content.startswith("Relevant context and evidence:\n")
    lowered_context = context_messages[0].content.lower()
    assert "session sharing:" not in lowered_context
    assert "privacy pact:" not in lowered_context
    assert "autonomy=" not in lowered_context
    assert context_result.assembled_context(prompt_profile="chat_minimal")[:200] in context_messages[0].content


@pytest.mark.parametrize(
    ("output_mode", "task_kind"),
    [
        ("action_plan", "action_plan"),
        ("tool_intent", "tool_intent"),
    ],
)
def test_structured_chat_system_prompt_keeps_operational_doctrine(
    output_mode: str,
    task_kind: str,
) -> None:
    request, _context_result = _build_request(
        "Find the latest OpenClaw release notes and tell me the next safe step.",
        task_class="system_design",
        task_kind=task_kind,
        output_mode=output_mode,
    )

    system_prompt = request.system_prompt().lower()
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})
    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_operational"
    assert chat_truth["system_prompt_profile"] == "chat_operational"
    assert chat_truth["speech_safety_mode"] == "conversation_freer"
    assert chat_truth["tooling_guidance_enabled"] is True
    assert chat_truth["execution_safety_guidance_enabled"] is True
    assert chat_truth["context_delivery"] == "context_message"
    assert "these action rules apply only when using tools or proposing real-world side effects" in system_prompt
    assert "never claim you searched the web" in system_prompt
    assert "email and inbox tooling are not guaranteed" in system_prompt
    assert "local web0 builder draft generation" in system_prompt
    assert "do not refuse or say you can only guide setup" in system_prompt
    assert "relevant context from your memory" not in system_prompt
    assert len(context_messages) == 1
    assert context_messages[0].content.startswith("Relevant context and evidence:\n")
    lowered_context = context_messages[0].content.lower()
    assert "session sharing:" in lowered_context
    assert any(
        marker in lowered_context
        for marker in (
            "execution default:",
            "session sharing:",
            "openclaw tool doctrine",
        )
    )


def test_tiered_context_loader_filters_heavy_doctrine_from_chat_minimal_profile() -> None:
    context_result = _synthetic_context_result()

    default_context = context_result.assembled_context().lower()
    minimal_context = context_result.assembled_context(prompt_profile="chat_minimal").lower()

    assert "persona: nulla. tone: direct." in default_context
    assert "execution default: advice_only." in default_context
    assert "never claim you searched the web without evidence." in default_context

    assert "persona: nulla. tone: direct." in minimal_context
    assert "sensitive conversation is allowed" in minimal_context
    assert "humor=20/100" in minimal_context
    assert "the user prefers direct, useful answers." in minimal_context
    assert "execution default: advice_only." not in minimal_context
    assert "session sharing: private vault." not in minimal_context
    assert "privacy pact: local only" not in minimal_context
    assert "autonomy=hands_off" not in minimal_context
    assert "never claim you searched the web without evidence." not in minimal_context


def test_prompt_normalizer_falls_back_cleanly_for_stub_context_without_profile_support() -> None:
    request = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Do you think boredom is useful?"),
        classification={"task_class": "unknown", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Do you think boredom is useful?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=SimpleNamespace(
            assembled_context=lambda: "Bootstrap Context:\n- OpenClaw tool doctrine: should stay as-is for plain stubs.",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="NULLA", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-1",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    context_messages = [message for message in request.messages if message.role == "context"]

    assert request.metadata["system_prompt_profile"] == "chat_minimal"
    assert "be truthful about uncertainty, freshness, and capabilities" in request.system_prompt().lower()
    assert "relevant context from your memory" not in request.system_prompt().lower()
    assert len(context_messages) == 1
    assert "openclaw tool doctrine: should stay as-is for plain stubs." in context_messages[0].content.lower()


@pytest.mark.parametrize(
    ("task_class", "task_kind", "prompt"),
    [
        ("unknown", "normalization_assist", "Do you think boredom is useful?"),
        ("business_advisory", "normalization_assist", "How should I position my B2B analytics product?"),
        ("research", "summarization", "Latest Telegram Bot API updates?"),
        ("integration_orchestration", "normalization_assist", "Show me the open Hive tasks."),
    ],
)
def test_provider_facing_messages_keep_context_as_separate_user_evidence_payload_for_chat(
    task_class: str,
    task_kind: str,
    prompt: str,
) -> None:
    request, context_result = _build_request(
        prompt,
        task_class=task_class,
        task_kind=task_kind,
        output_mode="plain_text",
    )

    provider_messages = request.as_openai_messages()
    provider_system = next(message for message in provider_messages if message["role"] == "system")
    provider_evidence = [message for message in provider_messages if "Relevant context and evidence:" in message["content"]]
    fake_assistant_evidence = [
        message
        for message in provider_messages
        if message["role"] == "assistant" and "Relevant context and evidence:" in message["content"]
    ]

    assert "relevant context from your memory" not in provider_system["content"].lower()
    assert len(provider_evidence) == 1
    assert provider_evidence[0]["role"] == "user"
    assert not fake_assistant_evidence
    assert context_result.assembled_context(prompt_profile="chat_minimal")[:200] in provider_evidence[0]["content"]


def test_internal_message_schema_maps_context_to_user_role_for_provider_calls() -> None:
    provider_message = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Do you think boredom is useful?"),
        classification={"task_class": "unknown", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Do you think boredom is useful?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=SimpleNamespace(
            assembled_context=lambda **_: "Relevant shard note: boredom can be a signal.",
            report=SimpleNamespace(
                retrieval_confidence=0.8,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="NULLA", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-1",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    ).as_openai_messages()[1]

    assert provider_message["role"] == "user"
    assert "relevant context and evidence:" in provider_message["content"].lower()


def test_plain_text_chat_uses_persisted_transcript_when_client_history_is_empty() -> None:
    session_id = _session_id("canonical-transcript")
    persona = load_active_persona("default")

    adapt_user_input("Do you think boredom is useful?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it exposes that your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input("What do you mean by that?", session_id=session_id)
    request = normalize_prompt(
        task=create_task_record("What do you mean by that?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-1",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "structured_dialogue_memory"
    assert request.metadata["chat_truth_prompt"]["history_messages"] == 2
    assert request.messages[1].role == "user"
    assert request.messages[1].content == "Do you think boredom is useful?"
    assert request.messages[2].role == "assistant"
    assert request.messages[2].content == "Boredom can be useful when it exposes that your environment is too flat."
    assert [message.content for message in request.messages].count("What do you mean by that?") == 1
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == "What do you mean by that?"


def test_plain_text_chat_prefers_persisted_transcript_over_thin_client_history() -> None:
    session_id = _session_id("persisted-over-client")
    persona = load_active_persona("default")

    adapt_user_input("How should I position my analytics product?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="How should I position my analytics product?",
        assistant_output="Position it around the painful decision it makes faster, not around dashboards.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input("Can you sharpen that?", session_id=session_id)
    request = normalize_prompt(
        task=create_task_record("Can you sharpen that?"),
        classification={"task_class": "business_advisory", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=_synthetic_context_result(),
        persona=persona,
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-2",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "client_conversation_history": [
                {"role": "assistant", "content": "Thin client history that should lose."},
            ],
            "conversation_history": [
                {"role": "assistant", "content": "Thin client history that should lose."},
            ],
        },
    )

    message_contents = [message.content for message in request.messages]

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "structured_dialogue_memory"
    assert "Thin client history that should lose." not in message_contents
    assert "How should I position my analytics product?" in message_contents
    assert "Position it around the painful decision it makes faster, not around dashboards." in message_contents


def test_plain_text_chat_falls_back_to_client_history_when_persisted_transcript_is_missing() -> None:
    request = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Can you continue that?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=SimpleNamespace(
            reconstructed_text="Can you continue that?",
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=_synthetic_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="NULLA", tone="direct"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id="trace-transcript-3",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "client_conversation_history": [
                {"role": "user", "content": "Do you think boredom is useful?"},
                {"role": "assistant", "content": "Yes. It can expose that your environment is too flat."},
            ],
        },
    )

    assert request.metadata["chat_truth_prompt"]["transcript_source"] == "client_conversation_history"
    assert request.metadata["chat_truth_prompt"]["history_messages"] == 2
    assert request.messages[1].content == "Do you think boredom is useful?"
    assert request.messages[2].content == "Yes. It can expose that your environment is too flat."


def test_provider_facing_chat_context_uses_structured_tool_observation_instead_of_fake_tool_prose() -> None:
    session_id = _session_id("tool-observation")
    persona = load_active_persona("default")
    checkpoint = create_runtime_checkpoint(
        session_id=session_id,
        request_text="latest qwen release notes",
        source_context={"runtime_session_id": session_id, "surface": "openclaw", "platform": "openclaw"},
    )
    update_runtime_checkpoint(
        checkpoint["checkpoint_id"],
        state={
            "executed_steps": [
                {
                    "tool_name": "web.search",
                    "status": "executed",
                    "summary": "Found Qwen release notes.",
                }
            ],
            "last_tool_response": {
                "handled": True,
                "ok": True,
                "status": "executed",
                "response_text": 'Search results for "latest qwen release notes": ...',
                "tool_name": "web.search",
                "details": {
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "query": "latest qwen release notes",
                        "results": [
                            {
                                "title": "Qwen release notes",
                                "url": "https://example.test/qwen",
                                "snippet": "Fresh update summary",
                            }
                        ],
                    }
                },
            },
        },
        status="completed",
    )

    interpretation = HumanInputInterpretation(
        raw_text="what changed in qwen lately?",
        normalized_text="what changed in qwen lately?",
        reconstructed_text="what changed in qwen lately?",
        intent_mode="request",
        topic_hints=["qwen", "release notes"],
        reference_targets=[],
        understanding_confidence=0.84,
        quality_flags=[],
    )
    request = normalize_prompt(
        task=create_task_record("what changed in qwen lately?"),
        classification={"task_class": "chat_research", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        context_result=TieredContextLoader().load(
            task=create_task_record("what changed in qwen lately?"),
            classification={"task_class": "chat_research", "risk_flags": [], "confidence_hint": 0.84},
            interpretation=interpretation,
            persona=persona,
            session_id=session_id,
            total_context_budget=5000,
        ),
        persona=persona,
        output_mode="plain_text",
        task_kind="summarization",
        trace_id="trace-tool-observation",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_session_id": session_id,
            "conversation_history": [],
        },
    )

    provider_messages = request.as_openai_messages()
    provider_evidence = [message for message in provider_messages if "Relevant context and evidence:" in message["content"]]

    assert len(provider_evidence) == 1
    assert provider_evidence[0]["role"] == "user"
    assert '"tool_surface": "web"' in provider_evidence[0]["content"]
    assert '"query": "latest qwen release notes"' in provider_evidence[0]["content"]
    assert '"results": [' in provider_evidence[0]["content"]
    assert "Real tool result from" not in provider_evidence[0]["content"]
