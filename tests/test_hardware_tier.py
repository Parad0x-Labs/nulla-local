from __future__ import annotations

from unittest import mock

from core.hardware_tier import MachineProbe, _cuda_probe_result, select_qwen_tier
from core.local_model_bundles import capacity_bucket_for_machine, local_multi_llm_fit_from_probe


def test_select_qwen_tier_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_supports_custom_override_tag(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "ollama/custom-qwen")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "override"
    assert tier.ollama_tag == "custom-qwen"


def test_select_qwen_tier_uses_ram_thresholds_for_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_unlocks_14b_on_higher_ram_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=12, ram_gb=36.0, gpu_name="Apple Silicon", vram_gb=36.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "mid"
    assert tier.ollama_tag == "qwen2.5:14b"


def test_select_qwen_tier_keeps_discrete_vram_selection_for_non_mps(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=16, ram_gb=16.0, gpu_name="NVIDIA", vram_gb=24.0, accelerator="cuda")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "heavy"
    assert tier.ollama_tag == "qwen2.5:32b"


def test_select_qwen_tier_ignores_discrete_vram_when_accelerator_is_cpu(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=8.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cpu",
        accelerator_status="legacy_cuda_cpu_recommended",
    )

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "lite"
    assert tier.ollama_tag == "qwen2.5:3b"
    assert local_multi_llm_fit_from_probe(probe) == "single_model_only"
    assert capacity_bucket_for_machine(probe=probe, free_disk_gb=120.0) == "A"


def test_windows_legacy_cuda_card_defaults_to_cpu_sizing(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_ALLOW_LEGACY_CUDA", raising=False)

    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        gpu_name, vram_gb, accelerator, status, advice = _cuda_probe_result(
            "NVIDIA GeForce GTX 1080",
            8.0,
        )

    assert gpu_name == "NVIDIA GeForce GTX 1080"
    assert vram_gb == 8.0
    assert accelerator == "cpu"
    assert status == "legacy_cuda_cpu_recommended"
    assert "NULLA_ALLOW_LEGACY_CUDA=1" in advice
