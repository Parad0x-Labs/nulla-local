from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.hardware_tier import MachineProbe

# Speed-tier roles describe throughput character (orthogonal to the older
# general/coding/reasoning/heavy_reasoning/lightweight_utility roles, which describe
# content specialization). Every capacity bucket A-E resolves to exactly these three
# roles so no hardware tier — including the weakest — collapses to a single model.
SPEED_TIER_ROLES: tuple[str, ...] = ("tiny_fast", "daily_accelerated", "deep_overnight")


@dataclass(frozen=True)
class BundleRoleModel:
    role: str
    model: str
    backend: str = "ollama"  # "ollama" | "llamacpp"
    expected_tokens_per_second: float = 0.0  # 0.0 = unmeasured; do not print a number
    requires_gpu_backend: bool = False  # only include this role if a live-verified GPU backend exists
    offload_note: str = ""  # e.g. "Partial GPU offload; expect ~10-14 tok/s, fine for overnight batch use."

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "backend": self.backend,
            "expected_tokens_per_second": self.expected_tokens_per_second,
            "requires_gpu_backend": self.requires_gpu_backend,
            "offload_note": self.offload_note,
        }


@dataclass(frozen=True)
class LocalBundleSpec:
    bundle_id: str
    kind: str
    display_name: str
    role_models: tuple[BundleRoleModel, ...]
    summary: str
    gpu_conditional: bool = False  # True => this spec assumes a live-verified llama.cpp GPU backend

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(item.model for item in self.role_models)

    @property
    def role_map(self) -> dict[str, str]:
        return {item.role: item.model for item in self.role_models}

    @property
    def primary_model(self) -> str:
        role_map = self.role_map
        return (
            role_map.get("general")
            or role_map.get("daily_accelerated")
            or role_map.get("coding")
            or role_map.get("reasoning")
            or next(iter(role_map.values()), "qwen3:8b")
        )


@dataclass(frozen=True)
class LocalBundleRecommendation:
    capacity_bucket: str
    local_multi_llm_fit: str
    free_disk_gb: float
    recommended_bundle: LocalBundleSpec
    fallback_bundle: LocalBundleSpec
    safe_disk_floor_gb: float
    advanced_optional_allowed: bool
    advanced_optional_profile: str
    selection_reasons: tuple[str, ...]
    legacy_mode: bool = False
    gpu_capability_used: bool = False


MODEL_STORAGE_GB: dict[str, float] = {
    "qwen3:0.6b": 0.7,
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3:14b": 9.3,
    "qwen3:30b": 19.0,
    "qwen3:30b-a3b": 19.0,
    "nulla-qwen3-30b-a3b:nothink": 19.0,
    "qwen3.5:35b-a3b": 23.0,
    "deepseek-r1:8b": 5.2,
    "deepseek-r1:14b": 9.0,
    "deepseek-r1:32b": 20.0,
    "gemma3:4b": 3.3,
    "gemma3:12b": 8.1,
    "gemma3:12b-qat": 8.1,
    "mistral-small:24b": 14.0,
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:14b-gguf": 18.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
    "nomic-embed-text": 0.3,
    "nomic-embed-text:latest": 0.3,
    # llama.cpp-served GGUF quants for the GPU-accelerated daily/deep tiers. These are
    # the exact model class measured at ~35-36 tok/s via full GPU offload on a legacy
    # GTX 1080 test host (see core/llamacpp_capability_probe.py for the live check that
    # gates when these are actually offered, instead of trusting a GPU name heuristic).
    "qwen2.5:7b-instruct-q4_k_m": 4.7,
    "qwen2.5:14b-instruct-q4_k_m": 9.0,
    "qwen2.5:32b-instruct-q4_k_m": 20.0,
    "deepseek-r1:14b-qwen-distill-q4_k_m": 9.5,
    "qwen3:30b-a3b-q4_k_m": 18.5,
}

