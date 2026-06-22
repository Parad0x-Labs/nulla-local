from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from core import policy_engine
from core.model_health import get_provider_health
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from storage.model_provider_manifest import ModelProviderManifest

if TYPE_CHECKING:
    from core.orchestration.task_envelope import TaskEnvelopeV1

ProviderRole = Literal["auto", "drone", "queen"]


@dataclass(frozen=True)
class ProviderCapabilityTruth:
    provider_id: str
    model_id: str
    role_fit: str
    context_window: int
    tool_support: tuple[str, ...]
    structured_output_support: bool
    tokens_per_second: float
    ram_budget_gb: float
    vram_budget_gb: float
    quantization: str
    locality: str
    privacy_class: str
    queue_depth: int
    max_safe_concurrency: int
    availability_state: str = "ready"
    circuit_open: bool = False
    last_error: str | None = None
    measurement_source: str = "manifest"
    measured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "nulla.provider_capability.v1",
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "role_fit": self.role_fit,
            "context_window": self.context_window,
            "tool_support": list(self.tool_support),
            "structured_output_support": self.structured_output_support,
            "tokens_per_second": self.tokens_per_second,
            "ram_budget_gb": self.ram_budget_gb,
            "vram_budget_gb": self.vram_budget_gb,
            "quantization": self.quantization,
            "locality": self.locality,
            "privacy_class": self.privacy_class,
            "queue_depth": self.queue_depth,
            "max_safe_concurrency": self.max_safe_concurrency,
            "availability_state": self.availability_state,
            "circuit_open": self.circuit_open,
            "last_error": self.last_error,
            "measurement_source": self.measurement_source,
            "measured_at": self.measured_at,
        }


@dataclass(frozen=True)
class ProviderRoutingPlan:
    role: ProviderRole
    task_kind: str
    output_mode: str
    allow_paid_fallback: bool
    swarm_size: int
    preferred_provider: str | None
    preferred_model: str | None
    selected: ModelProviderManifest | None
    candidates: tuple[ModelProviderManifest, ...]
    capability_truth: tuple[ProviderCapabilityTruth, ...] = field(default_factory=tuple)
    task_envelope: dict[str, Any] = field(default_factory=dict)
    routing_requirements: dict[str, Any] = field(default_factory=dict)
    rejected_candidates: tuple[dict[str, str], ...] = field(default_factory=tuple)
    selection_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def candidate_provider_ids(self) -> tuple[str, ...]:
        return tuple(manifest.provider_id for manifest in self.candidates)


def rank_provider_candidates(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
) -> list[ModelProviderManifest]:
    return _rank_provider_candidates_internal(
        registry,
        task_kind=task_kind,
        output_mode=output_mode,
        role=role,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        allow_paid_fallback=allow_paid_fallback,
        swarm_size=swarm_size,
        min_trust=min_trust,
        limit_to_swarm_size=True,
    )


def _rank_provider_candidates_internal(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
    limit_to_swarm_size: bool,
) -> list[ModelProviderManifest]:
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    base_ranked = registry.rank_manifests(
        ModelSelectionRequest(
            task_kind=task_kind,
            output_mode=output_mode,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            preferred_source_types=["http", "local_path", "subprocess"],
            allow_paid_fallback=resolved_allow_paid,
            min_trust=min_trust,
        )
    )
    if normalized_role == "auto":
        return base_ranked[:resolved_swarm_size] if limit_to_swarm_size else base_ranked

    total = max(len(base_ranked), 1)
    rescored: list[tuple[float, ModelProviderManifest]] = []
    for index, manifest in enumerate(base_ranked):
        base_score = float(total - index)
        role_bonus = _role_bonus(manifest, normalized_role)
        rescored.append((base_score + role_bonus, manifest))
    rescored.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    ranked = [manifest for _, manifest in rescored]
    return ranked[:resolved_swarm_size] if limit_to_swarm_size else ranked


