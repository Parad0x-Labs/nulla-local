from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib import request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.hardware_tier import MachineProbe, select_qwen_tier
from core.install_recommendations import (
    build_install_recommendation_truth,
    install_recommendation_machine_summary,
    local_multi_llm_fit,
)
from core.local_model_bundles import model_storage_gb
from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import (
    build_install_profile_truth,
    default_ollama_models_path,
    format_install_profile_id,
)


BENCHMARK_MARKER = "NULLA_BENCH_OK"


def detect_ollama_binary() -> str:
    candidate = shutil.which("ollama")
    return str(candidate or "")


def _ollama_api_url() -> str:
    raw = str(os.environ.get("NULLA_RAW_OLLAMA_API_URL") or "").strip()
    if raw:
        return raw
    host = str(os.environ.get("OLLAMA_HOST") or "").strip()
    if host:
        if host.startswith(("http://", "https://")):
            return host
        return f"http://{host}"
    return "http://127.0.0.1:11434"


def _format_ollama_size_label(value: Any) -> str:
    try:
        size = float(value)
    except Exception:
        return ""
    gib = 1024.0 ** 3
    mib = 1024.0 ** 2
    if size >= gib:
        return f"{size / gib:.1f} GB"
    if size >= mib:
        return f"{size / mib:.1f} MB"
    if size > 0:
        return f"{int(size)} B"
    return ""


