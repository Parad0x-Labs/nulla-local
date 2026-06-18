from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelResponse
from core.hardware_tier import MachineProbe
from core.local_inference_autopilot import (
    build_local_inference_autopilot_plan,
    build_prefix_cache_plan,
    compile_context_capsule,
)
from core.local_inference_evidence import (
    hydrate_capability_truth_with_benchmarks,
    latest_local_inference_benchmarks,
    record_ollama_generate_benchmark,
)
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


def _capability(
    model_id: str,
    *,
    role_fit: str,
    tokens_per_second: float,
    tool_support: tuple[str, ...] = ("structured_json",),
    availability_state: str = "ready",
) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=8192,
        tool_support=tool_support,
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state=availability_state,
    )


def _local_capabilities() -> tuple[ProviderCapabilityTruth, ...]:
    return (
        _capability("qwen2.5:0.5b", role_fit="drone", tokens_per_second=313.0),
        _capability("qwen3:8b", role_fit="drone", tokens_per_second=16.4),
        _capability("qwen3:14b", role_fit="queen", tokens_per_second=11.4, tool_support=("structured_json", "code_complex")),
        _capability("qwen2.5:32b", role_fit="queen", tokens_per_second=0.3, tool_support=("structured_json", "code_complex")),
    )


def _llamacpp_capability() -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id="llamacpp-local:qwen2.5:14b-gguf",
        model_id="qwen2.5:14b-gguf",
        role_fit="drone",
        context_window=4096,
        tool_support=("structured_json", "code_complex"),
        structured_output_support=True,
        tokens_per_second=0.0,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state="ready",
    )


def test_context_capsule_redacts_private_paths_and_keeps_stable_prefix_hash_off_user_prompt() -> None:
    source_context = {
        "repo_identity": "nulla-hive-mind",
        "memory_capsule": "Web0 is local-first. Source lives at /Users/loop/private/web0.",
        "diff_summary": "Touched /Users/loop/.openclaw/openclaw.json with sk-proj-abcDEF1234567890abcDEF1234567890.",
        "constraints": ["local only", "never expose private paths"],
        "evidence_refs": ["file:///Users/loop/private/log.txt"],
    }

    first = compile_context_capsule(user_text="tell me about web0", source_context=source_context)
    second = compile_context_capsule(user_text="different current prompt", source_context=source_context)

    assert first.stable_prefix_hash == second.stable_prefix_hash
    assert "/Users/" not in first.compressed_prompt
    assert "sk-proj-" not in first.compressed_prompt
    assert "<private-path>" in first.compressed_prompt
    assert first.omitted_private_items >= 2


def test_autopilot_routes_tiny_daily_and_deep_without_defaulting_to_32b() -> None:
    capabilities = _local_capabilities()

    tiny = build_local_inference_autopilot_plan(
        user_text="classify this tool intent",
        task_kind="tool_intent",
        output_mode="tool_intent",
        provider_role="drone",
        capability_truth=capabilities,
    )
    daily = build_local_inference_autopilot_plan(
        user_text="what can we do today?",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=capabilities,
    )
    deep = build_local_inference_autopilot_plan(
        user_text="patch the local runtime and run tests",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert tiny.lane == "tiny"
    assert tiny.selected_model == "qwen2.5:0.5b"
    assert daily.lane == "daily"
    assert daily.selected_model == "qwen3:8b"
    assert deep.lane == "deep"
    assert deep.selected_model == "qwen3:14b"
    assert deep.verifier_required is True
    assert deep.verifier_model == "qwen3:8b"
    assert "qwen2.5:32b" not in {deep.selected_model, deep.verifier_model}
    assert any(action.model_id == "qwen2.5:32b" and action.action == "refuse_default" for action in deep.residency)
    assert any("oversized_lane_not_default" in warning for warning in deep.warnings)


def test_autopilot_keeps_daily_repo_work_off_deep_queen_lane() -> None:
    capabilities = _local_capabilities()

    plan = build_local_inference_autopilot_plan(
        user_text="Using workspace tools only, read pyproject.toml and summarize the project metadata.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_prefers_live_llamacpp_specialist_for_deep_with_independent_small_verifier() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="High-risk engineering task. Refactor the adaptive lane proof architecture: identify two failure modes and the minimal tests. Verifier required.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(_llamacpp_capability(), *_local_capabilities()[1:3]),
    )

    assert plan.lane == "deep"
    assert plan.selected_provider_id == "llamacpp-local:qwen2.5:14b-gguf"
    assert plan.selected_model == "qwen2.5:14b-gguf"
    assert plan.verifier_required is True
    assert plan.verifier_provider_id == "ollama-local:qwen3:8b"
    assert plan.verifier_model == "qwen3:8b"


def test_autopilot_prefers_measured_fast_nothink_default_for_daily_lane() -> None:
    capabilities = (
        _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.8),
        _capability("nulla-qwen3-30b-a3b:nothink", role_fit="drone", tokens_per_second=37.2),
    )

    daily = build_local_inference_autopilot_plan(
        user_text="build a small website draft",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=capabilities,
    )

    assert daily.lane == "daily"
    assert daily.selected_model == "nulla-qwen3-30b-a3b:nothink"
    assert not any("nulla-qwen3-30b-a3b:nothink:oversized_lane_not_default" in warning for warning in daily.warnings)


