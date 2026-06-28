from __future__ import annotations

import json
import subprocess
from unittest import mock

from core.hardware_tier import GPUDevice, MachineProbe
from core.provider_routing import ProviderCapabilityTruth
from installer.provider_probe import (
    build_probe_report,
    list_ollama_models,
    remote_env_statuses,
    render_probe_report,
    run_ollama_benchmark,
)


def test_probe_report_prefers_bundle_local_stack_on_24gb_mps_host_with_required_models() -> None:
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[
                {"name": "qwen3:8b", "id": "a", "size": "5.2 GB", "modified": "today"},
                {"name": "deepseek-r1:8b", "id": "b", "size": "5.2 GB", "modified": "today"},
            ],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
        )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["recommended_install_profile_display_id"] == "ollama-only (local-only)"
    assert report["local_multi_llm_fit"] == "pressure_sensitive"
    assert report["capacity_bucket"] == "C"
    assert report["machine"]["ollama_model"] == "qwen3:8b"
    assert report["machine"]["selected_tier"] == "capacity-C"
    assert report["machine"]["param_billions"] == 8.0
    assert report["machine"]["recommended_bundle_models"] == ["qwen3:8b", "deepseek-r1:8b"]
    local_only = next(item for item in report["stacks"] if item["stack_id"] == "local_only")
    assert local_only["install_profile_id"] == "local-only"
    assert local_only["install_profile_display_id"] == "ollama-only (local-only)"
    assert local_only["status"] == "ready"
    assert local_only["recommended"] is True
    assert local_only["bundle_id"] == "dual_qwen3_8b_deepseek_r1_8b"
    recommendation = report["install_recommendation"]
    assert recommendation["recommended_default_profile"] == "local-only"
    assert recommendation["recommended_optional_profile"] == "local-max"
    assert recommendation["primary_local_model"] == "qwen3:8b"
    assert recommendation["recommended_bundle_id"] == "dual_qwen3_8b_deepseek_r1_8b"
    assert recommendation["recommended_bundle_models"] == ["qwen3:8b", "deepseek-r1:8b"]
    assert recommendation["secondary_local_model"] == "qwen2.5:14b-gguf"
    assert recommendation["secondary_local_supported"] is True
    dual = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_llamacpp")
    assert dual["install_profile_id"] == "local-max"
    assert dual["install_profile_display_id"] == "ollama-max (local-max)"
    assert dual["status"] == "needs_setup"
    assert dual["recommended"] is False
    assert dual["secondary_backend"] == "llama.cpp"
    assert dual["secondary_model"] == "qwen2.5:14b-gguf"


def test_probe_report_hides_remote_lanes_but_keeps_remote_env_truth_and_qvac_honest() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=4,
                availability_state="ready",
            ),
        ),
        show_unsupported=True,
    )

    qvac = next(item for item in report["unsupported_stacks"] if item["stack_id"] == "local_plus_qvac")

    assert all(item["stack_id"] != "local_plus_kimi" for item in report["stacks"])
    assert all(item["stack_id"] != "local_plus_tether" for item in report["stacks"])
    assert report["remote_env"]["kimi"]["configured"] is True
    assert report["remote_env"]["tether"]["configured"] is True
    assert qvac["status"] == "not_implemented"


def test_probe_report_keeps_local_only_default_when_smaller_host_has_real_remote_lane() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": True},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="openai-compatible-remote:gpt-4.1-mini",
                model_id="gpt-4.1-mini",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=2,
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["install_recommendation"]["recommended_default_profile"] == "local-only"
    assert report["install_recommendation"]["recommended_optional_profile"] == ""
    assert report["install_recommendation"]["secondary_local_supported"] is False
    assert report["remote_env"]["generic_remote"]["configured"] is True
    assert all(item["stack_id"] != "local_plus_remote_openai_compatible" for item in report["stacks"])


def test_probe_report_keeps_local_only_default_when_kimi_is_configured_and_ready() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=4,
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["recommended_install_profile_display_id"] == "ollama-only (local-only)"
    assert report["install_recommendation"]["recommended_default_profile"] == "local-only"
    assert report["install_recommendation"]["secondary_local_supported"] is False
    assert report["remote_env"]["kimi"]["configured"] is True
    assert all(item["stack_id"] != "local_plus_kimi" for item in report["stacks"])


def test_render_probe_report_surfaces_installed_models_and_recommendation() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
    )

    rendered = render_probe_report(report)
    assert "recommended install profile" in rendered.lower()
    assert "recommended stack" in rendered.lower()
    assert "gemma3:4b" in rendered
    assert "ollama-only (local-only)" in rendered
    assert "local_plus_llamacpp" in rendered
    assert "local_plus_remote_openai_compatible" not in rendered
    assert "local_plus_tether" not in rendered
    assert "ollama+tether (hybrid-tether)" not in rendered


def test_probe_report_surfaces_gpu_inventory_and_optional_live_check(monkeypatch) -> None:
    def fake_benchmark(**kwargs) -> dict[str, object]:
        return {
            "schema": "nulla.local_model_benchmark.v1",
            "status": "ok",
            "model": kwargs["model_name"],
            "elapsed_seconds": 2.5,
            "rough_output_tokens_per_second": 0.4,
        }

    monkeypatch.setattr("installer.provider_probe.run_ollama_benchmark", fake_benchmark)

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=16,
                ram_gb=32.0,
                gpu_name="NVIDIA GeForce RTX 4090",
                vram_gb=24.0,
                accelerator="cuda",
                accelerator_status="usable",
                gpu_devices=(
                    GPUDevice(
                        index=0,
                        name="NVIDIA GeForce RTX 4090",
                        vendor="nvidia",
                        vram_gb=24.0,
                        backend="cuda",
                        status="usable",
                        source="nvidia-smi",
                    ),
                ),
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            run_benchmark=True,
        )

    rendered = render_probe_report(report)

    assert report["local_model_benchmark"]["status"] == "ok"
    assert "detected GPUs: [0] NVIDIA GeForce RTX 4090 24.0 GB cuda usable active" in rendered
    assert "local model live check: ok on" in rendered
    assert "2.5s wall-clock" in rendered
    assert "rough output tokens/sec: 0.4" in rendered