def _list_ollama_models_via_api(api_url: str | None = None) -> list[dict[str, str]]:
    base = str(api_url or "").strip() or _ollama_api_url()
    url = f"{base.rstrip('/')}/api/tags"
    curl_binary = shutil.which("curl")
    if curl_binary:
        try:
            completed = subprocess.run(
                [curl_binary, "-fsS", url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            if completed.returncode == 0 and str(completed.stdout or "").strip():
                payload = json.loads(completed.stdout)
            else:
                payload = None
        except Exception:
            payload = None
    else:
        payload = None
    if payload is None:
        try:
            with request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
    rows: list[dict[str, str]] = []
    for raw in list(payload.get("models") or []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("model") or "").strip()
        if not name:
            continue
        digest = str(raw.get("digest") or "").strip()
        rows.append(
            {
                "name": name,
                "id": digest[:12] if digest else "",
                "size": _format_ollama_size_label(raw.get("size")),
                "modified": str(raw.get("modified_at") or "").strip(),
            }
        )
    return rows


def _list_ollama_models_via_manifests() -> list[dict[str, str]]:
    manifest_root = (default_ollama_models_path() / "manifests").resolve()
    if not manifest_root.exists():
        return []
    rows: list[dict[str, str]] = []
    for manifest_path in sorted(path for path in manifest_root.glob("**/*") if path.is_file()):
        try:
            relative = manifest_path.relative_to(manifest_root)
        except Exception:
            continue
        parts = relative.parts
        if len(parts) < 2:
            continue
        model_name = f"{parts[-2]}:{parts[-1]}"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        digest = ""
        size_label = ""
        if isinstance(payload, dict):
            layers = list(payload.get("layers") or [])
            model_layer = next(
                (
                    layer
                    for layer in layers
                    if isinstance(layer, dict)
                    and str(layer.get("mediaType") or "").strip() == "application/vnd.ollama.image.model"
                ),
                None,
            )
            if isinstance(model_layer, dict):
                digest = str(model_layer.get("digest") or "").strip().removeprefix("sha256:")
                size_label = _format_ollama_size_label(model_layer.get("size"))
        rows.append(
            {
                "name": model_name,
                "id": digest[:12] if digest else "",
                "size": size_label,
                "modified": str(int(manifest_path.stat().st_mtime)),
            }
        )
    return rows


def list_ollama_models(ollama_binary: str | None = None, ollama_api_url: str | None = None) -> list[dict[str, str]]:
    api_rows = _list_ollama_models_via_api(ollama_api_url)
    if api_rows:
        return api_rows
    manifest_rows = _list_ollama_models_via_manifests()
    if manifest_rows:
        return manifest_rows
    binary = str(ollama_binary or "").strip() or detect_ollama_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except Exception:
        return []
    lines = [line.rstrip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    rows: list[dict[str, str]] = []
    for raw in lines[1:]:
        parts = [part.strip() for part in re.split(r"\s{2,}", raw.strip()) if part.strip()]
        if len(parts) < 4:
            continue
        name = parts[0]
        model_id = parts[1]
        size = parts[2]
        modified = " ".join(parts[3:])
        rows.append({"name": name, "id": model_id, "size": size, "modified": modified})
    return rows


def run_ollama_benchmark(
    *,
    model_name: str,
    ollama_binary: str | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run an opt-in local generation smoke check for the selected Ollama model."""
    model = str(model_name or "").strip()
    binary = str(ollama_binary or "").strip() or detect_ollama_binary()
    base: dict[str, Any] = {
        "schema": "nulla.local_model_benchmark.v1",
        "model": model,
        "ollama_binary": binary,
        "marker": BENCHMARK_MARKER,
        "timeout_seconds": int(timeout_seconds),
        "note": "CLI wall-clock includes model load time; treat this as a local smoke and warmup check, not lab throughput.",
    }
    if not binary:
        return base | {"status": "skipped", "reason": "ollama binary missing"}
    if not model:
        return base | {"status": "skipped", "reason": "model missing"}

    prompt = (
        f"Return exactly this marker and no other text: {BENCHMARK_MARKER}"
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [binary, "run", model, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = max(0.0, time.perf_counter() - started)
        output = str(getattr(exc, "stdout", "") or "")
        stderr = str(getattr(exc, "stderr", "") or "")
        return base | {
            "status": "timeout",
            "elapsed_seconds": round(elapsed, 2),
            "output_excerpt": output[:400],
            "stderr_excerpt": stderr[:400],
            "reason": f"ollama run exceeded {int(timeout_seconds)} seconds",
        }
    except Exception as exc:
        elapsed = max(0.0, time.perf_counter() - started)
        return base | {
            "status": "failed",
            "elapsed_seconds": round(elapsed, 2),
            "reason": str(exc),
        }

    elapsed = max(0.0, time.perf_counter() - started)
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    output_tokens = len(stdout.split())
    rough_tps = round(output_tokens / elapsed, 2) if elapsed > 0 and output_tokens else 0.0
    marker_seen = BENCHMARK_MARKER in stdout
    status = "ok" if completed.returncode == 0 and marker_seen else "bad_output" if completed.returncode == 0 else "failed"
    result = base | {
        "status": status,
        "returncode": int(completed.returncode),
        "elapsed_seconds": round(elapsed, 2),
        "output_tokens": output_tokens,
        "rough_output_tokens_per_second": rough_tps,
        "marker_seen": marker_seen,
        "output_excerpt": stdout[:400],
        "stderr_excerpt": stderr[:400],
    }
    if status == "bad_output":
        result["reason"] = "ollama returned successfully, but the expected marker was not present"
    elif status == "failed":
        result["reason"] = "ollama run exited non-zero"
    return result


def remote_env_statuses() -> dict[str, dict[str, Any]]:
    def _present(*names: str) -> bool:
        return any(bool(os.environ.get(name)) for name in names)

    kimi = {
        "api_key_present": _present("KIMI_API_KEY", "MOONSHOT_API_KEY", "NULLA_KIMI_API_KEY"),
        "base_url_present": _present("KIMI_BASE_URL", "NULLA_KIMI_BASE_URL", "MOONSHOT_BASE_URL"),
        "model_present": _present("KIMI_MODEL", "NULLA_KIMI_MODEL", "MOONSHOT_MODEL"),
    }
    generic_remote_api_key = _present("OPENAI_API_KEY", "NULLA_REMOTE_API_KEY", "NULLA_CLOUD_API_KEY")
    generic_remote = {
        "api_key_present": generic_remote_api_key,
        "base_url_present": _present("NULLA_REMOTE_BASE_URL", "OPENAI_BASE_URL") or generic_remote_api_key,
        "model_present": _present("NULLA_REMOTE_MODEL", "OPENAI_MODEL") or generic_remote_api_key,
    }
    tether = {
        "api_key_present": bool(os.environ.get("TETHER_API_KEY")),
        "base_url_present": bool(os.environ.get("TETHER_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_TETHER_MODEL")),
    }
    qvac = {
        "api_key_present": bool(os.environ.get("QVAC_API_KEY")),
        "base_url_present": bool(os.environ.get("QVAC_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_QVAC_MODEL")),
    }
    return {
        "kimi": kimi | {"configured": kimi["api_key_present"]},
        "generic_remote": generic_remote | {"configured": all(generic_remote.values())},
        "tether": tether | {"configured": tether["api_key_present"] and tether["base_url_present"]},
        "qvac": qvac | {"configured": qvac["api_key_present"] and qvac["base_url_present"]},
    }


def _provider_state_for_prefix(
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    prefix: str,
) -> tuple[str, str]:
    matches = [item for item in capability_truth if item.provider_id.lower().startswith(prefix)]
    if not matches:
        return "unregistered", ""
    for candidate in matches:
        if candidate.availability_state == "ready":
            return candidate.availability_state, candidate.provider_id
    for candidate in matches:
        if candidate.availability_state == "degraded":
            return candidate.availability_state, candidate.provider_id
    return matches[0].availability_state, matches[0].provider_id


def _probe_env_for_install_profile(env_statuses: dict[str, dict[str, Any]]) -> dict[str, str]:
    env = dict(os.environ)
    if env_statuses.get("kimi", {}).get("configured"):
        env.setdefault("KIMI_API_KEY", "configured-via-provider-probe")
    if env_statuses.get("generic_remote", {}).get("configured"):
        env.setdefault("OPENAI_API_KEY", "configured-via-provider-probe")
    return env


def _model_installed(model_name: str, installed_names: set[str]) -> bool:
    clean = str(model_name or "").strip()
    if not clean:
        return False
    if clean in installed_names:
        return True
    if ":" not in clean and f"{clean}:latest" in installed_names:
        return True
    if clean.endswith(":latest") and clean.removesuffix(":latest") in installed_names:
        return True
    return False


def _local_model_pull_plan(
    *,
    recommended_models: tuple[str, ...],
    fallback_models: tuple[str, ...],
    installed_names: set[str],
    free_disk_gb: float,
    safe_disk_floor_gb: float,
) -> dict[str, Any]:
    recommended = tuple(str(item).strip() for item in recommended_models if str(item).strip())
    fallback = tuple(str(item).strip() for item in fallback_models if str(item).strip())
    installed_recommended = tuple(item for item in recommended if _model_installed(item, installed_names))
    missing = tuple(item for item in recommended if not _model_installed(item, installed_names))
    estimated_download_gb = round(sum(model_storage_gb(item) for item in missing), 1)
    free_gb = round(float(free_disk_gb or 0.0), 1)
    safe_floor_gb = round(float(safe_disk_floor_gb or 0.0), 1)
    disk_margin_gb = round(free_gb - safe_floor_gb, 1)
    needs_space = bool(missing) and disk_margin_gb < 0.0
    status = "ready" if recommended and not missing else "needs_space" if needs_space else "needs_setup"
    return {
        "recommended_models": list(recommended),
        "fallback_models": list(fallback),
        "installed_recommended_models": list(installed_recommended),
        "missing_recommended_models": list(missing),
        "pull_commands": [f"ollama pull {item}" for item in missing],
        "estimated_missing_download_gb": estimated_download_gb,
        "free_disk_gb": free_gb,
        "safe_disk_floor_gb": safe_floor_gb,
        "disk_margin_gb": disk_margin_gb,
        "minimum_space_to_free_gb": round(max(0.0, safe_floor_gb - free_gb), 1),
        "status": status,
    }


def build_probe_report(
    *,
    machine: MachineProbe | None = None,
    ollama_models: list[dict[str, str]] | None = None,
    ollama_binary: str | None = None,
    env_statuses: dict[str, dict[str, Any]] | None = None,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...] | None = None,
    show_unsupported: bool = False,
    run_benchmark: bool = False,
    benchmark_timeout_seconds: int = 180,
) -> dict[str, Any]:
    probe = machine
    if probe is None:
        from core.hardware_tier import probe_machine

        probe = probe_machine()
    primary_tier = select_qwen_tier(probe)
    binary = str(ollama_binary or "").strip() or detect_ollama_binary()
    models = list(ollama_models if ollama_models is not None else list_ollama_models(binary))
    model_names = {str(item.get("name") or "").strip() for item in models if str(item.get("name") or "").strip()}
    envs = dict(env_statuses or remote_env_statuses())
    local_fit = local_multi_llm_fit(probe)
    capability_truth = tuple(provider_capability_truth or build_provider_registry_snapshot().capability_truth)
    profile_truth = build_install_profile_truth(
        requested_profile="auto-recommended",
        probe=probe,
        tier=primary_tier,
        provider_capability_truth=capability_truth,
        env=_probe_env_for_install_profile(envs),
    )
    recommendation = build_install_recommendation_truth(
        probe=probe,
        tier=primary_tier,
    )
    summary = install_recommendation_machine_summary(
        probe=probe,
        tier=primary_tier,
        recommendation=recommendation,
    )
    secondary_local_state, secondary_local_provider_id = _provider_state_for_prefix(
        capability_truth,
        "llamacpp-local:",
    )

    stacks: list[dict[str, Any]] = []
    recommended_bundle_models = tuple(
        str(item).strip()
        for item in list(recommendation.recommended_bundle_models)
        if str(item).strip()
    )
    model_pull_plan = _local_model_pull_plan(
        recommended_models=recommended_bundle_models,
        fallback_models=recommendation.fallback_bundle_models,
        installed_names=model_names,
        free_disk_gb=recommendation.free_disk_gb,
        safe_disk_floor_gb=recommendation.safe_disk_floor_gb,
    )
    model_store_drive_plan = build_model_store_drive_plan(
        required_models=recommended_bundle_models,
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )
    local_only_ready = bool(binary) and not model_pull_plan["missing_recommended_models"]
    local_only_needs_space = bool(binary) and model_pull_plan["status"] == "needs_space"
    stacks.append(
        {
            "stack_id": "local_only",
            "install_profile_id": "local-only",
            "status": (
                "ready"
                if local_only_ready
                else "needs_space"
                if local_only_needs_space
                else "needs_setup"
                if binary
                else "needs_install"
            ),
            "recommended": False,
            "reason": (
                "The recommended local Ollama bundle is installed and ready."
                if local_only_ready
                else (
                    "Ollama is present, but the target volume is below the safe disk floor for the missing recommended bundle."
                )
                if local_only_needs_space
                else "Ollama is present, but the recommended local bundle is not fully pulled yet."
                if binary
                else "Ollama is missing; installer must provision it before the default local bundle is usable."
            ),
            "primary_model": recommendation.primary_local_model,
            "bundle_id": recommendation.recommended_bundle_id,
            "bundle_kind": recommendation.recommended_bundle_kind,
            "bundle_models": list(recommendation.recommended_bundle_models),
            "bundle_roles": list(recommendation.recommended_bundle_roles),
            "helper_model": "",
        }
    )

    if recommendation.secondary_local_supported:
        if not binary:
            dual_status = "needs_install"
            dual_reason = (
                "Ollama is missing, so NULLA cannot bring up the default local lane or its optional llama.cpp specialist lane yet."
            )
        elif secondary_local_state == "ready":
            dual_status = "ready"
            dual_reason = (
                "The optional llama.cpp verifier/coding lane is registered and healthy, so this host can run the stronger dual-local profile."
            )
        elif secondary_local_state == "degraded":
            dual_status = "degraded"
            dual_reason = (
                "The optional llama.cpp verifier/coding lane is present, but it is currently degraded and should not be treated as fully healthy."
            )
        elif secondary_local_state == "blocked":
            dual_status = "blocked"
            dual_reason = (
                "The optional llama.cpp verifier/coding lane is present, but current health state has it blocked."
            )
        else:
            dual_status = "needs_setup"
            dual_reason = (
                "This machine can support the optional llama.cpp verifier/coding lane, but it is not provisioned or configured yet."
            )
    else:
        dual_status = "too_small" if local_fit == "single_model_only" else "disk_constrained"
        dual_reason = (
            "This machine should stay on one local model at a time."
            if dual_status == "too_small"
            else "This machine could carry the optional llama.cpp verifier lane, but the active target volume does not have enough free space."
        )
    stacks.append(
        {
            "stack_id": "local_plus_llamacpp",
            "install_profile_id": "local-max",
            "status": dual_status,
            "recommended": False,
            "reason": dual_reason,
            "primary_model": recommendation.primary_local_model,
            "secondary_model": recommendation.secondary_local_model,
            "secondary_backend": recommendation.secondary_local_backend,
            "helper_model": recommendation.secondary_local_model,
            "provider_id": secondary_local_provider_id,
        }
    )

    unsupported_stacks: list[dict[str, Any]] = []
    if show_unsupported:
        unsupported_stacks.extend(
            [
                {
                    "stack_id": "local_plus_qvac",
                    "status": "not_implemented",
                    "recommended": False,
                    "reason": "QVAC does not have a real first-class installer/runtime lane in this repo yet.",
                    "primary_model": primary_tier.ollama_tag,
                    "helper_model": "",
                },
            ]
        )

    for stack in stacks:
        stack["recommended"] = stack.get("install_profile_id") == profile_truth.profile_id
        install_profile_id = str(stack.get("install_profile_id") or "").strip()
        stack["install_profile_display_id"] = (
            format_install_profile_id(install_profile_id, allow_auto=False) if install_profile_id else ""
        )
    recommended = next((item for item in stacks if item.get("recommended")), stacks[0])
    report = {
        "schema": "nulla.provider_probe.v1",
        "machine": summary,
        "ollama": {
            "binary_present": bool(binary),
            "binary_path": binary,
            "installed_models": models,
        },
        "remote_env": envs,
        "local_multi_llm_fit": local_fit,
        "capacity_bucket": recommendation.capacity_bucket,
        "recommended_install_profile_id": profile_truth.profile_id,
        "recommended_install_profile_display_id": format_install_profile_id(profile_truth.profile_id, allow_auto=False),
        "recommended_install_profile_label": profile_truth.label,
        "recommended_install_profile_summary": profile_truth.summary,
        "install_recommendation": recommendation.to_dict(),
        "local_model_plan": model_pull_plan,
        "model_store_drive_plan": model_store_drive_plan,
        "recommended_stack_id": str(recommended.get("stack_id") or ""),
        "stacks": stacks,
    }
    if unsupported_stacks:
        report["unsupported_stacks"] = unsupported_stacks
    if run_benchmark:
        report["local_model_benchmark"] = run_ollama_benchmark(
            model_name=recommendation.primary_local_model,
            ollama_binary=binary,
            timeout_seconds=benchmark_timeout_seconds,
        )
    return report


def render_probe_report(report: dict[str, Any]) -> str:
    machine = dict(report.get("machine") or {})
    ollama = dict(report.get("ollama") or {})
    stacks = [dict(item) for item in list(report.get("stacks") or []) if isinstance(item, dict)]
    unsupported_stacks = [dict(item) for item in list(report.get("unsupported_stacks") or []) if isinstance(item, dict)]
    lines = [
        "NULLA machine/provider probe",
        f"- machine: {machine.get('cpu_cores')} cores, {machine.get('ram_gb')} GiB RAM, {machine.get('gpu') or 'no gpu'}",
        f"- accelerator: {machine.get('accelerator') or 'unknown'}",
        f"- recommended local model: {machine.get('ollama_model') or 'unknown'}",
        f"- local multi-LLM fit: {report.get('local_multi_llm_fit') or 'unknown'}",
        f"- capacity bucket: {report.get('capacity_bucket') or 'unknown'}",
        f"- ollama present: {'yes' if ollama.get('binary_present') else 'no'}",
    ]
    accelerator_status = str(machine.get("accelerator_status") or "").strip()
    accelerator_advice = str(machine.get("accelerator_advice") or "").strip()
    if accelerator_status and accelerator_status not in {"usable", "cpu"}:
        lines.append(f"- accelerator status: {accelerator_status}")
    if accelerator_advice:
        lines.append(f"- accelerator advice: {accelerator_advice}")
    gpu_rows = [dict(item) for item in list(machine.get("gpu_devices") or []) if isinstance(item, dict)]
    if gpu_rows:
        rendered_gpus = []
        for row in gpu_rows[:8]:
            vram = row.get("vram_gb")
            vram_label = f"{vram} GB" if vram is not None else "unknown VRAM"
            active = " active" if row.get("active_accelerator") else ""
            selected = " selected" if row.get("selected") and not active else ""
            rendered_gpus.append(
                f"[{row.get('index')}] {row.get('name')} {vram_label} "
                f"{row.get('backend')} {row.get('status')}{active}{selected}"
            )
        lines.append(f"- detected GPUs: {'; '.join(rendered_gpus)}")
    installed = [str(item.get("name") or "").strip() for item in list(ollama.get("installed_models") or []) if str(item.get("name") or "").strip()]
    lines.append(f"- installed local models: {', '.join(installed) if installed else 'none'}")
    model_plan = dict(report.get("local_model_plan") or {})
    drive_plan = dict(report.get("model_store_drive_plan") or {})
    if drive_plan:
        recommended_drive = dict(drive_plan.get("recommended_drive") or {})
        current_drive = dict(drive_plan.get("current_drive") or {})
        lines.append(f"- current model store: {drive_plan.get('current_model_store_path') or 'unknown'}")
        if recommended_drive:
            lines.append(
                "- recommended model store: "
                f"{drive_plan.get('recommended_model_store_path')} "
                f"({recommended_drive.get('drive') or 'unknown'}, "
                f"{recommended_drive.get('free_gb')} GB free)"
            )
        if current_drive and str(drive_plan.get("status") or "") == "move_recommended":
            lines.append(
                "- current model-store drive: "
                f"{current_drive.get('drive') or 'unknown'} "
                f"({current_drive.get('free_gb')} GB free)"
            )
            if str(drive_plan.get("set_env_command") or "").strip():
                lines.append(f"- model-store action: {drive_plan.get('set_env_command')}")
        drive_rows = [
            dict(item)
            for item in list(drive_plan.get("drives") or [])
            if isinstance(item, dict)
        ]
        if drive_rows:
            drive_summary = "; ".join(
                f"{row.get('drive')} {row.get('free_gb')} GB {row.get('status')}"
                for row in drive_rows[:6]
            )
            lines.append(f"- mounted drive space: {drive_summary}")
    if model_plan:
        missing_models = [
            str(item).strip()
            for item in list(model_plan.get("missing_recommended_models") or [])
            if str(item).strip()
        ]
        if missing_models:
            lines.append(f"- missing recommended models: {', '.join(missing_models)}")
            pull_commands = [
                str(item).strip()
                for item in list(model_plan.get("pull_commands") or [])
                if str(item).strip()
            ]
            if pull_commands:
                lines.append(f"- pull commands: {'; '.join(pull_commands)}")
            lines.append(f"- estimated missing model download: {model_plan.get('estimated_missing_download_gb')} GB")
            lines.append(
                "- model disk headroom: "
                f"{model_plan.get('free_disk_gb')} GB free, "
                f"{model_plan.get('safe_disk_floor_gb')} GB safe floor, "
                f"{model_plan.get('disk_margin_gb')} GB margin"
            )
            if str(model_plan.get("status") or "") == "needs_space":
                lines.append(f"- disk action: free at least {model_plan.get('minimum_space_to_free_gb')} GB before pulling")
        else:
            lines.append("- recommended model bundle installed: yes")
    display_profile_id = str(report.get("recommended_install_profile_display_id") or "").strip()
    lines.append(
        f"- recommended install profile: {display_profile_id or report.get('recommended_install_profile_id') or 'unknown'}"
    )
    recommendation = dict(report.get("install_recommendation") or {})
    if recommendation:
        lines.append(f"- primary local model: {recommendation.get('primary_local_model') or 'unknown'}")
        bundle_models = ", ".join(str(item).strip() for item in list(recommendation.get("recommended_bundle_models") or []) if str(item).strip())
        if bundle_models:
            lines.append(
                f"- recommended bundle: {recommendation.get('recommended_bundle_id') or 'unknown'} "
                f"({recommendation.get('recommended_bundle_kind') or 'unknown'}): {bundle_models}"
            )
        fallback_models = ", ".join(str(item).strip() for item in list(recommendation.get("fallback_bundle_models") or []) if str(item).strip())
        if fallback_models:
            lines.append(f"- lighter fallback: {recommendation.get('fallback_bundle_id') or 'unknown'}: {fallback_models}")
        optional_profile = str(recommendation.get("recommended_optional_profile_display_id") or "").strip()
        if optional_profile:
            lines.append(f"- optional stronger profile: {optional_profile}")
            lines.append(f"- optional secondary model: {recommendation.get('secondary_local_model') or 'unknown'}")
    benchmark = dict(report.get("local_model_benchmark") or {})
    if benchmark:
        status = str(benchmark.get("status") or "unknown")
        elapsed = benchmark.get("elapsed_seconds")
        elapsed_label = f", {elapsed}s wall-clock" if elapsed is not None else ""
        lines.append(
            f"- local model live check: {status} on {benchmark.get('model') or 'unknown'}{elapsed_label}"
        )
        if benchmark.get("rough_output_tokens_per_second"):
            lines.append(f"- rough output tokens/sec: {benchmark.get('rough_output_tokens_per_second')}")
        reason = str(benchmark.get("reason") or "").strip()
        if reason:
            lines.append(f"- live check reason: {reason}")
    lines.append(f"- recommended stack: {report.get('recommended_stack_id') or 'unknown'}")
    lines.append("- stack status:")
    for stack in stacks:
        install_profile_id = str(stack.get("install_profile_display_id") or stack.get("install_profile_id") or "").strip()
        profile_suffix = f" -> {install_profile_id}" if install_profile_id else ""
        lines.append(f"  - {stack.get('stack_id')}{profile_suffix}: {stack.get('status')} — {stack.get('reason')}")
    if unsupported_stacks:
        lines.append("- unsupported stacks:")
        for stack in unsupported_stacks:
            lines.append(f"  - {stack.get('stack_id')}: {stack.get('status')} — {stack.get('reason')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-provider-probe")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    parser.add_argument(
        "--show-unsupported",
        action="store_true",
        help="Include unsupported remote ideas like Tether or QVAC in a separate section.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run an Ollama generation smoke/warmup check for the recommended local model.",
    )
    parser.add_argument(
        "--benchmark-timeout",
        type=int,
        default=180,
        help="Maximum seconds to wait for the optional Ollama generation check.",
    )
    args = parser.parse_args()

    report = build_probe_report(
        show_unsupported=bool(args.show_unsupported),
        run_benchmark=bool(args.benchmark),
        benchmark_timeout_seconds=max(1, int(args.benchmark_timeout)),
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_probe_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
