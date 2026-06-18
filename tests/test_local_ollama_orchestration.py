from __future__ import annotations

from unittest import mock

from core.hardware_tier import MachineProbe, QwenTier
from core.local_model_bundles import model_active_parameter_billions, model_metadata
from core.model_selection_policy import ModelSelectionRequest, rank_providers
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth, normalize_install_profile_id
from core.runtime_provider_defaults import (
    default_runtime_model_tag,
    ensure_default_runtime_providers,
    preferred_fast_local_model,
)
from storage.model_provider_manifest import ModelProviderManifest


def _mock_registry():
    manifests: dict[tuple[str, str], ModelProviderManifest] = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        values = list(manifests.values())[:limit]
        if enabled_only:
            return [item for item in values if item.enabled]
        return values

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.side_effect = lambda: [
        mock.Mock(provider_id=item.provider_id) for item in _list_manifests(enabled_only=True)
    ]
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    return registry, manifests


def _installed_lane_env() -> dict[str, str]:
    return {
        "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
        "NULLA_INSTALLED_OLLAMA_MODELS": "qwen2.5:0.5b,qwen3:8b,qwen3:14b,qwen2.5:32b",
    }


def test_runtime_provider_defaults_register_installed_ollama_lanes() -> None:
    registry, manifests = _mock_registry()

    changed = ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env=_installed_lane_env(),
        install_profile="local-only",
    )

    assert "ollama-local:qwen2.5:0.5b" in changed
    assert "ollama-local:qwen3:14b" in changed
    assert "ollama-local:qwen2.5:32b" in changed
    assert manifests[("ollama-local", "qwen2.5:0.5b")].metadata["bundle_role"] == "lightweight_utility"
    assert manifests[("ollama-local", "qwen3:8b")].metadata["bundle_role"] == "general"
    assert manifests[("ollama-local", "qwen3:14b")].metadata["bundle_role"] == "reasoning"
    assert manifests[("ollama-local", "qwen2.5:32b")].metadata["bundle_role"] == "heavy_reasoning"
    assert "api_path" not in manifests[("ollama-local", "qwen3:8b")].runtime_config
    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["think"] is False
    assert manifests[("ollama-local", "qwen2.5:0.5b")].runtime_config["context_window"] == 2048
    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["context_window"] == 4096
    assert manifests[("ollama-local", "qwen3:14b")].runtime_config["prewarm"]["options"]["num_ctx"] == 4096
    assert manifests[("ollama-local", "qwen2.5:32b")].runtime_config["prewarm"]["options"]["num_ctx"] == 1024


def test_default_runtime_model_prefers_installed_fast_nothink_lane() -> None:
    env = {
        "NULLA_INSTALLED_OLLAMA_MODELS": "qwen3:8b,nulla-qwen3-30b-a3b:nothink,qwen3.5:35b-a3b",
    }

    assert preferred_fast_local_model(env=env) == "nulla-qwen3-30b-a3b:nothink"
    assert default_runtime_model_tag(env=env) == "nulla-qwen3-30b-a3b:nothink"


def test_goblin_model_metadata_records_hybrid_moe_truth() -> None:
    qwen35 = model_metadata("qwen3.5:35b-a3b")
    gemma_qat = model_metadata("gemma3:12b-qat")

    assert qwen35["architecture"] == "hybrid_moe"
    assert qwen35["constant_vram"] is True
    assert qwen35["max_context"] == 262144
    assert model_active_parameter_billions("qwen3.5:35b-a3b") == 3.3
    assert gemma_qat["qat"] is True
    assert gemma_qat["license_name"] == "Gemma Terms"