# GGUF source registry for llama.cpp-served models, keyed by the same logical model
# name used in BundleRoleModel.model so lookups stay uniform with MODEL_STORAGE_GB.
# llama.cpp doesn't consume Ollama's name:tag registry — it needs a HF repo + filename.
GGUF_MODEL_SOURCES: dict[str, dict[str, str]] = {
    "qwen2.5:7b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
    },
    "qwen2.5:14b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "filename": "qwen2.5-14b-instruct-q4_k_m.gguf",
    },
    "qwen2.5:32b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "filename": "qwen2.5-32b-instruct-q4_k_m.gguf",
    },
    "deepseek-r1:14b-qwen-distill-q4_k_m": {
        "repo": "unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
    },
    "qwen3:30b-a3b-q4_k_m": {
        "repo": "Qwen/Qwen3-30B-A3B-GGUF",
        "filename": "Qwen3-30B-A3B-Q4_K_M.gguf",
    },
}

MODEL_METADATA: dict[str, dict[str, Any]] = {
    "qwen3:0.6b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "0.6B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:4b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "4B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:8b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "8B",
        "bundle_role": "general",
        "eagle3_draft_eligible": True,
    },
    "qwen3:14b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "14B",
        "bundle_role": "coding",
        "eagle3_draft_eligible": True,
    },
    "qwen3:30b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
    },
    "qwen3:30b-a3b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
    },
    "nulla-qwen3-30b-a3b:nothink": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "user-managed local Ollama model",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
        "thinking_disabled": True,
        "tokens_per_second": 37.2,
        "quantization": "local-measured",
        "notes": "Measured fast local no-think default when installed.",
    },
    "qwen3.5:35b-a3b": {
        "family": "qwen3.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3.5",
        "parameter_count": "35B",
        "active_parameter_count": "3.3B",
        "architecture": "hybrid_moe",
        "bundle_role": "heavy_reasoning",
        "eagle3_draft_eligible": False,
        "constant_vram": True,
        "max_context": 262144,
    },
    "deepseek-r1:8b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "8B",
    },
    "deepseek-r1:14b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "14B",
    },
    "deepseek-r1:32b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "32B",
    },
    "gemma3:4b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "4B",
    },
    "gemma3:12b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
    },
    "gemma3:12b-qat": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
        "bundle_role": "general",
        "qat": True,
        "eagle3_draft_eligible": False,
    },
    "mistral-small:24b": {
        "family": "mistral-small",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/mistral-small",
        "parameter_count": "24B",
    },
    "qwen2.5:0.5b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "0.5B",
    },
    "qwen2.5:3b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "3B",
    },
    "qwen2.5:7b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "7B",
    },
    "qwen2.5:14b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "14B",
    },
    "qwen2.5:32b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "32B",
    },
    "qwen2.5:7b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF",
        "parameter_count": "7B",
        "bundle_role": "daily_accelerated",
        "eagle3_draft_eligible": False,
    },
    "qwen2.5:14b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF",
        "parameter_count": "14B",
        "bundle_role": "daily_accelerated",
    },
    "qwen2.5:32b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-32B-Instruct-GGUF",
        "parameter_count": "32B",
        "bundle_role": "deep_overnight",
    },
    "deepseek-r1:14b-qwen-distill-q4_k_m": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF",
        "parameter_count": "14B",
        "bundle_role": "deep_overnight",
    },
    "qwen3:30b-a3b-q4_k_m": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "deep_overnight",
    },
}


