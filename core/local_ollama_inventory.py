from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


def env_flag_enabled(env: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    raw = str(env.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def installed_ollama_model_names(
    *,
    env: Mapping[str, str] | None = None,
    base_url: str = "http://127.0.0.1:11434",
    timeout_seconds: float = 2.0,
) -> tuple[str, ...]:
    env_map = os.environ if env is None else env
    explicit = str(env_map.get("NULLA_INSTALLED_OLLAMA_MODELS") or "").strip()
    if explicit:
        return _dedupe_model_names(
            item.strip()
            for chunk in explicit.splitlines()
            for item in chunk.split(",")
        )

    url = str(env_map.get("NULLA_RAW_OLLAMA_API_URL") or base_url or "").rstrip("/") + "/api/tags"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return tuple()
    models = payload.get("models")
    if not isinstance(models, list):
        return tuple()
    return _dedupe_model_names(
        str(item.get("name") or item.get("model") or "").strip()
        for item in models
        if isinstance(item, dict)
    )


def is_text_generation_ollama_model(model_name: str) -> bool:
    clean = str(model_name or "").strip().lower()
    if not clean:
        return False
    non_text_markers = (
        "all-minilm",
        "bge-",
        "clip",
        "embed",
        "embedding",
        "nomic-embed",
        "snowflake-arctic-embed",
    )
    return not any(marker in clean for marker in non_text_markers)


def _dedupe_model_names(values: Any) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.append(clean)
    return tuple(seen)


__all__ = ["env_flag_enabled", "installed_ollama_model_names", "is_text_generation_ollama_model"]