def test_autopilot_emits_apple_mlx_flags_eagle_candidate_and_sysctl_warning() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=48.0,
        gpu_name="Apple Silicon",
        vram_gb=48.0,
        accelerator="mps",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Darwin"), mock.patch(
        "core.local_inference_autopilot._read_iogpu_wired_limit_mb",
        return_value=20_000,
    ):
        plan = build_local_inference_autopilot_plan(
            user_text="write a short local helper reply",
            task_kind="normalization_assist",
            output_mode="plain_text",
            provider_role="auto",
            capability_truth=(_capability("qwen3:8b", role_fit="drone", tokens_per_second=44.0),),
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.framework == "ollama_mlx"
    assert plan.runtime_flags["OLLAMA_MLX"] == "1"
    assert plan.runtime_flags["num_gpu"] == 999
    assert plan.entropy_escalation_threshold == 0.35
    assert plan.suffix_decode_eligible is False
    assert "framework" in phase_names
    assert "eagle_candidate" in phase_names
    assert "speculative_decoding" not in phase_names
    assert "sysctl_warning" in phase_names
    assert any(phase.name == "sysctl_warning" and phase.status == "blocked" for phase in plan.phases)


def test_autopilot_marks_suffix_decode_tasks_and_does_not_stack_eagle() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Linux"):
        plan = build_local_inference_autopilot_plan(
            user_text="classify the next tool call",
            task_kind="tool_intent",
            output_mode="tool_intent",
            provider_role="drone",
            capability_truth=(
                _capability("qwen3:0.6b", role_fit="drone", tokens_per_second=220.0),
                _capability("qwen3:8b", role_fit="drone", tokens_per_second=44.0),
            ),
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.selected_model == "qwen3:0.6b"
    assert plan.framework == "llama_cpp"
    assert plan.runtime_flags["cache_type_k"] == "q8_0"
    assert plan.suffix_decode_eligible is True
    assert "suffix_decoding" in phase_names
    assert "speculative_decoding" not in phase_names
    assert "eagle_candidate" not in phase_names


def test_autopilot_adds_moe_flags_without_eagle_for_hybrid_lanes() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Linux"):
        plan = build_local_inference_autopilot_plan(
            user_text="use the heavy local lane for deep synthesis",
            task_kind="action_plan",
            output_mode="action_plan",
            provider_role="queen",
            capability_truth=(
                _capability("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=75.0),
            ),
            source_context={"autopilot_allow_heavy_model": True},
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.selected_model == "qwen3.5:35b-a3b"
    assert plan.runtime_flags["ngl"] == 999
    assert plan.runtime_flags["fit_target"] == 2048
    assert "speculative_decoding" not in phase_names
    assert "eagle_candidate" not in phase_names


def test_autopilot_blocks_35b_as_default_until_explicitly_requested() -> None:
    capabilities = (
        _capability(
            "qwen3.5:35b-a3b",
            role_fit="queen",
            tokens_per_second=1.2,
            tool_support=("structured_json", "code_complex"),
        ),
    )

    blocked = build_local_inference_autopilot_plan(
        user_text="patch this hard runtime bug",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )
    explicit = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert blocked.lane == "deep"
    assert blocked.selected_model is None
    assert blocked.verifier_model is None
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "refuse_default" for action in blocked.residency)
    assert any("oversized_lane_not_default" in warning for warning in blocked.warnings)
    assert explicit.selected_model == "qwen3.5:35b-a3b"
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "load_explicit_only" for action in explicit.residency)


def test_autopilot_blocks_explicit_heavy_when_no_heavy_lane_is_healthy() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="action_plan",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=(
            _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),
            _capability(
                "qwen3:14b",
                role_fit="queen",
                tokens_per_second=11.4,
                tool_support=("structured_json", "code_complex"),
            ),
            _capability("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=0.4, availability_state="blocked"),
        ),
    )

    assert plan.lane == "deep"
    assert plan.selected_model is None
    assert "explicit_heavy_lane_unavailable" in plan.warnings
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "refuse_default" for action in plan.residency)


