from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from core.hardware_tier import probe_machine, select_qwen_tier
from core.local_model_bundles import (
    installed_ollama_role_for_model,
    manifest_profile_for_model,
    resolve_local_bundle_recommendation,
)
from core.local_ollama_inventory import env_flag_enabled, installed_ollama_model_names, is_text_generation_ollama_model
from core.local_specialist_lane import secondary_local_model
from core.model_registry import ModelRegistry
from core.runtime_install_profiles import normalize_install_profile_id, required_ollama_models_for_profile
from storage.model_provider_manifest import ModelProviderManifest

_DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
_DEFAULT_KIMI_MODEL = "kimi-k2"
_KIMI_API_KEY_ENV_NAMES = ("KIMI_API_KEY", "MOONSHOT_API_KEY", "NULLA_KIMI_API_KEY")
_KIMI_BASE_URL_ENV_NAMES = ("KIMI_BASE_URL", "NULLA_KIMI_BASE_URL", "MOONSHOT_BASE_URL")
_KIMI_MODEL_ENV_NAMES = ("KIMI_MODEL", "NULLA_KIMI_MODEL", "MOONSHOT_MODEL")
_DEFAULT_GENERIC_REMOTE_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_GENERIC_REMOTE_MODEL = "gpt-4.1-mini"
_GENERIC_REMOTE_API_KEY_ENV_NAMES = ("OPENAI_API_KEY", "NULLA_REMOTE_API_KEY", "NULLA_CLOUD_API_KEY")
_GENERIC_REMOTE_BASE_URL_ENV_NAMES = ("NULLA_REMOTE_BASE_URL", "OPENAI_BASE_URL")
_GENERIC_REMOTE_MODEL_ENV_NAMES = ("NULLA_REMOTE_MODEL", "OPENAI_MODEL")
_DEFAULT_TETHER_MODEL = "tether-sonic"
_TETHER_API_KEY_ENV_NAMES = ("TETHER_API_KEY", "NULLA_TETHER_API_KEY")
_TETHER_BASE_URL_ENV_NAMES = ("TETHER_BASE_URL", "NULLA_TETHER_BASE_URL")
_TETHER_MODEL_ENV_NAMES = ("TETHER_MODEL", "NULLA_TETHER_MODEL")
_DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_VLLM_CONTEXT_WINDOW = 131072
_DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"
_DEFAULT_LLAMACPP_CONTEXT_WINDOW = 32768
_DEFAULT_MLX_BASE_URL = "http://127.0.0.1:8096/v1"
_DEFAULT_MLX_CONTEXT_WINDOW = 32768
_DEFAULT_MLX_MODEL = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
_FAST_LOCAL_DEFAULT_MODEL = "nulla-qwen3-30b-a3b:nothink"


