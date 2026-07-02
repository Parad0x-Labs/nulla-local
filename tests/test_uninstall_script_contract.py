from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_uninstall_script_tracks_runtime_install_and_openclaw_paths() -> None:
    script = (PROJECT_ROOT / "installer" / "uninstall_nulla_local.sh").read_text(encoding="utf-8")

    assert 'RUNTIME_HOME="${NULLA_HOME:-$HOME/.nulla_runtime}"' in script
    assert 'INSTALL_ROOT="${NULLA_INSTALL_ROOT:-$HOME/nulla-local}"' in script
    assert 'OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw-default}"' in script
    assert 'OPENCLAW_AGENT_DIR="${NULLA_OPENCLAW_AGENT_DIR:-$HOME/.openclaw/agents/main/agent/nulla}"' in script
    assert 'LAUNCH_AGENT_PATH="${NULLA_LAUNCH_AGENT_PATH:-$HOME/Library/LaunchAgents/ai.nulla.runtime.plist}"' in script
    assert 'pkill -f "apps.nulla_api_server"' in script
    assert 'pkill -f "llama_cpp.server"' in script
    assert 'launchctl bootout "gui/${UID}" "${LAUNCH_AGENT_PATH}"' in script
    assert 'trash_or_remove "${target}"' in script
    assert 'remove_empty_tree() {' in script
    assert 'remove_empty_tree "${INSTALL_ROOT}"' in script
    assert 'remove_empty_tree "${RUNTIME_HOME}"' in script