def test_goblin_stack_profile_selects_required_local_lanes(tmp_path) -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )
    tier = QwenTier("heavy", "qwen2.5:32b", 32.0, 20.0, 48.0)
    capability_truth = (
        _capability_truth("qwen3:0.6b", role_fit="drone", tokens_per_second=220.0),
        _capability_truth("qwen3:8b", role_fit="drone", tokens_per_second=130.0),
        _capability_truth("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=75.0),
    )

    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(256.0)):
        profile = build_install_profile_truth(
            requested_profile="goblin_stack",
            probe=probe,
            tier=tier,
            provider_capability_truth=capability_truth,
            env={
                "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
                "NULLA_INSTALLED_OLLAMA_MODELS": "qwen3:0.6b,qwen3:8b,qwen3.5:35b-a3b",
            },
            runtime_home=tmp_path,
        )

    assert normalize_install_profile_id("goblin_stack") == "goblin-stack"
    assert profile.profile_id == "goblin-stack"
    assert profile.ready is True
    assert profile.selected_models == ("qwen3:0.6b", "qwen3:8b", "qwen3.5:35b-a3b")
    assert profile.optional_models == ("qwen3:30b-a3b", "qwen3:14b")
    assert ("heavy_reasoning", "qwen3.5:35b-a3b") in profile.selected_model_roles


def test_provider_snapshot_keeps_installed_ollama_lanes_visible_under_local_profile(tmp_path) -> None:
    registry, _ = _mock_registry()

    snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        env=_installed_lane_env(),
    )

    provider_ids = {item.provider_id for item in snapshot.capability_truth}
    assert {
        "ollama-local:qwen2.5:0.5b",
        "ollama-local:qwen3:8b",
        "ollama-local:qwen3:14b",
        "ollama-local:qwen2.5:32b",
    } <= provider_ids


def test_provider_snapshot_explicit_installed_ollama_override_filters_stale_lanes(tmp_path) -> None:
    registry, manifests = _mock_registry()
    manifests[("ollama-local", "gemma3:4b")] = _manifest(
        "gemma3:4b",
        bundle_role="lightweight_utility",
        capabilities=["format", "structured_json", "tool_intent"],
    )

    snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        env=_installed_lane_env(),
    )

    provider_ids = {item.provider_id for item in snapshot.capability_truth}
    assert "ollama-local:gemma3:4b" not in provider_ids
    assert "ollama-local:qwen3:8b" in provider_ids
    assert "ollama-local:qwen3:14b" in provider_ids


def _manifest(model_name: str, *, bundle_role: str, capabilities: list[str]) -> ModelProviderManifest:
    orchestration_role = "queen" if bundle_role in {"reasoning", "heavy_reasoning"} else "drone"
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen",
        license_url_or_reference="https://ollama.com/library/qwen",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=capabilities,
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": orchestration_role,
            "bundle_role": bundle_role,
        },
        enabled=True,
    )


def _capability_truth(model_id: str, *, role_fit: str, tokens_per_second: float) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=8192,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state="ready",
    )


def _fake_disk_usage_with_free_gb(free_gb: float) -> mock.Mock:
    fake_usage = mock.Mock()
    fake_usage.free = int(free_gb * 1024**3)
    return fake_usage


def test_model_ranking_uses_utility_general_and_reasoning_lanes() -> None:
    manifests = [
        _manifest(
            "qwen2.5:0.5b",
            bundle_role="lightweight_utility",
            capabilities=["format", "structured_json", "tool_intent"],
        ),
        _manifest(
            "qwen3:8b",
            bundle_role="general",
            capabilities=["format", "structured_json", "tool_intent", "summarize"],
        ),
        _manifest(
            "qwen3:14b",
            bundle_role="reasoning",
            capabilities=["format", "structured_json", "summarize", "code_complex", "long_context"],
        ),
    ]

    utility_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"),
    )
    chat_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="normalization_assist", output_mode="plain_text"),
    )
    reasoning_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="action_plan", output_mode="action_plan"),
    )

    assert utility_ranked[0].model_name == "qwen2.5:0.5b"
    assert chat_ranked[0].model_name == "qwen3:8b"
    assert reasoning_ranked[0].model_name == "qwen3:14b"