def test_autopilot_does_not_substitute_32b_for_explicit_35b_request() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="action_plan",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=(
            _capability("qwen2.5:32b", role_fit="queen", tokens_per_second=0.6, tool_support=("structured_json", "code_complex")),
            _capability("qwen3:14b", role_fit="queen", tokens_per_second=11.4, tool_support=("structured_json", "code_complex")),
        ),
    )

    assert plan.selected_model is None
    assert "explicit_heavy_lane_unavailable" in plan.warnings


def test_autopilot_progress_phases_are_concrete_and_block_when_no_lane_exists() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="delete stale configs then patch code",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=tuple(),
    )

    phase_names = [phase.name for phase in plan.phases]
    assert plan.lane == "human"
    assert phase_names == ["route", "retrieve", "compress", "framework", "preload", "generate", "verify", "test", "repair"]
    assert plan.framework == "unknown"
    assert plan.runtime_flags == {}
    assert any(phase.name == "preload" and phase.status == "blocked" for phase in plan.phases)
    assert "no_local_or_provider_lane_available" in plan.warnings


def test_prefix_cache_plan_exposes_backend_specific_truth() -> None:
    llama_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="llama.cpp")
    mlx_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="mlx-lm")
    ollama_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="ollama")

    assert llama_plan.supported is True
    assert llama_plan.action == "slot_save_restore"
    assert mlx_plan.supported is True
    assert mlx_plan.action == "cache_prompt"
    assert ollama_plan.supported is False
    assert ollama_plan.action == "preload_keep_alive"


def test_local_inference_ledger_hydrates_provider_truth_for_autopilot() -> None:
    provider_id = "ollama-local:test-ledger-8b"
    fact = record_ollama_generate_benchmark(
        provider_id=provider_id,
        model_id="qwen3:8b",
        prompt="Write one short paragraph.",
        response_payload={
            "eval_count": 40,
            "eval_duration": 2_000_000_000,
            "load_duration": 120_000_000,
            "prompt_eval_duration": 80_000_000,
        },
        context_window=4096,
        processor="100% GPU",
        quantization="q4_K_M",
    )

    latest = latest_local_inference_benchmarks(provider_ids=(provider_id,), limit=4)
    hydrated = hydrate_capability_truth_with_benchmarks(
        (
            ProviderCapabilityTruth(
                provider_id=provider_id,
                model_id="qwen3:8b",
                role_fit="drone",
                context_window=0,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        )
    )
    plan = build_local_inference_autopilot_plan(
        user_text="normal daily helper answer",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=hydrated,
    )

    assert latest[0].benchmark_id == fact.benchmark_id
    assert latest[0].tokens_per_second == 20.0
    assert hydrated[0].tokens_per_second == 20.0
    assert hydrated[0].context_window == 4096
    assert hydrated[0].quantization == "q4_K_M"
    assert hydrated[0].measurement_source == "local_inference_benchmark"
    assert hydrated[0].measured_at == fact.created_at
    assert plan.evidence_refs == (f"measured:{provider_id}:tok_s=20.00",)
    assert plan.prefix_cache.backend == "ollama"


def test_local_inference_ledger_failure_keeps_manifest_truth_available() -> None:
    capability = _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0)

    with mock.patch(
        "core.local_inference_evidence.latest_local_inference_benchmarks",
        side_effect=RuntimeError("db locked"),
    ):
        hydrated = hydrate_capability_truth_with_benchmarks((capability,))

    assert hydrated == (capability,)
    assert hydrated[0].measurement_source == "manifest"


