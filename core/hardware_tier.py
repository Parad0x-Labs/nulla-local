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
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUDevice:
    index: int
    name: str
    vendor: str
    vram_gb: float | None
    backend: str  # cuda | mps | directml
    status: str  # usable | legacy_cuda_cpu_recommended | blocked
    advice: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "vendor": self.vendor,
            "vram_gb": round(self.vram_gb, 1) if self.vram_gb is not None else None,
            "backend": self.backend,
            "status": self.status,
            "advice": self.advice,
            "source": self.source,
        }


@dataclass
class MachineProbe:
    cpu_cores: int
    ram_gb: float
    gpu_name: str | None
    vram_gb: float | None
    accelerator: str  # cuda | mps | directml | cpu
    accelerator_status: str = ""
    accelerator_advice: str = ""
    gpu_devices: tuple[GPUDevice, ...] = ()


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
    gpu_devices = detect_gpu_devices()
    gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice = _select_accelerator(gpu_devices)
    return MachineProbe(
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        accelerator=accelerator,
        accelerator_status=accelerator_status,
        accelerator_advice=accelerator_advice,
        gpu_devices=gpu_devices,
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
    gpu_devices = _gpu_device_rows(probe)
    return {
        "cpu_cores": probe.cpu_cores,
        "ram_gb": round(probe.ram_gb, 1),
        "gpu": probe.gpu_name or "none",
        "vram_gb": round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
        "gpu_count": len(gpu_devices),
        "gpu_devices": gpu_devices,
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


def detect_gpu_devices() -> tuple[GPUDevice, ...]:
    """Return every usable GPU backend candidate the installer can reason about."""
    devices: list[GPUDevice] = list(_try_cuda_devices())
    devices.extend(_new_backend_devices(devices, _try_mps_devices()))
    devices.extend(_new_backend_devices(devices, _try_directml_devices()))
    return tuple(devices)


def _detect_gpu() -> tuple[str | None, float | None, str, str, str]:
    """Returns (gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice)."""
    return _select_accelerator(detect_gpu_devices())


def _try_cuda() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_cuda_devices())


def _try_cuda_devices() -> tuple[GPUDevice, ...]:
    devices = _try_torch_cuda_devices()
    if devices:
        return devices
    return _try_nvidia_smi_devices()


def _try_torch_cuda_devices() -> tuple[GPUDevice, ...]:
    try:
        import torch  # type: ignore
        if not torch.cuda.is_available():
            return ()
        devices: list[GPUDevice] = []
        for index in range(int(torch.cuda.device_count() or 0)):
            name = str(torch.cuda.get_device_name(index) or "").strip()
            if not name:
                continue
            vram_gb: float | None = None
            try:
                _free, total = torch.cuda.mem_get_info(index)
                vram_gb = float(total) / (1024.0 ** 3)
            except Exception:
                try:
                    props = torch.cuda.get_device_properties(index)
                    vram_gb = float(getattr(props, "total_memory")) / (1024.0 ** 3)
                except Exception:
                    vram_gb = None
            devices.append(_cuda_device_result(index=index, gpu_name=name, vram_gb=vram_gb, source="torch"))
        return tuple(devices)
    except Exception:
        return ()


def _try_nvidia_smi_devices() -> tuple[GPUDevice, ...]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return ()
        devices: list[GPUDevice] = []
        for line in (result.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                index = int(parts[0])
            except ValueError:
                index = len(devices)
            name = parts[1]
            try:
                vram_gb = float(parts[2]) / 1024.0
            except ValueError:
                vram_gb = None
            if name:
                devices.append(_cuda_device_result(index=index, gpu_name=name, vram_gb=vram_gb, source="nvidia-smi"))
        return tuple(devices)
    except Exception:
        return ()


def _try_mps() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_mps_devices())


def _try_mps_devices() -> tuple[GPUDevice, ...]:
    if platform.system().lower() != "darwin":
        return ()
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return ()
    try:
        import torch  # type: ignore
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            # MPS shares unified memory; report total RAM as proxy
            ram = _detect_ram_gb()
            return (
                GPUDevice(
                    index=0,
                    name="Apple Silicon (MPS)",
                    vendor="apple",
                    vram_gb=ram,
                    backend="mps",
                    status="usable",
                    source="torch",
                ),
            )
    except Exception:
        pass
    # Even without torch, Apple Silicon has unified memory
    ram = _detect_ram_gb()
    return (
        GPUDevice(
            index=0,
            name="Apple Silicon",
            vendor="apple",
            vram_gb=ram,
            backend="mps",
            status="usable",
            source="platform",
        ),
    )


def _try_directml() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_directml_devices())


