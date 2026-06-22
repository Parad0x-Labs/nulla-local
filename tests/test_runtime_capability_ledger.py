from __future__ import annotations

from unittest import mock

from apps.nulla_agent import NullaAgent
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.tool_intent_executor import capability_gap_for_intent, execute_tool_intent, runtime_capability_ledger


def _ledger_by_id(entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(entry.get("capability_id") or "").strip(): dict(entry) for entry in entries}


def test_runtime_capability_ledger_tracks_real_wiring() -> None:
    with mock.patch("core.tool_intent_executor.policy_engine.allow_web_fallback", return_value=False), mock.patch(
        "core.runtime_execution_tools.policy_engine.get",
        side_effect=lambda key, default=None: {
            "filesystem.allow_read_workspace": True,
            "filesystem.allow_write_workspace": False,
            "execution.allow_sandbox_execution": True,
        }.get(key, default),
    ), mock.patch(
        "core.tool_intent_executor.load_public_hive_bridge_config",
        return_value=PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://seed.example.test",),
            topic_target_url="https://seed.example.test",
            auth_token=None,
        ),
    ), mock.patch(
        "core.tool_intent_executor.load_hive_activity_tracker_config",
        return_value=HiveActivityTrackerConfig(enabled=True, watcher_api_url="https://watch.example.test/api/dashboard"),
    ), mock.patch(
        "core.tool_intent_executor.public_hive_write_enabled",
        return_value=False,
    ), mock.patch(
        "core.local_operator_actions.list_operator_tools",
        return_value=[
            {
                "tool_id": "inspect_processes",
                "category": "local_operator",
                "destructive": False,
                "available": True,
                "description": "Inspect running processes.",
            },
            {
                "tool_id": "schedule_calendar_event",
                "category": "calendar",
                "destructive": True,
                "available": False,
                "description": "Create local calendar events.",
            },
        ],
    ):
        ledger = _ledger_by_id(runtime_capability_ledger())

    assert ledger["web.live_lookup"]["supported"] is False
    assert ledger["workspace.read"]["supported"] is True
    assert ledger["workspace.write"]["supported"] is False
    assert ledger["sandbox.command"]["supported"] is True
    assert ledger["operator.inspect_processes"]["supported"] is True
    assert ledger["operator.schedule_calendar_event"]["supported"] is False
    assert ledger["hive.read"]["supported"] is True
    assert ledger["hive.write"]["supported"] is False


def test_help_capabilities_text_comes_from_ledger_and_marks_unsupported_truthfully() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent.start()

    with mock.patch(
        "core.agent_runtime.public_hive_support.runtime_capability_ledger",
        return_value=[
            {
                "capability_id": "web.live_lookup",
                "surface": "web",
                "claim": "run live web search and fetch pages",
                "supported": True,
                "unsupported_reason": "Live web lookup is disabled on this runtime.",
            },
            {
                "capability_id": "hive.write",
                "surface": "hive",
                "claim": "submit real Hive writes",
                "supported": False,
                "unsupported_reason": "Live Hive write actions are not enabled on this runtime.",
            },
        ],
    ), mock.patch(
        "core.agent_runtime.public_hive_support.policy_engine.get",
        side_effect=lambda key, default=None: {
            "filesystem.allow_write_workspace": True,
            "execution.allow_sandbox_execution": True,
        }.get(key, default),
    ):
        text = agent._help_capabilities_text()

    assert "Wired on this runtime:" in text
    assert "run live web search and fetch pages" in text
    assert "Partially supported on this runtime:" in text
    assert "bounded local build/edit/run/inspect loops" in text
    assert "telegram or discord bot scaffolds" in text.lower()
    assert "not a full autonomous research -> build -> debug -> test loop" in text
    assert "Live Hive write actions are not enabled on this runtime." in text
    assert "mesh-assisted lookups" not in text
    assert "agent_commons" not in text


def test_chat_surface_help_model_input_uses_grounded_capability_text() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent.start()

    with mock.patch.object(agent, "_help_capabilities_text", return_value="Wired on this runtime:\n- run live web search"):
        model_input = agent._chat_surface_smalltalk_model_input(user_input="help", phrase="help")

    assert "Ground your reply in currently wired runtime capabilities only." in model_input
    assert "run live web search" in model_input
    assert "agent_commons" not in model_input


def test_disabled_web_search_reports_unsupported_capability_honestly() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    with mock.patch("core.tool_intent_executor.policy_engine.allow_web_fallback", return_value=False):
        result = execute_tool_intent(
            {"intent": "web.search", "arguments": {"query": "latest qwen release notes"}},
            task_id="task-123",
            session_id="session-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
        )

    assert result.handled is True
    assert result.ok is False
    assert result.status == "disabled"
    assert "web lookup is opt-in; enable with nulla_enable_web=1" in result.response_text.lower()
    assert result.user_safe_response_text == result.response_text


def test_unknown_workspace_tool_reports_unwired_gap_and_nearby_supported_alternatives() -> None:
    gap = capability_gap_for_intent("workspace.delete_file")

    assert gap["support_level"] == "unsupported"
    assert gap["gap_kind"] == "unwired"
    assert "workspace.delete_file" in str(gap["reason"])
    assert any("read files" in item for item in gap["nearby_alternatives"])
    assert any("write files" in item for item in gap["nearby_alternatives"])


def test_unsupported_tool_execution_preserves_gap_metadata() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    result = execute_tool_intent(
        {"intent": "workspace.delete_file", "arguments": {"path": "secrets.txt"}},
        task_id="task-123",
        session_id="session-123",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        hive_activity_tracker=tracker,
    )

    assert result.handled is True
    assert result.ok is False
    assert result.status == "unsupported"
    gap = result.details["capability_gap"]
    assert gap["gap_kind"] == "unwired"
    assert gap["support_level"] == "unsupported"
    assert any("read files" in item for item in gap["nearby_alternatives"])
