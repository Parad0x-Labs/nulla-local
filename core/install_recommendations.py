from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier, tier_summary
from core.local_model_bundles import (
    bundle_spec,
    local_multi_llm_fit_from_probe,
    resolve_local_bundle_recommendation,
)
from core.local_specialist_lane import (
    DEFAULT_SECONDARY_LOCAL_BACKEND,
    DEFAULT_SECONDARY_LOCAL_BASE_URL,
    DEFAULT_SECONDARY_LOCAL_MODEL,
    secondary_local_model,
)
from core.provider_env import merge_provider_env
from core.runtime_install_profiles import default_ollama_models_path, format_install_profile_id


@dataclass(frozen=True)
class InstallRecommendationTruth:
    recommended_default_profile: str
    recommended_optional_profile: str
    primary_local_model: str
    secondary_local_model: str
    secondary_local_supported: bool
    selection_reasons: tuple[str, ...]
    local_multi_llm_fit: str
    capacity_bucket: str
    free_disk_gb: float
    safe_disk_floor_gb: float
    recommended_bundle_id: str
    recommended_bundle_kind: str
    recommended_bundle_models: tuple[str, ...]
    recommended_bundle_roles: tuple[tuple[str, str], ...]
    fallback_bundle_id: str
    fallback_bundle_models: tuple[str, ...]
    fallback_bundle_roles: tuple[tuple[str, str], ...]
    advanced_optional_profile: str = ""
    secondary_local_backend: str = DEFAULT_SECONDARY_LOCAL_BACKEND
    secondary_local_base_url: str = DEFAULT_SECONDARY_LOCAL_BASE_URL

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "nulla.install_recommendation.v2",
            "recommended_default_profile": self.recommended_default_profile,
            "recommended_default_profile_display_id": format_install_profile_id(
                self.recommended_default_profile,
                allow_auto=False,
            ),
            "recommended_optional_profile": self.recommended_optional_profile,
            "recommended_optional_profile_display_id": format_install_profile_id(
                self.recommended_optional_profile,
                allow_auto=False,
            )
            if self.recommended_optional_profile
            else "",
            "primary_local_model": self.primary_local_model,
            "secondary_local_model": self.secondary_local_model,
            "secondary_local_supported": self.secondary_local_supported,
            "selection_reasons": list(self.selection_reasons),
            "local_multi_llm_fit": self.local_multi_llm_fit,
            "capacity_bucket": self.capacity_bucket,
            "free_disk_gb": self.free_disk_gb,
            "safe_disk_floor_gb": self.safe_disk_floor_gb,
            "recommended_bundle_id": self.recommended_bundle_id,
            "recommended_bundle_kind": self.recommended_bundle_kind,
            "recommended_bundle_models": list(self.recommended_bundle_models),
            "recommended_bundle_roles": [
                {"role": role, "model": model} for role, model in self.recommended_bundle_roles
            ],
            "fallback_bundle_id": self.fallback_bundle_id,
            "fallback_bundle_models": list(self.fallback_bundle_models),
            "fallback_bundle_roles": [
                {"role": role, "model": model} for role, model in self.fallback_bundle_roles
            ],
            "advanced_optional_profile": self.advanced_optional_profile,
            "secondary_local_backend": self.secondary_local_backend,
            "secondary_local_base_url": self.secondary_local_base_url,
        }


def local_multi_llm_fit(probe: MachineProbe | Mapping[str, Any] | None = None) -> str:
    active_probe = probe or probe_machine()
    return local_multi_llm_fit_from_probe(active_probe)