def resolve_provider_routing_plan(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
    task_envelope: dict[str, Any] | None = None,
) -> ProviderRoutingPlan:
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    ranked_candidates = tuple(
        rank_provider_candidates(
            registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=normalized_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=resolved_swarm_size,
            min_trust=min_trust,
        )
    )
    total = max(len(ranked_candidates), 1)
    accepted: list[tuple[float, ModelProviderManifest, ProviderCapabilityTruth]] = []
    rejected: list[dict[str, str]] = []
    selection_notes: list[str] = []
    for index, manifest in enumerate(ranked_candidates):
        capability = provider_capability_truth_for_manifest(manifest)
        if capability.availability_state == "blocked":
            rejected.append({"provider_id": manifest.provider_id, "reason": "provider_circuit_open"})
            continue
        score = float(max(1, total - index))
        if capability.availability_state == "degraded":
            score -= 0.8
        accepted.append((score, manifest, capability))
    accepted.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    candidates = tuple(item[1] for item in accepted)
    capability_truth = tuple(item[2] for item in accepted)
    if rejected:
        selection_notes.append("Provider routing skipped one or more circuit-open lanes.")
    if any(item.availability_state == "degraded" for item in capability_truth):
        selection_notes.append("Provider routing penalized degraded lanes with recent failures.")
    return ProviderRoutingPlan(
        role=normalized_role,
        task_kind=task_kind,
        output_mode=output_mode,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        selected=candidates[0] if candidates else None,
        candidates=candidates,
        capability_truth=capability_truth,
        task_envelope=dict(task_envelope or {}),
        rejected_candidates=tuple(rejected),
        selection_notes=tuple(selection_notes),
    )


def resolve_provider_routing_plan_for_envelope(
    registry: ModelRegistry,
    *,
    envelope: TaskEnvelopeV1,
    task_kind: str,
    output_mode: str,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int | None = None,
    min_trust: float = 0.0,
) -> ProviderRoutingPlan:
    from core.orchestration import provider_role_for_task_role

    resolved_role = provider_role_for_task_role(envelope.role)
    explicit_swarm_size = int(swarm_size or envelope.model_constraints.get("swarm_size") or 0)
    resolved_swarm_size = _resolve_swarm_size(_normalize_role(resolved_role), explicit_swarm_size)
    resolved_allow_paid = _resolve_allow_paid_fallback(
        _normalize_role(resolved_role),
        allow_paid_fallback if allow_paid_fallback is not None else envelope.model_constraints.get("allow_paid_fallback"),
    )
    ranked = _rank_provider_candidates_internal(
        registry,
        task_kind=task_kind,
        output_mode=output_mode,
        role=resolved_role,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        min_trust=min_trust,
        limit_to_swarm_size=False,
    )
    requirements = _build_envelope_routing_requirements(envelope, output_mode=output_mode, provider_role=resolved_role)
    accepted: list[tuple[float, ModelProviderManifest, ProviderCapabilityTruth]] = []
    rejected: list[dict[str, str]] = []
    selection_notes = list(requirements["notes"])
    total = max(len(ranked), 1)
    for index, manifest in enumerate(ranked):
        capability = provider_capability_truth_for_manifest(manifest)
        rejection_reason = _routing_rejection_reason(capability, requirements)
        if rejection_reason:
            rejected.append({"provider_id": manifest.provider_id, "reason": rejection_reason})
            continue
        accepted.append(
            (
                _envelope_manifest_score(
                    capability,
                    requirements=requirements,
                    provider_role=resolved_role,
                    rank_index=index,
                    total_candidates=total,
                ),
                manifest,
                capability,
            )
        )
    accepted.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    selected_rows = accepted[:resolved_swarm_size]
    candidates = tuple(item[1] for item in selected_rows)
    capability_truth = tuple(item[2] for item in selected_rows)
    if not candidates and requirements["fail_closed"]:
        selection_notes.append("No providers satisfied the envelope's hard locality requirements; routing failed closed.")
    elif any(item[2].queue_depth > 0 for item in selected_rows):
        selection_notes.append("Queue-depth pressure was applied while scoring provider candidates.")
    return ProviderRoutingPlan(
        role=resolved_role,
        task_kind=task_kind,
        output_mode=output_mode,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        selected=candidates[0] if candidates else None,
        candidates=candidates,
        capability_truth=capability_truth,
        task_envelope=envelope.to_dict(),
        routing_requirements={key: value for key, value in requirements.items() if key != "notes"},
        rejected_candidates=tuple(rejected),
        selection_notes=tuple(selection_notes),
    )


