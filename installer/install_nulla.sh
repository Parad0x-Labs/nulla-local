#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NULLA_HOME_DEFAULT="${NULLA_HOME_DEFAULT:-$HOME/.nulla_runtime}"
OPENCLAW_AGENT_DEFAULT="${OPENCLAW_AGENT_DEFAULT:-$HOME/.openclaw/agents/main/agent/nulla}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
AUTO_YES=0
AUTO_START=0
RUNTIME_HOME_OVERRIDE=""
INSTALL_PROFILE_OVERRIDE="${NULLA_INSTALL_PROFILE:-}"
AGENT_NAME_OVERRIDE="${NULLA_AGENT_NAME:-}"
OPENCLAW_MODE="default" # prompt|skip|default|path
OPENCLAW_PATH_OVERRIDE=""
OPENCLAW_GATEWAY_BIND="${NULLA_OPENCLAW_GATEWAY_BIND:-}"
OPENCLAW_GATEWAY_CUSTOM_HOST="${NULLA_OPENCLAW_GATEWAY_CUSTOM_HOST:-}"
DESKTOP_SHORTCUT_PATH=""
LAUNCH_AGENT_PATH=""
RUNTIME_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements-runtime.txt"
WHEELHOUSE_DIR="${PROJECT_ROOT}/vendor/wheelhouse"
BUNDLED_LIQUEFY_DIR="${PROJECT_ROOT}/vendor/liquefy-openclaw-integration"
XSEARCH_URL="${XSEARCH_URL:-http://127.0.0.1:8080}"
WEB_PROVIDER_ORDER="${WEB_PROVIDER_ORDER:-searxng,ddg_instant,duckduckgo_html}"
DEFAULT_BROWSER_ENGINE="${DEFAULT_BROWSER_ENGINE:-chromium}"
PUBLIC_HIVE_SSH_KEY_PATH="${NULLA_PUBLIC_HIVE_SSH_KEY_PATH:-}"
PUBLIC_HIVE_WATCH_HOST="${NULLA_PUBLIC_HIVE_WATCH_HOST:-}"


say() {
  printf '%s\n' "$*"
}


