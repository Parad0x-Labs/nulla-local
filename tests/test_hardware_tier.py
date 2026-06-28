from __future__ import annotations

from unittest import mock

import subprocess

from core.hardware_tier import MachineProbe, _cuda_probe_result, _select_accelerator, detect_gpu_devices, select_qwen_tier, tier_summary
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


def test_windows_multi_gpu_inventory_selects_best_usable_cuda_device(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("NULLA_FORCE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("NULLA_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "0, NVIDIA GeForce GTX 1080, 8192\n"
                "1, NVIDIA GeForce RTX 4090, 24576\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("core.hardware_tier.subprocess.run", fake_run)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()
        gpu_name, vram_gb, accelerator, status, advice = _select_accelerator(devices)

    assert [device.name for device in devices] == ["NVIDIA GeForce GTX 1080", "NVIDIA GeForce RTX 4090"]
    assert devices[0].status == "legacy_cuda_cpu_recommended"
    assert devices[1].status == "usable"
    assert gpu_name == "NVIDIA GeForce RTX 4090"
    assert vram_gb == 24.0
    assert accelerator == "cuda"
    assert status == "usable"
    assert advice == ""

    summary = tier_summary(
        MachineProbe(
            cpu_cores=16,
            ram_gb=64.0,
            gpu_name=gpu_name,
            vram_gb=vram_gb,
            accelerator=accelerator,
            accelerator_status=status,
            accelerator_advice=advice,
            gpu_devices=devices,
        )
    )

    assert summary["gpu_count"] == 2
    assert summary["gpu_devices"][0]["selected"] is False
    assert summary["gpu_devices"][1]["selected"] is True
    assert summary["gpu_devices"][1]["active_accelerator"] is True


def test_windows_gpu_inventory_adds_nonduplicate_directml_devices(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0, NVIDIA GeForce RTX 4090, 24576\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Node,AdapterRAM,Name\n"
                "DESKTOP,25769803776,NVIDIA GeForce RTX 4090\n"
                "DESKTOP,17179869184,AMD Radeon RX 7900 XTX\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("core.hardware_tier.subprocess.run", fake_run)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()

    assert [device.name for device in devices] == [
        "NVIDIA GeForce RTX 4090",
        "AMD Radeon RX 7900 XTX",
    ]
    assert [device.backend for device in devices] == ["cuda", "directml"]
