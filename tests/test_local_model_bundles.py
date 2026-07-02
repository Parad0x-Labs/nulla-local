from __future__ import annotations

from core.llamacpp_capability_probe import CapabilityProbeResult
from core.local_model_bundles import (
    SPEED_TIER_ROLES,
    bundle_spec,
    llamacpp_offload_layers,
    resolve_local_bundle_recommendation,
)


def _fake_capability(*, usable: bool, gpu_tokens_per_second: float = 36.0, speedup_ratio: float = 18.0) -> CapabilityProbeResult:
    return CapabilityProbeResult(
        schema="nulla.llamacpp_capability_probe.v1",
        probed_at_epoch=0.0,
        probe_version=1,
        gpu_name="GeForce GTX 1080",
        gpu_vendor="nvidia",
        backend_tested="vulkan",
        binary_release_tag="b9856",
        cpu_baseline_tokens_per_second=2.0,
        gpu_tokens_per_second=gpu_tokens_per_second,
        speedup_ratio=speedup_ratio,
        status="gpu_confirmed_fast" if usable else "gpu_rejected_slow",
        verdict_backend="vulkan" if usable else "cpu",
        detail="",
    )


def test_every_bucket_always_resolves_to_three_speed_tier_roles_without_gpu() -> None:
    # accelerator="cpu" zeroes effective VRAM (core.local_model_bundles._probe_effective_vram_gb),
    # so a CPU-only host is always bucket A regardless of RAM under the current (unchanged)
    # capacity_bucket_for_machine thresholds. To exercise buckets B-E here, give each probe a
    # non-legacy "cuda" accelerator with enough VRAM for that tier — this models a host with a
    # modern GPU Ollama itself already trusts, independent of the llama.cpp gpu_capability probe.
    probes = {
        "A": {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 0.0},
        "B": {"ram_gb": 20.0, "accelerator": "cuda", "vram_gb": 10.0},
        "C": {"ram_gb": 28.0, "accelerator": "cuda", "vram_gb": 16.0},
        "D": {"ram_gb": 40.0, "accelerator": "cuda", "vram_gb": 24.0},
        "E": {"ram_gb": 64.0, "accelerator": "cuda", "vram_gb": 24.0},
    }
    free_disk_by_bucket = {"A": 10.0, "B": 40.0, "C": 80.0, "D": 150.0, "E": 200.0}
    for expected_bucket, probe in probes.items():
        rec = resolve_local_bundle_recommendation(
            probe=probe,
            free_disk_gb=free_disk_by_bucket[expected_bucket],
            secondary_local_model_name="gemma3:4b",
        )
        assert rec.capacity_bucket == expected_bucket
        roles = tuple(item.role for item in rec.recommended_bundle.role_models)
        assert roles == SPEED_TIER_ROLES, f"bucket {expected_bucket} did not resolve to all 3 speed tiers: {roles}"
        assert not rec.gpu_capability_used
        for role_model in rec.recommended_bundle.role_models:
            assert role_model.backend == "ollama"
            assert not role_model.requires_gpu_backend


def test_bucket_a_no_longer_collapses_to_a_single_model() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe, free_disk_gb=355.5, secondary_local_model_name="gemma3:4b",
    )
    assert rec.capacity_bucket == "A"
    assert len(rec.recommended_bundle.role_models) == 3


def test_verified_gpu_capability_unlocks_llamacpp_accelerated_daily_and_deep_tiers() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.gpu_capability_used
    role_map = {item.role: item for item in rec.recommended_bundle.role_models}
    assert role_map["tiny_fast"].backend == "ollama"
    assert role_map["daily_accelerated"].backend == "llamacpp"
    assert role_map["daily_accelerated"].requires_gpu_backend
    assert role_map["daily_accelerated"].expected_tokens_per_second > 30.0
    assert role_map["deep_overnight"].backend == "llamacpp"
    assert role_map["deep_overnight"].requires_gpu_backend
    # fallback must be the CPU-only variant of the same bucket, for a safe degrade
    fallback_roles = {item.role: item for item in rec.fallback_bundle.role_models}
    assert fallback_roles["daily_accelerated"].backend == "ollama"


def test_gpu_capability_rejected_falls_back_to_cpu_bundle() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=False),
    )
    assert not rec.gpu_capability_used
    for role_model in rec.recommended_bundle.role_models:
        assert role_model.backend == "ollama"


def test_explicit_selected_model_still_wins_regardless_of_gpu_capability() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        selected_model="qwen2.5:7b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.legacy_mode
    assert rec.recommended_bundle.primary_model == "qwen2.5:7b"


def test_llamacpp_offload_layers_full_offload_when_vram_comfortably_fits() -> None:
    assert llamacpp_offload_layers(model_name="qwen2.5:7b-instruct-q4_k_m", vram_gb=8.0) == 999


def test_llamacpp_offload_layers_partial_offload_when_model_exceeds_vram() -> None:
    layers = llamacpp_offload_layers(model_name="qwen2.5:14b-instruct-q4_k_m", vram_gb=8.0)
    assert 0 < layers < 999


def test_llamacpp_offload_layers_zero_when_no_vram() -> None:
    assert llamacpp_offload_layers(model_name="qwen2.5:7b-instruct-q4_k_m", vram_gb=0.0) == 0


def test_bucket_e_gpu_accelerated_bundle_uses_moe_deep_model() -> None:
    probe = {"ram_gb": 64.0, "accelerator": "cuda", "vram_gb": 24.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=300.0,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.capacity_bucket == "E"
    role_map = {item.role: item for item in rec.recommended_bundle.role_models}
    assert role_map["daily_accelerated"].backend == "llamacpp"
    assert role_map["deep_overnight"].backend == "llamacpp"


def test_primary_model_prefers_daily_accelerated_over_tiny_fast_for_speed_tier_bundles() -> None:
    # Regression test: LocalBundleSpec.primary_model previously only recognized the older
    # general/coding/reasoning roles, so for the new tiny_fast/daily_accelerated/deep_overnight
    # bundles it fell through to "whatever role_models happens to list first" - which is
    # tiny_fast by definition order, silently making a 0.6B utility model the "primary" /
    # default runtime model for a fresh install instead of the intended daily workhorse.
    for bucket in "abcde":
        spec = bundle_spec(f"triple_bucket_{bucket}_no_gpu")
        role_map = spec.role_map
        assert spec.primary_model == role_map["daily_accelerated"], (
            f"triple_bucket_{bucket}_no_gpu.primary_model should be the daily_accelerated model, "
            f"not {spec.primary_model!r}"
        )
