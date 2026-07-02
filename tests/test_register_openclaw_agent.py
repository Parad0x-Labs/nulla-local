from __future__ import annotations

from installer import register_openclaw_agent as roa


def test_build_nulla_provider_honors_api_url_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")

    provider = roa._build_nulla_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:21435"
    assert provider["models"][0]["id"] == "nulla"
    assert provider["timeoutSeconds"] == 600


def test_build_nulla_provider_reports_real_model_name_not_generic_alias() -> None:
    # OpenClaw's status bar renders this per-model `name`; it must show the actual
    # underlying model + size, not the constant "nulla" (the id stays "nulla" for
    # routing, but the name is what a human sees).
    provider = roa._build_nulla_provider("ollama/qwen2.5:7b")

    model = provider["models"][0]
    assert model["id"] == "nulla"
    assert model["name"] == "qwen2.5:7b (7B)"


def test_build_nulla_and_ollama_provider_report_reasoning_false_for_non_reasoning_models() -> None:
    assert roa._build_nulla_provider("ollama/gemma3:4b")["models"][0]["reasoning"] is False
    assert roa._build_ollama_provider("ollama/qwen2.5:7b")["models"][0]["reasoning"] is False


def test_build_nulla_and_ollama_provider_report_reasoning_true_for_reasoning_models() -> None:
    # Previously this was hardcoded False for every model, which meant a genuinely
    # reasoning-tuned tag would still (incorrectly) report reasoning support as off.
    assert roa._build_nulla_provider("ollama/deepseek-r1:14b")["models"][0]["reasoning"] is True
    assert roa._build_ollama_provider("ollama/qwen3:8b-thinking")["models"][0]["reasoning"] is True


def test_thinking_mode_enabled_reflects_show_workflow_preference(monkeypatch) -> None:
    from core.user_preferences import UserPreferences

    monkeypatch.setattr(
        "core.user_preferences.load_preferences",
        lambda: UserPreferences(show_workflow=True),
    )
    assert roa._thinking_mode_enabled() is True

    monkeypatch.setattr(
        "core.user_preferences.load_preferences",
        lambda: UserPreferences(show_workflow=False),
    )
    assert roa._thinking_mode_enabled() is False


def test_thinking_mode_enabled_defaults_false_on_read_failure(monkeypatch) -> None:
    def _boom():
        raise OSError("no runtime home")

    monkeypatch.setattr("core.user_preferences.load_preferences", _boom)
    assert roa._thinking_mode_enabled() is False


def test_ensure_ollama_provider_still_repairs_broken_nulla_alias_after_name_change(monkeypatch) -> None:
    # Regression guard: _provider_looks_like_broken_ollama_alias must keep detecting
    # a broken alias by `id` alone now that `name` is a descriptive label rather than
    # a second literal "nulla".
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")
    monkeypatch.setenv("NULLA_RAW_OLLAMA_API_URL", "http://127.0.0.1:11434")
    cfg = {
        "models": {
            "providers": {
                "ollama": roa._build_nulla_provider("ollama/qwen2.5:7b"),
            }
        }
    }

    roa._ensure_ollama_provider(cfg, "ollama/qwen2.5:7b")

    provider = cfg["models"]["providers"]["ollama"]
    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"


def test_build_ollama_provider_uses_raw_ollama_host(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")

    provider = roa._build_ollama_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"
    assert provider["timeoutSeconds"] == 600


def test_local_provider_timeout_honors_bounded_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_PROVIDER_TIMEOUT_SECONDS", "1200")

    assert roa._build_nulla_provider("ollama/qwen2.5:7b")["timeoutSeconds"] == 1200

    monkeypatch.setenv("NULLA_OPENCLAW_PROVIDER_TIMEOUT_SECONDS", "10")

    assert roa._build_nulla_provider("ollama/qwen2.5:7b")["timeoutSeconds"] == 60


def test_gateway_port_honors_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_GATEWAY_PORT", "28790")
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")

    cfg = roa._base_openclaw_config("/tmp/workspace")

    assert cfg["gateway"]["port"] == 28790
    assert cfg["models"]["providers"]["ollama"]["models"][0]["id"] == "qwen2.5:7b"


def test_base_openclaw_config_disables_web_search_for_local_only(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")

    cfg = roa._base_openclaw_config("/tmp/workspace")

    assert cfg["tools"]["web"]["search"] == {"enabled": False}
    assert cfg["agents"]["defaults"]["memorySearch"] == {
        "enabled": True,
        "provider": "ollama",
        "model": "nomic-embed-text",
        "fallback": "none",
        "remote": {
            "baseUrl": "http://127.0.0.1:11434",
            "apiKey": "ollama-local",
        },
    }


def test_ensure_config_defaults_repairs_invalid_local_search_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "nulla",
        agent_runtime_dir=home / "agents" / "nulla" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "nulla",
        source="test",
        discovered_existing=False,
    )
    cfg = {
        "tools": {
            "web": {
                "search": {
                    "enabled": True,
                    "provider": "ollama",
                },
            },
        },
        "plugins": {
            "entries": {
                "ollama": {
                    "enabled": True,
                },
            },
        },
    }

    repaired = roa._ensure_config_defaults(cfg, paths=paths)

    assert repaired["tools"]["web"]["search"] == {"enabled": False}
    assert repaired["agents"]["defaults"]["memorySearch"]["provider"] == "ollama"
    assert repaired["agents"]["defaults"]["memorySearch"]["model"] == "nomic-embed-text"
    assert repaired["agents"]["defaults"]["memorySearch"]["fallback"] == "none"
    assert "plugins" not in repaired


def test_ensure_config_defaults_adds_timeout_to_existing_ollama_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "nulla",
        agent_runtime_dir=home / "agents" / "nulla" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "nulla",
        source="test",
        discovered_existing=False,
    )
    cfg = {
        "models": {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:11434",
                    "api": "ollama",
                    "models": [{"id": "qwen2.5:7b", "name": "qwen2.5:7b"}],
                    "apiKey": "ollama-local",
                },
            },
        },
    }

    repaired = roa._ensure_config_defaults(cfg, paths=paths)

    assert repaired["models"]["providers"]["ollama"]["timeoutSeconds"] == 600


def test_workspace_memory_seed_creates_ignored_openclaw_memory_readme(tmp_path) -> None:
    roa._ensure_workspace_memory_seed(str(tmp_path))

    readme = tmp_path / "memory" / "README.md"
    assert readme.is_file()
    assert "Workspace memory notes for OpenClaw live here." in readme.read_text(encoding="utf-8")


def test_ensure_ollama_provider_repairs_broken_nulla_alias(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")
    monkeypatch.setenv("NULLA_RAW_OLLAMA_API_URL", "http://127.0.0.1:11434")
    cfg = {
        "models": {
            "providers": {
                "ollama": roa._build_nulla_provider("ollama/qwen2.5:7b"),
            }
        }
    }

    roa._ensure_ollama_provider(cfg, "ollama/qwen2.5:7b")

    provider = cfg["models"]["providers"]["ollama"]
    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"