def preferred_fast_local_model(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    explicit_inventory = str(env_map.get("NULLA_INSTALLED_OLLAMA_MODELS") or "").strip()
    if not explicit_inventory and not env_flag_enabled(env_map, "NULLA_ALLOW_OLLAMA_TAGS_FOR_DEFAULT", default=False):
        return ""
    installed = {item.strip().lower() for item in installed_ollama_model_names(env=env_map)}
    return _FAST_LOCAL_DEFAULT_MODEL if _FAST_LOCAL_DEFAULT_MODEL.lower() in installed else ""


def default_runtime_model_tag(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    fast_model = preferred_fast_local_model(env=env_map)
    if fast_model:
        return fast_model
    try:
        recommendation = resolve_local_bundle_recommendation(
            probe=probe_machine(),
            free_disk_gb=_default_free_disk_gb(),
            secondary_local_model_name=secondary_local_model(env_map),
        )
        primary_model = str(recommendation.recommended_bundle.primary_model or "").strip()
        if primary_model:
            return primary_model
    except Exception:
        pass
    return str(select_qwen_tier(probe_machine()).ollama_tag or "").strip() or "qwen3:8b"


def _bundle_role_for_model(required_models: tuple[str, ...], *, primary_model: str, model_name: str) -> str:
    clean = str(model_name or "").strip().lower()
    if clean == str(primary_model or "").strip().lower():
        return "general"
    if required_models and clean == str(required_models[-1]).strip().lower() and len(required_models) > 1:
        return "reasoning"
    return installed_ollama_role_for_model(model_name=clean, primary_model=primary_model)


def ensure_default_runtime_providers(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    env: Mapping[str, str] | None = None,
    install_profile: str | None = None,
    runtime_home: str | None = None,
) -> tuple[str, ...]:
    env_map = os.environ if env is None else env
    changed: list[str] = []
    local_model = str(model_tag or "").strip() or default_runtime_model_tag(env=env_map)
    active_profile = normalize_install_profile_id(install_profile, allow_auto=False)
    required_models = required_ollama_models_for_profile(
        profile_id=active_profile or "local-only",
        model_tag=local_model,
        runtime_home=runtime_home,
        env=env_map,
    )
    primary_model = required_models[0] if required_models else local_model
    provider_models = _runtime_provider_model_roles(
        required_models=required_models or (local_model,),
        primary_model=primary_model,
        env=env_map,
    )
    for bundle_model, role in provider_models:
        if _ensure_local_ollama_provider(registry, model_tag=bundle_model, bundle_role=role):
            changed.append(f"ollama-local:{bundle_model}")
    if _profile_allows_aux_local_providers(active_profile, runtime_home=runtime_home):
        llamacpp_provider_id = _ensure_llamacpp_provider(registry, model_name=local_model, env=env_map)
        if llamacpp_provider_id:
            changed.append(llamacpp_provider_id)
        llamacpp_deep_provider_id = _ensure_llamacpp_deep_provider(registry, env=env_map)
        if llamacpp_deep_provider_id:
            changed.append(llamacpp_deep_provider_id)
        vllm_provider_id = _ensure_vllm_provider(registry, model_name=local_model, env=env_map)
        if vllm_provider_id:
            changed.append(vllm_provider_id)
        mlx_provider_id = _ensure_mlx_provider(registry, model_name=local_model, env=env_map)
        if mlx_provider_id:
            changed.append(mlx_provider_id)
    if _profile_allows_kimi_provider(active_profile):
        kimi_provider_id = _ensure_kimi_provider(registry, env=env_map)
        if kimi_provider_id:
            changed.append(kimi_provider_id)
    if _profile_allows_generic_remote_provider(active_profile):
        generic_remote_provider_id = _ensure_generic_remote_provider(registry, env=env_map)
        if generic_remote_provider_id:
            changed.append(generic_remote_provider_id)
    tether_provider_id = _ensure_tether_provider(registry, env=env_map)
    if tether_provider_id:
        changed.append(tether_provider_id)
    return tuple(changed)


def _runtime_provider_model_roles(
    *,
    required_models: tuple[str, ...],
    primary_model: str,
    env: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    model_roles: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(model_name: str, role: str) -> None:
        clean_model = str(model_name or "").strip()
        if not clean_model:
            return
        key = clean_model.lower()
        if key in seen:
            return
        seen.add(key)
        model_roles.append((clean_model, str(role or "general").strip() or "general"))

    for bundle_model in required_models:
        add(bundle_model, _bundle_role_for_model(required_models, primary_model=primary_model, model_name=bundle_model))

    if not env_flag_enabled(env, "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS", default=False):
        return tuple(model_roles)

    for installed_model in installed_ollama_model_names(env=env):
        if not is_text_generation_ollama_model(installed_model):
            continue
        add(
            installed_model,
            installed_ollama_role_for_model(model_name=installed_model, primary_model=primary_model),
        )
    return tuple(model_roles)


def _ollama_context_window_for_bundle_role(bundle_role: str) -> int:
    clean = str(bundle_role or "").strip().lower()
    if clean == "lightweight_utility":
        return 2048
    if clean == "heavy_reasoning":
        return 1024
    return 4096


def _ensure_local_ollama_provider(registry: ModelRegistry, *, model_tag: str, bundle_role: str) -> bool:
    existing = registry.get_manifest("ollama-local", model_tag)
    if existing is not None and not isinstance(existing, ModelProviderManifest):
        existing = None
    context_window = _ollama_context_window_for_bundle_role(bundle_role)
    manifest_profile = manifest_profile_for_model(model_name=model_tag, bundle_role=bundle_role)
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    expected_role = str((getattr(existing, "metadata", {}) or {}).get("bundle_role") or "").strip().lower() if existing else ""
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_prewarm = dict(existing_runtime_config.get("prewarm") or {})
    existing_prewarm_options = dict(existing_prewarm.get("options") or {})
    uses_native_ollama_chat = not str(existing_runtime_config.get("api_path") or "").strip()
    disables_thinking = existing_runtime_config.get("think") is False
    has_context_contract = (
        int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0) == context_window
        and int(existing_prewarm_options.get("num_ctx") or 0) == context_window
        and uses_native_ollama_chat
        and disables_thinking
    )
    expected_tps = float(manifest_profile.get("tokens_per_second") or 0.0)
    has_measurement_contract = expected_tps <= 0 or float(existing_metadata.get("tokens_per_second") or 0.0) == expected_tps
    if existing and existing.enabled and has_license and expected_role == str(bundle_role or "").strip().lower() and has_context_contract and has_measurement_contract:
        return False
    parameter_size = str(manifest_profile.get("parameter_count") or parameter_size_for_model(model_tag))
    license_reference = str(manifest_profile.get("license_reference") or "user-managed")
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_tag,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name=str(manifest_profile.get("license_name") or "user-managed"),
        license_reference=license_reference,
        license_url_or_reference=license_reference,
        weight_location="external",
        runtime_dependency="ollama",
        notes=f"{manifest_profile.get('notes') or 'Local Ollama lane.'} ({parameter_size}) — auto-registered by NULLA runtime",
        capabilities=list(manifest_profile.get("capabilities") or ()),
        runtime_config={
            "base_url": "http://127.0.0.1:11434",
            "health_path": "/v1/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.7,
            "think": False,
            "supports_json_mode": False,
            "context_window": context_window,
            "prewarm": {
                "strategy": "ollama_chat",
                "keep_alive": "15m",
                "message": " ",
                "timeout_seconds": 45,
                "options": {
                    "num_ctx": context_window,
                    "num_predict": 1,
                },
            },
        },
        metadata={
            "runtime_family": "ollama",
            "confidence_baseline": float(manifest_profile.get("confidence_baseline") or 0.65),
            "parameter_count": parameter_size,
            "tokens_per_second": float(manifest_profile.get("tokens_per_second") or 0.0),
            "quantization": str(manifest_profile.get("quantization") or "").strip(),
            "orchestration_role": str(manifest_profile.get("orchestration_role") or "drone"),
            "bundle_role": str(manifest_profile.get("bundle_role") or bundle_role or "general"),
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": list(manifest_profile.get("tool_support") or ()),
            "max_safe_concurrency": 1,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return True


def _ensure_kimi_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_KIMI_API_KEY_ENV_NAMES)
    if not api_key:
        return ""
    api_key_env = next((name for name in _KIMI_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "KIMI_API_KEY")
    model_name = _env_first(env, *_KIMI_MODEL_ENV_NAMES) or _DEFAULT_KIMI_MODEL
    existing = registry.get_manifest("kimi-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    base_url = _env_first(env, *_KIMI_BASE_URL_ENV_NAMES) or _DEFAULT_KIMI_BASE_URL
    manifest = ModelProviderManifest(
        provider_name="kimi-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="Kimi via Moonshot OpenAI-compatible API — auto-registered when a Kimi/Moonshot API key is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.78,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_vllm_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "VLLM_BASE_URL", "NULLA_VLLM_BASE_URL")
    if not base_url:
        return ""
    resolved_model_name = _env_first(env, "VLLM_MODEL", "NULLA_VLLM_MODEL") or model_name or default_runtime_model_tag(env=env)
    existing = registry.get_manifest("vllm-local", resolved_model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    context_window = _env_int(
        env,
        "VLLM_CONTEXT_WINDOW",
        "NULLA_VLLM_CONTEXT_WINDOW",
        default=_DEFAULT_VLLM_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "VLLM_MAX_SAFE_CONCURRENCY",
        "NULLA_VLLM_MAX_SAFE_CONCURRENCY",
        default=2,
    )
    manifest = ModelProviderManifest(
        provider_name="vllm-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="vllm",
        notes="Local vLLM OpenAI-compatible lane — auto-registered when VLLM_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.74,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "tool_calls", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_generic_remote_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_GENERIC_REMOTE_API_KEY_ENV_NAMES)
    if not api_key:
        return ""
    api_key_env = next(
        (name for name in _GENERIC_REMOTE_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()),
        "OPENAI_API_KEY",
    )
    model_name = _env_first(env, *_GENERIC_REMOTE_MODEL_ENV_NAMES) or _DEFAULT_GENERIC_REMOTE_MODEL
    existing = registry.get_manifest("openai-compatible-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    base_url = _env_first(env, *_GENERIC_REMOTE_BASE_URL_ENV_NAMES) or _DEFAULT_GENERIC_REMOTE_BASE_URL
    manifest = ModelProviderManifest(
        provider_name="openai-compatible-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes=(
            "Generic remote OpenAI-compatible fallback lane — auto-registered when "
            "OPENAI_API_KEY or NULLA_REMOTE_API_KEY is configured."
        ),
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "cloud",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_tether_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_TETHER_API_KEY_ENV_NAMES)
    base_url = _env_first(env, *_TETHER_BASE_URL_ENV_NAMES)
    if not api_key or not base_url:
        return ""
    api_key_env = next((name for name in _TETHER_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "TETHER_API_KEY")
    model_name = _env_first(env, *_TETHER_MODEL_ENV_NAMES) or _DEFAULT_TETHER_MODEL
    existing = registry.get_manifest("tether-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="tether-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="Tether remote lane via a user-managed OpenAI-compatible endpoint — auto-registered when TETHER_API_KEY and TETHER_BASE_URL are configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.76,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_llamacpp_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(
        env,
        "LLAMACPP_BASE_URL",
        "NULLA_LLAMACPP_BASE_URL",
        "LLAMA_CPP_BASE_URL",
        "NULLA_LLAMA_CPP_BASE_URL",
    )
    if not base_url:
        return ""
    resolved_model_name = _env_first(
        env,
        "LLAMACPP_MODEL",
        "NULLA_LLAMACPP_MODEL",
        "LLAMA_CPP_MODEL",
        "NULLA_LLAMA_CPP_MODEL",
    ) or model_name or default_runtime_model_tag(env=env)
    context_window = _env_int(
        env,
        "LLAMACPP_CONTEXT_WINDOW",
        "NULLA_LLAMACPP_CONTEXT_WINDOW",
        "LLAMA_CPP_CONTEXT_WINDOW",
        "NULLA_LLAMA_CPP_CONTEXT_WINDOW",
        default=_DEFAULT_LLAMACPP_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "LLAMACPP_MAX_SAFE_CONCURRENCY",
        "NULLA_LLAMACPP_MAX_SAFE_CONCURRENCY",
        "LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        "NULLA_LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("llamacpp-local", resolved_model_name)
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_base_url = str(existing_runtime_config.get("base_url") or "").strip()
    if (
        existing
        and existing.enabled
        and existing_base_url == base_url
        and int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0) == context_window
        and int(existing_metadata.get("max_safe_concurrency") or 0) == max_safe_concurrency
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="Local llama.cpp OpenAI-compatible verifier/coding lane — auto-registered when LLAMACPP_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.7,
            "orchestration_role": "drone",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_llamacpp_deep_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "NULLA_LLAMACPP_DEEP_BASE_URL", "LLAMACPP_DEEP_BASE_URL")
    if not base_url:
        return ""
    model_name = _env_first(env, "NULLA_LLAMACPP_DEEP_MODEL", "LLAMACPP_DEEP_MODEL")
    if not model_name:
        return ""
    context_window = _env_int(
        env,
        "NULLA_LLAMACPP_DEEP_CONTEXT_WINDOW",
        "LLAMACPP_DEEP_CONTEXT_WINDOW",
        default=4096,
    )
    max_safe_concurrency = _env_int(
        env,
        "NULLA_LLAMACPP_DEEP_MAX_SAFE_CONCURRENCY",
        "LLAMACPP_DEEP_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("llamacpp-local", model_name)
    if existing and existing.enabled:
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="Local llama.cpp deep/quality lane — auto-registered when NULLA_LLAMACPP_DEEP_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_mlx_provider(
    registry: ModelRegistry,
    *,
    model_name: str = "",
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "MLX_BASE_URL", "NULLA_MLX_BASE_URL")
    if not base_url:
        return ""
    resolved_model_name = (
        _env_first(env, "NULLA_MLX_MODEL", "MLX_MODEL") or model_name or _DEFAULT_MLX_MODEL
    )
    context_window = _env_int(
        env,
        "NULLA_MLX_CONTEXT_WINDOW",
        "MLX_CONTEXT_WINDOW",
        default=_DEFAULT_MLX_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "NULLA_MLX_MAX_SAFE_CONCURRENCY",
        "MLX_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("mlx-local", resolved_model_name)
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_base_url = str(existing_runtime_config.get("base_url") or "").strip()
    if (
        existing
        and existing.enabled
        and existing_base_url == base_url
        and int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0) == context_window
        and int(existing_metadata.get("max_safe_concurrency") or 0) == max_safe_concurrency
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="mlx-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="mlx-lm",
        notes="Local MLX OpenAI-compatible lane — auto-registered when MLX_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _env_int(env: Mapping[str, str], *names: str, default: int) -> int:
    for name in names:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return max(1, int(default))


def _default_free_disk_gb() -> float:
    try:
        usage = shutil.disk_usage(Path.home())
    except Exception:
        return 0.0
    return round(float(usage.free) / float(1024**3), 1)


def _profile_allows_aux_local_providers(profile_id: str, *, runtime_home: str | None = None) -> bool:
    if not profile_id:
        return True
    if profile_id in {"local-max", "full-orchestrated"}:
        return True
    if not runtime_home:
        return False
    # A live-verified llama.cpp GPU backend (core.llamacpp_capability_probe) unlocks the
    # aux local provider lane regardless of install profile — a measured speedup, not a
    # profile choice, is what earns this. Import kept local to avoid a hard dependency
    # for every runtime_provider_defaults caller that never touches GPU acceleration.
    try:
        from core.llamacpp_capability_probe import has_any_verified_gpu_backend

        return has_any_verified_gpu_backend(runtime_home)
    except Exception:
        return False


def _profile_allows_kimi_provider(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"hybrid-kimi", "full-orchestrated"}


def _profile_allows_generic_remote_provider(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"hybrid-fallback", "full-orchestrated"}


__all__ = [
    "default_runtime_model_tag",
    "ensure_default_runtime_providers",
    "preferred_fast_local_model",
]