def test_memory_router_prioritizes_autopilot_lane_and_emits_runtime_plan() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    heavy = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:32b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 0.3,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    verifier = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-test-stream", events.append)

    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.estimate_cost_class.return_value = "free_local"
    adapter.get_license_metadata.return_value = {}
    adapter.run_text_task.return_value = ModelResponse(output_text="Patch plan ready.", confidence=0.78)

    task = SimpleNamespace(task_id="autopilot-task", task_summary="patch this local runtime safely")
    interpretation = SimpleNamespace(reconstructed_text="patch this local runtime safely")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.2,
        report=context_report,
    )

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[heavy, verifier]), mock.patch.object(
            router.registry,
            "build_adapter",
            return_value=adapter,
        ) as build_adapter:
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "debugging"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-task-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-test-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-test-stream")

    assert build_adapter.call_args.args[0].model_name == "qwen3:14b"
    assert decision.provider_id == verifier.provider_id
    assert decision.details["ranked_candidates"][0] == verifier.provider_id
    assert decision.details["autopilot_plan"]["selected_model"] == "qwen3:14b"
    assert decision.details["autopilot_plan"]["lane"] == "deep"
    assert events[0]["event_type"] == "model_routing_started"
    assert events[0]["autopilot_plan"]["selected_model"] == "qwen3:14b"
    proof_events = [event for event in events if event["event_type"] == "model_lane_proof"]
    assert proof_events
    proof = proof_events[-1]
    assert proof["schema"] == "nulla.model_lane_proof.v1"
    assert proof["phase"] == "completed"
    assert proof["lane"] == "deep"
    assert proof["provider_id"] == verifier.provider_id
    assert proof["actual_adapter_provider_id"] == verifier.provider_id
    assert proof["actual_adapter_model_id"] == "qwen3:14b"
    assert proof["measurement_source"] == "manifest"
    assert proof["verifier_status"] == "blocked"
    assert proof["verifier_provider_id"] == ""
    assert proof["verifier_model_id"] == ""
    assert proof["kv_cache_status"] == "ollama=not_supported_keep_alive_only"
    assert proof["speculative_status"] == "inactive"
    assert proof["eagle_status"] == "unsupported_by_backend"
    assert decision.details["lane_proof"]["schema"] == "nulla.model_lane_proof.v1"