def _build_envelope_routing_requirements(
    envelope: TaskEnvelopeV1,
    *,
    output_mode: str,
    provider_role: str,
) -> dict[str, Any]:
    constraints = dict(envelope.model_constraints or {})
    tool_permissions = {str(item).strip() for item in envelope.tool_permissions if str(item).strip()}
    allowed_side_effects = {str(item).strip() for item in envelope.allowed_side_effects if str(item).strip()}
    privacy_class = str(envelope.privacy_class or "").strip().lower()
    inferred_requires_local = privacy_class in {"local_private"} or bool(
        allowed_side_effects & {"workspace_write", "memory_write"}
    )
    required_locality = str(constraints.get("required_locality") or "").strip()
    requires_local = inferred_requires_local or required_locality == "local"
    preferred_tool_support: list[str] = []
    preferred_tool_support.extend(str(item).strip() for item in list(constraints.get("preferred_tool_support") or []) if str(item).strip())
    if any(permission.startswith("web.") for permission in tool_permissions) and "web_search" not in preferred_tool_support:
        preferred_tool_support.append("web_search")
    notes: list[str] = []
    if requires_local:
        notes.append("Envelope requires a local provider because the task is private or has mutating side effects.")
    if constraints.get("queue_pressure_strategy") == "fail_closed":
        notes.append("Queue-pressure strategy is fail-closed for this envelope.")
    return {
        "required_locality": required_locality or ("local" if requires_local else None),
        "preferred_locality": str(constraints.get("preferred_locality") or "").strip() or ("local" if envelope.role in {"coder", "verifier", "memory_clerk"} else None),
        "preferred_role_fit": str(constraints.get("preferred_provider_role") or "").strip() or (provider_role if provider_role != "auto" else ""),
        "preferred_tool_support": tuple(dict.fromkeys(preferred_tool_support)),
        "prefer_structured_output": bool(constraints.get("prefer_structured_output", output_mode != "plain_text" or envelope.role in {"coder", "verifier", "queen", "narrator"})),
        "prefer_long_context": bool(constraints.get("prefer_long_context", envelope.role in {"queen", "researcher"})),
        "prefer_code_complex": bool(constraints.get("prefer_code_complex", envelope.role == "coder")),
        "fail_closed": requires_local,
        "notes": tuple(notes),
    }


def _routing_rejection_reason(capability: ProviderCapabilityTruth, requirements: dict[str, Any]) -> str:
    if capability.availability_state == "blocked":
        return "provider_circuit_open"
    required_locality = str(requirements.get("required_locality") or "").strip()
    if required_locality and capability.locality != required_locality:
        return "requires_local_provider"
    return ""


@lru_cache(maxsize=1)
def _device_probe():
    """Probe the host once per process; hardware is stable for a run.

    Returns None if probing fails for any reason, so scoring degrades to its
    prior behavior rather than erroring.
    """
    try:
        from core.hardware_tier import probe_machine

        return probe_machine()
    except Exception:
        return None


def _hardware_fit_adjustment(
    capability: ProviderCapabilityTruth,
    *,
    ram_gb: float,
    vram_gb: float,
    accelerator: str,
) -> float:
    """Score delta for whether a manifest's declared footprint fits this machine.

    Penalizes a model that will not fit the box the user actually has (the core
    commodity-hardware concern); mildly prefers one that leaves headroom. INERT
    when the manifest declares no footprint — we never penalize on missing data,
    so manifests without ram/vram budgets keep their prior score exactly.

    Unified memory (Apple mps) has no separate VRAM pool: weights live in the
    SAME RAM the OS uses, and the probe reports vram_gb == ram_gb. Gauging the
    requirement against a phantom VRAM budget would double-count and over-admit,
    so on mps the footprint is measured against total RAM.
    """
    need_ram = float(capability.ram_budget_gb or 0.0)
    need_vram = float(capability.vram_budget_gb or 0.0)
    if need_ram <= 0.0 and need_vram <= 0.0:
        return 0.0
    accel = (accelerator or "cpu").strip().lower()
    if accel == "mps":
        need, budget = max(need_ram, need_vram), ram_gb
    elif accel in {"cuda", "directml"} and need_vram > 0.0 and vram_gb > 0.0:
        need, budget = need_vram, vram_gb
    else:
        need, budget = max(need_ram, need_vram), ram_gb
    if need <= 0.0 or budget <= 0.0:
        return 0.0
    if need > budget:
        # Over capacity — strong, overage-scaled penalty that sinks the candidate
        # below any model that actually fits. Base scores differ by O(1), so this
        # is decisive without being a hard reject (a no-fit box can still pick the
        # least-bad option if nothing fits).
        return -3.0 - min(5.0, (need - budget) / budget)
    # Fits — small headroom bonus (<= +0.6) that nudges among fitting models
    # without overriding role/locality intent.
    return min(0.6, (budget - need) / budget * 0.6)


