"""Hardware-aware Qwen model tier selection.

Probes GPU VRAM, system RAM, and CPU cores, then picks the heaviest
Qwen variant the machine can comfortably run via Ollama.

Baseline tier ladder (all Apache-2.0 Qwen family):
  titan   – qwen2.5:72b   (needs ≥48 GB VRAM or ≥80 GB RAM)
  heavy   – qwen2.5:32b   (needs ≥20 GB VRAM or ≥48 GB RAM)
  mid     – qwen2.5:14b   (needs ≥10 GB VRAM or ≥24 GB RAM)
  base    – qwen2.5:7b    (needs ≥4 GB VRAM  or ≥12 GB RAM)
  lite    – qwen2.5:3b    (needs ≥2 GB VRAM  or ≥6 GB RAM)
  nano    – qwen2.5:0.5b  (anything else)

Apple Silicon uses unified memory, so the effective thresholds are more
conservative:
  titan   – qwen2.5:72b   (needs ≥96 GB RAM)
  heavy   – qwen2.5:32b   (needs ≥64 GB RAM)
  mid     – qwen2.5:14b   (needs ≥36 GB RAM)
  base    – qwen2.5:7b    (needs ≥12 GB RAM)
  lite    – qwen2.5:3b    (needs ≥6 GB RAM)
  nano    – qwen2.5:0.5b  (anything else)
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass


@dataclass
class MachineProbe:
    cpu_cores: int
    ram_gb: float
    gpu_name: str | None
    vram_gb: float | None
    accelerator: str  # cuda | mps | directml | cpu
    accelerator_status: str = ""
    accelerator_advice: str = ""


@dataclass
class QwenTier:
    tier_name: str
    ollama_tag: str
    param_billions: float
    min_vram_gb: float
    min_ram_gb: float


TIERS: list[QwenTier] = [
    QwenTier("titan", "qwen2.5:72b",  72.0, 48.0, 80.0),
    QwenTier("heavy", "qwen2.5:32b",  32.0, 20.0, 48.0),
    QwenTier("mid",   "qwen2.5:14b",  14.0, 10.0, 24.0),
    QwenTier("base",  "qwen2.5:7b",    7.0,  4.0, 12.0),
    QwenTier("lite",  "qwen2.5:3b",    3.0,  2.0,  6.0),
    QwenTier("nano",  "qwen2.5:0.5b",  0.5,  0.0,  0.0),
]

MPS_TIERS: list[QwenTier] = [
    QwenTier("titan", "qwen2.5:72b",  72.0, 48.0, 96.0),
    QwenTier("heavy", "qwen2.5:32b",  32.0, 20.0, 64.0),
    QwenTier("mid",   "qwen2.5:14b",  14.0, 10.0, 36.0),
    QwenTier("base",  "qwen2.5:7b",    7.0,  4.0, 12.0),
    QwenTier("lite",  "qwen2.5:3b",    3.0,  2.0,  6.0),
    QwenTier("nano",  "qwen2.5:0.5b",  0.5,  0.0,  0.0),
]


def probe_machine() -> MachineProbe:
    cpu_cores = os.cpu_count() or 2
    ram_gb = _detect_ram_gb()
    gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice = _detect_gpu()
    return MachineProbe(
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        accelerator=accelerator,
        accelerator_status=accelerator_status,
        accelerator_advice=accelerator_advice,
    )


def select_qwen_tier(probe: MachineProbe | None = None) -> QwenTier:
    """Pick the best Qwen tier this machine can handle."""
    override_tag = _override_model_tag()
    if override_tag:
        for tier in TIERS:
            if tier.ollama_tag == override_tag:
                return tier
        return QwenTier("override", override_tag, 0.0, 0.0, 0.0)

    if probe is None:
        probe = probe_machine()

    normalized_accelerator = str(probe.accelerator or "").strip().lower()
    if normalized_accelerator == "mps":
        for tier in MPS_TIERS:
            if probe.ram_gb >= tier.min_ram_gb:
                return tier
        return MPS_TIERS[-1]

    discrete_accelerator = normalized_accelerator in {"cuda", "directml"}
    for tier in TIERS:
        if discrete_accelerator and probe.vram_gb is not None and probe.vram_gb >= tier.min_vram_gb:
            return tier
        if probe.ram_gb >= tier.min_ram_gb:
            return tier
    return TIERS[-1]


def recommended_ollama_model(probe: MachineProbe | None = None) -> str:
    """Return the Ollama model tag string for the best tier."""
    return select_qwen_tier(probe).ollama_tag


def tier_summary(probe: MachineProbe | None = None) -> dict:
    """Human-readable summary for installers/logs."""
    if probe is None:
        probe = probe_machine()
    tier = select_qwen_tier(probe)
    accelerator = str(probe.accelerator or "").strip().lower()
    accelerator_status = str(getattr(probe, "accelerator_status", "") or "").strip()
    if not accelerator_status:
        accelerator_status = "cpu" if accelerator == "cpu" else "usable"
    return {
        "cpu_cores": probe.cpu_cores,
        "ram_gb": round(probe.ram_gb, 1),
        "gpu": probe.gpu_name or "none",
        "vram_gb": round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
        "accelerator": probe.accelerator,
        "accelerator_status": accelerator_status,
        "accelerator_advice": str(getattr(probe, "accelerator_advice", "") or "").strip(),
        "selected_tier": tier.tier_name,
        "ollama_model": tier.ollama_tag,
        "param_billions": tier.param_billions,
    }


# ---------------------------------------------------------------------------
# Internal probes
# ---------------------------------------------------------------------------

def _detect_ram_gb() -> float:
    try:
        import psutil  # type: ignore
        return float(psutil.virtual_memory().total) / (1024.0 ** 3)
    except Exception:
        pass
    # Fallback: Windows wmic
    if platform.system().lower() == "windows":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                text=True, timeout=5,
            )
            for line in out.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    return float(line) / (1024.0 ** 3)
        except Exception:
            pass
    return 4.0  # conservative fallback


def _override_model_tag() -> str | None:
    raw = str(
        os.environ.get("NULLA_OLLAMA_MODEL")
        or os.environ.get("NULLA_FORCE_OLLAMA_MODEL")
        or ""
    ).strip()
    if not raw:
        return None
    if "/" in raw:
        raw = raw.split("/", 1)[-1].strip()
    return raw or None


def _detect_gpu() -> tuple[str | None, float | None, str, str, str]:
    """Returns (gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice)."""

    # CUDA path (Windows + Linux NVIDIA)
    gpu = _try_cuda()
    if gpu[0] is not None:
        return gpu

    # Apple Silicon MPS
    gpu = _try_mps()
    if gpu[0] is not None:
        return gpu

    # DirectML fallback (AMD/Intel on Windows)
    gpu = _try_directml()
    if gpu[0] is not None:
        return gpu

    return None, None, "cpu", "cpu", ""


def _try_cuda() -> tuple[str | None, float | None, str, str, str]:
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            _free, total = torch.cuda.mem_get_info(0)
            return _cuda_probe_result(name, float(total) / (1024.0 ** 3))
    except Exception:
        pass
    # nvidia-smi fallback
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            line = (result.stdout or "").strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name = parts[0]
                vram_mb = float(parts[1])
                return _cuda_probe_result(name, vram_mb / 1024.0)
    except Exception:
        pass
    return None, None, "cpu", "cpu", ""


def _try_mps() -> tuple[str | None, float | None, str, str, str]:
    if platform.system().lower() != "darwin":
        return None, None, "cpu", "cpu", ""
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return None, None, "cpu", "cpu", ""
    try:
        import torch  # type: ignore
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            # MPS shares unified memory; report total RAM as proxy
            ram = _detect_ram_gb()
            return "Apple Silicon (MPS)", ram, "mps", "usable", ""
    except Exception:
        pass
    # Even without torch, Apple Silicon has unified memory
    ram = _detect_ram_gb()
    return "Apple Silicon", ram, "mps", "usable", ""


def _try_directml() -> tuple[str | None, float | None, str, str, str]:
    """Detect AMD/Intel GPUs on Windows via WMI."""
    if platform.system().lower() != "windows":
        return None, None, "cpu", "cpu", ""
    try:
        import subprocess
        result = subprocess.run(
            ["wmic", "path", "Win32_VideoController", "get",
             "Name,AdapterRAM", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    try:
                        vram_bytes = int(parts[1])
                    except ValueError:
                        continue
                    name = parts[2]
                    if vram_bytes > 512 * 1024 * 1024:  # >512 MB = real GPU
                        return name, float(vram_bytes) / (1024.0 ** 3), "directml", "usable", ""
    except Exception:
        pass
    return None, None, "cpu", "cpu", ""


def _cuda_probe_result(gpu_name: str, vram_gb: float) -> tuple[str | None, float | None, str, str, str]:
    if _windows_legacy_cuda_cpu_fallback(gpu_name):
        return (
            gpu_name,
            vram_gb,
            "cpu",
            "legacy_cuda_cpu_recommended",
            (
                "Legacy NVIDIA CUDA device on Windows; NULLA sizes local models as CPU-only "
                "unless NULLA_ALLOW_LEGACY_CUDA=1 is set after a successful Ollama warmup."
            ),
        )
    return gpu_name, vram_gb, "cuda", "usable", ""


def _windows_legacy_cuda_cpu_fallback(gpu_name: str) -> bool:
    if platform.system().lower() != "windows":
        return False
    if _env_truthy("NULLA_ALLOW_LEGACY_CUDA"):
        return False
    clean = re.sub(r"[^a-z0-9]+", " ", str(gpu_name or "").strip().lower())
    if not clean:
        return False
    return bool(re.search(r"\bgtx\s*(?:10|9|7|6|5)\d{2}\b", clean))


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
