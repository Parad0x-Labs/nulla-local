from __future__ import annotations

from dataclasses import dataclass, field

from core import policy_engine
from core.local_model_bundles import model_parameter_billions
from core.model_capabilities import capability_score, required_capabilities
from core.model_health import circuit_is_open
from core.model_trust import provider_base_trust
from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class ModelSelectionRequest:
    task_kind: str
    output_mode: str = "plain_text"
    preferred_provider: str | None = None
    preferred_model: str | None = None
    preferred_source_types: list[str] = field(default_factory=list)
    require_license_metadata: bool = True
    forbid_bundled_weights: bool = True
    allow_paid_fallback: bool = False
    exclude_provider_ids: list[str] = field(default_factory=list)
    min_trust: float = 0.0


def provider_cost_class(manifest: ModelProviderManifest) -> str:
    if manifest.adapter_type == "cloud_fallback_provider":
        return "paid_cloud"
    base_url = str(manifest.runtime_config.get("base_url") or "")
    if manifest.source_type in {"local_path", "subprocess"}:
        return "free_local"
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        return "free_local"
    return "remote_unknown"


def rank_providers(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> list[ModelProviderManifest]:
    ranked: list[tuple[float, ModelProviderManifest]] = []
    required = required_capabilities(request.task_kind, request.output_mode)
    for manifest in manifests:
        if not manifest.enabled:
            continue
        if manifest.provider_id in set(request.exclude_provider_ids):
            continue
        if request.forbid_bundled_weights and manifest.weights_are_bundled:
            continue
        if request.require_license_metadata and (
            not str(manifest.license_name or "").strip() or not str(manifest.resolved_license_reference or "").strip()
        ):
            continue
        if request.preferred_provider and manifest.provider_name != request.preferred_provider:
            continue
        if request.preferred_model and manifest.model_name != request.preferred_model:
            continue
        cost_class = provider_cost_class(manifest)
        if policy_engine.local_only_mode() and cost_class != "free_local":
            continue
        if cost_class == "paid_cloud" and not request.allow_paid_fallback:
            continue

        score = capability_score(manifest, task_kind=request.task_kind, output_mode=request.output_mode)
        if required and score <= 0.0:
            continue

        trust = provider_base_trust(manifest)
        if trust < request.min_trust:
            continue
        score += 0.55 * trust

        if request.preferred_source_types and manifest.source_type in set(request.preferred_source_types):
            score += 0.22
        if cost_class == "free_local":
            score += 0.24
        elif cost_class == "remote_unknown":
            score -= 0.05
        elif cost_class == "paid_cloud":
            score -= 0.12
        score += _lane_fit_score(manifest, request)
        if circuit_is_open(manifest.provider_id):
            score -= 10.0

        ranked.append((score, manifest))

    ranked.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    return [item[1] for item in ranked]


def select_provider(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> ModelProviderManifest | None:
    ranked = rank_providers(manifests, request)
    return ranked[0] if ranked else None


def _lane_fit_score(manifest: ModelProviderManifest, request: ModelSelectionRequest) -> float:
    metadata = dict(manifest.metadata or {})
    bundle_role = str(metadata.get("bundle_role") or "").strip().lower()
    orchestration_role = str(metadata.get("orchestration_role") or "").strip().lower()
    task_kind = str(request.task_kind or "").strip().lower()
    output_mode = str(request.output_mode or "").strip().lower()
    parameter_b = model_parameter_billions(manifest.model_name)

    if task_kind in {"classification", "tool_intent", "format", "extract", "tag"} or output_mode == "tool_intent":
        if bundle_role == "lightweight_utility":
            return 0.75
        if bundle_role in {"reasoning", "heavy_reasoning"} or orchestration_role == "queen":
            return -0.45
        if parameter_b >= 13.0:
            return -0.25
        return 0.12

    if task_kind in {"reasoning", "agent_planning"}:
        if bundle_role in {"reasoning", "heavy_reasoning"}:
            return 0.6
        if bundle_role == "coding":
            return 0.3
        if bundle_role == "lightweight_utility":
            return -1.2
        return 0.0

    if task_kind == "normalization_assist" and output_mode == "plain_text":
        if bundle_role == "general":
            return 0.35
        if bundle_role == "lightweight_utility":
            return -0.4
        if bundle_role == "heavy_reasoning":
            return -0.55
        if bundle_role == "reasoning":
            return -0.2
        return 0.0

    if task_kind in {"action_plan", "coding_help_complex"} or output_mode == "action_plan":
        if bundle_role == "coding":
            return 0.5
        if bundle_role == "reasoning":
            return 0.45
        if bundle_role == "heavy_reasoning":
            return 0.25
        if bundle_role == "lightweight_utility":
            return -1.25
        if parameter_b >= 13.0:
            return 0.15
        return 0.0

    if task_kind in {"summarization", "candidate_shard_generation"}:
        if bundle_role in {"reasoning", "coding"}:
            return 0.25
        if bundle_role == "heavy_reasoning":
            return 0.1
        if bundle_role == "lightweight_utility":
            return -0.65
    return 0.0
