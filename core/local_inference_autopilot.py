from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Literal

from core.hardware_tier import MachineProbe, probe_machine
from core.local_model_bundles import (
    model_active_parameter_billions,
    model_metadata,
    model_parameter_billions,
)
from core.provider_routing import ProviderCapabilityTruth, ProviderRole

AutopilotLane = Literal["tiny", "daily", "deep", "cloud", "human"]
AutopilotPhaseStatus = Literal["planned", "blocked"]
AutopilotFramework = Literal["ollama_mlx", "ollama_metal", "llama_cpp", "exllamav2", "unknown"]
RuntimeFlagValue = str | int | float | bool

_SECRET_RE = re.compile(
    r"(?i)\b("
    r"sk-[a-z0-9_-]{20,}|"
    r"sk-proj-[a-z0-9_-]{20,}|"
    r"AIza[a-z0-9_-]{20,}|"
    r"gh[pousr]_[a-z0-9_]{20,}|"
    r"xox[baprs]-[a-z0-9-]{20,}"
    r")\b"
)
_LONG_TOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{48,}\b)(?=.*[A-Z])(?=.*[a-z])(?=.*\d)[A-Za-z0-9_-]+\b")
_MAC_PATH_RE = re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp)/[^\s'\"`<>)]{3,}")
_WIN_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s'\"`<>)]{3,}")
_HOME_PATH_RE = re.compile(r"(?<![\w.-])~/(?:[^\s'\"`<>)]{2,})")

_RISK_TERMS = {
    # explicit high-risk markers
    "api key",
    "credential",
    "secret",
    "high-risk",
    "high risk",
    "verifier required",
    "verification required",
    # destructive / irreversible ops
    "delete",
    "overwrite",
    "rm -rf",
    # engineering mutations that need review
    "failure mode",
    "patch",
    "refactor",
    "edit file",
    "write file",
    "production",
}

_EAGLE3_DRAFT_MAP = {
    "qwen3:8b": "AngelSlim/Qwen3-8B_eagle3",
    "qwen3:14b": "AngelSlim/Qwen3-14B_eagle3",
    "phi4:14b": "",
    "llama3.3:70b": "AngelSlim/Llama-3.3-70B-Instruct_eagle3",
}
_EAGLE3_SPEEDUP_RANGE = (1.5, 2.5)
_ENTROPY_ESCALATION_THRESHOLD = 0.35
_SUFFIX_DECODE_TASK_KINDS = frozenset(
    {
        "tool_intent",
        "tool_loop",
        "agentic_loop",
        "classification",
        "candidate_shard_generation",
        # trivial-pattern tasks — tiny models, suffix decode eligible
        "format",
        "extract",
        "tag",
    }
)

# Task kinds that route directly to the tiny lane regardless of text content
_TINY_TASK_KINDS = frozenset(
    {
        "classification",
        "tool_intent",
        "format",   # pure text reformatting; tiny model sufficient
        "extract",  # structured field pull; tiny model sufficient
        "tag",      # synonym for classify
    }
)

# Task kinds that always need deep lane (verifier path)
_DEEP_TASK_KINDS = frozenset(
    {
        "action_plan",
        "coding_help_complex",
        "reasoning",      # explicit multi-step reasoning
        "agent_planning", # multi-step plan building
    }
)


@dataclass(frozen=True)
class AutopilotPhase:
    name: str
    summary: str
    status: AutopilotPhaseStatus = "planned"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "summary": self.summary}


@dataclass(frozen=True)
class ContextCapsule:
    schema: str
    stable_prefix_hash: str
    task_summary: str
    compressed_prompt: str
    constraints: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    raw_chars: int = 0
    compressed_chars: int = 0
    omitted_private_items: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "stable_prefix_hash": self.stable_prefix_hash,
            "task_summary": self.task_summary,
            "compressed_prompt": self.compressed_prompt,
            "constraints": list(self.constraints),
            "evidence_refs": list(self.evidence_refs),
            "raw_chars": self.raw_chars,
            "compressed_chars": self.compressed_chars,
            "omitted_private_items": self.omitted_private_items,
        }


