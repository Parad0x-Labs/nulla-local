from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.hardware_tier import MachineProbe


@dataclass(frozen=True)
class BundleRoleModel:
    role: str
    model: str


@dataclass(frozen=True)
class LocalBundleSpec:
    bundle_id: str
    kind: str
    display_name: str
    role_models: tuple[BundleRoleModel, ...]
    summary: str

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(item.model for item in self.role_models)

    @property
    def role_map(self) -> dict[str, str]:
        return {item.role: item.model for item in self.role_models}

    @property
    def primary_model(self) -> str:
        role_map = self.role_map
        return (
            role_map.get("general")
            or role_map.get("coding")
            or role_map.get("reasoning")
            or next(iter(role_map.values()), "qwen3:8b")
        )


@dataclass(frozen=True)
class LocalBundleRecommendation:
    capacity_bucket: str
    local_multi_llm_fit: str
    free_disk_gb: float
    recommended_bundle: LocalBundleSpec
    fallback_bundle: LocalBundleSpec
    safe_disk_floor_gb: float
    advanced_optional_allowed: bool
    advanced_optional_profile: str
    selection_reasons: tuple[str, ...]
    legacy_mode: bool = False


MODEL_STORAGE_GB: dict[str, float] = {
    "qwen3:0.6b": 0.7,
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3:14b": 9.3,
    "qwen3:30b": 19.0,
    "qwen3:30b-a3b": 19.0,
    "nulla-qwen3-30b-a3b:nothink": 19.0,
    "qwen3.5:35b-a3b": 23.0,
    "deepseek-r1:8b": 5.2,
    "deepseek-r1:14b": 9.0,
    "deepseek-r1:32b": 20.0,
    "gemma3:4b": 3.3,
    "gemma3:12b": 8.1,
    "gemma3:12b-qat": 8.1,
    "mistral-small:24b": 14.0,
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:14b-gguf": 18.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
}

MODEL_METADATA: dict[str, dict[str, Any]] = {
    "qwen3:0.6b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "0.6B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:4b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "4B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:8b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "8B",
        "bundle_role": "general",
        "eagle3_draft_eligible": True,
    },
    "qwen3:14b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "14B",
        "bundle_role": "coding",
        "eagle3_draft_eligible": True,
    },
    "qwen3:30b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
    },
    "qwen3:30b-a3b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
    },
    "nulla-qwen3-30b-a3b:nothink": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "user-managed local Ollama model",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
        "thinking_disabled": True,
        "tokens_per_second": 37.2,
        "quantization": "local-measured",
        "notes": "Measured fast local no-think default when installed.",
    },
    "qwen3.5:35b-a3b": {
        "family": "qwen3.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3.5",
        "parameter_count": "35B",
        "active_parameter_count": "3.3B",
        "architecture": "hybrid_moe",
        "bundle_role": "heavy_reasoning",
        "eagle3_draft_eligible": False,
        "constant_vram": True,
        "max_context": 262144,
    },
    "deepseek-r1:8b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "8B",
    },
    "deepseek-r1:14b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "14B",
    },
    "deepseek-r1:32b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "32B",
    },
    "gemma3:4b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "4B",
    },
    "gemma3:12b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
    },
    "gemma3:12b-qat": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
        "bundle_role": "general",
        "qat": True,
        "eagle3_draft_eligible": False,
    },
    "mistral-small:24b": {
        "family": "mistral-small",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/mistral-small",
        "parameter_count": "24B",
    },
    "qwen2.5:0.5b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "0.5B",
    },
    "qwen2.5:3b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "3B",
    },
    "qwen2.5:7b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "7B",
    },
    "qwen2.5:14b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "14B",
    },
    "qwen2.5:32b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "32B",
    },
}


