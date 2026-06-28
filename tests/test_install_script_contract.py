from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_install_script_autodetects_supported_python() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert "resolve_python_bin()" in script
    assert "uv python find" in script
    assert "python3.11" in script


def test_install_script_rebuilds_unsupported_venv() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert "Existing virtual environment uses unsupported Python. Rebuilding..." in script
    assert 'rm -rf "${VENV_DIR}"' in script


def test_install_script_hardens_openclaw_launcher_bootstrap() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert "--install-profile <profile>" in script
    assert "ollama-only" in script
    assert "ollama-max" in script
    assert 'validate_selected_install_profile() {' in script
    assert 'ensure_profile_remote_credentials() {' in script
    assert 'Enter Kimi / Moonshot API key' in script
    assert 'Enter Tether API key' in script
    assert 'Enter OpenAI-compatible remote API key' in script
    assert '"${SCRIPT_DIR}/validate_install_profile.py"' in script
    assert 'persist_install_profile_record() {' in script
    assert 'persist_provider_env_file() {' in script
    assert 'PROVIDER_ENV_FILE="\\${NULLA_HOME}/config/provider-env.sh"' in script
    assert "from core.install_recommendations import build_install_recommendation_truth" in script
    assert "print(build_install_recommendation_truth().primary_local_model)" in script
    assert '") 2>/dev/null || echo "qwen3:8b"' in script
    assert 'PRIMARY_LOCAL_MODEL=qwen3:8b' in script
    assert "from core.install_recommendations import install_recommendation_machine_summary" in script
    assert "print(json.dumps(install_recommendation_machine_summary(), ensure_ascii=False))" in script
    assert '\'{"selected_tier":"capacity-C","ollama_model":"qwen3:8b","recommended_bundle_models":["qwen3:8b","deepseek-r1:8b"]}\'' in script
    assert 'NULLA_REMOTE_API_KEY NULLA_REMOTE_BASE_URL NULLA_REMOTE_MODEL NULLA_CLOUD_API_KEY' in script
    assert 'detect_install_profile_display() {' in script
    assert script.count('cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -c "') >= 3
    assert 'cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" NULLA_INSTALL_PROFILE="${requested_profile}" "${VENV_DIR}/bin/python" -c "' in script
    assert 'Profile: ${install_profile_display}' in script
    assert 'Recommended profile: ${recommended_install_profile_display}' in script
    assert 'Install profile: ${install_profile_display}' in script
    assert 'wait_for_http_ready() {' in script
    assert 'port_listening() {' in script
    assert 'spawn_detached() {' in script
    assert 'curl -sf --max-time 2 "\\${url}" >/dev/null 2>&1' in script
    assert 'if port_listening "127.0.0.1" "\\${NULLA_OPENCLAW_API_PORT}"; then' in script
    assert 'cd "${PROJECT_ROOT}"' in script
    assert 'export NULLA_HOME="\\${NULLA_HOME:-${runtime_home}}"' in script
    assert 'export NULLA_WORKSPACE_ROOT="\\${NULLA_WORKSPACE_ROOT:-\\${NULLA_HOME}/workspace}"' in script
    assert 'export NULLA_OPENCLAW_API_PORT="\\${NULLA_OPENCLAW_API_PORT:-11435}"' in script
    assert 'export NULLA_OPENCLAW_API_URL="\\${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:\\${NULLA_OPENCLAW_API_PORT}}"' in script
    assert 'export PATH="${SCRIPT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"' in script
    assert 'VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"' in script
    assert 'VENV_PY="$(bash "${VENV_RESOLVER}")"' in script
    assert 'export NULLA_OPENCLAW_API_URL="\\${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:\\${NULLA_OPENCLAW_API_PORT}}"' in script
    assert 'if [[ "\\${NULLA_LAUNCHD_SUPERVISOR:-0}" == "1" ]]; then' in script
    assert 'API_LOG_PATH="\\${NULLA_API_LOG_PATH:-\\${NULLA_HOME}/logs/api-supervised.log}"' in script
    assert 'terminate_pid() {' in script
    assert 'api_pid="\\$(spawn_detached "\\${API_LOG_PATH}" "\\${VENV_PY}" -m apps.nulla_api_server --port "\\${NULLA_OPENCLAW_API_PORT}")"' in script
    assert 'wait_for_http_ready "\\${NULLA_OPENCLAW_API_URL}/healthz" 240 "\\${api_pid}" 5' in script
    assert 'start_new_session=True' in script
    assert 'api_pid="\\$(spawn_detached /tmp/nulla_api_server.log "\\${PROJECT_ROOT}/Start_NULLA.sh")"' in script
    assert 'wait_for_http_ready "\\${NULLA_OPENCLAW_API_URL}/healthz" 30 "\\${api_pid}" 3' in script
    assert 'spawn_detached /tmp/nulla_openclaw.log ollama launch openclaw --yes --model "\\${MODEL_TAG}"' in script
    assert 'launch openclaw --yes --config --model "${model_tag}"' in script
    assert 'openclaw gateway run --force' in script
    assert '${HOME}/.openclaw-default' in script
    assert 'Skipping Ollama OpenClaw auto-config for isolated home' in script
    assert 'say "Verifying live launch through the shell launcher..."' in script
    assert 'local launchd_runtime_ready=0' in script
    assert 'local launchd_runtime_consecutive=0' in script
    assert 'for _ in $(seq 1 240); do' in script
    assert 'curl -sf --max-time 2 "http://127.0.0.1:11435/v1/models" >/dev/null 2>&1' in script
    assert 'if [[ "${launchd_runtime_consecutive}" -ge 5 ]]; then' in script
    assert 'say "Launchd runtime verified at http://127.0.0.1:11435 (stable health + /v1/models)"' in script
    assert 'say "ERROR: launchd installed NULLA, but the API did not stay healthy long enough to verify /v1/models within 240 seconds."' in script
    assert 'exec "${PROJECT_ROOT}/OpenClaw_NULLA.sh"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}"' in script
    assert 'pull_models "${ollama_exe}" "${install_profile}" "${model_tag}" "${runtime_home}" "${openclaw_enabled}"' in script
    assert 'required_model="nomic-embed-text"' in script