@dataclass(frozen=True)
class ResidencyAction:
    provider_id: str
    model_id: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PrefixCachePlan:
    stable_prefix_hash: str
    backend: str
    action: str
    supported: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_prefix_hash": self.stable_prefix_hash,
            "backend": self.backend,
            "action": self.action,
            "supported": self.supported,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LocalInferenceAutopilotPlan:
    schema: str
    lane: AutopilotLane
    provider_role: ProviderRole
    task_kind: str
    output_mode: str
    selected_provider_id: str | None
    selected_model: str | None
    framework: AutopilotFramework
    runtime_flags: dict[str, RuntimeFlagValue]
    verifier_required: bool
    verifier_provider_id: str | None
    verifier_model: str | None
    entropy_escalation_threshold: float
    suffix_decode_eligible: bool
    context: ContextCapsule
    phases: tuple[AutopilotPhase, ...]
    residency: tuple[ResidencyAction, ...]
    prefix_cache: PrefixCachePlan
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "lane": self.lane,
            "provider_role": self.provider_role,
            "task_kind": self.task_kind,
            "output_mode": self.output_mode,
            "selected_provider_id": self.selected_provider_id,
            "selected_model": self.selected_model,
            "framework": self.framework,
            "runtime_flags": dict(self.runtime_flags),
            "verifier_required": self.verifier_required,
            "verifier_provider_id": self.verifier_provider_id,
            "verifier_model": self.verifier_model,
            "entropy_escalation_threshold": self.entropy_escalation_threshold,
            "suffix_decode_eligible": self.suffix_decode_eligible,
            "context": self.context.to_dict(),
            "phases": [phase.to_dict() for phase in self.phases],
            "residency": [action.to_dict() for action in self.residency],
            "prefix_cache": self.prefix_cache.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "warnings": list(self.warnings),
        }


def build_local_inference_autopilot_plan(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    provider_role: ProviderRole,
    capability_truth: tuple[ProviderCapabilityTruth, ...] | list[ProviderCapabilityTruth],
    source_context: dict[str, Any] | None = None,
    machine_probe: MachineProbe | None = None,
    eagle3_active: bool = False,
) -> LocalInferenceAutopilotPlan:
    context = compile_context_capsule(user_text=user_text, source_context=source_context)
    capabilities = tuple(capability_truth or ())
    lane = _resolve_lane(
        user_text=user_text,
        task_kind=task_kind,
        output_mode=output_mode,
        source_context=source_context,
        local_available=any(item.locality == "local" and item.availability_state != "blocked" for item in capabilities),
    )
    explicit_heavy = _explicit_heavy_requested(user_text=user_text, source_context=source_context)
    requested_heavy_marker = _requested_heavy_marker(user_text=user_text, source_context=source_context)
    selected = _select_primary_capability(
        capabilities,
        lane=lane,
        provider_role=provider_role,
        explicit_heavy=explicit_heavy,
        requested_heavy_marker=requested_heavy_marker,
        eagle3_active=eagle3_active,
    )
    risky = _needs_verifier(user_text=user_text, task_kind=task_kind, output_mode=output_mode, source_context=source_context)
    verifier = _select_verifier_capability(capabilities, selected=selected, required=risky)
    warnings = _build_warnings(capabilities=capabilities, selected=selected, explicit_heavy=explicit_heavy)
    evidence_refs = _evidence_refs(capabilities)
    residency = _build_residency(
        capabilities=capabilities,
        selected=selected,
        verifier=verifier,
        explicit_heavy=explicit_heavy,
    )
    framework, runtime_flags = _select_framework_and_flags(
        selected=selected,
        machine_probe=machine_probe,
        eagle3_active=eagle3_active,
    )
    prefix_cache = _build_prefix_cache_plan(context=context, selected=selected)
    suffix_decode_eligible = _suffix_decode_eligible(task_kind)
    phases = _build_phases(
        lane=lane,
        selected=selected,
        verifier_required=risky,
        verifier=verifier,
        task_kind=task_kind,
        output_mode=output_mode,
        framework=framework,
        runtime_flags=runtime_flags,
        suffix_decode_eligible=suffix_decode_eligible,
        machine_probe=machine_probe,
    )

    return LocalInferenceAutopilotPlan(
        schema="nulla.local_inference_autopilot.v1",
        lane=lane,
        provider_role=provider_role,
        task_kind=str(task_kind or "unknown"),
        output_mode=str(output_mode or "plain_text"),
        selected_provider_id=selected.provider_id if selected else None,
        selected_model=selected.model_id if selected else None,
        framework=framework,
        runtime_flags=runtime_flags,
        verifier_required=risky,
        verifier_provider_id=verifier.provider_id if verifier else None,
        verifier_model=verifier.model_id if verifier else None,
        entropy_escalation_threshold=_ENTROPY_ESCALATION_THRESHOLD,
        suffix_decode_eligible=suffix_decode_eligible,
        context=context,
        phases=phases,
        residency=residency,
        prefix_cache=prefix_cache,
        evidence_refs=evidence_refs,
        warnings=warnings,
    )


