from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from core.backend_manager import BackendManager
from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier, tier_summary
from core.local_ollama_inventory import env_flag_enabled, installed_ollama_model_names
from core.model_registry import ModelRegistry, ProviderAuditRow
from core.provider_env import merge_provider_env
from core.provider_routing import ProviderCapabilityTruth, provider_capability_truth_for_manifest
from core.runtime_bootstrap import BootstrappedRuntime, bootstrap_runtime_mode
from core.runtime_install_profiles import InstallProfileTruth, active_install_profile_id, build_install_profile_truth
from core.runtime_provider_defaults import ensure_default_runtime_providers


@dataclass(frozen=True)
class ProviderRegistrySnapshot:
    warnings: tuple[str, ...]
    audit_rows: tuple[ProviderAuditRow, ...]
    capability_truth: tuple[ProviderCapabilityTruth, ...]
    prewarm_results: tuple[dict[str, Any], ...] = tuple()


@dataclass(frozen=True)
class LocalModelProfile:
    probe: MachineProbe
    tier: QwenTier
    summary: dict[str, Any]


@dataclass(frozen=True)
class RuntimeBackbone:
    boot: BootstrappedRuntime
    local_model_profile: LocalModelProfile
    provider_snapshot: ProviderRegistrySnapshot
    install_profile: InstallProfileTruth


def build_provider_registry_snapshot(
    registry: ModelRegistry | None = None,
    *,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
    honor_install_profile: bool = False,
    run_prewarm: bool = False,
    env: dict[str, str] | None = None,
) -> ProviderRegistrySnapshot:
    active_registry = registry or ModelRegistry()
    env_map = merge_provider_env(runtime_home, env=os.environ if env is None else env)
    install_profile = ""
    if honor_install_profile:
        install_profile = (
            str(requested_profile or "").strip()
            or active_install_profile_id(runtime_home=runtime_home, env=env_map)
        )
    ensure_default_runtime_providers(
        active_registry,
        env=env_map,
        install_profile=install_profile,
        runtime_home=runtime_home,
    )
    manifests: tuple[Any, ...]
    try:
        manifests = tuple(active_registry.list_manifests(enabled_only=True))
    except Exception:
        manifests = tuple()
    warnings = tuple(active_registry.startup_warnings())
    audit_rows = tuple(active_registry.provider_audit_rows())
    capability_truth = tuple(provider_capability_truth_for_manifest(manifest) for manifest in manifests)
    manifests, audit_rows, capability_truth = _filter_snapshot_to_installed_ollama_inventory(
        manifests=manifests,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        env=env_map,
    )
    visible_provider_ids: tuple[str, ...] = tuple()
    if honor_install_profile:
        visible_provider_ids = _visible_provider_ids_for_install_profile(
            capability_truth=capability_truth,
            requested_profile=install_profile or None,
            runtime_home=runtime_home,
            env=env_map,
        )
        manifests, audit_rows, capability_truth = _filter_snapshot_to_provider_ids(
            manifests=manifests,
            audit_rows=audit_rows,
            capability_truth=capability_truth,
            provider_ids=visible_provider_ids,
        )
    prewarm_results: tuple[dict[str, Any], ...] = tuple()
    if run_prewarm:
        try:
            prewarm_results = tuple(active_registry.prewarm_enabled_providers(provider_ids=visible_provider_ids or None))
        except Exception:
            prewarm_results = tuple()
    return ProviderRegistrySnapshot(
        warnings=warnings,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        prewarm_results=prewarm_results,
    )


def _filter_snapshot_to_installed_ollama_inventory(
    *,
    manifests: tuple[Any, ...],
    audit_rows: tuple[ProviderAuditRow, ...],
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: dict[str, str],
) -> tuple[tuple[Any, ...], tuple[ProviderAuditRow, ...], tuple[ProviderCapabilityTruth, ...]]:
    if not env_flag_enabled(env, "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS", default=False):
        return manifests, audit_rows, capability_truth
    installed_tags = {tag.lower() for tag in installed_ollama_model_names(env=env)}
    if not installed_tags:
        return manifests, audit_rows, capability_truth
    visible_provider_ids = {
        item.provider_id
        for item in capability_truth
        if not _is_local_ollama_capability(item) or str(item.model_id or "").strip().lower() in installed_tags
    }
    return _filter_snapshot_to_provider_ids(
        manifests=manifests,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        provider_ids=tuple(visible_provider_ids),
    )