LOCAL_BUNDLE_SPECS: dict[str, LocalBundleSpec] = {
    "single_gemma3_4b": LocalBundleSpec(
        bundle_id="single_gemma3_4b",
        kind="single",
        display_name="Single S2 lightweight fallback",
        role_models=(BundleRoleModel("general", "gemma3:4b"),),
        summary="Single lightweight fallback for constrained hosts.",
    ),
    "single_qwen3_4b": LocalBundleSpec(
        bundle_id="single_qwen3_4b",
        kind="single",
        display_name="Single lightweight Qwen fallback",
        role_models=(BundleRoleModel("general", "qwen3:4b"),),
        summary="Single lightweight Qwen fallback when Gemma is not preferred.",
    ),
    "single_qwen3_8b": LocalBundleSpec(
        bundle_id="single_qwen3_8b",
        kind="single",
        display_name="Single S1 safest default",
        role_models=(BundleRoleModel("general", "qwen3:8b"),),
        summary="Single best-overall local model for constrained-but-capable hosts.",
    ),
    "dual_qwen3_8b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D1 balanced default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Balanced local pair for general companion work plus deeper reasoning and review.",
    ),
    "dual_qwen3_8b_gemma3_4b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_gemma3_4b",
        kind="dual",
        display_name="Dual D3 lighter fallback",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("lightweight_utility", "gemma3:4b"),
        ),
        summary="Lighter local pair with a cheap utility backup lane.",
    ),
    "dual_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_mistral_small_24b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D2 coding and reasoning",
        role_models=(
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Stronger local dual for code/tool tasks plus second-pass reasoning.",
    ),
    "triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b",
        kind="triple",
        display_name="Triple T1 practical default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Practical triple with explicit general, coding, and reasoning lanes.",
    ),
    "triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b": LocalBundleSpec(
        bundle_id="triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b",
        kind="triple",
        display_name="Triple T3 enthusiast",
        role_models=(
            BundleRoleModel("general", "qwen3:14b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:14b"),
        ),
        summary="High-end triple for strong hosts with clear role separation.",
    ),
    "goblin_stack": LocalBundleSpec(
        bundle_id="goblin_stack",
        kind="triple",
        display_name="Goblin local stack",
        role_models=(
            BundleRoleModel("lightweight_utility", "qwen3:0.6b"),
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("heavy_reasoning", "qwen3.5:35b-a3b"),
        ),
        summary="Qwen3-family local stack with tiny routing, daily workhorse, and hybrid-MoE deep lane.",
    ),
}


