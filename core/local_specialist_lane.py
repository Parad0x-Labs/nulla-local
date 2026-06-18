from __future__ import annotations

from collections.abc import Mapping

DEFAULT_SECONDARY_LOCAL_MODEL = "qwen2.5:14b-gguf"
DEFAULT_SECONDARY_LOCAL_PROFILE = "local-max"
DEFAULT_SECONDARY_LOCAL_BACKEND = "llama.cpp"
DEFAULT_SECONDARY_LOCAL_BASE_URL = "http://127.0.0.1:8090/v1"
DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW = 32768
DEFAULT_SECONDARY_LOCAL_PORT = 8090

# Deep lane vars take precedence: when a separate deep/quality llamacpp server is
# configured (NULLA_LLAMACPP_DEEP_*), it should be used as the verifier/secondary,
# not the fast lane primary.
SECONDARY_LOCAL_MODEL_ENV_KEYS = (
    "NULLA_LLAMACPP_DEEP_MODEL",
    "LLAMACPP_DEEP_MODEL",
    "NULLA_LLAMACPP_MODEL",
    "LLAMACPP_MODEL",
    "NULLA_LLAMA_CPP_MODEL",
    "LLAMA_CPP_MODEL",
)
SECONDARY_LOCAL_BASE_URL_ENV_KEYS = (
    "NULLA_LLAMACPP_DEEP_BASE_URL",
    "LLAMACPP_DEEP_BASE_URL",
    "LLAMACPP_BASE_URL",
    "NULLA_LLAMACPP_BASE_URL",
    "LLAMA_CPP_BASE_URL",
    "NULLA_LLAMA_CPP_BASE_URL",
)
SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS = (
    "LLAMACPP_CONTEXT_WINDOW",
    "NULLA_LLAMACPP_CONTEXT_WINDOW",
    "LLAMA_CPP_CONTEXT_WINDOW",
    "NULLA_LLAMA_CPP_CONTEXT_WINDOW",
)
SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS = (
    "LLAMACPP_MODEL_PATH",
    "NULLA_LLAMACPP_MODEL_PATH",
    "LLAMA_CPP_MODEL_PATH",
    "NULLA_LLAMA_CPP_MODEL_PATH",
)


def secondary_local_model(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_MODEL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_SECONDARY_LOCAL_MODEL


def secondary_local_base_url(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_BASE_URL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_SECONDARY_LOCAL_BASE_URL


def secondary_local_context_window(env: Mapping[str, str]) -> int:
    for key in SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW


def secondary_local_model_path(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return ""


def secondary_local_provider_id(env: Mapping[str, str]) -> str:
    return f"llamacpp-local:{secondary_local_model(env)}"


__all__ = [
    "DEFAULT_SECONDARY_LOCAL_BACKEND",
    "DEFAULT_SECONDARY_LOCAL_BASE_URL",
    "DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW",
    "DEFAULT_SECONDARY_LOCAL_MODEL",
    "DEFAULT_SECONDARY_LOCAL_PORT",
    "DEFAULT_SECONDARY_LOCAL_PROFILE",
    "SECONDARY_LOCAL_BASE_URL_ENV_KEYS",
    "SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS",
    "SECONDARY_LOCAL_MODEL_ENV_KEYS",
    "SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS",
    "secondary_local_base_url",
    "secondary_local_context_window",
    "secondary_local_model",
    "secondary_local_model_path",
    "secondary_local_provider_id",
]