def _try_directml_devices() -> tuple[GPUDevice, ...]:
    """Detect AMD/Intel GPUs on Windows via WMI."""
    if platform.system().lower() != "windows":
        return ()
    try:
        result = subprocess.run(
            ["wmic", "path", "Win32_VideoController", "get",
             "Name,AdapterRAM", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return ()
        devices: list[GPUDevice] = []
        for line in (result.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                vram_bytes = int(parts[1])
            except ValueError:
                continue
            name = parts[2]
            if vram_bytes > 512 * 1024 * 1024 and name:  # >512 MB = real GPU
                devices.append(
                    GPUDevice(
                        index=len(devices),
                        name=name,
                        vendor=_gpu_vendor(name),
                        vram_gb=float(vram_bytes) / (1024.0 ** 3),
                        backend="directml",
                        status="usable",
                        source="wmic",
                    )
                )
        return tuple(devices)
    except Exception:
        return ()


def _cuda_probe_result(gpu_name: str, vram_gb: float) -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(
        (_cuda_device_result(index=0, gpu_name=gpu_name, vram_gb=vram_gb, source="manual"),)
    )


def _cuda_device_result(*, index: int, gpu_name: str, vram_gb: float | None, source: str) -> GPUDevice:
    advice = ""
    status = "usable"
    if _windows_legacy_cuda_cpu_fallback(gpu_name):
        status = "legacy_cuda_cpu_recommended"
        advice = (
            "Legacy NVIDIA CUDA device on Windows; NULLA sizes local models as CPU-only "
            "unless NULLA_ALLOW_LEGACY_CUDA=1 is set after a successful Ollama warmup."
        )
    return GPUDevice(
        index=index,
        name=gpu_name,
        vendor="nvidia",
        vram_gb=vram_gb,
        backend="cuda",
        status=status,
        advice=advice,
        source=source,
    )


def _select_accelerator(devices: tuple[GPUDevice, ...]) -> tuple[str | None, float | None, str, str, str]:
    for backend in ("mps", "cuda", "directml"):
        usable = [device for device in devices if device.backend == backend and device.status == "usable"]
        if usable:
            selected = max(usable, key=lambda item: (float(item.vram_gb or 0.0), -int(item.index or 0)))
            return selected.name, selected.vram_gb, selected.backend, selected.status, selected.advice
    if devices:
        selected = max(devices, key=lambda item: (float(item.vram_gb or 0.0), -int(item.index or 0)))
        return selected.name, selected.vram_gb, "cpu", selected.status or "blocked", selected.advice
    return None, None, "cpu", "cpu", ""


def _gpu_device_rows(probe: MachineProbe) -> list[dict[str, object]]:
    devices = tuple(getattr(probe, "gpu_devices", ()) or ())
    rows: list[dict[str, object]] = []
    for device in devices:
        if isinstance(device, GPUDevice):
            row = device.to_dict()
        elif isinstance(device, Mapping):
            row = dict(device)
        else:
            continue
        selected = str(row.get("name") or "") == str(probe.gpu_name or "")
        row["selected"] = selected
        row["active_accelerator"] = (
            selected
            and str(row.get("backend") or "").strip().lower() == str(probe.accelerator or "").strip().lower()
            and str(row.get("status") or "").strip().lower() == "usable"
        )
        rows.append(row)
    return rows


def _gpu_vendor(gpu_name: str) -> str:
    clean = str(gpu_name or "").strip().lower()
    if "nvidia" in clean or "geforce" in clean or "quadro" in clean or "tesla" in clean or "rtx" in clean:
        return "nvidia"
    if "amd" in clean or "radeon" in clean:
        return "amd"
    if "intel" in clean or "arc" in clean or "iris" in clean or "uhd" in clean:
        return "intel"
    if "apple" in clean:
        return "apple"
    return "unknown"


def _new_backend_devices(
    existing_devices: list[GPUDevice],
    candidate_devices: tuple[GPUDevice, ...],
) -> list[GPUDevice]:
    existing = {_gpu_identity(device) for device in existing_devices}
    added: list[GPUDevice] = []
    for candidate in candidate_devices:
        identity = _gpu_identity(candidate)
        if identity in existing:
            continue
        existing.add(identity)
        added.append(candidate)
    return added


def _gpu_identity(device: GPUDevice) -> tuple[str, str]:
    return (
        str(device.vendor or "").strip().lower(),
        re.sub(r"[^a-z0-9]+", " ", str(device.name or "").strip().lower()).strip(),
    )


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