LOCAL_BUNDLE_SPECS: dict[str, LocalBundleSpec] = {
    "single_gemma3_4b": LocalBundleSpec(
        bundle_id="single_gemma3_4b",
        kind="single",
        display_name="Single S2 lightweight fallback",
        role_models=(BundleRoleModel("general", "gemma3:4b"),),
        summary="Single lightweight fallback for constrained hosts.",
    ),
    "single_qwen3_4b": LocalBundleSpec(
        bundle_id="single_qwen3_4b",
        kind="single",
        display_name="Single lightweight Qwen fallback",
        role_models=(BundleRoleModel("general", "qwen3:4b"),),
        summary="Single lightweight Qwen fallback when Gemma is not preferred.",
    ),
    "single_qwen3_8b": LocalBundleSpec(
        bundle_id="single_qwen3_8b",
        kind="single",
        display_name="Single S1 safest default",
        role_models=(BundleRoleModel("general", "qwen3:8b"),),
        summary="Single best-overall local model for constrained-but-capable hosts.",
    ),
    "dual_qwen3_8b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D1 balanced default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Balanced local pair for general companion work plus deeper reasoning and review.",
    ),
    "dual_qwen3_8b_gemma3_4b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_gemma3_4b",
        kind="dual",
        display_name="Dual D3 lighter fallback",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("lightweight_utility", "gemma3:4b"),
        ),
        summary="Lighter local pair with a cheap utility backup lane.",
    ),
    "dual_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_mistral_small_24b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D2 coding and reasoning",
        role_models=(
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Stronger local dual for code/tool tasks plus second-pass reasoning.",
    ),
    "triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b",
        kind="triple",
        display_name="Triple T1 practical default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Practical triple with explicit general, coding, and reasoning lanes.",
    ),
    "triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b": LocalBundleSpec(
        bundle_id="triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b",
        kind="triple",
        display_name="Triple T3 enthusiast",
        role_models=(
            BundleRoleModel("general", "qwen3:14b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:14b"),
        ),
        summary="High-end triple for strong hosts with clear role separation.",
    ),
    "goblin_stack": LocalBundleSpec(
        bundle_id="goblin_stack",
        kind="triple",
        display_name="Goblin local stack",
        role_models=(
            BundleRoleModel("lightweight_utility", "qwen3:0.6b"),
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("heavy_reasoning", "qwen3.5:35b-a3b"),
        ),
        summary="Qwen3-family local stack with tiny routing, daily workhorse, and hybrid-MoE deep lane.",
    ),

    # ------------------------------------------------------------------
    # Always-3-tier bucket bundles (tiny_fast / daily_accelerated / deep_overnight).
    # Every bucket A-E gets both a no-GPU (CPU-only, always safe) variant and a
    # gpu_conditional variant that is only ever selected once
    # core.llamacpp_capability_probe.probe_llamacpp_capability() has *measured* real
    # speedup on this exact host — never based on a GPU name heuristic alone. A weak
    # host (bucket A) with a live-verified GPU can genuinely beat a stronger host
    # (bucket C/D) that has no working GPU backend on the daily lane. deep_overnight
    # is honestly allowed to be slow in every row; it exists for tasks queued and
    # left running, not live chat.
    # ------------------------------------------------------------------

    "triple_bucket_a_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_a_no_gpu",
        kind="triple",
        display_name="Bucket A - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=15.0),
            BundleRoleModel("daily_accelerated", "qwen3:4b", expected_tokens_per_second=5.0),
            BundleRoleModel(
                "deep_overnight", "gemma3:4b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Weakest-hardware tier still gets a fast/daily/deep spread, sized for RAM-only inference.",
    ),
    "triple_bucket_a_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_a_gpu_accelerated",
        kind="triple",
        display_name="Bucket A - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=15.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:7b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=35.5, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "deepseek-r1:14b-qwen-distill-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=12.0, requires_gpu_backend=True,
                offload_note="Partial GPU offload; larger reasoning model queued for overnight/batch use.",
            ),
        ),
        gpu_conditional=True,
        summary="Same weak-RAM host, but a live-verified llama.cpp GPU backend unlocks a materially faster daily lane and a real overnight deep lane.",
    ),
    "triple_bucket_b_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_b_no_gpu",
        kind="triple",
        display_name="Bucket B - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=17.0),
            BundleRoleModel("daily_accelerated", "qwen3:8b", expected_tokens_per_second=7.0),
            BundleRoleModel(
                "deep_overnight", "deepseek-r1:14b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket B without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_b_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_b_gpu_accelerated",
        kind="triple",
        display_name="Bucket B - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=17.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:7b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=33.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=17.0, requires_gpu_backend=True,
                offload_note="Near-full GPU offload; comfortably faster deep lane than CPU-only.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket B with a live-verified GPU backend: fast daily lane plus a genuinely usable deep lane.",
    ),
    "triple_bucket_c_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_c_no_gpu",
        kind="triple",
        display_name="Bucket C - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=21.0),
            BundleRoleModel("daily_accelerated", "qwen3:8b", expected_tokens_per_second=8.0),
            BundleRoleModel(
                "deep_overnight", "qwen3:14b", expected_tokens_per_second=5.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket C without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_c_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_c_gpu_accelerated",
        kind="triple",
        display_name="Bucket C - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=21.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=25.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=10.0, requires_gpu_backend=True,
                offload_note="Partial GPU offload; a 32B model won't fully fit under 16GB VRAM, still faster than CPU.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket C with a live-verified GPU backend: a stronger daily lane plus a genuinely usable heavy deep lane.",
    ),
    "triple_bucket_d_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_d_no_gpu",
        kind="triple",
        display_name="Bucket D - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=23.0),
            BundleRoleModel("daily_accelerated", "qwen3:14b", expected_tokens_per_second=7.5),
            BundleRoleModel(
                "deep_overnight", "mistral-small:24b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket D without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_d_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_d_gpu_accelerated",
        kind="triple",
        display_name="Bucket D - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=23.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=29.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen3:30b-a3b-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=21.0, requires_gpu_backend=True,
                offload_note="MoE model with ~3B active params; stays fast even as the deep lane via CPU-expert-offload.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket D with a live-verified GPU backend: strong daily lane plus a fast MoE-based deep lane.",
    ),
    "triple_bucket_e_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_e_no_gpu",
        kind="triple",
        display_name="Bucket E - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=25.0),
            BundleRoleModel("daily_accelerated", "qwen3:14b", expected_tokens_per_second=10.0),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b", expected_tokens_per_second=5.5,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket E without a working GPU backend: a strong CPU host still gets a full fast/daily/deep spread.",
    ),
    "triple_bucket_e_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_e_gpu_accelerated",
        kind="triple",
        display_name="Bucket E - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=25.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=35.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=17.0, requires_gpu_backend=True,
                offload_note="Full GPU offload; comfortably fits under >=24GB VRAM.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket E with a live-verified GPU backend: the fastest daily lane and a genuinely fast deep lane too.",
    ),
}


