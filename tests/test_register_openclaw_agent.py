from __future__ import annotations

from installer import register_openclaw_agent as roa


def test_build_nulla_provider_honors_api_url_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")

    provider = roa._build_nulla_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:21435"
    assert provider["models"][0]["id"] == "nulla"


def test_build_ollama_provider_uses_raw_ollama_host(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")

    provider = roa._build_ollama_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"


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