def build_install_recommendation_truth(
    *,
    probe: MachineProbe | None = None,
    tier: QwenTier | None = None,
    selected_model: str | None = None,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> InstallRecommendationTruth:
    env_map = merge_provider_env(runtime_home, env=os.environ if env is None else env)
    active_probe = probe or probe_machine()
    active_tier = tier or select_qwen_tier(active_probe)
    free_gb = _free_gb(runtime_home)
    specialist_model = secondary_local_model(env_map)
    bundle_recommendation = resolve_local_bundle_recommendation(
        probe=active_probe,
        free_disk_gb=free_gb,
        secondary_local_model_name=specialist_model,
        selected_model=str(selected_model or "").strip(),
    )
    recommended_bundle = bundle_recommendation.recommended_bundle
    fallback_bundle = bundle_recommendation.fallback_bundle
    primary_local_model = recommended_bundle.primary_model or str(active_tier.ollama_tag or "").strip() or "qwen3:8b"

    reasons = [
        (
            f"Primary local companion model resolves to `{primary_local_model}` "
            f"for capacity bucket `{bundle_recommendation.capacity_bucket}`."
        ),
        (
            f"Recommended local bundle `{recommended_bundle.bundle_id}` ({recommended_bundle.kind}) "
            f"fits the current hardware and SSD headroom."
        ),
        *bundle_recommendation.selection_reasons,
    ]
    if bundle_recommendation.advanced_optional_allowed:
        reasons.append(
            f"The explicit advanced profile `{DEFAULT_SECONDARY_LOCAL_BACKEND}` lane remains optional and is not auto-installed by the default one-line path."
        )
    else:
        reasons.append(
            f"The explicit advanced `{DEFAULT_SECONDARY_LOCAL_BACKEND}` lane is not recommended on this host right now."
        )

    return InstallRecommendationTruth(
        recommended_default_profile="local-only",
        recommended_optional_profile=bundle_recommendation.advanced_optional_profile,
        primary_local_model=primary_local_model,
        secondary_local_model=specialist_model,
        secondary_local_supported=bundle_recommendation.advanced_optional_allowed,
        selection_reasons=tuple(dict.fromkeys(reason.strip() for reason in reasons if reason.strip())),
        local_multi_llm_fit=bundle_recommendation.local_multi_llm_fit,
        capacity_bucket=bundle_recommendation.capacity_bucket,
        free_disk_gb=bundle_recommendation.free_disk_gb,
        safe_disk_floor_gb=bundle_recommendation.safe_disk_floor_gb,
        recommended_bundle_id=recommended_bundle.bundle_id,
        recommended_bundle_kind=recommended_bundle.kind,
        recommended_bundle_models=recommended_bundle.models,
        recommended_bundle_roles=tuple((item.role, item.model) for item in recommended_bundle.role_models),
        fallback_bundle_id=fallback_bundle.bundle_id,
        fallback_bundle_models=fallback_bundle.models,
        fallback_bundle_roles=tuple((item.role, item.model) for item in fallback_bundle.role_models),
        advanced_optional_profile=bundle_recommendation.advanced_optional_profile,
    )


def install_recommendation_machine_summary(
    *,
    probe: MachineProbe | None = None,
    tier: QwenTier | None = None,
    recommendation: InstallRecommendationTruth | None = None,
    selected_model: str | None = None,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    active_probe = probe or probe_machine()
    active_tier = tier or select_qwen_tier(active_probe)
    active_recommendation = recommendation or build_install_recommendation_truth(
        probe=active_probe,
        tier=active_tier,
        selected_model=selected_model,
        runtime_home=runtime_home,
        env=env,
    )
    summary = dict(tier_summary(active_probe))
    primary_local_model = str(active_recommendation.primary_local_model or "").strip()
    if primary_local_model:
        summary["ollama_model"] = primary_local_model
        param_billions = _param_billions_for_model(primary_local_model)
        if param_billions is not None:
            summary["param_billions"] = param_billions
    capacity_bucket = str(active_recommendation.capacity_bucket or "").strip()
    if capacity_bucket:
        summary["selected_tier"] = f"capacity-{capacity_bucket}"
        summary["capacity_bucket"] = capacity_bucket
    if active_recommendation.recommended_bundle_id:
        summary["recommended_bundle_id"] = active_recommendation.recommended_bundle_id
    if active_recommendation.recommended_bundle_kind:
        summary["recommended_bundle_kind"] = active_recommendation.recommended_bundle_kind
    if active_recommendation.recommended_bundle_models:
        summary["recommended_bundle_models"] = list(active_recommendation.recommended_bundle_models)
    if active_recommendation.recommended_optional_profile:
        summary["recommended_optional_profile"] = active_recommendation.recommended_optional_profile
    return summary


def _free_gb(runtime_home: str | Path | None) -> float:
    candidate = (
        Path(runtime_home).expanduser().resolve()
        if runtime_home
        else default_ollama_models_path()
    )
    existing = _nearest_existing_path(candidate)
    try:
        usage = shutil.disk_usage(existing)
    except OSError:
        try:
            usage = shutil.disk_usage(Path.home().resolve())
        except OSError:
            return 0.0
    return float(usage.free) / (1024.0**3)


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    if current.exists():
        return current.resolve()
    return Path.home().resolve()


def _param_billions_for_model(model_tag: str) -> float | None:
    match = re.search(r":([0-9]+(?:\.[0-9]+)?)b(?:$|[^a-z0-9])", str(model_tag or "").strip().lower())
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


__all__ = [
    "DEFAULT_SECONDARY_LOCAL_BACKEND",
    "DEFAULT_SECONDARY_LOCAL_BASE_URL",
    "DEFAULT_SECONDARY_LOCAL_MODEL",
    "InstallRecommendationTruth",
    "build_install_recommendation_truth",
    "bundle_spec",
    "install_recommendation_machine_summary",
    "local_multi_llm_fit",
]
