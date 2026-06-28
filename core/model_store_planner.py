from __future__ import annotations

import os
import platform
import shutil
import string
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from core.local_model_bundles import model_storage_gb, safe_disk_floor_gb
from core.runtime_install_profiles import default_ollama_models_path

DEFAULT_OPENCLAW_MEMORY_MODEL = "nomic-embed-text"


def build_model_store_drive_plan(
    *,
    required_models: Iterable[str],
    support_models: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env_map = os.environ if env is None else env
    current_store = default_ollama_models_path(env_map)
    models = _unique_model_names([*required_models, *support_models])
    required_storage_gb = round(sum(model_storage_gb(model) for model in models), 1)
    safe_floor_gb = safe_disk_floor_gb(models)
    candidates = _candidate_model_store_paths(current_store=current_store)
    rows: list[dict[str, Any]] = []
    for root, model_store in candidates:
        usage = _disk_usage(root)
        if usage is None:
            continue
        free_gb = round(float(usage.free) / (1024.0**3), 1)
        total_gb = round(float(usage.total) / (1024.0**3), 1)
        enough = free_gb >= safe_floor_gb
        rows.append(
            {
                "drive": _drive_label(root),
                "root": str(root),
                "model_store_path": str(model_store),
                "free_gb": free_gb,
                "total_gb": total_gb,
                "enough_for_required_models": enough,
                "status": "enough_space" if enough else "not_enough_space",
                "is_current_drive": _same_volume(root, current_store),
                "is_current_model_store": _same_path(model_store, current_store),
            }
        )
    rows.sort(key=lambda item: (bool(item["enough_for_required_models"]), float(item["free_gb"])), reverse=True)
    recommended = rows[0] if rows else {}
    current_row = next((row for row in rows if row["is_current_model_store"]), None)
    if current_row is None:
        current_root = _nearest_existing_path(current_store)
        current_usage = _disk_usage(current_root)
        if current_usage is not None:
            current_row = {
                "drive": _drive_label(current_root),
                "root": str(current_root),
                "model_store_path": str(current_store),
                "free_gb": round(float(current_usage.free) / (1024.0**3), 1),
                "total_gb": round(float(current_usage.total) / (1024.0**3), 1),
                "enough_for_required_models": float(current_usage.free) / (1024.0**3) >= safe_floor_gb,
                "status": "enough_space"
                if float(current_usage.free) / (1024.0**3) >= safe_floor_gb
                else "not_enough_space",
                "is_current_drive": True,
                "is_current_model_store": True,
            }
    recommended_path = str(recommended.get("model_store_path") or current_store)
    current_path = str(current_store)
    return {
        "schema": "nulla.model_store_drive_plan.v1",
        "required_models": list(models),
        "required_model_storage_gb": required_storage_gb,
        "safe_disk_floor_gb": safe_floor_gb,
        "current_model_store_path": current_path,
        "current_drive": dict(current_row or {}),
        "recommended_model_store_path": recommended_path,
        "recommended_drive": dict(recommended),
        "drives": rows,
        "set_env_command": f"setx OLLAMA_MODELS \"{recommended_path}\"" if recommended_path else "",
        "status": (
            "current_best"
            if current_path.lower() == recommended_path.lower()
            else "move_recommended"
            if recommended_path
            else "no_drive_found"
        ),
    }


def _unique_model_names(models: Iterable[str]) -> tuple[str, ...]:
    clean: list[str] = []
    seen: set[str] = set()
    for model in models:
        value = str(model or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        clean.append(value)
    return tuple(clean)


def _candidate_model_store_paths(*, current_store: Path) -> tuple[tuple[Path, Path], ...]:
    if platform.system().lower() == "windows":
        rows: list[tuple[Path, Path]] = []
        for root in _mounted_windows_drive_roots():
            rows.append((root, root / "Ollama" / "models"))
        if not any(_same_volume(root, current_store) for root, _path in rows):
            current_root = _nearest_existing_path(current_store)
            rows.append((current_root, current_store))
        return tuple(rows)
    current_root = _nearest_existing_path(current_store)
    return ((current_root, current_store),)


def _mounted_windows_drive_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            roots.append(root)
    return tuple(roots)


def _disk_usage(path: Path) -> shutil._ntuple_diskusage | None:
    try:
        return shutil.disk_usage(path)
    except OSError:
        return None


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    if current.exists():
        return current.resolve()
    return Path.home().resolve()


def _same_volume(left: Path, right: Path) -> bool:
    if platform.system().lower() == "windows":
        return str(left.drive or left.anchor).lower() == str(right.drive or right.anchor).lower()
    try:
        return _nearest_existing_path(left).stat().st_dev == _nearest_existing_path(right).stat().st_dev
    except OSError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _drive_label(path: Path) -> str:
    if platform.system().lower() == "windows":
        return str(path.drive or path.anchor).upper()
    return str(path.anchor or path)


__all__ = [
    "DEFAULT_OPENCLAW_MEMORY_MODEL",
    "build_model_store_drive_plan",
]