def _envelope_manifest_score(
    capability: ProviderCapabilityTruth,
    *,
    requirements: dict[str, Any],
    provider_role: str,
    rank_index: int,
    total_candidates: int,
) -> float:
    score = float(max(1, total_candidates - rank_index))
    cap_tools = {str(item).strip().lower() for item in capability.tool_support if str(item).strip()}
    preferred_role_fit = str(requirements.get("preferred_role_fit") or "").strip()
    if preferred_role_fit and capability.role_fit == preferred_role_fit:
        score += 1.4
    elif preferred_role_fit and capability.role_fit not in {"", "auto", preferred_role_fit}:
        score -= 0.6
    preferred_locality = str(requirements.get("preferred_locality") or "").strip()
    if preferred_locality and capability.locality == preferred_locality:
        score += 1.0
    elif preferred_locality and capability.locality != preferred_locality:
        score -= 0.35
    if bool(requirements.get("prefer_structured_output", False)):
        score += 0.45 if capability.structured_output_support else -0.2
    if bool(requirements.get("prefer_long_context", False)) and capability.context_window > 0:
        score += min(0.8, float(capability.context_window) / 64000.0)
    if bool(requirements.get("prefer_code_complex", False)):
        if "code_complex" in cap_tools:
            score += 0.55
        elif capability.locality == "local":
            score += 0.15
    for preferred_tool in tuple(requirements.get("preferred_tool_support") or ()):
        if preferred_tool in cap_tools:
            score += 0.4
        else:
            score -= 0.15
    if capability.availability_state == "degraded":
        score -= 0.75
    queue_pressure = float(capability.queue_depth) / float(max(1, capability.max_safe_concurrency))
    score -= min(2.5, queue_pressure)
    if provider_role == "queen" and capability.locality == "remote":
        score += 0.15
    # Hardware-fit: prefer a model that fits the user's actual box, sink one that
    # won't. Inert for manifests that declare no ram/vram footprint.
    probe = _device_probe()
    if probe is not None:
        score += _hardware_fit_adjustment(
            capability,
            ram_gb=float(probe.ram_gb or 0.0),
            vram_gb=float(probe.vram_gb or 0.0),
            accelerator=str(probe.accelerator or "cpu"),
        )
    return score


def _normalize_role(role: str) -> ProviderRole:
    clean = str(role or "auto").strip().lower()
    if clean in {"drone", "queen"}:
        return clean
    return "auto"