def test_install_script_launch_agent_enables_supervised_runtime() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert '<key>NULLA_LAUNCHD_SUPERVISOR</key>' in script
    assert '<string>1</string>' in script
    assert '<key>NULLA_API_LOG_PATH</key>' in script
    assert '<string>${log_dir}/api-supervised.log</string>' in script


def test_install_wrappers_forward_install_profile_and_extra_args() -> None:
    install_and_run = (PROJECT_ROOT / "Install_And_Run_NULLA.sh").read_text(encoding="utf-8")
    install_and_run_bat = (PROJECT_ROOT / "Install_And_Run_NULLA.bat").read_text(encoding="utf-8")
    install_bat = (PROJECT_ROOT / "Install_NULLA.bat").read_text(encoding="utf-8")
    install_bat_script = (PROJECT_ROOT / "installer" / "install_nulla.bat").read_text(encoding="utf-8")

    assert '--start "$@"' in install_and_run
    assert "%*" in install_and_run_bat
    assert "%*" in install_bat
    assert "requested_profile=r'%NULLA_INSTALL_PROFILE%'" in install_bat_script
    assert '"%SCRIPT_DIR%validate_install_profile.py"' in install_bat_script


def test_windows_launchers_use_module_entrypoint_for_api_server() -> None:
    install_bat_script = (PROJECT_ROOT / "installer" / "install_nulla.bat").read_text(encoding="utf-8")

    assert '"%VENV_DIR%\\Scripts\\python.exe" -m apps.nulla_api_server' in install_bat_script
    assert "where openclaw" in install_bat_script
    assert "gateway run --force --port 18789" in install_bat_script
    assert "Trying Ollama OpenClaw bootstrap" in install_bat_script
    assert "OPENCLAW_MEMORY_MODEL=nomic-embed-text" in install_bat_script


def test_install_script_surfaces_machine_probe_command() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert 'Probe:   ${PROJECT_ROOT}/Probe_NULLA_Stack.sh' in script


def test_public_hive_auth_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "ops" / "ensure_public_hive_auth.py"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert 'default=""' in content
    assert "from core.public_hive_bridge import ensure_public_hive_auth" in content


def test_workspace_runtime_bootstrap_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "scripts" / "ensure_workspace_runtime.sh"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert "runtime_python_ready()" in content
    assert "ensure_pip()" in content
    assert '"${VENV_DIR}/bin/python" -m ensurepip --upgrade' in content
    assert 'required = ("starlette", "uvicorn")' in content
    assert 'pip install -e "${PROJECT_ROOT}[runtime,proof]"' in content


def test_install_script_runs_public_hive_auth_helper_from_project_root() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert 'result_json="$(cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" \\' in script
    assert '"${VENV_DIR}/bin/python" -m ops.ensure_public_hive_auth \\' in script
    assert "hydrated_from_local_cluster" in script
