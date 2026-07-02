from __future__ import annotations

import json
from pathlib import Path

from core.runtime_provider_defaults import _profile_allows_aux_local_providers


def _write_verified_cache(runtime_home: Path) -> None:
    cache_path = runtime_home / "config" / "llamacpp-capability-probe.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "geforce gtx 1080|b9856|1": {
                    "schema": "nulla.llamacpp_capability_probe.v1",
                    "probed_at_epoch": 9999999999.0,
                    "probe_version": 1,
                    "gpu_name": "GeForce GTX 1080",
                    "gpu_vendor": "nvidia",
                    "backend_tested": "vulkan",
                    "binary_release_tag": "b9856",
                    "cpu_baseline_tokens_per_second": 2.0,
                    "gpu_tokens_per_second": 36.0,
                    "speedup_ratio": 18.0,
                    "status": "gpu_confirmed_fast",
                    "verdict_backend": "vulkan",
                    "detail": "",
                }
            }
        ),
        encoding="utf-8",
    )


def test_local_max_and_full_orchestrated_always_allowed() -> None:
    assert _profile_allows_aux_local_providers("local-max") is True
    assert _profile_allows_aux_local_providers("full-orchestrated") is True


def test_empty_profile_id_is_allowed() -> None:
    assert _profile_allows_aux_local_providers("") is True


def test_local_only_denied_without_runtime_home() -> None:
    assert _profile_allows_aux_local_providers("local-only") is False


def test_local_only_denied_when_no_verified_probe_cached(tmp_path) -> None:
    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is False


def test_local_only_allowed_when_gpu_capability_was_live_verified(tmp_path) -> None:
    _write_verified_cache(tmp_path)

    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is True


def test_local_only_denied_when_cached_probe_rejected_the_gpu(tmp_path) -> None:
    cache_path = tmp_path / "config" / "llamacpp-capability-probe.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "ancient igpu|b9856|1": {
                    "schema": "nulla.llamacpp_capability_probe.v1",
                    "probed_at_epoch": 9999999999.0,
                    "probe_version": 1,
                    "gpu_name": "Ancient iGPU",
                    "gpu_vendor": "intel",
                    "backend_tested": "vulkan",
                    "binary_release_tag": "b9856",
                    "cpu_baseline_tokens_per_second": 2.0,
                    "gpu_tokens_per_second": 2.1,
                    "speedup_ratio": 1.05,
                    "status": "gpu_rejected_slow",
                    "verdict_backend": "cpu",
                    "detail": "",
                }
            }
        ),
        encoding="utf-8",
    )

    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is False
