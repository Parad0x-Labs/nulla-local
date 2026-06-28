from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan


def test_model_store_drive_plan_recommends_biggest_sufficient_windows_drive(monkeypatch) -> None:
    gib = 1024**3
    free_by_drive = {
        "C:": 20.0,
        "D:": 120.0,
        "G:": 500.0,
    }

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        drive = str(path.drive or path.anchor).upper()
        free_gb = free_by_drive[drive]
        return SimpleNamespace(total=int(1000 * gib), used=int((1000 - free_gb) * gib), free=int(free_gb * gib))

    monkeypatch.setattr("core.model_store_planner.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.model_store_planner.default_ollama_models_path",
        lambda env=None: Path("D:\\Ollama\\models"),
    )
    monkeypatch.setattr(
        "core.model_store_planner._mounted_windows_drive_roots",
        lambda: (Path("C:\\"), Path("D:\\"), Path("G:\\")),
    )
    monkeypatch.setattr("core.model_store_planner.shutil.disk_usage", fake_disk_usage)

    plan = build_model_store_drive_plan(
        required_models=("qwen3:8b", "deepseek-r1:8b"),
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )

    assert plan["current_model_store_path"] == "D:\\Ollama\\models"
    assert plan["recommended_model_store_path"] == "G:\\Ollama\\models"
    assert plan["recommended_drive"]["drive"] == "G:"
    assert plan["recommended_drive"]["free_gb"] == 500.0
    assert plan["current_drive"]["drive"] == "D:"
    assert plan["status"] == "move_recommended"
    assert plan["set_env_command"] == 'setx OLLAMA_MODELS "G:\\Ollama\\models"'
    assert [drive["drive"] for drive in plan["drives"]] == ["G:", "D:", "C:"]
    assert plan["drives"][-1]["status"] == "not_enough_space"