def compile_context_capsule(
    *,
    user_text: str,
    source_context: dict[str, Any] | None = None,
    max_chars: int = 2400,
) -> ContextCapsule:
    context = dict(source_context or {})
    raw_parts: list[str] = [str(user_text or "")]
    stable_parts: list[str] = ["nulla.local_inference_autopilot.v1"]
    constraints: list[str] = []
    evidence_refs: list[str] = []
    omitted_private = 0

    for key in ("repo_identity", "repo_map", "memory_capsule", "rules_summary", "tool_schema_hashes"):
        value = _context_value(context.get(key))
        if value:
            stable_parts.append(f"{key}:{value}")

    compressed_lines = [f"Task: {_clip(_sanitize_text(user_text)[0], 600)}"]
    for key, label, limit in (
        ("memory_capsule", "Memory", 520),
        ("repo_map", "Repo", 420),
        ("diff_summary", "Diff", 420),
        ("failing_tests", "Failing tests", 360),
        ("constraints", "Constraints", 360),
    ):
        value = _context_value(context.get(key))
        if not value:
            continue
        clean, redacted = _sanitize_text(value)
        omitted_private += redacted
        raw_parts.append(value)
        clipped = _clip(clean, limit)
        if key == "constraints":
            constraints.extend(_split_constraints(clipped))
        compressed_lines.append(f"{label}: {clipped}")

    for item in _as_tuple(context.get("evidence_refs")):
        clean, redacted = _sanitize_text(item)
        omitted_private += redacted
        if clean:
            evidence_refs.append(_clip(clean, 180))

    stable_text = "\n".join(stable_parts)
    prompt = "\n".join(line for line in compressed_lines if line.strip())
    if len(prompt) > max_chars:
        prompt = prompt[: max(0, max_chars - 1)].rstrip() + "…"
    clean_task, task_redacted = _sanitize_text(user_text)
    omitted_private += task_redacted

    return ContextCapsule(
        schema="nulla.context_capsule.v1",
        stable_prefix_hash=hashlib.sha256(stable_text.encode("utf-8")).hexdigest()[:16],
        task_summary=_clip(clean_task, 220),
        compressed_prompt=prompt,
        constraints=tuple(dict.fromkeys(constraints)),
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        raw_chars=sum(len(part) for part in raw_parts),
        compressed_chars=len(prompt),
        omitted_private_items=omitted_private,
    )


def build_prefix_cache_plan(
    *,
    stable_prefix_hash: str,
    backend: str,
) -> PrefixCachePlan:
    clean_backend = str(backend or "").strip().lower()
    if clean_backend in {"llama.cpp", "llamacpp", "llama-cpp"}:
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="llama.cpp",
            action="slot_save_restore",
            supported=True,
            reason="llama.cpp can reuse stable prefixes through server slot cache save/restore.",
        )
    if clean_backend in {"mlx", "mlx-lm", "mlx_lm"}:
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="mlx-lm",
            action="cache_prompt",
            supported=True,
            reason="MLX-LM exposes prompt-cache primitives for stable prefixes.",
        )
    if clean_backend == "ollama":
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="ollama",
            action="preload_keep_alive",
            supported=False,
            reason="Ollama can keep a model resident, but does not expose portable prefix-cache handles.",
        )
    return PrefixCachePlan(
        stable_prefix_hash=stable_prefix_hash,
        backend=clean_backend or "unknown",
        action="none",
        supported=False,
        reason="No backend-specific prefix-cache hook is configured for this lane.",
    )