def _resolve_allow_paid_fallback(role: ProviderRole, explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    if role == "drone":
        return False
    if role == "queen":
        return bool(policy_engine.get("model_orchestration.queen_allow_paid_fallback", True))
    return True


def _resolve_swarm_size(role: ProviderRole, requested: int) -> int:
    requested_value = max(1, int(requested or 1))
    if role == "drone":
        default_width = int(policy_engine.get("model_orchestration.drone_swarm_width", 2) or 2)
    elif role == "queen":
        default_width = int(policy_engine.get("model_orchestration.queen_swarm_width", 1) or 1)
    else:
        default_width = requested_value
    return max(1, min(4, requested_value if requested else default_width))


def _role_bonus(manifest: ModelProviderManifest, role: ProviderRole) -> float:
    if role == "auto":
        return 0.0
    provider_hint = str(policy_engine.get("model_orchestration.queen_provider_hint", "kimi") or "kimi").strip().lower()
    drone_hint = str(policy_engine.get("model_orchestration.drone_provider_hint", "qwen") or "qwen").strip().lower()
    orchestration_role = str((manifest.metadata or {}).get("orchestration_role") or "").strip().lower()
    deployment_class = str((manifest.metadata or {}).get("deployment_class") or "").strip().lower()
    text_blob = " ".join(
        [
            manifest.provider_name,
            manifest.model_name,
            str(manifest.notes or ""),
            str((manifest.metadata or {}).get("runtime_family") or ""),
        ]
    ).lower()
    capabilities = {str(item).strip().lower() for item in list(manifest.capabilities or [])}
    local_http = _is_local_http(manifest)
    is_local = manifest.source_type in {"local_path", "subprocess"} or local_http or deployment_class == "local"
    is_remote = not is_local or deployment_class == "cloud"

    if role == "drone":
        score = 0.0
        if orchestration_role == "drone":
            score += 1.5
        if is_local:
            score += 0.9
        if drone_hint and drone_hint in text_blob:
            score += 0.55
        if "structured_json" in capabilities:
            score += 0.12
        if orchestration_role == "queen":
            score -= 1.25
        if provider_hint and provider_hint in text_blob:
            score -= 0.45
        if is_remote:
            score -= 0.65
        return score

    score = 0.0
    if orchestration_role == "queen":
        score += 1.7
    if provider_hint and provider_hint in text_blob:
        score += 1.35
    if "long_context" in capabilities:
        score += 0.45
    if "code_complex" in capabilities:
        score += 0.18
    if is_remote:
        score += 0.32
    if is_local and drone_hint and drone_hint in text_blob:
        score += 0.16
    if orchestration_role == "drone":
        score -= 0.55
    return score


def _is_local_http(manifest: ModelProviderManifest) -> bool:
    base_url = str((manifest.runtime_config or {}).get("base_url") or "").strip().lower()
    if not base_url:
        return False
    try:
        hostname = str(urlparse(base_url).hostname or "").strip().lower()
    except Exception:
        hostname = ""
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback or ip_address(hostname).is_unspecified
    except ValueError:
        return False


def provider_capability_truth_for_manifest(manifest: ModelProviderManifest) -> ProviderCapabilityTruth:
    metadata = dict(manifest.metadata or {})
    runtime_config = dict(manifest.runtime_config or {})
    capabilities = {str(item).strip().lower() for item in list(manifest.capabilities or []) if str(item).strip()}
    health = get_provider_health(manifest.provider_id)
    availability_state = "blocked" if health.circuit_open else ("degraded" if health.consecutive_failures > 0 else "ready")
    orchestration_role = str(metadata.get("orchestration_role") or "").strip().lower()
    deployment_class = str(metadata.get("deployment_class") or "").strip().lower()
    locality = "local" if manifest.source_type in {"local_path", "subprocess"} or _is_local_http(manifest) or deployment_class == "local" else "remote"
    privacy_class = "local_private" if locality == "local" else "remote_provider"
    role_fit = orchestration_role or ("drone" if locality == "local" else "queen")
    tool_support = tuple(
        str(item).strip()
        for item in list(metadata.get("tool_support") or [])
        if str(item).strip()
    ) or tuple(sorted(cap for cap in capabilities if cap in {"tool_calls", "structured_json", "web_search", "code_complex"}))
    return ProviderCapabilityTruth(
        provider_id=manifest.provider_id,
        model_id=manifest.model_name,
        role_fit=role_fit,
        context_window=max(0, int(metadata.get("context_window") or runtime_config.get("context_window") or 0)),
        tool_support=tool_support,
        structured_output_support="structured_json" in capabilities,
        tokens_per_second=float(metadata.get("tokens_per_second") or metadata.get("tps") or 0.0),
        ram_budget_gb=float(metadata.get("ram_budget_gb") or metadata.get("ram_gb") or 0.0),
        vram_budget_gb=float(metadata.get("vram_budget_gb") or metadata.get("vram_gb") or 0.0),
        quantization=str(metadata.get("quantization") or runtime_config.get("quantization") or "").strip(),
        locality=locality,
        privacy_class=privacy_class,
        queue_depth=max(0, int(metadata.get("queue_depth") or 0)),
        max_safe_concurrency=max(1, int(metadata.get("max_safe_concurrency") or 1)),
        availability_state=availability_state,
        circuit_open=health.circuit_open,
        last_error=health.last_error,
    )


__all__ = [
    "ProviderCapabilityTruth",
    "ProviderRole",
    "ProviderRoutingPlan",
    "provider_capability_truth_for_manifest",
    "rank_provider_candidates",
    "resolve_provider_routing_plan",
    "resolve_provider_routing_plan_for_envelope",
]