def model_storage_gb(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    if clean in MODEL_STORAGE_GB:
        return float(MODEL_STORAGE_GB[clean])

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return round(max(1.0, float(match.group(1)) * 0.75), 1)
    return 8.0


def model_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return 8.0


def model_active_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("active_parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    match = re.search(r"-a(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return model_parameter_billions(clean)


def model_metadata(model_name: str) -> dict[str, Any]:
    clean = str(model_name or "").strip().lower()
    return dict(MODEL_METADATA.get(clean) or {})


def installed_ollama_role_for_model(*, model_name: str, primary_model: str = "") -> str:
    clean = str(model_name or "").strip().lower()
    primary = str(primary_model or "").strip().lower()
    if primary and clean == primary:
        return "general"
    if "mistral-small" in clean or "coder" in clean or "code" in clean:
        return "coding"
    if "deepseek-r1" in clean or "reason" in clean:
        return "reasoning"
    parameter_b = model_parameter_billions(clean)
    if parameter_b <= 3.0:
        return "lightweight_utility"
    if parameter_b >= 24.0:
        return "heavy_reasoning"
    if parameter_b >= 13.0:
        return "reasoning"
    return "general"


def safe_disk_floor_gb(models: tuple[str, ...] | list[str]) -> float:
    total_size = sum(model_storage_gb(item) for item in models if str(item).strip())
    return round(max(total_size * 2.0, total_size + 25.0), 1)


def llamacpp_offload_layers(*, model_name: str, vram_gb: float, layer_count_hint: int = 0) -> int:
    """Returns the -ngl value for llama.cpp given a model and available VRAM.

    999 means "offload everything, it fits." A smaller value means partial offload:
    that many layers go to GPU, the rest stay in CPU RAM and llama.cpp handles the
    split. This is what lets deep_overnight stay honestly labeled — a model too big
    for VRAM still runs, just slower, instead of either refusing to load or silently
    claiming full-GPU speed it can't deliver.
    """
    if vram_gb <= 0:
        return 0
    model_gb = model_storage_gb(model_name)
    if model_gb <= 0:
        return 999
    if vram_gb >= model_gb * 1.15:  # comfortable headroom left over for KV cache
        return 999
    total_layers = layer_count_hint or _estimate_layer_count(model_name)
    usable_vram = max(0.5, vram_gb - 1.5)  # reserve ~1.5GB for KV cache / daily-lane coexistence
    fraction = min(1.0, usable_vram / model_gb)
    return max(1, int(total_layers * fraction))


def _estimate_layer_count(model_name: str) -> int:
    # Rough heuristic: dense transformer layer counts scale close to linearly with
    # parameter count in this size range (roughly 1.4-1.7 layers per billion params
    # for the Qwen/DeepSeek/Mistral families used here). Good enough for sizing a
    # partial-offload split; llama.cpp's own metadata is authoritative at load time.
    params_b = model_parameter_billions(model_name)
    return max(1, round(params_b * 1.6))


def bundle_spec(bundle_id: str) -> LocalBundleSpec:
    return LOCAL_BUNDLE_SPECS[bundle_id]


def local_multi_llm_fit_from_probe(probe: MachineProbe | Mapping[str, Any]) -> str:
    ram_gb = _probe_ram_gb(probe)
    accelerator = _probe_accelerator(probe)
    vram_gb = _probe_effective_vram_gb(probe)
    if accelerator == "mps":
        if ram_gb >= 48.0:
            return "comfortable"
        if ram_gb >= 24.0:
            return "pressure_sensitive"
        return "single_model_only"
    if vram_gb >= 20.0 or ram_gb >= 48.0:
        return "comfortable"
    if vram_gb >= 10.0 or ram_gb >= 24.0:
        return "pressure_sensitive"
    return "single_model_only"


def capacity_bucket_for_machine(*, probe: MachineProbe | Mapping[str, Any], free_disk_gb: float) -> str:
    ram_gb = _probe_ram_gb(probe)
    accelerator = _probe_accelerator(probe)
    vram_gb = _probe_effective_vram_gb(probe)
    effective_vram = ram_gb if accelerator == "mps" else vram_gb
    free_gb = float(free_disk_gb or 0.0)
    if ram_gb < 16.0 or effective_vram < 6.0 or free_gb < 20.0:
        return "A"
    if ram_gb < 24.0 or effective_vram < 10.0 or free_gb < 40.0:
        return "B"
    if ram_gb < 32.0 or effective_vram < 16.0 or free_gb < 80.0:
        return "C"
    if ram_gb < 48.0 or effective_vram < 24.0 or free_gb < 150.0:
        return "D"
    return "E"


def resolve_local_bundle_recommendation(
    *,
    probe: MachineProbe | Mapping[str, Any],
    free_disk_gb: float,
    secondary_local_model_name: str,
    selected_model: str = "",
    gpu_capability: Any = None,
) -> LocalBundleRecommendation:
    """Every capacity bucket A-E always resolves to a 3-role tiny_fast/daily_accelerated/
    deep_overnight bundle — no bucket, including the weakest, collapses to a single
    model. gpu_capability, when provided, should be a
    core.llamacpp_capability_probe.CapabilityProbeResult (or None). It is intentionally
    typed loosely here to avoid a hard import dependency for callers that never probe.
    The GPU-accelerated variant of the bucket's bundle is only ever selected when a
    live probe result says `usable=True` — never from a GPU name heuristic alone. With
    no probe result (gpu_capability=None) or a probe that rejected the GPU, the
    CPU-only variant is used, which is still a full 3-tier spread, just RAM-sized."""
    explicit_model = str(selected_model or "").strip()
    fit = local_multi_llm_fit_from_probe(probe)
    bucket = capacity_bucket_for_machine(probe=probe, free_disk_gb=free_disk_gb)
    advanced_allowed = fit != "single_model_only" and free_disk_gb >= model_storage_gb(secondary_local_model_name) + 8.0
    gpu_usable = bool(gpu_capability is not None and getattr(gpu_capability, "usable", False))

    if explicit_model:
        legacy_bundle = LocalBundleSpec(
            bundle_id="legacy_single_explicit_model",
            kind="single",
            display_name="Explicit single model",
            role_models=(BundleRoleModel("general", explicit_model),),
            summary="Preserve an explicitly selected local model without silently replacing it.",
        )
        fallback_bundle = bundle_spec("dual_qwen3_8b_gemma3_4b")
        reasons = (
            f"Explicit primary model `{explicit_model}` is preserved instead of silently switching the runtime to a new bundle.",
            "Bundle auto-selection remains available for fresh installs that do not pin a legacy model.",
        )
        return LocalBundleRecommendation(
            capacity_bucket=bucket,
            local_multi_llm_fit=fit,
            free_disk_gb=round(float(free_disk_gb), 1),
            recommended_bundle=legacy_bundle,
            fallback_bundle=fallback_bundle,
            safe_disk_floor_gb=safe_disk_floor_gb(legacy_bundle.models),
            advanced_optional_allowed=advanced_allowed,
            advanced_optional_profile="local-max" if advanced_allowed else "",
            selection_reasons=reasons,
            legacy_mode=True,
        )

    bucket_key = bucket.lower()
    no_gpu_bundle = bundle_spec(f"triple_bucket_{bucket_key}_no_gpu")
    gpu_bundle = bundle_spec(f"triple_bucket_{bucket_key}_gpu_accelerated")
    if gpu_usable:
        recommended = gpu_bundle
        fallback = no_gpu_bundle  # safe degrade if the GPU later becomes unavailable
        reasons = (
            f"Capacity bucket {bucket} has a live-verified llama.cpp GPU backend "
            f"(measured {getattr(gpu_capability, 'gpu_tokens_per_second', 0.0):.1f} tok/s, "
            f"{getattr(gpu_capability, 'speedup_ratio', 0.0):.1f}x over CPU baseline), so the daily and deep "
            "lanes are both GPU-accelerated instead of RAM-sized.",
            "Every bucket always resolves to a tiny_fast/daily_accelerated/deep_overnight spread, "
            "never a single model, regardless of GPU state.",
        )
    else:
        recommended = no_gpu_bundle
        fallback = no_gpu_bundle
        reasons = (
            f"Capacity bucket {bucket} has no live-verified GPU-accelerated backend, so the "
            "tiny_fast/daily_accelerated/deep_overnight spread is sized for RAM-only inference.",
            "A GPU name alone is never enough to promise acceleration — only a measured "
            "llama.cpp benchmark (core.llamacpp_capability_probe) can unlock the GPU-accelerated variant.",
        )

    return LocalBundleRecommendation(
        capacity_bucket=bucket,
        local_multi_llm_fit=fit,
        free_disk_gb=round(float(free_disk_gb), 1),
        recommended_bundle=recommended,
        fallback_bundle=fallback,
        safe_disk_floor_gb=safe_disk_floor_gb(recommended.models),
        advanced_optional_allowed=advanced_allowed,
        advanced_optional_profile="local-max" if advanced_allowed else "",
        selection_reasons=reasons,
        gpu_capability_used=gpu_usable,
    )


def provider_role_for_bundle_role(bundle_role: str) -> str:
    clean = str(bundle_role or "").strip().lower()
    if clean in {"reasoning", "heavy_reasoning", "deep_overnight"}:
        return "queen"
    return "drone"


def manifest_profile_for_model(*, model_name: str, bundle_role: str) -> dict[str, Any]:
    metadata = model_metadata(model_name)
    family = str(metadata.get("family") or model_name).strip()
    clean_role = str(bundle_role or "general").strip().lower()
    capabilities = ["summarize", "classify", "format", "extract", "structured_json"]
    tool_support = ["structured_json"]
    confidence = 0.67
    notes = "General local Ollama lane."
    if clean_role == "general":
        capabilities.extend(["code_basic", "tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.72
        notes = "General local Ollama companion lane."
    elif clean_role == "coding":
        capabilities.extend(["code_basic", "code_complex", "tool_intent"])
        tool_support.extend(["tool_calls", "code_complex"])
        confidence = 0.77
        notes = "Coding-focused local Ollama lane."
    elif clean_role == "reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.79
        notes = "Reasoning and review local Ollama lane."
    elif clean_role == "heavy_reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.73
        notes = "Oversized local Ollama lane for explicit deep work."
    elif clean_role == "lightweight_utility":
        capabilities.extend(["tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.63
        notes = "Lightweight local Ollama utility lane for cheap classification and tool intent."
    elif clean_role == "tiny_fast":
        capabilities.extend(["tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.6
        notes = "Always-resident tiny local lane for instant classification, tool intent, and formatting."
    elif clean_role == "daily_accelerated":
        capabilities.extend(["code_basic", "tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.74
        notes = "Daily-driver local lane, GPU-accelerated via llama.cpp when a live-verified backend exists."
    elif clean_role == "deep_overnight":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.7
        notes = "Slow-but-powerful overnight/batch local lane; honestly framed as non-interactive-speed."
    profile = {
        "family": family,
        "license_name": str(metadata.get("license_name") or "user-managed"),
        "license_reference": str(metadata.get("license_reference") or "user-managed"),
        "parameter_count": str(metadata.get("parameter_count") or "").strip(),
        "capabilities": tuple(dict.fromkeys(capabilities)),
        "tool_support": tuple(dict.fromkeys(tool_support)),
        "confidence_baseline": confidence,
        "notes": notes,
        "orchestration_role": provider_role_for_bundle_role(clean_role),
        "bundle_role": clean_role,
    }
    for key in ("tokens_per_second", "quantization"):
        if metadata.get(key) not in (None, ""):
            profile[key] = metadata[key]
    return profile


def _probe_ram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    if isinstance(probe, Mapping):
        return float(probe.get("ram_gb") or 0.0)
    return float(probe.ram_gb or 0.0)


def _probe_accelerator(probe: MachineProbe | Mapping[str, Any]) -> str:
    if isinstance(probe, Mapping):
        return str(probe.get("accelerator") or "").strip().lower()
    return str(probe.accelerator or "").strip().lower()


def _probe_effective_vram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    accelerator = _probe_accelerator(probe)
    if accelerator not in {"cuda", "directml"}:
        return 0.0
    if isinstance(probe, Mapping):
        raw_vram = probe.get("vram_gb")
        return float(raw_vram or 0.0) if raw_vram is not None else 0.0
    return float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0


__all__ = [
    "GGUF_MODEL_SOURCES",
    "LOCAL_BUNDLE_SPECS",
    "MODEL_METADATA",
    "MODEL_STORAGE_GB",
    "SPEED_TIER_ROLES",
    "BundleRoleModel",
    "LocalBundleRecommendation",
    "LocalBundleSpec",
    "bundle_spec",
    "capacity_bucket_for_machine",
    "installed_ollama_role_for_model",
    "llamacpp_offload_layers",
    "local_multi_llm_fit_from_probe",
    "manifest_profile_for_model",
    "model_active_parameter_billions",
    "model_metadata",
    "model_parameter_billions",
    "model_storage_gb",
    "provider_role_for_bundle_role",
    "resolve_local_bundle_recommendation",
    "safe_disk_floor_gb",
]