def _resolve_lane(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    source_context: dict[str, Any] | None,
    local_available: bool,
) -> AutopilotLane:
    if not local_available and bool((source_context or {}).get("allow_cloud")):
        return "cloud"
    if not local_available:
        return "human"
    clean_task_kind = str(task_kind or "").strip().lower()
    clean_output = str(output_mode or "").strip().lower()
    if clean_task_kind in _TINY_TASK_KINDS or clean_output == "tool_intent":
        return "tiny"
    if _needs_verifier(
        user_text=user_text,
        task_kind=task_kind,
        output_mode=output_mode,
        source_context=source_context,
    ):
        return "deep"
    return "daily"


def _needs_verifier(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    source_context: dict[str, Any] | None,
) -> bool:
    clean_task_kind = str(task_kind or "").strip().lower()
    clean_output = str(output_mode or "").strip().lower()
    if clean_task_kind in _DEEP_TASK_KINDS or clean_output == "action_plan":
        return True
    if bool((source_context or {}).get("requires_verifier")):
        return True
    lowered = str(user_text or "").lower()
    return any(term in lowered for term in _RISK_TERMS)


def _select_primary_capability(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    lane: AutopilotLane,
    provider_role: ProviderRole,
    explicit_heavy: bool,
    requested_heavy_marker: str | None,
    eagle3_active: bool = False,
) -> ProviderCapabilityTruth | None:
    if explicit_heavy:
        viable = [
            item
            for item in capabilities
            if item.availability_state != "blocked"
            and model_parameter_billions(item.model_id) >= 24.0
            and not _is_fast_nothink_default(item.model_id)
        ]
        if requested_heavy_marker:
            viable = [item for item in viable if requested_heavy_marker in str(item.model_id or "").strip().lower()]
    else:
        viable = [
            item
            for item in capabilities
            if item.availability_state != "blocked"
            and (model_parameter_billions(item.model_id) < 24.0 or _is_fast_nothink_default(item.model_id))
        ]
    if not viable:
        return None
    ranked = sorted(
        viable,
        key=lambda item: (
            _primary_score(item, lane=lane, provider_role=provider_role, explicit_heavy=explicit_heavy, eagle3_active=eagle3_active),
            item.provider_id,
        ),
        reverse=True,
    )
    return ranked[0]


def _select_verifier_capability(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    selected: ProviderCapabilityTruth | None,
    required: bool,
) -> ProviderCapabilityTruth | None:
    if not required:
        return None
    viable = [
        item
        for item in capabilities
        if item.availability_state != "blocked"
        and (model_parameter_billions(item.model_id) < 24.0 or _is_fast_nothink_default(item.model_id))
        and (
            selected is None
            or (
                item.provider_id != selected.provider_id
                and str(item.model_id or "").strip().lower() != str(selected.model_id or "").strip().lower()
            )
        )
    ]
    if not viable:
        return None
    ranked = sorted(
        viable,
        key=lambda item: (
            _verifier_score(item, selected=selected),
            item.provider_id,
        ),
        reverse=True,
    )
    return ranked[0]