def _visible_provider_ids_for_install_profile(
    *,
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    requested_profile: str | None,
    runtime_home: str | None,
    env: dict[str, str],
) -> tuple[str, ...]:
    if not capability_truth:
        return tuple()
    install_profile = build_install_profile_truth(
        requested_profile=requested_profile,
        provider_capability_truth=capability_truth,
        runtime_home=runtime_home,
        env=env,
    )
    return tuple(
        str(item.provider_id or "").strip()
        for item in install_profile.provider_mix
        if str(item.provider_id or "").strip()
    )


def _is_local_ollama_capability(item: ProviderCapabilityTruth) -> bool:
    return item.locality == "local" and str(item.provider_id or "").strip().lower().startswith("ollama-local:")


def _filter_snapshot_to_provider_ids(
    *,
    manifests: tuple[Any, ...],
    audit_rows: tuple[ProviderAuditRow, ...],
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    provider_ids: tuple[str, ...],
) -> tuple[tuple[Any, ...], tuple[ProviderAuditRow, ...], tuple[ProviderCapabilityTruth, ...]]:
    visible_provider_ids = {str(provider_id or "").strip() for provider_id in provider_ids if str(provider_id or "").strip()}
    if not visible_provider_ids:
        return manifests, audit_rows, capability_truth
    filtered_manifests = tuple(item for item in manifests if str(getattr(item, "provider_id", "") or "").strip() in visible_provider_ids)
    filtered_audit_rows = tuple(item for item in audit_rows if str(item.provider_id or "").strip() in visible_provider_ids)
    filtered_capability_truth = tuple(item for item in capability_truth if str(item.provider_id or "").strip() in visible_provider_ids)
    return filtered_manifests, filtered_audit_rows, filtered_capability_truth


def build_runtime_backbone(
    *,
    mode: str,
    workspace_root: str | None = None,
    db_path: str | None = None,
    force_policy_reload: bool = False,
    configure_logging: bool = False,
    resolve_backend: bool = False,
    manager: BackendManager | None = None,
    allow_remote_only: bool | None = None,
    registry: ModelRegistry | None = None,
    machine_probe: MachineProbe | None = None,
) -> RuntimeBackbone:
    boot = bootstrap_runtime_mode(
        mode=mode,
        workspace_root=workspace_root,
        db_path=db_path,
        force_policy_reload=force_policy_reload,
        configure_logging=configure_logging,
        resolve_backend=resolve_backend,
        manager=manager,
        allow_remote_only=allow_remote_only,
    )
    probe = machine_probe or probe_machine()
    tier = select_qwen_tier(probe)
    summary = dict(tier_summary(probe))
    if boot.backend_selection is not None:
        summary["backend_name"] = boot.backend_selection.backend_name
        summary["backend_device"] = boot.backend_selection.device
        summary["backend_reason"] = boot.backend_selection.reason
    provider_snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(getattr(getattr(boot, "context", None), "paths", None).runtime_home)
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
        honor_install_profile=True,
        run_prewarm=True,
    )
    install_profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        provider_capability_truth=provider_snapshot.capability_truth,
        runtime_home=getattr(getattr(boot, "context", None), "paths", None).runtime_home
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
    )
    return RuntimeBackbone(
        boot=boot,
        local_model_profile=LocalModelProfile(
            probe=probe,
            tier=tier,
            summary=summary,
        ),
        provider_snapshot=provider_snapshot,
        install_profile=install_profile,
    )


__all__ = [
    "LocalModelProfile",
    "ProviderRegistrySnapshot",
    "RuntimeBackbone",
    "build_provider_registry_snapshot",
    "build_runtime_backbone",
]
