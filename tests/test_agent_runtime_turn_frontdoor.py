from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import NullaAgent, maybe_handle_preference_command, set_hive_interaction_state


def _build_agent() -> NullaAgent:
    return NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_handle_turn_frontdoor_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8)
    persona = mock.sentinel.persona

    with mock.patch(
        "core.agent_runtime.turn_frontdoor.handle_turn_frontdoor",
        return_value={"result": {"response": "done"}},
    ) as handle_turn_frontdoor:
        result = agent._handle_turn_frontdoor(
            raw_user_input="help",
            effective_input="help",
            normalized_input="help",
            source_surface="openclaw",
            session_id="turn-frontdoor-session",
            source_context={"surface": "openclaw"},
            persona=persona,
            interpreted=interpreted,
        )

    assert result == {"result": {"response": "done"}}
    handle_turn_frontdoor.assert_called_once_with(
        agent,
        raw_user_input="help",
        effective_input="help",
        normalized_input="help",
        source_surface="openclaw",
        session_id="turn-frontdoor-session",
        source_context={"surface": "openclaw"},
        persona=persona,
        interpreted=interpreted,
        maybe_handle_preference_command_fn=maybe_handle_preference_command,
        set_hive_interaction_state_fn=set_hive_interaction_state,
    )


def test_handle_turn_frontdoor_uses_app_level_preference_override() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8)

    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch(
        "apps.nulla_agent.maybe_handle_preference_command",
        return_value=(True, "saved"),
    ) as maybe_handle_preference_command_mock, mock.patch.object(
        agent,
        "_sync_public_presence",
    ) as sync_public_presence, mock.patch.object(
        agent,
        "_idle_public_presence_status",
        return_value="idle",
    ), mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "saved"},
    ) as fast_path_result:
        result = agent._handle_turn_frontdoor(
            raw_user_input="remember that",
            effective_input="remember that",
            normalized_input="remember that",
            source_surface="openclaw",
            session_id="turn-frontdoor-session",
            source_context={"surface": "openclaw"},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": {"response": "saved"}}
    maybe_handle_preference_command_mock.assert_called_once_with("remember that")
    sync_public_presence.assert_called_once()
    fast_path_result.assert_called_once_with(
        session_id="turn-frontdoor-session",
        user_input="remember that",
        response="saved",
        confidence=0.92,
        source_context={"surface": "openclaw"},
        reason="user_preference_command",
    )


def test_handle_turn_frontdoor_blocks_explicit_35b_before_model_planning() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8)

    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "blocked"},
    ) as fast_path_result, mock.patch.object(agent, "_maybe_handle_direct_workspace_runtime_request") as workspace_runtime:
        result = agent._handle_turn_frontdoor(
            raw_user_input="Explicitly use qwen3.5:35b-a3b for this hard engineering analysis.",
            effective_input="Explicitly use qwen3.5:35b-a3b for this hard engineering analysis.",
            normalized_input="explicitly use qwen3.5:35b-a3b for this hard engineering analysis.",
            source_surface="openclaw",
            session_id="turn-frontdoor-session",
            source_context={"surface": "openclaw"},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": {"response": "blocked"}}
    workspace_runtime.assert_not_called()
    fast_path_result.assert_called_once()
    assert fast_path_result.call_args.kwargs["reason"] == "explicit_heavy_model_blocked"
    assert "qwen3.5:35b-a3b" in fast_path_result.call_args.kwargs["response"]


def test_handle_turn_frontdoor_uses_app_level_utility_state_override() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8)

    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch(
        "apps.nulla_agent.maybe_handle_preference_command",
        return_value=(False, ""),
    ), mock.patch.object(
        agent,
        "_maybe_handle_credit_command",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_frontdoor",
        return_value=(None, None, False),
    ), mock.patch.object(
        agent,
        "_maybe_handle_memory_fast_path",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_ui_command_fast_path",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_credit_status_fast_path",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_date_time_fast_path",
        return_value="Current time in Vilnius is 12:00 EET.",
    ), mock.patch.object(
        agent,
        "_extract_utility_timezone",
        return_value=("Europe/Vilnius", "Vilnius"),
    ), mock.patch(
        "apps.nulla_agent.set_hive_interaction_state",
    ) as set_hive_interaction_state_mock, mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "time"},
    ) as fast_path_result:
        result = agent._handle_turn_frontdoor(
            raw_user_input="what time is now in Vilnius?",
            effective_input="what time is now in Vilnius?",
            normalized_input="what time is now in vilnius?",
            source_surface="openclaw",
            session_id="turn-frontdoor-session",
            source_context={"surface": "openclaw"},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": {"response": "time"}}
    set_hive_interaction_state_mock.assert_called_once_with(
        "turn-frontdoor-session",
        mode="utility",
        payload={"utility_kind": "time", "timezone": "Europe/Vilnius", "label": "Vilnius"},
    )
    fast_path_result.assert_called_once_with(
        session_id="turn-frontdoor-session",
        user_input="what time is now in Vilnius?",
        response="Current time in Vilnius is 12:00 EET.",
        confidence=0.97,
        source_context={"surface": "openclaw"},
        reason="date_time_fast_path",
    )


def test_handle_turn_frontdoor_routes_web0_builder_before_ui_command() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8)

    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch(
        "apps.nulla_agent.maybe_handle_preference_command",
        return_value=(False, ""),
    ), mock.patch.object(
        agent,
        "_maybe_handle_credit_command",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_frontdoor",
        return_value=(None, None, False),
    ), mock.patch.object(
        agent,
        "_maybe_handle_memory_fast_path",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_handle_web0_builder_fast_path",
        return_value={"response": "builder-url"},
    ) as web0_builder:
        result = agent._handle_turn_frontdoor(
            raw_user_input="build a website on web0",
            effective_input="build a website on web0",
            normalized_input="build a website on web0",
            source_surface="openclaw",
            session_id="turn-frontdoor-session",
            source_context={"surface": "openclaw"},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": {"response": "builder-url"}}
    web0_builder.assert_called_once_with(
        "build a website on web0",
        session_id="turn-frontdoor-session",
        source_context={"surface": "openclaw"},
    )