def model_storage_gb(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    if clean in MODEL_STORAGE_GB:
        return float(MODEL_STORAGE_GB[clean])

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return round(max(1.0, float(match.group(1)) * 0.75), 1)
    return 8.0


def model_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return 8.0


def model_active_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("active_parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    match = re.search(r"-a(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return model_parameter_billions(clean)


def model_metadata(model_name: str) -> dict[str, Any]:
    clean = str(model_name or "").strip().lower()
    return dict(MODEL_METADATA.get(clean) or {})


def installed_ollama_role_for_model(*, model_name: str, primary_model: str = "") -> str:
    clean = str(model_name or "").strip().lower()
    primary = str(primary_model or "").strip().lower()
    if primary and clean == primary:
        return "general"
    if "mistral-small" in clean or "coder" in clean or "code" in clean:
        return "coding"
    if "deepseek-r1" in clean or "reason" in clean:
        return "reasoning"
    parameter_b = model_parameter_billions(clean)
    if parameter_b <= 3.0:
        return "lightweight_utility"
    if parameter_b >= 24.0:
        return "heavy_reasoning"
    if parameter_b >= 13.0:
        return "reasoning"
    return "general"


def safe_disk_floor_gb(models: tuple[str, ...] | list[str]) -> float:
    total_size = sum(model_storage_gb(item) for item in models if str(item).strip())
    return round(max(total_size * 2.0, total_size + 25.0), 1)


def bundle_spec(bundle_id: str) -> LocalBundleSpec:
    return LOCAL_BUNDLE_SPECS[bundle_id]


def local_multi_llm_fit_from_probe(probe: MachineProbe | Mapping[str, Any]) -> str:
    if isinstance(probe, Mapping):
        ram_gb = float(probe.get("ram_gb") or 0.0)
        accelerator = str(probe.get("accelerator") or "").strip().lower()
        vram_gb = float(probe.get("vram_gb") or 0.0) if probe.get("vram_gb") is not None else 0.0
    else:
        ram_gb = float(probe.ram_gb or 0.0)
        accelerator = str(probe.accelerator or "").strip().lower()
        vram_gb = float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0
    if accelerator == "mps":
        if ram_gb >= 48.0:
            return "comfortable"
        if ram_gb >= 24.0:
            return "pressure_sensitive"
        return "single_model_only"
    if vram_gb >= 20.0 or ram_gb >= 48.0:
        return "comfortable"
    if vram_gb >= 10.0 or ram_gb >= 24.0:
        return "pressure_sensitive"
    return "single_model_only"


def capacity_bucket_for_machine(*, probe: MachineProbe | Mapping[str, Any], free_disk_gb: float) -> str:
    if isinstance(probe, Mapping):
        ram_gb = float(probe.get("ram_gb") or 0.0)
        accelerator = str(probe.get("accelerator") or "").strip().lower()
        raw_vram = probe.get("vram_gb")
        vram_gb = float(raw_vram or 0.0) if raw_vram is not None else 0.0
    else:
        ram_gb = float(probe.ram_gb or 0.0)
        accelerator = str(probe.accelerator or "").strip().lower()
        vram_gb = float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0
    effective_vram = ram_gb if accelerator == "mps" else vram_gb
    free_gb = float(free_disk_gb or 0.0)
    if ram_gb < 16.0 or effective_vram < 6.0 or free_gb < 20.0:
        return "A"
    if ram_gb < 24.0 or effective_vram < 10.0 or free_gb < 40.0:
        return "B"
    if ram_gb < 32.0 or effective_vram < 16.0 or free_gb < 80.0:
        return "C"
    if ram_gb < 48.0 or effective_vram < 24.0 or free_gb < 150.0:
        return "D"
    return "E"


def resolve_local_bundle_recommendation(
    *,
    probe: MachineProbe | Mapping[str, Any],
    free_disk_gb: float,
    secondary_local_model_name: str,
    selected_model: str = "",
) -> LocalBundleRecommendation:
    explicit_model = str(selected_model or "").strip()
    fit = local_multi_llm_fit_from_probe(probe)
    bucket = capacity_bucket_for_machine(probe=probe, free_disk_gb=free_disk_gb)
    advanced_allowed = fit != "single_model_only" and free_disk_gb >= model_storage_gb(secondary_local_model_name) + 8.0

    if explicit_model:
        legacy_bundle = LocalBundleSpec(
            bundle_id="legacy_single_explicit_model",
            kind="single",
            display_name="Explicit single model",
            role_models=(BundleRoleModel("general", explicit_model),),
            summary="Preserve an explicitly selected local model without silently replacing it.",
        )
        fallback_bundle = bundle_spec("dual_qwen3_8b_gemma3_4b")
        reasons = (
            f"Explicit primary model `{explicit_model}` is preserved instead of silently switching the runtime to a new bundle.",
            "Bundle auto-selection remains available for fresh installs that do not pin a legacy model.",
        )
        return LocalBundleRecommendation(
            capacity_bucket=bucket,
            local_multi_llm_fit=fit,
            free_disk_gb=round(float(free_disk_gb), 1),
            recommended_bundle=legacy_bundle,
            fallback_bundle=fallback_bundle,
            safe_disk_floor_gb=safe_disk_floor_gb(legacy_bundle.models),
            advanced_optional_allowed=advanced_allowed,
            advanced_optional_profile="local-max" if advanced_allowed else "",
            selection_reasons=reasons,
            legacy_mode=True,
        )

    if bucket == "A":
        recommended = bundle_spec("single_gemma3_4b")
        fallback = bundle_spec("single_qwen3_4b")
        reasons = (
            "Capacity bucket A stays on one lightweight local model because RAM, VRAM, or free SSD is too constrained for a useful bundle.",
            "Gemma 3 4B is the safest local fallback on small hosts.",
        )
    elif bucket == "B":
        recommended = bundle_spec("single_qwen3_8b")
        dual_fallback = bundle_spec("dual_qwen3_8b_gemma3_4b")
        dual_floor = safe_disk_floor_gb(dual_fallback.models)
        fallback = dual_fallback if free_disk_gb >= dual_floor and _probe_ram_gb(probe) >= 20.0 else bundle_spec("single_gemma3_4b")
        reasons = (
            "Capacity bucket B defaults to one strong general local model to keep first-run latency and disk pressure sane.",
            "A lighter dual fallback is surfaced only when RAM and SSD headroom are both good enough.",
        )
    elif bucket == "C":
        recommended = bundle_spec("dual_qwen3_8b_deepseek_r1_8b")
        fallback = bundle_spec("dual_qwen3_8b_gemma3_4b")
        reasons = (
            "Capacity bucket C prefers a dual local bundle with a clear general lane and a clear reasoning lane.",
            "This keeps the default install fully local while materially improving review and planning quality over a single-model setup.",
        )
    elif bucket == "D":
        dual = bundle_spec("dual_mistral_small_24b_deepseek_r1_8b")
        triple = bundle_spec("triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b")
        triple_floor = safe_disk_floor_gb(triple.models)
        if free_disk_gb >= triple_floor + 10.0 and _probe_ram_gb(probe) >= 36.0:
            recommended = triple
            fallback = dual
            reasons = (
                "Capacity bucket D is strong enough for an explicit general/coding/reasoning triple when disk headroom is comfortably above the safe floor.",
                "The dual coding-plus-reasoning pair remains the lighter fallback.",
            )
        else:
            recommended = dual
            fallback = bundle_spec("dual_qwen3_8b_deepseek_r1_8b")
            reasons = (
                "Capacity bucket D defaults to a coding and reasoning pair unless disk and memory headroom are clearly comfortable for a triple.",
                "The balanced Qwen plus DeepSeek pair remains the lighter fallback.",
            )
    else:
        recommended = bundle_spec("triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b")
        fallback = bundle_spec("triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b")
        reasons = (
            "Capacity bucket E defaults to a full three-lane local bundle because the host has high-end RAM and SSD headroom.",
            "The smaller practical triple remains the lighter fallback for the same role split.",
        )

    return LocalBundleRecommendation(
        capacity_bucket=bucket,
        local_multi_llm_fit=fit,
        free_disk_gb=round(float(free_disk_gb), 1),
        recommended_bundle=recommended,
        fallback_bundle=fallback,
        safe_disk_floor_gb=safe_disk_floor_gb(recommended.models),
        advanced_optional_allowed=advanced_allowed,
        advanced_optional_profile="local-max" if advanced_allowed else "",
        selection_reasons=reasons,
    )


def provider_role_for_bundle_role(bundle_role: str) -> str:
    clean = str(bundle_role or "").strip().lower()
    if clean in {"reasoning", "heavy_reasoning"}:
        return "queen"
    return "drone"


def manifest_profile_for_model(*, model_name: str, bundle_role: str) -> dict[str, Any]:
    metadata = model_metadata(model_name)
    family = str(metadata.get("family") or model_name).strip()
    clean_role = str(bundle_role or "general").strip().lower()
    capabilities = ["summarize", "classify", "format", "extract", "structured_json"]
    tool_support = ["structured_json"]
    confidence = 0.67
    notes = "General local Ollama lane."
    if clean_role == "general":
        capabilities.extend(["code_basic", "tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.72
        notes = "General local Ollama companion lane."
    elif clean_role == "coding":
        capabilities.extend(["code_basic", "code_complex", "tool_intent"])
        tool_support.extend(["tool_calls", "code_complex"])
        confidence = 0.77
        notes = "Coding-focused local Ollama lane."
    elif clean_role == "reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.79
        notes = "Reasoning and review local Ollama lane."
    elif clean_role == "heavy_reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.73
        notes = "Oversized local Ollama lane for explicit deep work."
    elif clean_role == "lightweight_utility":
        capabilities.extend(["tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.63
        notes = "Lightweight local Ollama utility lane for cheap classification and tool intent."
    profile = {
        "family": family,
        "license_name": str(metadata.get("license_name") or "user-managed"),
        "license_reference": str(metadata.get("license_reference") or "user-managed"),
        "parameter_count": str(metadata.get("parameter_count") or "").strip(),
        "capabilities": tuple(dict.fromkeys(capabilities)),
        "tool_support": tuple(dict.fromkeys(tool_support)),
        "confidence_baseline": confidence,
        "notes": notes,
        "orchestration_role": provider_role_for_bundle_role(clean_role),
        "bundle_role": clean_role,
    }
    for key in ("tokens_per_second", "quantization"):
        if metadata.get(key) not in (None, ""):
            profile[key] = metadata[key]
    return profile


def _probe_ram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    if isinstance(probe, Mapping):
        return float(probe.get("ram_gb") or 0.0)
    return float(probe.ram_gb or 0.0)


__all__ = [
    "LOCAL_BUNDLE_SPECS",
    "MODEL_METADATA",
    "MODEL_STORAGE_GB",
    "BundleRoleModel",
    "LocalBundleRecommendation",
    "LocalBundleSpec",
    "bundle_spec",
    "capacity_bucket_for_machine",
    "installed_ollama_role_for_model",
    "local_multi_llm_fit_from_probe",
    "manifest_profile_for_model",
    "model_active_parameter_billions",
    "model_metadata",
    "model_parameter_billions",
    "model_storage_gb",
    "provider_role_for_bundle_role",
    "resolve_local_bundle_recommendation",
    "safe_disk_floor_gb",
]