dir_has_files() {
  local dir="$1"
  [[ -d "${dir}" ]] || return 1
  shopt -s nullglob dotglob
  local matches=("${dir}"/*)
  shopt -u nullglob dotglob
  [[ ${#matches[@]} -gt 0 ]]
}


prompt() {
  local label="$1"
  local default_value="$2"
  local value=""
  read -r -p "${label} [${default_value}]: " value || true
  if [[ -z "${value}" ]]; then
    value="${default_value}"
  fi
  printf '%s' "${value}"
}


usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --yes, -y                    Non-interactive install using defaults
  --start                      Launch NULLA immediately after install
  --runtime-home <path>        Override NULLA_HOME path
  --install-profile <profile>  auto-recommended | local-only (alias: ollama-only) | local-max (alias: ollama-max)
  --agent-name <name>          Visible agent name for OpenClaw and chat
  --openclaw <mode-or-path>    skip | default | prompt | <custom-path>
  --gateway-bind <mode>        OpenClaw gateway bind: loopback | lan | custom
  --gateway-custom-host <ip>   Custom host/IP when --gateway-bind custom
  --help, -h                   Show this help
EOF
}


parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes|-y)
        AUTO_YES=1
        ;;
      --start)
        AUTO_START=1
        ;;
      --runtime-home)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --runtime-home requires a value."
          exit 2
        fi
        RUNTIME_HOME_OVERRIDE="$1"
        ;;
      --install-profile)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --install-profile requires a value."
          exit 2
        fi
        INSTALL_PROFILE_OVERRIDE="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
        ;;
      --agent-name)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --agent-name requires a value."
          exit 2
        fi
        AGENT_NAME_OVERRIDE="$1"
        ;;
      --openclaw)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --openclaw requires a value."
          exit 2
        fi
        case "$1" in
          skip|none|no)
            OPENCLAW_MODE="skip"
            ;;
          default|yes)
            OPENCLAW_MODE="default"
            ;;
          prompt)
            OPENCLAW_MODE="prompt"
            ;;
          *)
            OPENCLAW_MODE="path"
            OPENCLAW_PATH_OVERRIDE="$1"
            ;;
        esac
        ;;
      --gateway-bind)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --gateway-bind requires a value."
          exit 2
        fi
        OPENCLAW_GATEWAY_BIND="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
        ;;
      --gateway-custom-host)
        shift
        if [[ $# -eq 0 ]]; then
          say "ERROR: --gateway-custom-host requires a value."
          exit 2
        fi
        OPENCLAW_GATEWAY_CUSTOM_HOST="$1"
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        say "ERROR: Unknown option: $1"
        usage
        exit 2
        ;;
    esac
    shift
  done
}


canonical_install_profile() {
  local profile
  profile="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "${profile}" in
    "")
      printf '%s' ""
      ;;
    auto|recommended|auto-recommended)
      printf '%s' "auto-recommended"
      ;;
    local-only|local_only|ollama-only|ollama_only)
      printf '%s' "local-only"
      ;;
    local-max|local_max|ollama-max|ollama_max)
      printf '%s' "local-max"
      ;;
    hybrid-kimi|hybrid_kimi|ollama+kimi|ollama-kimi|ollama_kimi)
      printf '%s' "hybrid-kimi"
      ;;
    hybrid-tether|hybrid_tether|ollama+tether|ollama-tether|ollama_tether)
      printf '%s' "hybrid-tether"
      ;;
    hybrid-fallback|hybrid_fallback)
      printf '%s' "hybrid-fallback"
      ;;
    full-orchestrated|full_orchestrated)
      printf '%s' "full-orchestrated"
      ;;
    *)
      printf '%s' ""
      ;;
  esac
}


validate_install_profile() {
  local profile="${1:-}"
  if [[ -z "${profile}" ]]; then
    return
  fi
  if [[ -z "$(canonical_install_profile "${profile}")" ]]; then
    say "ERROR: --install-profile must be auto-recommended, local-only/ollama-only, or local-max/ollama-max."
    exit 2
  fi
}


validate_args() {
  case "${OPENCLAW_GATEWAY_BIND}" in
    ""|loopback|lan|custom)
      ;;
    *)
      say "ERROR: --gateway-bind must be loopback, lan, or custom."
      exit 2
      ;;
  esac
  if [[ "${OPENCLAW_GATEWAY_BIND}" == "custom" && -z "${OPENCLAW_GATEWAY_CUSTOM_HOST}" ]]; then
    say "ERROR: --gateway-custom-host is required when --gateway-bind custom is used."
    exit 2
  fi
  validate_install_profile "${INSTALL_PROFILE_OVERRIDE}"
  INSTALL_PROFILE_OVERRIDE="$(canonical_install_profile "${INSTALL_PROFILE_OVERRIDE}")"
}


prompt_yn() {
  local label="$1"
  local default_value="$2"
  local value=""
  read -r -p "${label} [${default_value}]: " value || true
  value="$(printf '%s' "${value:-$default_value}" | tr '[:upper:]' '[:lower:]')"
  case "${value}" in
    y|yes) return 0 ;;
    n|no) return 1 ;;
    *) return 1 ;;
  esac
}


ensure_python() {
  local explicit_python="${PYTHON_BIN:-}"
  if [[ -n "${explicit_python}" && "${explicit_python}" != "python3" ]]; then
    if ! command -v "${explicit_python}" >/dev/null 2>&1; then
      say "ERROR: '${explicit_python}' not found."
      say "Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and rerun this installer."
      exit 1
    fi
    if ! python_supports_minimum "${explicit_python}"; then
      say "ERROR: '${explicit_python}' is below Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}."
      say "Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and rerun this installer."
      exit 1
    fi
    PYTHON_BIN="$(command -v "${explicit_python}")"
  else
    PYTHON_BIN="$(resolve_python_bin)"
    if [[ -z "${PYTHON_BIN}" ]]; then
      say "ERROR: No supported Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ interpreter was found."
      say "Tried local python commands first, then uv-managed Python if uv is installed."
      say "Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ or uv and rerun this installer."
      exit 1
    fi
  fi
  say "Using Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"
}


python_supports_minimum() {
  local candidate="$1"
  "${candidate}" - <<PY >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)
PY
}


resolve_python_bin() {
  local candidate=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_supports_minimum "${candidate}"; then
      command -v "${candidate}"
      return 0
    fi
  done

  if command -v uv >/dev/null 2>&1; then
    local uv_target=""
    for uv_target in 3.13 3.12 3.11 3.10; do
      candidate="$(uv python find "${uv_target}" 2>/dev/null || true)"
      if [[ -n "${candidate}" && -x "${candidate}" ]] && python_supports_minimum "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done

    say "No supported system Python found. Attempting uv-managed Python bootstrap..."
    for uv_target in 3.12 3.11 3.10; do
      if uv python install "${uv_target}" >/tmp/nulla_uv_python_install.log 2>&1; then
        candidate="$(uv python find "${uv_target}" 2>/dev/null || true)"
        if [[ -n "${candidate}" && -x "${candidate}" ]] && python_supports_minimum "${candidate}"; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      fi
    done
  fi

  return 1
}


create_or_update_venv() {
  if [[ -x "${VENV_DIR}/bin/python" ]] && ! python_supports_minimum "${VENV_DIR}/bin/python"; then
    say "Step 1/14: Existing virtual environment uses unsupported Python. Rebuilding..."
    rm -rf "${VENV_DIR}"
  fi
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    say "Step 1/14: Creating virtual environment..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  else
    say "Step 1/14: Virtual environment already exists."
  fi
}


install_dependencies() {
  local requirements_file="${PROJECT_ROOT}/requirements.txt"
  if [[ -f "${RUNTIME_REQUIREMENTS_FILE}" ]]; then
    requirements_file="${RUNTIME_REQUIREMENTS_FILE}"
  fi

  say "Step 2/14: Installing dependencies (this can take a while)..."
  "${VENV_DIR}/bin/python" -m pip install --upgrade "pip<26" setuptools wheel
  if dir_has_files "${WHEELHOUSE_DIR}"; then
    say "Using bundled wheelhouse from ${WHEELHOUSE_DIR}"
    if ! "${VENV_DIR}/bin/python" -m pip install --no-index --find-links "${WHEELHOUSE_DIR}" -r "${requirements_file}"; then
      say "WARNING: Bundled wheelhouse install failed. Falling back to online dependency install."
      "${VENV_DIR}/bin/python" -m pip install "${PROJECT_ROOT}[runtime,proof]"
    fi
  else
    "${VENV_DIR}/bin/python" -m pip install "${PROJECT_ROOT}[runtime,proof]"
  fi

  if dir_has_files "${WHEELHOUSE_DIR}"; then
    "${VENV_DIR}/bin/python" -m pip install --no-deps "${PROJECT_ROOT}"
  fi

  local liquefy_dir=""
  if [[ -f "${BUNDLED_LIQUEFY_DIR}/pyproject.toml" ]]; then
    liquefy_dir="${BUNDLED_LIQUEFY_DIR}"
    say "Using bundled Liquefy payload."
  elif [[ -f "${PROJECT_ROOT}/../liquefy-openclaw-integration/pyproject.toml" ]]; then
    liquefy_dir="${PROJECT_ROOT}/../liquefy-openclaw-integration"
  elif command -v git >/dev/null 2>&1; then
    liquefy_dir="${PROJECT_ROOT}/../liquefy-openclaw-integration"
    say "Cloning Liquefy into OpenClaw folder..."
    git clone --depth 1 https://github.com/Parad0x-Labs/liquefy-openclaw-integration.git "${liquefy_dir}" 2>/dev/null || true
  else
    say "WARNING: git is not available and no bundled Liquefy payload was found. Continuing without Liquefy."
  fi

  if [[ -n "${liquefy_dir}" && -f "${liquefy_dir}/pyproject.toml" ]]; then
    sed -i.bak 's/setuptools\.backends\._legacy:_Backend/setuptools.build_meta/' "${liquefy_dir}/pyproject.toml" 2>/dev/null || true
    "${VENV_DIR}/bin/python" -m pip install "${liquefy_dir}" 2>/dev/null && \
      say "Liquefy installed into NULLA venv from OpenClaw folder." || \
      say "WARNING: Liquefy installation failed. Continuing without it."
  else
    say "WARNING: Could not locate Liquefy payload. Continuing without it."
  fi
}


initialize_runtime() {
  local runtime_home="$1"
  say "Step 5/14: Initializing runtime home at ${runtime_home}"
  mkdir -p "${runtime_home}"
  NULLA_HOME="${runtime_home}" "${VENV_DIR}/bin/python" -m storage.migrations
}


bootstrap_public_hive_auth() {
  local runtime_home="$1"
  say "Step 5b/14: Ensuring public Hive auth/bootstrap..."
  local result_json=""
  result_json="$(cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" \
    "${VENV_DIR}/bin/python" -m ops.ensure_public_hive_auth \
      --project-root "${PROJECT_ROOT}" \
      --watch-host "${PUBLIC_HIVE_WATCH_HOST}" \
      --json 2>/tmp/nulla_public_hive_auth_stderr.log || true)"
  local status=""
  status="$("${VENV_DIR}/bin/python" -c 'import json,sys; data=json.loads(sys.argv[1]) if sys.argv[1].strip() else {}; print(data.get("status",""))' "${result_json}" 2>/dev/null || true)"
  case "${status}" in
    already_configured|hydrated_from_bundle|hydrated_from_local_cluster|synced_from_ssh|no_auth_required|disabled)
      say "Public Hive auth/bootstrap status: ${status}."
      ;;
    *)
      say "WARNING: Public Hive auth/bootstrap is incomplete (${status:-unknown}). Public Hive writes and watcher presence/export will stay offline until auth is configured."
      ;;
  esac
}


persist_install_profile_record() {
  local runtime_home="$1"
  local install_profile="$2"
  local model_tag="$3"
  local selected_models_csv="${4:-}"
  local bundle_id="${5:-}"
  local bundle_kind="${6:-}"
  "${VENV_DIR}/bin/python" - "$runtime_home" "$install_profile" "$model_tag" "$selected_models_csv" "$bundle_id" "$bundle_kind" <<'PY' >/dev/null 2>&1 || true
from pathlib import Path
import json
import sys

runtime_home = Path(sys.argv[1]).expanduser().resolve()
selected_models = [item.strip() for item in str(sys.argv[4]).split(",") if item.strip()]
payload = {
    "schema": "nulla.install_profile_record.v1",
    "profile_id": str(sys.argv[2]).strip(),
    "selected_model": str(sys.argv[3]).strip(),
    "selected_models": selected_models,
    "bundle_id": str(sys.argv[5]).strip(),
    "bundle_kind": str(sys.argv[6]).strip(),
}
target = runtime_home / "config" / "install-profile.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}


persist_provider_env_file() {
  local runtime_home="$1"
  local provider_env_file="${runtime_home}/config/provider-env.sh"
  local persisted=0
  mkdir -p "${runtime_home}/config"
  : > "${provider_env_file}"
  chmod 600 "${provider_env_file}"
  printf '#!/usr/bin/env bash\n' >> "${provider_env_file}"
  for name in \
    KIMI_API_KEY MOONSHOT_API_KEY NULLA_KIMI_API_KEY KIMI_BASE_URL NULLA_KIMI_BASE_URL MOONSHOT_BASE_URL KIMI_MODEL NULLA_KIMI_MODEL MOONSHOT_MODEL \
    TETHER_API_KEY NULLA_TETHER_API_KEY TETHER_BASE_URL NULLA_TETHER_BASE_URL TETHER_MODEL NULLA_TETHER_MODEL \
    OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL NULLA_REMOTE_API_KEY NULLA_REMOTE_BASE_URL NULLA_REMOTE_MODEL NULLA_CLOUD_API_KEY \
    VLLM_BASE_URL NULLA_VLLM_BASE_URL VLLM_MODEL NULLA_VLLM_MODEL VLLM_CONTEXT_WINDOW NULLA_VLLM_CONTEXT_WINDOW \
    LLAMACPP_BASE_URL NULLA_LLAMACPP_BASE_URL LLAMA_CPP_BASE_URL NULLA_LLAMA_CPP_BASE_URL \
    LLAMACPP_MODEL NULLA_LLAMACPP_MODEL LLAMACPP_CONTEXT_WINDOW NULLA_LLAMACPP_CONTEXT_WINDOW \
    LLAMACPP_MODEL_PATH NULLA_LLAMACPP_MODEL_PATH LLAMA_CPP_MODEL_PATH NULLA_LLAMA_CPP_MODEL_PATH \
    NULLA_LLAMACPP_HOST NULLA_LLAMACPP_PORT NULLA_LLAMACPP_CHAT_FORMAT NULLA_LLAMACPP_N_GPU_LAYERS \
    NULLA_LLAMACPP_CACHE NULLA_LLAMACPP_CACHE_TYPE NULLA_LLAMACPP_DRAFT_MODEL NULLA_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS \
    NULLA_LLAMACPP_REPO_ID NULLA_LLAMACPP_FILENAME; do
    local value="${!name:-}"
    if [[ -n "${value}" ]]; then
      printf 'export %s=%q\n' "${name}" "${value}" >> "${provider_env_file}"
      persisted=1
    fi
  done
  if [[ "${persisted}" -eq 0 ]]; then
    rm -f "${provider_env_file}"
    return
  fi
  say "Persisted provider runtime env to ${provider_env_file}"
}


detect_model_tag() {
  local override_model="${NULLA_OLLAMA_MODEL:-${NULLA_FORCE_OLLAMA_MODEL:-}}"
  if [[ -n "${override_model}" ]]; then
    printf '%s\n' "${override_model##*/}"
    return
  fi
  (cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -c "
from core.install_recommendations import build_install_recommendation_truth
print(build_install_recommendation_truth().primary_local_model)
") 2>/dev/null || echo "qwen3:8b"
}


detect_hardware_summary() {
  (cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -c "
import json
from core.install_recommendations import install_recommendation_machine_summary
print(json.dumps(install_recommendation_machine_summary(), ensure_ascii=False))
") 2>/dev/null || echo '{"selected_tier":"capacity-C","ollama_model":"qwen3:8b","recommended_bundle_models":["qwen3:8b","deepseek-r1:8b"]}'
}


detect_install_profile() {
  local runtime_home="$1"
  local model_tag="$2"
  local requested_profile="${3:-}"
  (cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" NULLA_INSTALL_PROFILE="${requested_profile}" "${VENV_DIR}/bin/python" -c "
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth
snapshot = build_provider_registry_snapshot()
profile = build_install_profile_truth(
    requested_profile='''${requested_profile}''' or None,
    selected_model='${model_tag}',
    runtime_home='${runtime_home}',
    provider_capability_truth=snapshot.capability_truth,
)
print(profile.profile_id)
") 2>/dev/null || echo "local-only"
}


detect_install_profile_display() {
  local install_profile="$1"
  (cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -c "
from core.runtime_install_profiles import format_install_profile_id
print(format_install_profile_id('${install_profile}', allow_auto=False))
") 2>/dev/null || echo "${install_profile}"
}


detect_install_profile_summary() {
  local runtime_home="$1"
  local model_tag="$2"
  local requested_profile="${3:-}"
  (cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" NULLA_INSTALL_PROFILE="${requested_profile}" "${VENV_DIR}/bin/python" -c "
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth
snapshot = build_provider_registry_snapshot()
profile = build_install_profile_truth(
    requested_profile='''${requested_profile}''' or None,
    selected_model='${model_tag}',
    runtime_home='${runtime_home}',
    provider_capability_truth=snapshot.capability_truth,
)
print(profile.display_summary())
") 2>/dev/null || echo "local-only -> ${model_tag}"
}


detect_install_recommendation_exports() {
  local runtime_home="$1"
  local model_tag="$2"
  (cd "${PROJECT_ROOT}" && NULLA_HOME="${runtime_home}" "${VENV_DIR}/bin/python" -c "
import shlex
from core.install_recommendations import build_install_recommendation_truth
from core.runtime_install_profiles import format_install_profile_id

recommendation = build_install_recommendation_truth(
    runtime_home='${runtime_home}',
)
fields = {
    'RECOMMENDED_DEFAULT_PROFILE': recommendation.recommended_default_profile,
    'RECOMMENDED_DEFAULT_PROFILE_DISPLAY': format_install_profile_id(recommendation.recommended_default_profile, allow_auto=False),
    'RECOMMENDED_OPTIONAL_PROFILE': recommendation.recommended_optional_profile,
    'RECOMMENDED_OPTIONAL_PROFILE_DISPLAY': format_install_profile_id(recommendation.recommended_optional_profile, allow_auto=False),
    'PRIMARY_LOCAL_MODEL': recommendation.primary_local_model,
    'CAPACITY_BUCKET': recommendation.capacity_bucket,
    'RECOMMENDED_BUNDLE_ID': recommendation.recommended_bundle_id,
    'RECOMMENDED_BUNDLE_KIND': recommendation.recommended_bundle_kind,
    'RECOMMENDED_BUNDLE_MODELS': ','.join(recommendation.recommended_bundle_models),
    'FALLBACK_BUNDLE_ID': recommendation.fallback_bundle_id,
    'FALLBACK_BUNDLE_MODELS': ','.join(recommendation.fallback_bundle_models),
    'SECONDARY_LOCAL_MODEL': recommendation.secondary_local_model,
    'SECONDARY_LOCAL_SUPPORTED': '1' if recommendation.secondary_local_supported else '0',
    'SECONDARY_LOCAL_BACKEND': recommendation.secondary_local_backend,
}
for key, value in fields.items():
    print(f'{key}={shlex.quote(str(value))}')
") 2>/dev/null || cat <<'EOF'
RECOMMENDED_DEFAULT_PROFILE=local-only
RECOMMENDED_DEFAULT_PROFILE_DISPLAY='ollama-only (local-only)'
RECOMMENDED_OPTIONAL_PROFILE=
RECOMMENDED_OPTIONAL_PROFILE_DISPLAY=
PRIMARY_LOCAL_MODEL=qwen3:8b
CAPACITY_BUCKET=B
RECOMMENDED_BUNDLE_ID=single_qwen3_8b
RECOMMENDED_BUNDLE_KIND=single
RECOMMENDED_BUNDLE_MODELS=qwen3:8b
FALLBACK_BUNDLE_ID=single_gemma3_4b
FALLBACK_BUNDLE_MODELS=gemma3:4b
SECONDARY_LOCAL_MODEL=qwen2.5:14b-gguf
SECONDARY_LOCAL_SUPPORTED=0
SECONDARY_LOCAL_BACKEND=llama.cpp
EOF
}


optional_localmax_followup_command() {
  local runtime_home="$1"
  printf '%s' "bash \"${PROJECT_ROOT}/installer/install_nulla.sh\" --runtime-home \"${runtime_home}\" --install-profile ollama-max --openclaw default"
}


prompt_install_profile() {
  local default_value="${1:-auto-recommended}"
  local profile=""
  local raw_profile=""
  read -r -p "Install profile [auto-recommended/local-only(ollama-only)/local-max(ollama-max)] [${default_value}]: " profile || true
  raw_profile="$(printf '%s' "${profile:-$default_value}" | tr '[:upper:]' '[:lower:]')"
  validate_install_profile "${raw_profile}"
  profile="$(canonical_install_profile "${raw_profile}")"
  printf '%s' "${profile}"
}


validate_selected_install_profile() {
  local runtime_home="$1"
  local model_tag="$2"
  local install_profile="$3"
  local validation_output=""

  if ! validation_output="$(NULLA_HOME="${runtime_home}" NULLA_INSTALL_PROFILE="${install_profile}" \
    "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/validate_install_profile.py" \
    "${runtime_home}" "${model_tag}" "${install_profile}" 2>&1)"; then
    say "${validation_output}"
    exit 1
  fi
}


read_secret_from_tty() {
  local label="$1"
  local value=""
  if [[ ! -r /dev/tty ]]; then
    return 1
  fi
  printf '%s' "${label}" > /dev/tty
  IFS= read -r -s value < /dev/tty || true
  printf '\n' > /dev/tty
  printf '%s' "${value}"
}


ensure_profile_remote_credentials() {
  local install_profile="$1"
  case "${install_profile}" in
    hybrid-kimi|full-orchestrated)
      if [[ -n "${KIMI_API_KEY:-${MOONSHOT_API_KEY:-${NULLA_KIMI_API_KEY:-}}}" ]]; then
        return
      fi
      if [[ "${AUTO_YES}" -eq 1 ]]; then
        say "ERROR: ${install_profile} requires KIMI_API_KEY or MOONSHOT_API_KEY for the Kimi lane."
        say "Export KIMI_API_KEY before running the one-line install, or rerun interactively so the installer can prompt and persist it."
        exit 1
      fi
      local captured_key=""
      captured_key="$(read_secret_from_tty "Enter Kimi / Moonshot API key (input hidden): ")" || true
      if [[ -z "${captured_key}" ]]; then
        say "ERROR: ${install_profile} requires KIMI_API_KEY or MOONSHOT_API_KEY."
        exit 1
      fi
      export KIMI_API_KEY="${captured_key}"
      say "Captured Kimi credential for this install session. It will be persisted into NULLA runtime config."
      ;;
  esac

  case "${install_profile}" in
    hybrid-tether)
      if [[ -n "${TETHER_API_KEY:-${NULLA_TETHER_API_KEY:-}}" && -n "${TETHER_BASE_URL:-${NULLA_TETHER_BASE_URL:-}}" ]]; then
        return
      fi
      if [[ "${AUTO_YES}" -eq 1 ]]; then
        say "ERROR: ${install_profile} requires TETHER_API_KEY and TETHER_BASE_URL for the Tether lane."
        say "Export both before running the one-line install, or rerun interactively so the installer can prompt and persist them."
        exit 1
      fi
      local captured_tether_key=""
      local captured_tether_base_url=""
      captured_tether_key="$(read_secret_from_tty "Enter Tether API key (input hidden): ")" || true
      if [[ -z "${captured_tether_key}" ]]; then
        say "ERROR: ${install_profile} requires TETHER_API_KEY."
        exit 1
      fi
      if [[ ! -r /dev/tty ]]; then
        say "ERROR: ${install_profile} requires TETHER_BASE_URL."
        exit 1
      fi
      printf '%s' "Enter Tether base URL: " > /dev/tty
      IFS= read -r captured_tether_base_url < /dev/tty || true
      printf '\n' > /dev/tty
      if [[ -z "${captured_tether_base_url}" ]]; then
        say "ERROR: ${install_profile} requires TETHER_BASE_URL."
        exit 1
      fi
      export TETHER_API_KEY="${captured_tether_key}"
      export TETHER_BASE_URL="${captured_tether_base_url}"
      say "Captured Tether credentials for this install session. They will be persisted into NULLA runtime config."
      ;;
  esac

  case "${install_profile}" in
    hybrid-fallback|full-orchestrated)
      if [[ -n "${OPENAI_API_KEY:-${NULLA_REMOTE_API_KEY:-${NULLA_CLOUD_API_KEY:-}}}" ]]; then
        return
      fi
      if [[ "${AUTO_YES}" -eq 1 ]]; then
        say "ERROR: ${install_profile} requires OPENAI_API_KEY, NULLA_REMOTE_API_KEY, or NULLA_CLOUD_API_KEY for the generic remote fallback lane."
        say "Export one of those before running the one-line install, or rerun interactively so the installer can prompt and persist it."
        exit 1
      fi
      local captured_remote_key=""
      captured_remote_key="$(read_secret_from_tty "Enter OpenAI-compatible remote API key (input hidden): ")" || true
      if [[ -z "${captured_remote_key}" ]]; then
        say "ERROR: ${install_profile} requires OPENAI_API_KEY, NULLA_REMOTE_API_KEY, or NULLA_CLOUD_API_KEY."
        exit 1
      fi
      export OPENAI_API_KEY="${captured_remote_key}"
      say "Captured generic remote credential for this install session. It will default to OpenAI unless NULLA_REMOTE_BASE_URL or OPENAI_BASE_URL overrides it."
      ;;
  esac
}


install_llamacpp_runtime_package() {
  if "${VENV_DIR}/bin/python" -c "import llama_cpp.server" >/dev/null 2>&1; then
    say "Optional llama.cpp runtime already available in the NULLA virtualenv."
    return
  fi

  say "Installing optional llama.cpp server runtime into the NULLA virtualenv..."
  local python_minor
  python_minor="$("${VENV_DIR}/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "")"
  if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    if [[ "${python_minor}" == "3.10" || "${python_minor}" == "3.11" || "${python_minor}" == "3.12" ]]; then
      if CMAKE_ARGS="" "${VENV_DIR}/bin/python" -m pip install "llama-cpp-python[server]>=0.3.0" \
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal >/tmp/nulla_llamacpp_install.log 2>&1; then
        say "Installed llama.cpp runtime from the Metal wheel index."
      fi
    fi
    if ! "${VENV_DIR}/bin/python" -c "import llama_cpp.server" >/dev/null 2>&1; then
      say "Falling back to source-build install for llama.cpp runtime."
      CMAKE_ARGS="-DGGML_METAL=on -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_APPLE_SILICON_PROCESSOR=arm64" \
        FORCE_CMAKE=1 \
        "${VENV_DIR}/bin/python" -m pip install --upgrade --force-reinstall --no-cache-dir \
        "llama-cpp-python[server]>=0.3.0" >/tmp/nulla_llamacpp_install.log 2>&1
    fi
  else
    "${VENV_DIR}/bin/python" -m pip install --upgrade "llama-cpp-python[server]>=0.3.0" \
      >/tmp/nulla_llamacpp_install.log 2>&1
  fi

  if ! "${VENV_DIR}/bin/python" -c "import llama_cpp.server" >/dev/null 2>&1; then
    say "ERROR: llama.cpp server runtime did not install cleanly."
    say "Check /tmp/nulla_llamacpp_install.log for the package build/install output."
    exit 1
  fi
  say "Optional llama.cpp server runtime installed."
}


provision_optional_llamacpp_lane() {
  local runtime_home="$1"
  say "Provisioning optional llama.cpp local specialist lane..."
  install_llamacpp_runtime_package
  local provision_exports=""
  if ! provision_exports="$("${VENV_DIR}/bin/python" "${SCRIPT_DIR}/provision_llamacpp_local.py" \
    --runtime-home "${runtime_home}" --download --emit-shell-env 2>/tmp/nulla_llamacpp_provision.log)"; then
    say "ERROR: Could not provision the optional llama.cpp local specialist lane."
    say "Check /tmp/nulla_llamacpp_provision.log for details."
    exit 1
  fi
  eval "${provision_exports}"
  say "Optional llama.cpp specialist model ready: ${NULLA_LLAMACPP_MODEL:-qwen2.5:14b-gguf}"
}


detect_required_ollama_models() {
  local install_profile="$1"
  local model_tag="$2"
  local runtime_home="$3"
  NULLA_INSTALL_PROFILE="${install_profile}" "${VENV_DIR}/bin/python" -c "
from core.runtime_install_profiles import required_ollama_models_for_profile
for item in required_ollama_models_for_profile(profile_id='${install_profile}', model_tag='${model_tag}', runtime_home='${runtime_home}'):
    print(item)
" 2>/dev/null || printf '%s\n' "${model_tag}"
}


install_playwright_runtime() {
  say "Step 3/14: Installing Playwright browser runtime..."
  if "${VENV_DIR}/bin/python" -m playwright install "${DEFAULT_BROWSER_ENGINE}" >/tmp/nulla_playwright_install.log 2>&1; then
    say "Playwright ${DEFAULT_BROWSER_ENGINE} runtime installed."
  else
    say "WARNING: Playwright browser install failed. Browser rendering may stay unavailable until fixed manually."
  fi
}


bootstrap_xsearch() {
  say "Step 4/14: Enabling local XSEARCH (SearXNG)..."
  if bash "${PROJECT_ROOT}/scripts/xsearch_up.sh" >/tmp/nulla_xsearch_install.log 2>&1; then
    if curl -sf --max-time 3 "${XSEARCH_URL}/search?q=nulla&format=json" >/dev/null 2>&1; then
      say "Local XSEARCH online at ${XSEARCH_URL}"
    else
      say "WARNING: SearXNG bootstrap ran but readiness check failed at ${XSEARCH_URL}."
    fi
  else
    say "WARNING: Could not start SearXNG automatically. Docker or docker compose may be unavailable."
  fi
}


web_runtime_exports() {
  cat <<EOF
export PLAYWRIGHT_ENABLED="1"
export ALLOW_BROWSER_FALLBACK="1"
export BROWSER_ENGINE="${DEFAULT_BROWSER_ENGINE}"
export WEB_SEARCH_PROVIDER_ORDER="${WEB_PROVIDER_ORDER}"
export SEARXNG_URL="\${SEARXNG_URL:-${XSEARCH_URL}}"
export NULLA_PUBLIC_HIVE_SSH_KEY_PATH="\${NULLA_PUBLIC_HIVE_SSH_KEY_PATH:-${PUBLIC_HIVE_SSH_KEY_PATH}}"
export NULLA_PUBLIC_HIVE_WATCH_HOST="\${NULLA_PUBLIC_HIVE_WATCH_HOST:-${PUBLIC_HIVE_WATCH_HOST}}"
"\${VENV_PY}" -m ops.ensure_public_hive_auth --project-root "\${PROJECT_ROOT}" --watch-host "\${NULLA_PUBLIC_HIVE_WATCH_HOST}" >/tmp/nulla_public_hive_auth.log 2>&1 || true
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  bash "${PROJECT_ROOT}/scripts/xsearch_up.sh" >/tmp/nulla_xsearch.log 2>&1 || true
fi
EOF
}


write_launcher() {
  local target_path="$1"
  local runtime_home="$2"
  local install_profile="$3"
  cat >"${target_path}" <<'LAUNCHER_HEAD'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
export PATH="${SCRIPT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"
VENV_PY="$(bash "${VENV_RESOLVER}")"
cd "${PROJECT_ROOT}"
LAUNCHER_HEAD
  cat >>"${target_path}" <<EOF
export NULLA_HOME="\${NULLA_HOME:-${runtime_home}}"
export NULLA_WORKSPACE_ROOT="\${NULLA_WORKSPACE_ROOT:-\${NULLA_HOME}/workspace}"
export NULLA_OPENCLAW_API_PORT="\${NULLA_OPENCLAW_API_PORT:-11435}"
export NULLA_OPENCLAW_API_URL="\${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:\${NULLA_OPENCLAW_API_PORT}}"
PROVIDER_ENV_FILE="\${NULLA_HOME}/config/provider-env.sh"
if [[ -f "\${PROVIDER_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  . "\${PROVIDER_ENV_FILE}"
fi
$(web_runtime_exports)
wait_for_http_ready() {
  local url="\$1"
  local max_attempts="\$2"
  local pid="\${3:-}"
  local consecutive_target="\${4:-2}"
  local consecutive=0
  for _ in \$(seq 1 "\${max_attempts}"); do
    if [[ -n "\${pid}" ]] && ! kill -0 "\${pid}" >/dev/null 2>&1; then
      return 1
    fi
    if curl -sf --max-time 2 "\${url}" >/dev/null 2>&1; then
      consecutive=\$((consecutive + 1))
      if [[ "\${consecutive}" -ge "\${consecutive_target}" ]]; then
        return 0
      fi
    else
      consecutive=0
    fi
    sleep 1
  done
  return 1
}

port_listening() {
  local host="\$1"
  local port="\$2"
  "\${VENV_PY}" - "\${host}" "\${port}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

spawn_detached() {
  local log_path="\$1"
  shift
  "\${VENV_PY}" - "\${log_path}" "\$@" <<'PY'
import subprocess
import sys

log_path = sys.argv[1]
command = sys.argv[2:]
with open(log_path, "ab", buffering=0) as log_stream:
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
print(child.pid)
PY
}

terminate_pid() {
  local pid="\$1"
  [[ -n "\${pid}" ]] || return 0
  if ! kill -0 "\${pid}" >/dev/null 2>&1; then
    return 0
  fi
  kill "\${pid}" >/dev/null 2>&1 || true
  for _ in \$(seq 1 10); do
    if ! kill -0 "\${pid}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  kill -9 "\${pid}" >/dev/null 2>&1 || true
}

ensure_llamacpp_server() {
  local base_url="\${LLAMACPP_BASE_URL:-\${NULLA_LLAMACPP_BASE_URL:-}}"
  local model_path="\${NULLA_LLAMACPP_MODEL_PATH:-\${LLAMACPP_MODEL_PATH:-}}"
  local model_alias="\${NULLA_LLAMACPP_MODEL:-\${LLAMACPP_MODEL:-}}"
  local host="\${NULLA_LLAMACPP_HOST:-127.0.0.1}"
  local port="\${NULLA_LLAMACPP_PORT:-8090}"
  local context_window="\${LLAMACPP_CONTEXT_WINDOW:-\${NULLA_LLAMACPP_CONTEXT_WINDOW:-32768}}"
  local chat_format="\${NULLA_LLAMACPP_CHAT_FORMAT:-chatml}"
  local n_gpu_layers="\${NULLA_LLAMACPP_N_GPU_LAYERS:--1}"
  local cache_enabled="\${NULLA_LLAMACPP_CACHE:-1}"
  local cache_type="\${NULLA_LLAMACPP_CACHE_TYPE:-ram}"
  local draft_model="\${NULLA_LLAMACPP_DRAFT_MODEL:-prompt-lookup-decoding}"
  local draft_tokens="\${NULLA_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS:-10}"
  [[ -n "\${base_url}" && -n "\${model_path}" ]] || return 0

  local health_url="\${base_url%/}/models"
  mkdir -p "\${NULLA_HOME}/logs"
  if wait_for_http_ready "\${health_url}" 2 ""; then
    return 0
  fi
  if port_listening "\${host}" "\${port}"; then
    if wait_for_http_ready "\${health_url}" 45 "" 2; then
      return 0
    fi
  fi
  if [[ ! -f "\${model_path}" ]]; then
    echo "ERROR: local-max selected but llama.cpp model file is missing at \${model_path}" >&2
    return 1
  fi
  if ! "\${VENV_PY}" -c "import llama_cpp.server" >/dev/null 2>&1; then
    echo "ERROR: local-max selected but llama.cpp server runtime is missing from the NULLA virtualenv." >&2
    return 1
  fi
  local server_log="\${NULLA_HOME}/logs/llamacpp-local.log"
  local server_pid
  local server_args=("\${VENV_PY}" -m llama_cpp.server --host "\${host}" --port "\${port}" --model "\${model_path}" --model_alias "\${model_alias}" --n_ctx "\${context_window}" --chat_format "\${chat_format}" --n_gpu_layers "\${n_gpu_layers}")
  if [[ "\${cache_enabled}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    server_args+=(--cache true --cache_type "\${cache_type}")
  fi
  if [[ -n "\${draft_model}" ]]; then
    server_args+=(--draft_model "\${draft_model}" --draft_model_num_pred_tokens "\${draft_tokens}")
  fi
  server_pid="\$(spawn_detached "\${server_log}" "\${server_args[@]}")"
  if ! wait_for_http_ready "\${health_url}" 120 "\${server_pid}" 2; then
    terminate_pid "\${server_pid}"
    echo "ERROR: llama.cpp specialist lane failed to reach \${health_url}" >&2
    return 1
  fi
  return 0
}

if [[ "\${NULLA_LAUNCHD_SUPERVISOR:-0}" == "1" ]]; then
  API_LOG_PATH="\${NULLA_API_LOG_PATH:-\${NULLA_HOME}/logs/api-supervised.log}"
  mkdir -p "\$(dirname "\${API_LOG_PATH}")"
  while true; do
    if ! ensure_llamacpp_server; then
      sleep 3
      continue
    fi
    api_pid="\$(spawn_detached "\${API_LOG_PATH}" "\${VENV_PY}" -m apps.nulla_api_server --port "\${NULLA_OPENCLAW_API_PORT}")"
    if ! wait_for_http_ready "\${NULLA_OPENCLAW_API_URL}/healthz" 240 "\${api_pid}" 5; then
      terminate_pid "\${api_pid}"
      sleep 3
      continue
    fi
    unhealthy=0
    while kill -0 "\${api_pid}" >/dev/null 2>&1; do
      if curl -sf --max-time 2 "\${NULLA_OPENCLAW_API_URL}/healthz" >/dev/null 2>&1; then
        unhealthy=0
      else
        unhealthy=\$((unhealthy + 1))
        if [[ "\${unhealthy}" -ge 3 ]]; then
          terminate_pid "\${api_pid}"
          break
        fi
      fi
      sleep 5
    done
    sleep 2
  done
fi
ensure_llamacpp_server
echo "Starting NULLA (API + mesh daemon)..."
echo "OpenClaw connects to http://127.0.0.1:11435"
exec "\${VENV_PY}" -m apps.nulla_api_server
EOF
  chmod +x "${target_path}"
}


write_chat_launcher() {
  local target_path="$1"
  local runtime_home="$2"
  local install_profile="$3"
  cat >"${target_path}" <<'LAUNCHER_HEAD'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
export PATH="${SCRIPT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"
VENV_PY="$(bash "${VENV_RESOLVER}")"
cd "${PROJECT_ROOT}"
LAUNCHER_HEAD
  cat >>"${target_path}" <<EOF
export NULLA_HOME="\${NULLA_HOME:-${runtime_home}}"
export NULLA_WORKSPACE_ROOT="\${NULLA_WORKSPACE_ROOT:-\${NULLA_HOME}/workspace}"
PROVIDER_ENV_FILE="\${NULLA_HOME}/config/provider-env.sh"
if [[ -f "\${PROVIDER_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  . "\${PROVIDER_ENV_FILE}"
fi
$(web_runtime_exports)
exec "\${VENV_PY}" -m apps.nulla_chat --platform openclaw --device openclaw
EOF
  chmod +x "${target_path}"
}


write_openclaw_launcher() {
  local target_path="$1"
  local runtime_home="$2"
  local model_tag="$3"
  local openclaw_home="$4"
  local install_profile="${5:-local-only}"
  cat >"${target_path}" <<'LAUNCHER_HEAD'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
export PATH="${SCRIPT_DIR}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
VENV_RESOLVER="${PROJECT_ROOT}/scripts/ensure_workspace_runtime.sh"
VENV_PY="$(bash "${VENV_RESOLVER}")"
cd "${PROJECT_ROOT}"
LAUNCHER_HEAD
  cat >>"${target_path}" <<EOF
MODEL_TAG="${model_tag}"
export NULLA_HOME="\${NULLA_HOME:-${runtime_home}}"
export NULLA_WORKSPACE_ROOT="\${NULLA_WORKSPACE_ROOT:-\${NULLA_HOME}/workspace}"
PROVIDER_ENV_FILE="\${NULLA_HOME}/config/provider-env.sh"
if [[ -f "\${PROVIDER_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  . "\${PROVIDER_ENV_FILE}"
fi
export NULLA_OLLAMA_MODEL="\${NULLA_OLLAMA_MODEL:-\${MODEL_TAG}}"
export NULLA_OPENCLAW_API_PORT="\${NULLA_OPENCLAW_API_PORT:-11435}"
export NULLA_OPENCLAW_API_URL="\${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:\${NULLA_OPENCLAW_API_PORT}}"
export NULLA_OPENCLAW_GATEWAY_PORT="\${NULLA_OPENCLAW_GATEWAY_PORT:-18789}"
$(web_runtime_exports)
EOF
  if [[ -n "${openclaw_home}" ]]; then
    cat >>"${target_path}" <<EOF
export OPENCLAW_HOME="${openclaw_home}"
export OPENCLAW_STATE_DIR="\${OPENCLAW_STATE_DIR:-${openclaw_home}}"
EOF
  fi
cat >>"${target_path}" <<EOF

wait_for_http_ready() {
  local url="\$1"
  local max_attempts="\$2"
  local pid="\${3:-}"
  local consecutive_target="\${4:-2}"
  local consecutive=0
  for _ in \$(seq 1 "\${max_attempts}"); do
    if [[ -n "\${pid}" ]] && ! kill -0 "\${pid}" >/dev/null 2>&1; then
      return 1
    fi
    if curl -sf --max-time 2 "\${url}" >/dev/null 2>&1; then
      consecutive=\$((consecutive + 1))
      if [[ "\${consecutive}" -ge "\${consecutive_target}" ]]; then
        return 0
      fi
    else
      consecutive=0
    fi
    sleep 1
  done
  return 1
}

port_listening() {
  local host="\$1"
  local port="\$2"
  "\${VENV_PY}" - "\${host}" "\${port}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

spawn_detached() {
  local log_path="\$1"
  shift
  "\${VENV_PY}" - "\${log_path}" "\$@" <<'PY'
import subprocess
import sys

log_path = sys.argv[1]
command = sys.argv[2:]
with open(log_path, "ab", buffering=0) as log_stream:
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
print(child.pid)
PY
}

if ! wait_for_http_ready "\${NULLA_OPENCLAW_API_URL}/healthz" 2 ""; then
  if port_listening "127.0.0.1" "\${NULLA_OPENCLAW_API_PORT}"; then
    wait_for_http_ready "\${NULLA_OPENCLAW_API_URL}/healthz" 30 "" 3
  else
    api_pid="\$(spawn_detached /tmp/nulla_api_server.log "\${PROJECT_ROOT}/Start_NULLA.sh")"
    wait_for_http_ready "\${NULLA_OPENCLAW_API_URL}/healthz" 30 "\${api_pid}" 3
  fi
fi

if ! curl -sf --max-time 2 "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" >/dev/null 2>&1; then
  if port_listening "127.0.0.1" "\${NULLA_OPENCLAW_GATEWAY_PORT}"; then
    wait_for_http_ready "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" 30 "" 2
  else
    if command -v openclaw >/dev/null 2>&1; then
      openclaw_pid="\$(spawn_detached /tmp/nulla_openclaw.log openclaw gateway run --force)"
    elif command -v ollama >/dev/null 2>&1; then
      openclaw_pid="\$(spawn_detached /tmp/nulla_openclaw.log ollama launch openclaw --yes --model "\${MODEL_TAG}")"
    fi
    wait_for_http_ready "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" 30 "\${openclaw_pid:-}" 2
  fi
fi

if ! curl -sf --max-time 2 "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" >/dev/null 2>&1 && command -v openclaw >/dev/null 2>&1; then
  openclaw_pid="\$(spawn_detached /tmp/nulla_openclaw.log openclaw gateway run --force)"
  wait_for_http_ready "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" 30 "\${openclaw_pid}" 2
fi

wait_for_http_ready "\${NULLA_OPENCLAW_API_URL}/healthz" 3 "" 2
wait_for_http_ready "http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}" 3 "" 2

GW_TOKEN="\$("\${VENV_PY}" -c "import os, sys; sys.path.insert(0, '\${PROJECT_ROOT}'); from core.openclaw_locator import discover_openclaw_paths, load_gateway_token; paths = discover_openclaw_paths(explicit_home=os.environ.get('OPENCLAW_HOME') or os.environ.get('OPENCLAW_STATE_DIR'), create_default=True); print(load_gateway_token(paths))" 2>/dev/null || true)"
OPENCLAW_URL="http://127.0.0.1:\${NULLA_OPENCLAW_GATEWAY_PORT}"
TRACE_URL="\${NULLA_OPENCLAW_API_URL}/trace"
if [[ -n "\${GW_TOKEN}" ]]; then
  OPENCLAW_URL="\${OPENCLAW_URL}/#token=\${GW_TOKEN}"
fi

if command -v open >/dev/null 2>&1; then
  open "\${OPENCLAW_URL}" >/dev/null 2>&1 || true
  open "\${TRACE_URL}" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "\${OPENCLAW_URL}" >/dev/null 2>&1 || true
  xdg-open "\${TRACE_URL}" >/dev/null 2>&1 || true
fi

echo "NULLA running. OpenClaw URL: \${OPENCLAW_URL}"
echo "NULLA trace rail: \${TRACE_URL}"
EOF
  chmod +x "${target_path}"
}


write_mac_wrapper() {
  local target_path="$1"
  local sh_launcher="$2"
  cat >"${target_path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${sh_launcher}"
EOF
  chmod +x "${target_path}"
}


create_desktop_shortcut() {
  local os_name
  os_name="$(uname)"
  if [[ "${os_name}" == "Darwin" ]]; then
    local mac_desktop="${HOME}/Desktop"
    local target="${PROJECT_ROOT}/OpenClaw_NULLA.command"
    local shortcut="${mac_desktop}/OpenClaw_NULLA.command"
    if [[ -d "${mac_desktop}" && -f "${target}" ]]; then
      cp -f "${target}" "${shortcut}"
      chmod +x "${shortcut}" || true
      DESKTOP_SHORTCUT_PATH="${shortcut}"
    fi
    return 0
  fi

  local desktop_dir="${HOME}/Desktop"
  if command -v xdg-user-dir >/dev/null 2>&1; then
    local detected
    detected="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    if [[ -n "${detected}" ]]; then
      desktop_dir="${detected}"
    fi
  fi
  if [[ ! -d "${desktop_dir}" ]]; then
    return 0
  fi

  local desktop_file="${desktop_dir}/OpenClaw_NULLA.desktop"
  cat >"${desktop_file}" <<EOF
[Desktop Entry]
Type=Application
Name=OpenClaw + NULLA
Comment=Start NULLA and open OpenClaw
Exec=/usr/bin/env bash "${PROJECT_ROOT}/OpenClaw_NULLA.sh"
Path=${PROJECT_ROOT}
Terminal=false
Categories=Utility;Development;
EOF
  chmod +x "${desktop_file}" || true
  DESKTOP_SHORTCUT_PATH="${desktop_file}"
}


install_macos_launch_agent() {
  local runtime_home="$1"
  [[ "$(uname)" == "Darwin" ]] || return 0

  local launch_agents_dir="${HOME}/Library/LaunchAgents"
  local launch_agent_path="${launch_agents_dir}/ai.nulla.runtime.plist"
  local log_dir="${runtime_home}/logs"
  mkdir -p "${launch_agents_dir}" "${log_dir}"
  cat >"${launch_agent_path}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.nulla.runtime</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${PROJECT_ROOT}/Start_NULLA.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NULLA_HOME</key>
    <string>${runtime_home}</string>
    <key>NULLA_LAUNCHD_SUPERVISOR</key>
    <string>1</string>
    <key>NULLA_API_LOG_PATH</key>
    <string>${log_dir}/api-supervised.log</string>
    <key>PATH</key>
    <string>${PROJECT_ROOT}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${log_dir}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${log_dir}/launchd.err.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)" "${launch_agent_path}" >/dev/null 2>&1 || true
  if ! launchctl bootstrap "gui/$(id -u)" "${launch_agent_path}" >/dev/null 2>&1; then
    launchctl load -w "${launch_agent_path}" >/dev/null 2>&1 || true
  fi
  LAUNCH_AGENT_PATH="${launch_agent_path}"
  say "macOS launch agent installed: ${launch_agent_path}"
}


resolve_openclaw_agent_dir() {
  local resolved=""
  case "${OPENCLAW_MODE}" in
    skip)
      resolved=""
      ;;
    default)
      resolved=""
      ;;
    path)
      resolved="${OPENCLAW_PATH_OVERRIDE}"
      ;;
    prompt)
      if [[ "${AUTO_YES}" -eq 1 ]]; then
        resolved=""
      elif prompt_yn "Create OpenClaw bridge launcher?" "Y"; then
        resolved="$(prompt "OpenClaw agent folder" "${OPENCLAW_AGENT_DEFAULT}")"
      else
        resolved=""
      fi
      ;;
    *)
      resolved=""
      ;;
  esac
  printf '%s' "${resolved}"
}


resolve_openclaw_home_override() {
  case "${OPENCLAW_MODE}" in
    default)
      printf '%s' "${HOME}/.openclaw-default"
      ;;
    *)
      printf '%s' ""
      ;;
  esac
}


resolve_openclaw_config_path() {
  local openclaw_home=""
  openclaw_home="$(resolve_openclaw_home_override)"
  if [[ -n "${openclaw_home}" ]]; then
    OPENCLAW_HOME="${openclaw_home}" OPENCLAW_STATE_DIR="${openclaw_home}" \
      "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${PROJECT_ROOT}'); from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).config_path)" 2>/dev/null || true
    return
  fi
  "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${PROJECT_ROOT}'); from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).config_path)" 2>/dev/null || true
}


resolve_openclaw_compat_dir() {
  local openclaw_home=""
  openclaw_home="$(resolve_openclaw_home_override)"
  if [[ -n "${openclaw_home}" ]]; then
    OPENCLAW_HOME="${openclaw_home}" OPENCLAW_STATE_DIR="${openclaw_home}" \
      "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${PROJECT_ROOT}'); from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).compat_bridge_dir)" 2>/dev/null || true
    return
  fi
  "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${PROJECT_ROOT}'); from core.openclaw_locator import discover_openclaw_paths; print(discover_openclaw_paths(create_default=True).compat_bridge_dir)" 2>/dev/null || true
}


setup_openclaw_bridge() {
  local agent_dir="$1"
  local runtime_home="$2"
  local agent_name="$3"
  say "Step 11/14: Creating OpenClaw bridge at ${agent_dir}"
  mkdir -p "${agent_dir}"
  write_launcher "${agent_dir}/Start_NULLA.sh" "${runtime_home}"
  write_chat_launcher "${agent_dir}/Talk_To_NULLA.sh" "${runtime_home}"
  cat >"${agent_dir}/openclaw.agent.json" <<EOF
{
  "id": "nulla",
  "name": "${agent_name}",
  "type": "external_bridge",
  "entrypoints": {
    "start": "Start_NULLA.sh",
    "chat": "Talk_To_NULLA.sh"
  },
  "runtime_home": "${runtime_home}",
  "project_root": "${PROJECT_ROOT}",
  "api_url": "http://127.0.0.1:11435"
}
EOF
  cat >"${agent_dir}/README_NULLA_BRIDGE.txt" <<EOF
NULLA OpenClaw Bridge

Files:
- Start_NULLA.sh      (boot runtime)
- Talk_To_NULLA.sh    (interactive chat)
- openclaw.agent.json (metadata for agent discovery)

If OpenClaw supports external agent discovery in this folder, NULLA should appear in the side menu.
If not, run Talk_To_NULLA.sh directly.
EOF
}


ensure_ollama_api_key() {
  local shell_rc="${HOME}/.bashrc"
  [[ -f "${HOME}/.zshrc" ]] && shell_rc="${HOME}/.zshrc"

  if ! grep -q 'OLLAMA_API_KEY' "${shell_rc}" 2>/dev/null; then
    echo 'export OLLAMA_API_KEY="ollama-local"' >> "${shell_rc}"
    say "Added OLLAMA_API_KEY to ${shell_rc}"
  fi
  export OLLAMA_API_KEY="ollama-local"
}


ensure_ollama_installed() {
  local ollama_exe=""
  if command -v ollama >/dev/null 2>&1; then
    ollama_exe="ollama"
  fi

  if [[ -z "${ollama_exe}" ]]; then
    printf '%s\n' "Step 8/14: Ollama not found. Installing..." >&2
    if [[ "$(uname)" == "Darwin" ]]; then
      if command -v brew >/dev/null 2>&1; then
        brew install ollama
      else
        curl -fsSL https://ollama.com/install.sh | sh
      fi
    else
      curl -fsSL https://ollama.com/install.sh | sh
    fi
    command -v ollama >/dev/null 2>&1 && ollama_exe="ollama"
  else
    printf '%s\n' "Step 8/14: Ollama already installed." >&2
  fi

  printf '%s' "${ollama_exe}"
}


start_ollama_server() {
  local ollama_exe="$1"
  say "Step 9/14: Ensuring Ollama server is running..."
  if [[ -z "${ollama_exe}" ]]; then
    say "WARNING: Ollama is unavailable. Install manually from https://ollama.com/download"
    return
  fi
  if ! curl -sf http://localhost:11434 >/dev/null 2>&1; then
    "${ollama_exe}" serve >/tmp/nulla_ollama.log 2>&1 &
    sleep 5
  fi
}


configure_openclaw_with_ollama() {
  local ollama_exe="$1"
  local model_tag="$2"
  local openclaw_enabled="$3"
  local openclaw_home="$4"

  if [[ "${openclaw_enabled}" != "1" ]]; then
    say "Step 10/14: OpenClaw integration skipped."
    return
  fi
  if [[ -z "${ollama_exe}" ]]; then
    say "Step 10/14: OpenClaw integration deferred because Ollama is unavailable."
    return
  fi

  if [[ -n "${openclaw_home}" ]]; then
    say "Step 10/14: Skipping Ollama OpenClaw auto-config for isolated home ${openclaw_home}."
    return
  fi

  say "Step 10/14: Configuring OpenClaw through Ollama..."
  if ! "${ollama_exe}" launch openclaw --yes --config --model "${model_tag}" >/tmp/nulla_openclaw_config.log 2>&1; then
    say "WARNING: OpenClaw auto-config via Ollama failed. Continuing with direct config patch."
  fi
}


register_openclaw() {
  local runtime_home="$1"
  local model_tag="$2"
  local openclaw_agent_dir="$3"
  local openclaw_enabled="$4"
  local openclaw_home="$5"
  local agent_name="$6"

  if [[ "${openclaw_enabled}" != "1" ]]; then
    say "Step 11/14: OpenClaw registration skipped."
    return
  fi

  if [[ -n "${openclaw_agent_dir}" ]]; then
    setup_openclaw_bridge "${openclaw_agent_dir}" "${runtime_home}" "${agent_name}"
  fi

  say "Step 11/14: Registering NULLA in OpenClaw..."
  if [[ -n "${openclaw_home}" ]]; then
    if ! OPENCLAW_HOME="${openclaw_home}" OPENCLAW_STATE_DIR="${openclaw_home}" \
      NULLA_OPENCLAW_GATEWAY_BIND="${OPENCLAW_GATEWAY_BIND}" \
      NULLA_OPENCLAW_GATEWAY_CUSTOM_HOST="${OPENCLAW_GATEWAY_CUSTOM_HOST}" \
      "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/register_openclaw_agent.py" "${PROJECT_ROOT}" "${runtime_home}" "${model_tag}" "${agent_name}"; then
      say "WARNING: Could not register NULLA in OpenClaw config. You can register manually later."
    fi
    return
  fi
  if ! NULLA_OPENCLAW_GATEWAY_BIND="${OPENCLAW_GATEWAY_BIND}" \
    NULLA_OPENCLAW_GATEWAY_CUSTOM_HOST="${OPENCLAW_GATEWAY_CUSTOM_HOST}" \
    "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/register_openclaw_agent.py" "${PROJECT_ROOT}" "${runtime_home}" "${model_tag}" "${agent_name}"; then
    say "WARNING: Could not register NULLA in OpenClaw config. You can register manually later."
  fi
}


seed_agent_identity() {
  local runtime_home="$1"
  local agent_name="$2"
  local resolved_name=""
  resolved_name="$(NULLA_HOME="${runtime_home}" \
    "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/seed_identity.py" --agent-name "${agent_name}" 2>/dev/null || printf '%s' "${agent_name}")"
  printf '%s' "${resolved_name:-$agent_name}"
}


pull_models() {
  local ollama_exe="$1"
  local install_profile="$2"
  local model_tag="$3"
  local runtime_home="$4"
  if [[ -z "${ollama_exe}" ]]; then
    say "Step 12/14: Model pull skipped because Ollama is unavailable."
    return
  fi

  say "Step 12/14: Pulling AI model (this may take a while)..."
  local required_model=""
  while IFS= read -r required_model; do
    [[ -n "${required_model}" ]] || continue
    if "${ollama_exe}" list 2>/dev/null | grep -qi "${required_model}"; then
      say "Model ${required_model} already available."
      continue
    fi
    say "Downloading ${required_model}..."
    "${ollama_exe}" pull "${required_model}" || say "WARNING: Model pull failed. Run manually: ollama pull ${required_model}"
  done < <(detect_required_ollama_models "${install_profile}" "${model_tag}" "${runtime_home}")
}


configure_liquefy() {
  say "Step 13/14: Configuring Liquefy..."
  "${VENV_DIR}/bin/python" -c "
import json
from pathlib import Path
d = Path.home() / '.liquefy'
d.mkdir(parents=True, exist_ok=True)
p = d / 'config.json'
c = {'enabled': True, 'version': '1.1.0', 'mode': 'auto', 'vault_dir': str(d / 'vault'), 'profile': 'default', 'policy_mode': 'strict', 'verify_mode': 'full', 'encrypt': False, 'leak_scan': True}
p.write_text(json.dumps(c, indent=2), encoding='utf-8')
print('Liquefy config written to ' + str(p))
" 2>/dev/null || say "WARNING: Could not configure Liquefy."
}


write_install_receipt() {
  local runtime_home="$1"
  local model_tag="$2"
  local openclaw_enabled="$3"
  local ollama_exe="$4"
  local openclaw_agent_dir="$5"
  local launch_agent_path="$6"
  local openclaw_config_path=""
  local actual_agent_dir=""
  local receipt_path=""

  if [[ "${openclaw_enabled}" == "1" ]]; then
    openclaw_config_path="$(resolve_openclaw_config_path)"
    actual_agent_dir="$(resolve_openclaw_compat_dir)"
    if [[ -n "${openclaw_agent_dir}" ]]; then
      actual_agent_dir="${openclaw_agent_dir}"
    fi
  fi

  say "Step 14/14: Writing install receipt..."
  receipt_path="$("${VENV_DIR}/bin/python" "${SCRIPT_DIR}/write_install_receipt.py" \
    "${PROJECT_ROOT}" \
    "${runtime_home}" \
    "${model_tag}" \
    "${openclaw_enabled}" \
    "${openclaw_config_path}" \
    "${actual_agent_dir}" \
    "${ollama_exe:-}" \
    "${launch_agent_path:-}" 2>/dev/null || true)"
  if [[ -n "${receipt_path}" ]]; then
    say "Install receipt written to ${receipt_path}"
  else
    say "WARNING: Could not write install receipt."
  fi
}


run_install_doctor() {
  local runtime_home="$1"
  local model_tag="$2"
  local openclaw_enabled="$3"
  local ollama_exe="$4"
  local openclaw_agent_dir="$5"
  local launch_agent_path="$6"
  local openclaw_config_path=""
  local actual_agent_dir=""
  local report_path=""

  if [[ "${openclaw_enabled}" == "1" ]]; then
    openclaw_config_path="$(resolve_openclaw_config_path)"
    actual_agent_dir="$(resolve_openclaw_compat_dir)"
    if [[ -n "${openclaw_agent_dir}" ]]; then
      actual_agent_dir="${openclaw_agent_dir}"
    fi
  fi

  say "Post-install: Running NULLA doctor..."
  report_path="$("${VENV_DIR}/bin/python" "${SCRIPT_DIR}/doctor.py" \
    "${PROJECT_ROOT}" \
    "${runtime_home}" \
    "${model_tag}" \
    "${openclaw_enabled}" \
    "${openclaw_config_path}" \
    "${actual_agent_dir}" \
    "${ollama_exe:-}" \
    "${launch_agent_path:-}" 2>/dev/null || true)"
  if [[ -n "${report_path}" ]]; then
    say "Doctor report written to ${report_path}"
  else
    say "WARNING: Could not generate doctor report."
  fi
}


main() {
  say "==============================================="
  say "NULLA Installer (Linux/macOS)"
  say "This will set up NULLA in the extracted folder."
  say "==============================================="
  say
  ensure_python

  local runtime_home
  local agent_name_default="NULLA"
  if [[ -n "${RUNTIME_HOME_OVERRIDE}" ]]; then
    runtime_home="${RUNTIME_HOME_OVERRIDE}"
  elif [[ "${AUTO_YES}" -eq 1 ]]; then
    runtime_home="${NULLA_HOME_DEFAULT}"
  else
    runtime_home="$(prompt "NULLA runtime folder" "${NULLA_HOME_DEFAULT}")"
  fi

  if [[ -n "${AGENT_NAME_OVERRIDE}" ]]; then
    agent_name_default="${AGENT_NAME_OVERRIDE}"
  fi
  local agent_name="${agent_name_default}"
  if [[ "${AUTO_YES}" -eq 0 && -z "${AGENT_NAME_OVERRIDE}" ]]; then
    agent_name="$(prompt "Agent display name" "${agent_name_default}")"
  fi

  create_or_update_venv
  install_dependencies
  install_playwright_runtime
  bootstrap_xsearch
  initialize_runtime "${runtime_home}"
  bootstrap_public_hive_auth "${runtime_home}"
  agent_name="$(seed_agent_identity "${runtime_home}" "${agent_name}")"

  local hardware_summary
  local model_tag
  local primary_local_model
  local capacity_bucket
  local recommended_bundle_id
  local recommended_bundle_kind
  local recommended_bundle_models
  local fallback_bundle_id
  local fallback_bundle_models
  local recommended_optional_profile
  local recommended_optional_profile_display
  local secondary_local_model
  local secondary_local_supported
  local secondary_local_backend
  local optional_followup_command
  local recommended_install_profile
  local recommended_install_profile_display
  local requested_install_profile
  local install_profile
  local install_profile_display
  local install_profile_summary
  local openclaw_home_override
  hardware_summary="$(detect_hardware_summary)"
  model_tag="$(detect_model_tag)"
  eval "$(detect_install_recommendation_exports "${runtime_home}" "${model_tag}")"
  primary_local_model="${PRIMARY_LOCAL_MODEL:-${model_tag}}"
  model_tag="${primary_local_model}"
  capacity_bucket="${CAPACITY_BUCKET:-unknown}"
  recommended_bundle_id="${RECOMMENDED_BUNDLE_ID:-}"
  recommended_bundle_kind="${RECOMMENDED_BUNDLE_KIND:-}"
  recommended_bundle_models="${RECOMMENDED_BUNDLE_MODELS:-${model_tag}}"
  fallback_bundle_id="${FALLBACK_BUNDLE_ID:-}"
  fallback_bundle_models="${FALLBACK_BUNDLE_MODELS:-}"
  recommended_optional_profile="${RECOMMENDED_OPTIONAL_PROFILE:-}"
  recommended_optional_profile_display="${RECOMMENDED_OPTIONAL_PROFILE_DISPLAY:-}"
  secondary_local_model="${SECONDARY_LOCAL_MODEL:-qwen2.5:14b-gguf}"
  secondary_local_supported="${SECONDARY_LOCAL_SUPPORTED:-0}"
  secondary_local_backend="${SECONDARY_LOCAL_BACKEND:-llama.cpp}"
  optional_followup_command="$(optional_localmax_followup_command "${runtime_home}")"
  recommended_install_profile="$(detect_install_profile "${runtime_home}" "${model_tag}" "")"
  requested_install_profile="${INSTALL_PROFILE_OVERRIDE}"
  if [[ -z "${requested_install_profile}" && "${AUTO_YES}" -eq 0 ]]; then
    requested_install_profile="$(prompt_install_profile "auto-recommended")"
  fi
  if [[ -z "${requested_install_profile}" ]]; then
    requested_install_profile="auto-recommended"
  fi
  install_profile="$(detect_install_profile "${runtime_home}" "${model_tag}" "${requested_install_profile}")"
  recommended_install_profile_display="$(detect_install_profile_display "${recommended_install_profile}")"
  install_profile_display="$(detect_install_profile_display "${install_profile}")"
  ensure_profile_remote_credentials "${install_profile}"
  if [[ "${install_profile}" == "local-max" || "${install_profile}" == "full-orchestrated" ]]; then
    provision_optional_llamacpp_lane "${runtime_home}"
  fi
  install_profile_summary="$(detect_install_profile_summary "${runtime_home}" "${model_tag}" "${requested_install_profile}")"
  openclaw_home_override="$(resolve_openclaw_home_override)"
  say "Step 6/14: Hardware probe complete."
  say "Detected: ${hardware_summary}"
  say "Capacity bucket: ${capacity_bucket}"
  say "Primary local model: ${primary_local_model}"
  say "Recommended local bundle: ${recommended_bundle_id:-unknown} (${recommended_bundle_kind:-unknown}) -> ${recommended_bundle_models}"
  if [[ -n "${fallback_bundle_models}" ]]; then
    say "Lighter fallback bundle: ${fallback_bundle_id:-unknown} -> ${fallback_bundle_models}"
  fi
  say "Recommended profile: ${recommended_install_profile_display}"
  say "Install profile: ${install_profile_display}"
  if [[ "${secondary_local_supported}" == "1" && -n "${recommended_optional_profile_display}" ]]; then
    say "Optional stronger lane: ${recommended_optional_profile_display} via ${secondary_local_backend} (${secondary_local_model})"
    say "Optional switch command: ${optional_followup_command}"
  else
    say "Optional stronger lane: not recommended on this machine/runtime."
  fi
  say "Profile summary: ${install_profile_summary}"
  validate_selected_install_profile "${runtime_home}" "${model_tag}" "${install_profile}"
  persist_install_profile_record "${runtime_home}" "${install_profile}" "${model_tag}" "${recommended_bundle_models}" "${recommended_bundle_id}" "${recommended_bundle_kind}"
  persist_provider_env_file "${runtime_home}"

  say "Step 7/14: Creating launchers..."
  write_launcher "${PROJECT_ROOT}/Start_NULLA.sh" "${runtime_home}" "${install_profile}"
  write_chat_launcher "${PROJECT_ROOT}/Talk_To_NULLA.sh" "${runtime_home}" "${install_profile}"
  write_openclaw_launcher "${PROJECT_ROOT}/OpenClaw_NULLA.sh" "${runtime_home}" "${model_tag}" "${openclaw_home_override}" "${install_profile}"
  write_mac_wrapper "${PROJECT_ROOT}/Start_NULLA.command" "${PROJECT_ROOT}/Start_NULLA.sh"
  write_mac_wrapper "${PROJECT_ROOT}/Talk_To_NULLA.command" "${PROJECT_ROOT}/Talk_To_NULLA.sh"
  write_mac_wrapper "${PROJECT_ROOT}/OpenClaw_NULLA.command" "${PROJECT_ROOT}/OpenClaw_NULLA.sh"
  create_desktop_shortcut
  if [[ -n "${DESKTOP_SHORTCUT_PATH}" ]]; then
    say "Desktop shortcut created: ${DESKTOP_SHORTCUT_PATH}"
  fi

  local openclaw_agent_dir=""
  local openclaw_enabled="1"
  if [[ "${OPENCLAW_MODE}" == "skip" ]]; then
    openclaw_enabled="0"
  elif [[ "${OPENCLAW_MODE}" == "prompt" && "${AUTO_YES}" -eq 1 ]]; then
    openclaw_enabled="0"
  fi
  openclaw_agent_dir="$(resolve_openclaw_agent_dir)"

  ensure_ollama_api_key
  local ollama_exe
  ollama_exe="$(ensure_ollama_installed)"
  start_ollama_server "${ollama_exe}"
  configure_openclaw_with_ollama "${ollama_exe}" "${model_tag}" "${openclaw_enabled}" "${openclaw_home_override}"
  register_openclaw "${runtime_home}" "${model_tag}" "${openclaw_agent_dir}" "${openclaw_enabled}" "${openclaw_home_override}" "${agent_name}"
  pull_models "${ollama_exe}" "${install_profile}" "${model_tag}" "${runtime_home}"
  configure_liquefy
  install_macos_launch_agent "${runtime_home}"
  write_install_receipt "${runtime_home}" "${model_tag}" "${openclaw_enabled}" "${ollama_exe}" "${openclaw_agent_dir}" "${LAUNCH_AGENT_PATH}"
  run_install_doctor "${runtime_home}" "${model_tag}" "${openclaw_enabled}" "${ollama_exe}" "${openclaw_agent_dir}" "${LAUNCH_AGENT_PATH}"
  if [[ "${AUTO_YES}" -eq 0 && "${install_profile}" == "local-only" && "${secondary_local_supported}" == "1" ]]; then
    if prompt_yn "Install the optional stronger local coding/verifier lane now?" "N"; then
      provision_optional_llamacpp_lane "${runtime_home}"
      install_profile="local-max"
      install_profile_display="$(detect_install_profile_display "${install_profile}")"
      validate_selected_install_profile "${runtime_home}" "${model_tag}" "${install_profile}"
      persist_install_profile_record "${runtime_home}" "${install_profile}" "${model_tag}" "${recommended_bundle_models}" "${recommended_bundle_id}" "${recommended_bundle_kind}"
      persist_provider_env_file "${runtime_home}"
      write_install_receipt "${runtime_home}" "${model_tag}" "${openclaw_enabled}" "${ollama_exe}" "${openclaw_agent_dir}" "${LAUNCH_AGENT_PATH}"
      run_install_doctor "${runtime_home}" "${model_tag}" "${openclaw_enabled}" "${ollama_exe}" "${openclaw_agent_dir}" "${LAUNCH_AGENT_PATH}"
      say "Optional stronger local lane activated: ${install_profile_display}"
    else
      say "Optional stronger local lane skipped. Add it later with:"
      say "${optional_followup_command}"
    fi
  elif [[ "${AUTO_YES}" -eq 1 && "${install_profile}" == "local-only" && "${secondary_local_supported}" == "1" ]]; then
    say "Optional stronger local lane available but not auto-installed in non-interactive mode."
    say "Add it later with: ${optional_followup_command}"
  fi

  say
  say "==============================================="
  say "NULLA is installed. It IS your OpenClaw now."
  say "==============================================="
  say
  say "Visible agent name: ${agent_name}"
  say "Selected model: ${model_tag}"
  say "Selected bundle: ${recommended_bundle_id:-unknown} -> ${recommended_bundle_models}"
  say "Profile: ${install_profile_display}"
  say "Start:   ${PROJECT_ROOT}/OpenClaw_NULLA.sh"
  if [[ -n "${DESKTOP_SHORTCUT_PATH}" ]]; then
    say "Desktop: ${DESKTOP_SHORTCUT_PATH}"
  fi
  if [[ -n "${LAUNCH_AGENT_PATH}" ]]; then
    say "Launchd: ${LAUNCH_AGENT_PATH}"
  fi
  say "Chat:    ${PROJECT_ROOT}/Talk_To_NULLA.sh"
  say "Probe:   ${PROJECT_ROOT}/Probe_NULLA_Stack.sh"
  say "Credits: cd '${PROJECT_ROOT}' && ${VENV_DIR}/bin/python -m apps.nulla_cli credits"
  say "Profiles: cd '${PROJECT_ROOT}' && ${VENV_DIR}/bin/python -m apps.nulla_cli install-profile"
  say "Ollama only: cd '${PROJECT_ROOT}' && ${VENV_DIR}/bin/python -m apps.nulla_cli install-profile --set ollama-only"
  say "Ollama max:  cd '${PROJECT_ROOT}' && ${VENV_DIR}/bin/python -m apps.nulla_cli install-profile --set ollama-max"
  say
  say "NULLA is now wired for OpenClaw-friendly launch,"
  say "with Ollama checked, hardware-tier model selection applied,"
  say "starter credits seeded through the work-based credit model,"
  say "Playwright browser rendering enabled through install launchers,"
  say "local SearXNG bootstrap attempted on install and launcher start,"
  say "and an install_receipt.json written for support/debugging."

  if [[ "${AUTO_START}" -eq 1 ]]; then
    say
    say "Launching NULLA now..."
    say "Verifying live launch through the shell launcher..."
    if [[ -n "${LAUNCH_AGENT_PATH}" ]]; then
      local launchd_runtime_ready=0
      local launchd_runtime_consecutive=0
      for _ in $(seq 1 240); do
        if curl -sf --max-time 2 "http://127.0.0.1:11435/healthz" >/dev/null 2>&1 && \
          curl -sf --max-time 2 "http://127.0.0.1:11435/v1/models" >/dev/null 2>&1; then
          launchd_runtime_consecutive=$((launchd_runtime_consecutive + 1))
          if [[ "${launchd_runtime_consecutive}" -ge 5 ]]; then
            launchd_runtime_ready=1
            break
          fi
        else
          launchd_runtime_consecutive=0
        fi
        sleep 1
      done
      if [[ "${launchd_runtime_ready}" -eq 1 ]]; then
        say "Launchd runtime verified at http://127.0.0.1:11435 (stable health + /v1/models)"
        exit 0
      fi
      say "ERROR: launchd installed NULLA, but the API did not stay healthy long enough to verify /v1/models within 240 seconds."
      exit 1
    fi
    exec "${PROJECT_ROOT}/OpenClaw_NULLA.sh"
  fi
}


parse_args "$@"
validate_args
main