def _primary_score(
    capability: ProviderCapabilityTruth,
    *,
    lane: AutopilotLane,
    provider_role: ProviderRole,
    explicit_heavy: bool,
    eagle3_active: bool = False,
) -> float:
    size_b = model_parameter_billions(capability.model_id)
    fast_nothink_default = _is_fast_nothink_default(capability.model_id)
    score = 0.0
    if capability.locality == "local":
        score += 2.0
    else:
        score += 0.4
    if capability.tokens_per_second > 0:
        # Cap at 50 tok/s (divisor 20) — better differentiates 20→40 tok/s range
        score += min(2.5, capability.tokens_per_second / 20.0)
    else:
        score -= 0.25
    if capability.availability_state == "degraded":
        score -= 1.0
    score -= min(2.0, capability.queue_depth / max(1, capability.max_safe_concurrency))
    if provider_role == "queen" and lane != "daily":
        score += 2.0 if capability.role_fit == "queen" else -0.5
    elif provider_role == "drone":
        score += 1.0 if capability.role_fit == "drone" else -1.0

    if lane == "tiny":
        if size_b <= 4.0:
            score += 3.0
        elif size_b <= 10.0:
            score += 0.6
        elif size_b >= 13.0:
            score -= 2.0
        if capability.role_fit == "drone":
            score += 0.8
    elif lane == "daily":
        if fast_nothink_default:
            score += 3.4
        elif 4.0 <= size_b <= 10.0:
            score += 2.6
        elif size_b < 4.0:
            score -= 0.8
        elif 13.0 <= size_b <= 16.0:
            score -= 0.6
        else:
            score -= 1.8
    elif lane == "deep":
        if fast_nothink_default:
            score += 3.2  # MoE nothink: preferred deep fallback when llama.cpp unavailable
        elif 13.0 <= size_b <= 16.0:
            score += 2.8
        elif 8.0 <= size_b < 13.0:
            score += 1.0
        elif 18.0 <= size_b < 24.0:
            score += 0.4
        elif size_b >= 24.0:
            score += 0.2 if explicit_heavy else -2.8
        if capability.role_fit == "queen":
            score += 0.8
        if "code_complex" in {item.lower() for item in capability.tool_support}:
            score += 0.35
        if _is_llamacpp_specialist(capability) and "code_complex" in {item.lower() for item in capability.tool_support}:
            score += 4.4
        if eagle3_active and _is_llamacpp_specialist(capability):
            score += 1.5  # EAGLE-3 confirmed running: 1.4-1.9x speedup bonus
    elif lane == "cloud":
        score += 1.5 if capability.locality == "remote" else 0.2
    elif lane == "human":
        score -= 4.0

    if size_b >= 30.0 and not explicit_heavy and not fast_nothink_default:
        score -= 3.0
    return score


def _verifier_score(capability: ProviderCapabilityTruth, *, selected: ProviderCapabilityTruth | None) -> float:
    size_b = model_parameter_billions(capability.model_id)
    score = 0.0
    if 13.0 <= size_b <= 16.0:
        score += 3.2
    elif 8.0 <= size_b < 13.0:
        score += 1.0
    elif size_b >= 24.0:
        score -= 1.6
    if capability.role_fit == "queen":
        score += 0.8
    if capability.locality == "local":
        score += 1.0
    if "code_complex" in {item.lower() for item in capability.tool_support}:
        score += 0.35
    if selected is not None and capability.provider_id == selected.provider_id:
        score -= 0.45
    if capability.tokens_per_second > 0:
        score += min(1.2, capability.tokens_per_second / 30.0)
    if selected is not None and _is_llamacpp_specialist(selected):
        if 8.0 <= size_b < 13.0:
            score += 2.4
        elif 13.0 <= size_b <= 16.0:
            score -= 1.0
    return score


def _is_llamacpp_specialist(capability: ProviderCapabilityTruth) -> bool:
    provider_id = str(capability.provider_id or "").strip().lower()
    return provider_id.startswith("llamacpp-local:")


def _build_residency(
    *,
    capabilities: tuple[ProviderCapabilityTruth, ...],
    selected: ProviderCapabilityTruth | None,
    verifier: ProviderCapabilityTruth | None,
    explicit_heavy: bool,
) -> tuple[ResidencyAction, ...]:
    actions: list[ResidencyAction] = []
    if selected is not None:
        size_b = model_parameter_billions(selected.model_id)
        if size_b <= 10.0:
            action = "keep_hot"
            reason = "daily lane stays resident to reduce first-token delay"
        elif size_b <= 16.0:
            action = "load_on_demand"
            reason = "deep lane is loaded only when needed"
        elif explicit_heavy:
            action = "load_explicit_only"
            reason = "oversized model was explicitly requested"
        else:
            action = "blocked_by_default"
            reason = "oversized local model is too slow for default UX"
        actions.append(ResidencyAction(selected.provider_id, selected.model_id, action, reason))

    if verifier is not None and (selected is None or verifier.provider_id != selected.provider_id):
        actions.append(
            ResidencyAction(
                verifier.provider_id,
                verifier.model_id,
                "load_for_verification",
                "verifier lane is not kept hot unless a risky output needs review",
            )
        )

    for capability in capabilities:
        size_b = model_parameter_billions(capability.model_id)
        if size_b >= 24.0 and (not explicit_heavy or capability.availability_state == "blocked"):
            reason = (
                "explicit heavy lane is blocked or unhealthy"
                if explicit_heavy and capability.availability_state == "blocked"
                else "24B+ lanes require explicit operator request or measured proof"
            )
            actions.append(
                ResidencyAction(
                    capability.provider_id,
                    capability.model_id,
                    "refuse_default",
                    reason,
                )
            )
    return tuple(_dedupe_residency(actions))