def test_run_ollama_benchmark_records_marker_and_elapsed(monkeypatch) -> None:
    clock = mock.Mock(side_effect=[10.0, 12.5])
    monkeypatch.setattr("installer.provider_probe.time.perf_counter", clock)

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        assert args[0][:3] == ["ollama", "run", "gemma3:4b"]
        assert kwargs["timeout"] == 9
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="NULLA_BENCH_OK",
            stderr="",
        )

    monkeypatch.setattr("installer.provider_probe.subprocess.run", fake_run)

    result = run_ollama_benchmark(
        model_name="gemma3:4b",
        ollama_binary="ollama",
        timeout_seconds=9,
    )

    assert result["status"] == "ok"
    assert result["marker_seen"] is True
    assert result["elapsed_seconds"] == 2.5
    assert result["rough_output_tokens_per_second"] == 0.4


def test_probe_report_surfaces_accelerator_warning_and_model_pull_plan() -> None:
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=8,
                ram_gb=8.0,
                gpu_name="NVIDIA GeForce GTX 1080",
                vram_gb=8.0,
                accelerator="cpu",
                accelerator_status="legacy_cuda_cpu_recommended",
                accelerator_advice="Legacy CUDA fallback active.",
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
        )

    assert report["machine"]["accelerator"] == "cpu"
    assert report["machine"]["accelerator_status"] == "legacy_cuda_cpu_recommended"
    assert report["local_model_plan"]["recommended_models"] == ["gemma3:4b"]
    assert report["local_model_plan"]["missing_recommended_models"] == ["gemma3:4b"]
    assert report["local_model_plan"]["pull_commands"] == ["ollama pull gemma3:4b"]
    assert report["local_model_plan"]["status"] == "needs_setup"
    assert report["stacks"][0]["status"] == "needs_setup"

    rendered = render_probe_report(report)

    assert "accelerator status: legacy_cuda_cpu_recommended" in rendered
    assert "missing recommended models: gemma3:4b" in rendered
    assert "ollama pull gemma3:4b" in rendered


def test_probe_report_blocks_pull_plan_when_safe_disk_floor_is_not_met() -> None:
    with mock.patch("core.install_recommendations._free_gb", return_value=27.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=8,
                ram_gb=8.0,
                gpu_name="NVIDIA GeForce GTX 1080",
                vram_gb=8.0,
                accelerator="cpu",
                accelerator_status="legacy_cuda_cpu_recommended",
                accelerator_advice="Legacy CUDA fallback active.",
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
        )

    assert report["local_model_plan"]["status"] == "needs_space"
    assert report["local_model_plan"]["minimum_space_to_free_gb"] == 1.3
    assert report["stacks"][0]["status"] == "needs_space"

    rendered = render_probe_report(report)

    assert "disk action: free at least 1.3 GB before pulling" in rendered
    assert "target volume is below the safe disk floor" in rendered


def test_default_probe_report_hides_unsupported_remote_ideas() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
    )

    assert "unsupported_stacks" not in report
    assert all(item["stack_id"] != "local_plus_tether" for item in report["stacks"])
    assert all(item["stack_id"] != "local_plus_qvac" for item in report["stacks"])


def test_list_ollama_models_preserves_size_and_modified_columns(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "NAME           ID              SIZE      MODIFIED      \n"
                "qwen2.5:14b    7cdf5a0187d5    9.0 GB    3 minutes ago    \n"
            ),
            stderr="",
        )

    monkeypatch.setattr("installer.provider_probe.subprocess.run", fake_run)
    monkeypatch.setattr("installer.provider_probe.detect_ollama_binary", lambda: "/usr/local/bin/ollama")

    rows = list_ollama_models()

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "9.0 GB",
            "modified": "3 minutes ago",
        }
    ]


def test_list_ollama_models_prefers_tags_api_and_avoids_cli_shellout(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe.shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else "")
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])
    monkeypatch.setattr(
        "installer.provider_probe.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                '{"models":[{"name":"qwen2.5:14b","digest":"7cdf5a0187d5abcd","size":8988124069,'
                '"modified_at":"2026-03-28T09:38:28Z"}]}'
            ),
            stderr="",
        ),
    )

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "8.4 GB",
            "modified": "2026-03-28T09:38:28Z",
        }
    ]


def test_list_ollama_models_falls_back_to_manifest_inventory(monkeypatch, tmp_path) -> None:
    manifest_root = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5"
    manifest_root.mkdir(parents=True)
    (manifest_root / "14b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54",
                        "size": 8988110688,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (manifest_root / "7b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730",
                        "size": 4683073952,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe.default_ollama_models_path", lambda: (tmp_path / "models").resolve())

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert [row["name"] for row in rows] == ["qwen2.5:14b", "qwen2.5:7b"]


def test_remote_env_statuses_accepts_moonshot_aliases_for_kimi() -> None:
    with mock.patch.dict(
        "os.environ",
        {
            "MOONSHOT_API_KEY": "test-key",
            "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
            "MOONSHOT_MODEL": "kimi-k2",
        },
        clear=True,
    ):
        statuses = remote_env_statuses()

    assert statuses["kimi"]["api_key_present"] is True
    assert statuses["kimi"]["base_url_present"] is True
    assert statuses["kimi"]["model_present"] is True
    assert statuses["kimi"]["configured"] is True
