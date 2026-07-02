from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.hardware_tier import MachineProbe
from core.install_recommendations import build_install_recommendation_truth


def test_install_recommendation_uses_ollama_model_store_disk(monkeypatch, tmp_path) -> None:
    model_store = (tmp_path / "ollama" / "models").resolve()
    model_store.mkdir(parents=True)
    seen_paths: list[Path] = []

    def fake_disk_usage(path: str | Path) -> SimpleNamespace:
        seen_paths.append(Path(path).resolve())
        gib = 1024**3
        return SimpleNamespace(total=500 * gib, used=278 * gib, free=222 * gib)

    monkeypatch.setenv("OLLAMA_MODELS", str(model_store))
    monkeypatch.setattr("core.install_recommendations.shutil.disk_usage", fake_disk_usage)

    recommendation = build_install_recommendation_truth(
        probe=MachineProbe(
            cpu_cores=8,
            ram_gb=8.0,
            gpu_name=None,
            vram_gb=None,
            accelerator="cpu",
        ),
        env={},
    )

    assert seen_paths == [model_store]
    assert recommendation.free_disk_gb == 222.0