def _build_prefix_cache_plan(
    *,
    context: ContextCapsule,
    selected: ProviderCapabilityTruth | None,
) -> PrefixCachePlan:
    if selected is None:
        return build_prefix_cache_plan(stable_prefix_hash=context.stable_prefix_hash, backend="")
    provider_id = selected.provider_id.lower()
    if "llamacpp" in provider_id or "llama.cpp" in provider_id:
        backend = "llama.cpp"
    elif "mlx" in provider_id:
        backend = "mlx-lm"
    elif "ollama" in provider_id:
        backend = "ollama"
    else:
        backend = provider_id.partition(":")[0]
    return build_prefix_cache_plan(stable_prefix_hash=context.stable_prefix_hash, backend=backend)


def _select_framework_and_flags(
    *,
    selected: ProviderCapabilityTruth | None,
    machine_probe: MachineProbe | None,
    eagle3_active: bool = False,
) -> tuple[AutopilotFramework, dict[str, RuntimeFlagValue]]:
    if selected is None:
        return "unknown", {}

    probe = machine_probe or probe_machine()
    system = platform.system().lower()
    accelerator = str(probe.accelerator or "").strip().lower()
    ram_gb = float(probe.ram_gb or 0.0)
    vram_gb = float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0
    model_size_b = model_parameter_billions(selected.model_id)

    if system == "darwin" and accelerator == "mps":
        if ram_gb >= 32.0:
            framework: AutopilotFramework = "ollama_mlx"
            flags: dict[str, RuntimeFlagValue] = {"OLLAMA_MLX": "1", "num_gpu": 999}
        else:
            framework = "ollama_metal"
            flags = {"num_gpu": 999}
    elif accelerator == "cuda" and vram_gb >= 16.0 and model_size_b >= 60.0:
        framework = "exllamav2"
        flags = {
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
            "batch_size": 2048,
            "ubatch_size": 2048,
        }
    elif accelerator == "cuda" and vram_gb >= 16.0:
        framework = "llama_cpp"
        flags = {
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
            "batch_size": 2048,
            "ubatch_size": 2048,
        }
    else:
        framework = "llama_cpp"
        flags = {
            "flash_attn": True,
            "batch_size": 2048,
            "ubatch_size": 2048,
        }

    if _is_moe_model(selected.model_id):
        flags = {**flags, "ngl": 999, "fit_target": 2048}
    if eagle3_active and _is_llamacpp_specialist(selected):
        flags = {
            **flags,
            "speculative": "draft-eagle3",
            "spec_draft_n_max": 8,
            "spec_draft_p_min": 0.5,
        }
    return framework, flags