def test_memory_router_blocks_explicit_heavy_plan_without_adapter_fallback() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=None,
        to_dict=lambda: {
            "schema": "nulla.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": None,
            "selected_model": None,
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "", "supported": False},
            "warnings": ["explicit_heavy_lane_unavailable"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-block-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-block-task", task_summary="use 35b heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the 35b heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=AssertionError("explicit heavy block must not invoke fallback adapter"),
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-block-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-block-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-block-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "autopilot_blocked"
    assert decision.used_model is False
    assert proof["phase"] == "blocked"
    assert proof["lane"] == "deep"
    assert proof["fallback_reason"] == "explicit_heavy_lane_unavailable"


def test_memory_router_blocks_missing_planned_heavy_manifest_without_fallback() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id="ollama-local:qwen3.5:35b-a3b",
        to_dict=lambda: {
            "schema": "nulla.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": "ollama-local:qwen3.5:35b-a3b",
            "selected_model": "qwen3.5:35b-a3b",
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "ollama", "supported": False},
            "warnings": [],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-missing-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-missing-task", task_summary="use 35b heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the 35b heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=AssertionError("missing planned heavy provider must not invoke fallback adapter"),
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-missing-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-missing-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-missing-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "autopilot_blocked"
    assert proof["phase"] == "blocked"
    assert proof["planned_model_id"] == "qwen3.5:35b-a3b"
    assert proof["fallback_reason"] == "explicit_heavy_lane_unavailable"


def test_memory_router_does_not_fallback_after_planned_heavy_lane_failure() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    heavy = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:32b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 0.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    heavy_adapter = mock.Mock()
    heavy_adapter.health_check.return_value = {"ok": True}
    heavy_adapter.run_structured_task.side_effect = RuntimeError("too slow")
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=heavy.provider_id,
        to_dict=lambda: {
            "schema": "nulla.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": heavy.provider_id,
            "selected_model": heavy.model_name,
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "ollama", "supported": False},
            "warnings": [],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-failed-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-failed-task", task_summary="use heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
        if manifest.provider_id == heavy.provider_id:
            return heavy_adapter
        raise AssertionError("planned heavy failure must not invoke smaller fallback adapter")

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[heavy, fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=build_adapter,
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-failed-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-failed-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-failed-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "explicit_heavy_lane_failed"
    assert proof["phase"] == "failed"
    assert proof["provider_id"] == heavy.provider_id
    assert proof["fallback_reason"] == "explicit_heavy_lane_failed:too slow"


def test_memory_router_marks_planned_actual_lane_mismatch_as_failed_proof() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    actual = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "drone",
            "tokens_per_second": 18.0,
            "tool_support": ["structured_json"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-mismatch-stream", events.append)

    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.estimate_cost_class.return_value = "free_local"
    adapter.get_license_metadata.return_value = {}
    adapter.run_text_task.return_value = ModelResponse(output_text="Fallback answer.", confidence=0.72)
    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id="ollama-local:qwen3:14b",
        to_dict=lambda: {
            "schema": "nulla.local_inference_autopilot.v1",
            "lane": "daily",
            "selected_provider_id": "ollama-local:qwen3:14b",
            "selected_model": "qwen3:14b",
            "verifier_required": False,
            "verifier_provider_id": None,
            "prefix_cache": {
                "backend": "ollama",
                "supported": False,
            },
        },
    )
    task = SimpleNamespace(task_id="autopilot-mismatch-task", task_summary="answer simply")
    interpretation = SimpleNamespace(reconstructed_text="answer simply")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[actual]), mock.patch.object(
            router.registry,
            "build_adapter",
            return_value=adapter,
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "quick_answer"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-mismatch-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="auto",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-mismatch-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-mismatch-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert proof["phase"] == "failed"
    assert proof["mismatch"] is True
    assert proof["failure_reason"] == "planned_adapter_mismatch"
    assert proof["planned_provider_id"] == "ollama-local:qwen3:14b"
    assert proof["actual_adapter_provider_id"] == actual.provider_id
    assert decision.details["lane_proof"]["mismatch"] is True


def test_memory_router_invokes_independent_verifier_lane_before_final_proof() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    primary = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "drone",
            "tokens_per_second": 18.0,
            "tool_support": ["structured_json"],
        },
    )
    verifier = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-verifier-stream", events.append)

    primary_adapter = mock.Mock()
    primary_adapter.health_check.return_value = {"ok": True}
    primary_adapter.supports_streaming.return_value = False
    primary_adapter.estimate_cost_class.return_value = "free_local"
    primary_adapter.get_license_metadata.return_value = {}
    primary_adapter.run_text_task.return_value = ModelResponse(output_text="Primary patch plan.", confidence=0.75)
    verifier_adapter = mock.Mock()
    verifier_adapter.health_check.return_value = {"ok": True}
    verifier_adapter.supports_streaming.return_value = False
    verifier_adapter.run_text_task.return_value = ModelResponse(output_text="Verifier found no blockers.", confidence=0.7)
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=primary.provider_id,
        to_dict=lambda: {
            "schema": "nulla.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": primary.provider_id,
            "selected_model": primary.model_name,
            "verifier_required": True,
            "verifier_provider_id": verifier.provider_id,
            "verifier_model": verifier.model_name,
            "prefix_cache": {"backend": "ollama", "supported": False},
        },
    )
    task = SimpleNamespace(task_id="autopilot-verifier-task", task_summary="review a risky patch plan")
    interpretation = SimpleNamespace(reconstructed_text="review a risky patch plan")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[primary, verifier]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=[primary_adapter, verifier_adapter],
        ) as build_adapter, mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "debugging"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-verifier-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-verifier-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-verifier-stream")

    assert [call.args[0].provider_id for call in build_adapter.call_args_list] == [
        primary.provider_id,
        verifier.provider_id,
    ]
    assert verifier_adapter.run_text_task.call_count == 1
    verifier_events = [event for event in events if event["event_type"].startswith("model_lane_verifier_")]
    assert [event["event_type"] for event in verifier_events] == [
        "model_lane_verifier_started",
        "model_lane_verifier_completed",
    ]
    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert proof["phase"] == "completed"
    assert proof["lane"] == "deep"
    assert proof["provider_id"] == primary.provider_id
    assert proof["verifier_status"] == "independent_completed"
    assert decision.details["lane_proof"]["verifier_status"] == "independent_completed"
