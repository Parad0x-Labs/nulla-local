from __future__ import annotations

from types import SimpleNamespace

from core.provider_routing import ProviderCapabilityTruth
from installer import validate_install_profile as validator


def test_validate_install_profile_blocks_unready_hybrid_kimi(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(capability_truth=()),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/nulla-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-kimi",
    )

    assert ok is False
    assert "KIMI_API_KEY" in message
    assert "hybrid-kimi" in message


def test_validate_install_profile_accepts_ready_hybrid_kimi(monkeypatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(
            capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
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
            )
        ),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/nulla-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-kimi",
    )

    assert ok is True
    assert message == ""


def test_validate_install_profile_blocks_unready_full_orchestrated(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(capability_truth=()),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/nulla-runtime",
        selected_model="qwen2.5:14b",
        requested_profile="full-orchestrated",
    )

    assert ok is False
    assert "full-orchestrated" in message
    assert "not ready" in message


def test_validate_install_profile_accepts_ready_hybrid_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(
            capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
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
            )
        ),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/nulla-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-fallback",
    )

    assert ok is True
    assert message == ""


def test_validate_install_profile_passes_requested_profile_to_registry_snapshot(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)

    def _snapshot(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(capability_truth=())

    monkeypatch.setattr(validator, "build_provider_registry_snapshot", _snapshot)

    ok, _message = validator.validate_install_profile(
        runtime_home="/tmp/nulla-runtime",
        selected_model="qwen2.5:14b",
        requested_profile="full-orchestrated",
    )

    assert ok is False
    assert captured["runtime_home"] == "/tmp/nulla-runtime"
    assert captured["requested_profile"] == "full-orchestrated"
    assert captured["honor_install_profile"] is True