def _build_phases(
    *,
    lane: AutopilotLane,
    selected: ProviderCapabilityTruth | None,
    verifier_required: bool,
    verifier: ProviderCapabilityTruth | None,
    task_kind: str,
    output_mode: str,
    framework: AutopilotFramework,
    runtime_flags: dict[str, RuntimeFlagValue],
    suffix_decode_eligible: bool,
    machine_probe: MachineProbe | None,
) -> tuple[AutopilotPhase, ...]:
    selected_label = selected.provider_id if selected else "no provider"
    phases = [
        AutopilotPhase("route", f"Classify request into {lane} lane."),
        AutopilotPhase("retrieve", "Collect only relevant memory, repo, diff, and test facts."),
        AutopilotPhase("compress", "Build bounded context capsule instead of raw context dump."),
        AutopilotPhase("framework", _framework_summary(framework, runtime_flags), "planned" if selected else "blocked"),
        AutopilotPhase("preload", f"Prepare {selected_label}.", "planned" if selected else "blocked"),
        AutopilotPhase("generate", f"Generate with {selected_label}.", "planned" if selected else "blocked"),
    ]
    if selected is not None:
        sysctl_warning = _apple_wired_memory_phase(machine_probe=machine_probe)
        if sysctl_warning is not None:
            phases.append(sysctl_warning)
        if suffix_decode_eligible:
            phases.append(
                AutopilotPhase(
                    "suffix_decoding",
                    "Task is repetitive/agentic; prefer suffix-tree decoding over draft-model speculation when the backend supports it.",
                )
            )
        else:
            eagle_phase = _eagle3_phase(selected.model_id)
            if eagle_phase is not None:
                phases.append(eagle_phase)
    if verifier_required:
        verifier_label = verifier.provider_id if verifier else "no verifier"
        phases.append(
            AutopilotPhase(
                "verify",
                f"Review risky output with {verifier_label}.",
                "planned" if verifier else "blocked",
            )
        )
    if str(task_kind).lower() in {"action_plan", "coding_help_complex"} or str(output_mode).lower() == "action_plan":
        phases.extend(
            [
                AutopilotPhase("test", "Run focused proof for generated changes."),
                AutopilotPhase("repair", "Repair failures and re-run the current proof set."),
            ]
        )
    return tuple(phases)


def _framework_summary(framework: AutopilotFramework, runtime_flags: dict[str, RuntimeFlagValue]) -> str:
    if framework == "unknown":
        return "No inference framework can be selected without a provider lane."
    if not runtime_flags:
        return f"Use {framework} with default runtime flags."
    flag_summary = ", ".join(f"{key}={value}" for key, value in sorted(runtime_flags.items()))
    return f"Use {framework} with {flag_summary}."


def _apple_wired_memory_phase(*, machine_probe: MachineProbe | None) -> AutopilotPhase | None:
    probe = machine_probe or probe_machine()
    if platform.system().lower() != "darwin":
        return None
    if str(probe.accelerator or "").strip().lower() != "mps":
        return None
    current = _read_iogpu_wired_limit_mb()
    if current is None:
        return None
    recommended = int(float(probe.ram_gb or 0.0) * 1024.0 * 0.85)
    if recommended <= 0 or current >= recommended:
        return None
    return AutopilotPhase(
        name="sysctl_warning",
        summary=(
            f"iogpu.wired_limit_mb is low ({current}). "
            f"Recommend: sudo sysctl iogpu.wired_limit_mb={recommended}"
        ),
        status="blocked",
    )


def _read_iogpu_wired_limit_mb() -> int | None:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "iogpu.wired_limit_mb"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    raw = str(result.stdout or "").strip().splitlines()[0:1]
    if not raw:
        return None
    try:
        return int(float(raw[0].strip()))
    except ValueError:
        return None


def _eagle3_phase(model_id: str) -> AutopilotPhase | None:
    if _is_moe_model(model_id):
        return None
    draft_repo = _EAGLE3_DRAFT_MAP.get(str(model_id or "").strip().lower())
    if not draft_repo:
        return None
    return AutopilotPhase(
        name="eagle_candidate",
        summary=(
            f"EAGLE-3 draft available: {draft_repo}. "
            f"Expected {_EAGLE3_SPEEDUP_RANGE[0]}-{_EAGLE3_SPEEDUP_RANGE[1]}x speedup only after a backend proves a real EAGLE draft lane."
        ),
    )


def _suffix_decode_eligible(task_kind: str) -> bool:
    return str(task_kind or "").strip().lower() in _SUFFIX_DECODE_TASK_KINDS


def _is_moe_model(model_id: str) -> bool:
    clean = str(model_id or "").strip().lower()
    metadata = model_metadata(clean)
    architecture = str(metadata.get("architecture") or "").strip().lower()
    if architecture in {"moe", "hybrid_moe"}:
        return True
    if re.search(r"-a\d+(?:\.\d+)?b", clean):
        return True
    return model_active_parameter_billions(clean) != model_parameter_billions(clean)


def _is_fast_nothink_default(model_id: str) -> bool:
    clean = str(model_id or "").strip().lower()
    if clean == "nulla-qwen3-30b-a3b:nothink":
        return True
    # Any Ollama custom model with :nothink variant (user-built Modelfiles)
    if ":nothink" in clean:
        return True
    # Metadata flag for GGUF / other backends with thinking disabled at launch
    return bool(model_metadata(clean).get("thinking_disabled", False))


def _build_warnings(
    *,
    capabilities: tuple[ProviderCapabilityTruth, ...],
    selected: ProviderCapabilityTruth | None,
    explicit_heavy: bool,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if selected is None:
        warnings.append("no_local_or_provider_lane_available")
        if explicit_heavy:
            warnings.append("explicit_heavy_lane_unavailable")
    if capabilities and all(item.tokens_per_second <= 0 for item in capabilities):
        warnings.append("routing_has_no_measured_tokens_per_second")
    for capability in capabilities:
        size_b = model_parameter_billions(capability.model_id)
        if size_b >= 24.0 and not explicit_heavy and not _is_fast_nothink_default(capability.model_id):
            warnings.append(f"{capability.provider_id}:oversized_lane_not_default")
        if capability.availability_state == "degraded":
            warnings.append(f"{capability.provider_id}:degraded")
    return tuple(dict.fromkeys(warnings))


def _evidence_refs(capabilities: tuple[ProviderCapabilityTruth, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for capability in capabilities:
        if capability.tokens_per_second > 0:
            refs.append(f"measured:{capability.provider_id}:tok_s={capability.tokens_per_second:.2f}")
        else:
            refs.append(f"manifest:{capability.provider_id}:unmeasured")
    return tuple(refs)


def _explicit_heavy_requested(*, user_text: str, source_context: dict[str, Any] | None) -> bool:
    if bool((source_context or {}).get("autopilot_allow_heavy_model")):
        return True
    requested = str((source_context or {}).get("requested_model") or "").lower()
    lowered = str(user_text or "").lower()
    return any(marker in requested or marker in lowered for marker in ("24b", "30b", "32b", "35b", "72b", "heavy"))


def _requested_heavy_marker(*, user_text: str, source_context: dict[str, Any] | None) -> str | None:
    requested = str((source_context or {}).get("requested_model") or "").lower()
    lowered = str(user_text or "").lower()
    for marker in ("24b", "30b", "32b", "35b", "72b"):
        if marker in requested or marker in lowered:
            return marker
    return None


def _sanitize_text(value: Any) -> tuple[str, int]:
    text = str(value or "")
    redactions = 0
    for pattern, replacement in (
        (_SECRET_RE, "<secret-like-token>"),
        (_LONG_TOKEN_RE, "<secret-like-token>"),
        (_MAC_PATH_RE, "<private-path>"),
        (_WIN_PATH_RE, "<private-path>"),
        (_HOME_PATH_RE, "<private-path>"),
    ):
        text, count = pattern.subn(replacement, text)
        redactions += count
    return text, redactions


def _context_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),) if str(value).strip() else tuple()


def _split_constraints(value: str) -> list[str]:
    if not value.strip():
        return []
    if "\n" in value:
        return [_clip(item.strip("-* \t"), 160) for item in value.splitlines() if item.strip()]
    return [_clip(value, 220)]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _dedupe_residency(actions: list[ResidencyAction]) -> list[ResidencyAction]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ResidencyAction] = []
    for action in actions:
        key = (action.provider_id, action.model_id, action.action)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


__all__ = [
    "AutopilotFramework",
    "AutopilotLane",
    "AutopilotPhase",
    "ContextCapsule",
    "LocalInferenceAutopilotPlan",
    "PrefixCachePlan",
    "ResidencyAction",
    "build_local_inference_autopilot_plan",
    "build_prefix_cache_plan",
    "compile_context_capsule",
]
