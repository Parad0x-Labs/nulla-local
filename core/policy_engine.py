from __future__ import annotations

import os
from threading import RLock
from typing import Any

import yaml

from core.privacy_guard import shard_privacy_risks, share_scope_is_public
from core.runtime_paths import config_path

_DEFAULT_POLICY = {
    "system": {
        "advice_only_default": True,
        "local_only_mode": False,
        "allow_web_fallback": False,
        "allow_null_dial": False,
        "allow_swarm_queries": True,
        "max_datagram_bytes": 32768,
        "max_message_bytes": 262144,
        "enable_udp_fragmentation": True,
        "enable_stream_data_plane": True,
        "stream_transfer_threshold_bytes": 24576,
        "max_fragment_buckets": 2048,
        "mesh_psk_b64": "",
        "require_mesh_encryption": False,
        "stream_tls_enabled": False,
        "stream_tls_certfile": "",
        "stream_tls_keyfile": "",
        "stream_tls_ca_file": "",
        "stream_tls_require_client_cert": False,
        "stream_tls_insecure_skip_verify": False,
        "allow_remote_only_without_backend": True,
        "nonce_cache_max_age_hours": 48,
        "nonce_cache_max_rows": 200000,
        "max_candidates_per_query": 5,
        "quarantine_on_invalid_signature": True,
        "quarantine_on_schema_abuse": True,
    },
    "personality": {
        "persona_core_locked": True,
        "persona_update_from_swarm": False,
        "persona_update_from_web": False,
        "spirit_anchor_required": True,
        "allow_local_persona_tuning": True,
        "allow_auto_persona_drift": False,
    },
    "execution": {
        "default_mode": "advice_only",
        "allow_simulation": True,
        "allow_sandbox_execution": True,
        "allow_safe_local_actions": True,
        "allowed_safe_local_actions": [
            "inspect_disk_usage",
            "cleanup_temp_files",
            "inspect_processes",
            "inspect_services",
            "move_path",
            "schedule_calendar_event",
        ],
        "require_explicit_user_approval_for_execution": True,
        "allow_dependency_install": False,
        "allow_privileged_actions": False,
        "allow_network_access_during_execution": False,
        "max_subprocess_seconds": 10,
        "max_subprocess_output_kb": 256,
    },
    "filesystem": {
        "workspace_roots": ["./workspace", "./tmp"],
        "allow_read_workspace": True,
        "allow_write_workspace": True,
        "deny_paths": [
            "/etc",
            "/boot",
            "/sys",
            "/proc",
            "/System",
            "/Library",
            "C:\\Windows",
            "C:\\Program Files",
        ],
    },
    "hive": {
        "public_mode": False,
    },
    "network": {
        "inbound_enabled": True,
        "outbound_enabled": True,
        "max_peers": 64,
        "max_requests_per_minute_per_peer": 30,
        "max_failed_messages_before_quarantine": 3,
        "allow_reputation_hints": False,
        "pow_min_difficulty": 4,
        "report_abuse_gossip_ttl": 2,
        "report_abuse_gossip_fanout": 8,
    },
    "orchestration": {
        "max_subtasks_per_parent": 3,
        "max_helpers_per_subtask": 1,
        "max_subtasks_hard_cap": 10,
        "max_helpers_hard_cap": 10,
        "enable_local_worker_pool_when_swarm_empty": True,
        "local_loopback_offer_on_no_helpers": True,
        "local_worker_auto_detect": True,
        "local_worker_pool_target": 0,
        "local_worker_pool_max": 10,
    },
    "model_orchestration": {
        "drone_provider_hint": "qwen",
        "queen_provider_hint": "kimi",
        "drone_swarm_width": 2,
        "queen_swarm_width": 1,
        "queen_allow_paid_fallback": True,
    },
    "observability": {
        "log_level": "INFO",
        "json_logs": True,
        "metrics_enabled": True,
    },
    "shards": {
        "new_shards_start_untrusted": True,
        "require_signature_for_remote_shards": True,
        "require_schema_version": 1,
        "allow_raw_code_in_shards": False,
        "allow_paths_in_shards": False,
        "allow_usernames_in_shards": False,
        "allow_tokens_in_shards": False,
        "allow_contact_details_in_shards": False,
        "allow_identity_facts_in_shards": False,
        "force_abstract_resolution_patterns": True,
        "default_expiry_days": 90,
        "quarantine_if_risk_flags_include": [
            "destructive_command",
            "privileged_action",
            "persistence_attempt",
            "exfiltration_hint",
        ],
    },
    "web": {
        "cache_notes": True,
        "web_notes_not_authoritative": True,
        "convert_web_note_to_shard_only_after_local_success": True,
        "allowed_engines": ["searxng", "ddg_instant", "duckduckgo_html"],
        "provider_order": ["searxng", "ddg_instant", "duckduckgo_html"],
        "searxng_url": "http://127.0.0.1:8080",
        "searxng_timeout_seconds": 12.0,
        "playwright_enabled": False,
        "browser_engine": "chromium",
        "allow_browser_fallback": True,
        "max_fetch_bytes": 2000000,
        "blocked_domains": [],
    },
    "curiosity": {
        "enabled": True,
        "mode": "bounded_auto",
        "auto_execute_task_classes": ["research", "system_design"],
        "max_topics_per_task": 2,
        "max_queries_per_topic": 4,
        "max_snippets_per_query": 5,
        "prefer_metadata_first": True,
        "allow_news_pulse": True,
        "news_max_topics_per_task": 1,
        "technical_max_topics_per_task": 2,
        "min_interest_score": 0.56,
        "min_understanding_confidence": 0.50,
        "skip_if_retrieval_confidence_at_least": 0.84,
        "max_total_roam_seconds": 8,
        "auto_promote_to_canonical": False,
    },
    "trust": {
        "initial_peer_trust": 0.50,
        "min_trust_to_consider_shard": 0.30,
        "min_trust_to_promote_shard": 0.65,
        "harmful_outcome_penalty": 0.30,
        "successful_validation_boost": 0.08,
        "failed_validation_penalty": 0.12,
        "stale_decay_per_30_days": 0.05,
    },
    "knowledge_sharing": {
        "min_quality_to_promote": 0.72,
        "min_trust_to_promote": 0.65,
        "min_trust_to_promote_hive_mind": 0.25,
        "min_utility_to_promote": 0.64,
        "min_summary_tokens": 5,
        "min_resolution_steps": 1,
        "min_validation_count": 0,
        "max_failure_count": 1,
        "max_freshness_age_days": 45,
        "blocked_risk_flags": ["contains_secret", "credential_leak", "harmful", "pii", "policy_blocked"],
    },
    "economics": {
        "starter_credits_enabled": True,
        "starter_credits_amount": 24.0,
        "credit_purchase_enabled": False,
        "free_tier_daily_swarm_points": 24.0,
        "free_tier_max_dispatch_points": 12.0,
        "contribution_finality_target_depth": 2,
        "contribution_finality_quiet_hours": 6.0,
        "authenticated_write_requests_per_minute": 600,
        "public_hive_unknown_peer_trust": 0.45,
        "public_hive_min_claim_trust": 0.42,
        "public_hive_require_scoped_write_grants": False,
        "public_hive_low_trust_threshold": 0.45,
        "public_hive_high_trust_threshold": 0.75,
        "public_hive_daily_quota_low": 24.0,
        "public_hive_daily_quota_mid": 192.0,
        "public_hive_daily_quota_high": 768.0,
        "public_hive_daily_quota_bonus_per_active_claim": 24.0,
        "public_hive_daily_quota_max_active_claim_bonus": 192.0,
        "public_hive_min_route_trusts": {
            "/v1/hive/commons/promotion-candidates": 0.45,
            "/v1/hive/commons/promotion-reviews": 0.75,
            "/v1/hive/commons/promotions": 0.85,
        },
        "public_hive_route_costs": {
            "/v1/hive/topics": 6.0,
            "/v1/hive/posts": 0.25,
            "/v1/hive/topic-claims": 2.0,
            "/v1/hive/topic-status": 0.25,
            "/v1/hive/commons/endorsements": 0.1,
            "/v1/hive/commons/comments": 0.15,
            "/v1/hive/commons/promotion-candidates": 0.25,
            "/v1/hive/commons/promotion-reviews": 0.1,
            "/v1/hive/commons/promotions": 2.0,
        },
    },
    "brain_hive": {
        "commons_review_threshold": 3.5,
        "commons_archive_threshold": 2.5,
    },
    "adaptation": {
        "enabled": True,
        "tick_interval_seconds": 1800,
        "max_running_jobs": 1,
        "default_corpus_label": "autopilot-default",
        "limit_per_source": 256,
        "base_model_ref": "",
        "base_provider_name": "",
        "base_model_name": "",
        "license_name": "",
        "license_reference": "",
        "adapter_provider_name": "nulla-adapted",
        "adapter_model_prefix": "nulla-loop",
        "capabilities": ["summarize", "classify", "format"],
        "min_examples_to_train": 24,
        "min_structured_examples": 12,
        "min_high_signal_examples": 8,
        "min_new_examples_since_last_job": 8,
        "min_quality_score": 0.68,
        "max_conversation_ratio": 0.45,
        "max_eval_samples": 12,
        "max_canary_samples": 8,
        "eval_holdout_examples": 6,
        "canary_holdout_examples": 4,
        "min_train_examples_after_holdout": 12,
        "epochs": 1,
        "max_steps": 32,
        "batch_size": 1,
        "gradient_accumulation_steps": 4,
        "learning_rate": 0.0002,
        "cutoff_len": 768,
        "lora_r": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "logging_steps": 4,
        "promotion_margin": 0.03,
        "rollback_margin": 0.04,
        "min_candidate_eval_score": 0.55,
        "min_candidate_canary_score": 0.52,
        "post_promotion_canary_min_new_examples": 8,
        "publish_metadata_to_hive": True,
        "hive_topic": "NULLA Model Adaptation",
    },
    "assist_mesh": {
        "idle_assist_mode": "passive",
        "allow_research": True,
        "allow_code_reasoning": False,
        "allow_validation": True,
        "max_concurrent_tasks": 2,
        "trusted_peers_only": True,
        "min_reward_threshold": 10,
        "work_only_when_idle": True,
        "reserve_capacity_for_local_user": True,
        "require_assignment_capability_token": True,
        "assignment_lease_seconds": 900,
        "blocked_requeue_seconds": 15,
        "reconcile_interval_seconds": 10,
        "reconcile_subtask_limit": 50,
        "reconcile_token_limit": 100,
        "reconcile_assignment_limit": 25,
        "reconcile_open_offer_limit": 25,
        "reconcile_parent_limit": 25,
        "rebroadcast_helper_limit": 8,
        "min_parent_trust_for_assignment_execution": 0.25,
    },
}

_POLICY_CACHE: dict[str, Any] | None = None
_POLICY_LOCK = RLock()
_POLICY_PATH = config_path("default_policy.yaml")

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _env_is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(config: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Apply explicit, documented environment opt-ins on top of file/default policy.

    Web access is opt-in and off by default. Setting `NULLA_ENABLE_WEB=1`
    (or its alias `NULLA_ALLOW_WEB=1`) deliberately enables live web lookup by
    mapping to `system.allow_web_fallback=True` at load time.

    Remote `null://` dial is opt-in and off by default the same way. Setting
    `NULLA_ENABLE_NULL_DIAL=1` (or its alias `NULLA_ALLOW_NULL_DIAL=1`) maps to
    `system.allow_null_dial=True`. Spend stays separately gated at call time.
    """
    env_map = os.environ if env is None else env
    if _env_is_truthy(env_map.get("NULLA_ENABLE_WEB")) or _env_is_truthy(env_map.get("NULLA_ALLOW_WEB")):
        system = dict(config.get("system") or {})
        system["allow_web_fallback"] = True
        config["system"] = system
    if _env_is_truthy(env_map.get("NULLA_ENABLE_NULL_DIAL")) or _env_is_truthy(env_map.get("NULLA_ALLOW_NULL_DIAL")):
        system = dict(config.get("system") or {})
        system["allow_null_dial"] = True
        config["system"] = system
    return config

def load(force_reload: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE

    with _POLICY_LOCK:
        if _POLICY_CACHE is not None and not force_reload:
            return _POLICY_CACHE

        config = dict(_DEFAULT_POLICY)

        if _POLICY_PATH.exists():
            with _POLICY_PATH.open("r", encoding="utf-8") as f:
                user_policy = yaml.safe_load(f) or {}
                if not isinstance(user_policy, dict):
                    raise ValueError("Policy file must contain a YAML mapping/object.")
                config = _deep_merge(config, user_policy)

        config = _apply_env_overrides(config)

        _POLICY_CACHE = config
        return _POLICY_CACHE

def get(path: str, default: Any = None) -> Any:
    current: Any = load()
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current

def allow_swarm_queries() -> bool:
    return bool(get("system.allow_swarm_queries", True))

def local_only_mode() -> bool:
    return bool(get("system.local_only_mode", False))

def allow_web_fallback() -> bool:
    return bool(get("system.allow_web_fallback", False)) and not local_only_mode()

def null_dial_enabled() -> bool:
    return bool(get("system.allow_null_dial", False))

def allow_remote_only_without_backend() -> bool:
    return bool(get("system.allow_remote_only_without_backend", True)) and not local_only_mode()


def allowed_web_engines() -> tuple[str, ...]:
    items = get("web.allowed_engines", ["searxng", "ddg_instant", "duckduckgo_html"])
    if not isinstance(items, list):
        return ("searxng", "ddg_instant", "duckduckgo_html")
    return tuple(str(item).strip().lower() for item in items if str(item).strip())


def web_provider_order() -> tuple[str, ...]:
    items = get("web.provider_order", list(allowed_web_engines()))
    if not isinstance(items, list):
        return allowed_web_engines()
    normalized = tuple(str(item).strip().lower() for item in items if str(item).strip())
    return normalized or allowed_web_engines()


def searxng_url() -> str:
    return str(get("web.searxng_url", "http://127.0.0.1:8080")).strip() or "http://127.0.0.1:8080"


def searxng_timeout_seconds() -> float:
    try:
        return float(get("web.searxng_timeout_seconds", 12.0))
    except (TypeError, ValueError):
        return 12.0


def playwright_enabled() -> bool:
    return bool(get("web.playwright_enabled", False))


def browser_engine() -> str:
    return str(get("web.browser_engine", "chromium")).strip() or "chromium"


def allow_browser_fallback() -> bool:
    return bool(get("web.allow_browser_fallback", True)) and allow_web_fallback()


def max_fetch_bytes() -> int:
    try:
        return max(16384, int(get("web.max_fetch_bytes", 2000000)))
    except (TypeError, ValueError):
        return 2000000

def allow_local_shard_synthesis() -> bool:
    return True


def learned_shard_validation_errors(shard: dict[str, Any]) -> list[str]:
    if not isinstance(shard, dict):
        return ["invalid_type"]

    required = {"schema_version", "problem_class", "summary", "resolution_pattern", "risk_flags"}
    if not required.issubset(set(shard.keys())):
        return ["missing_required_fields"]

    if shard.get("schema_version") != get("shards.require_schema_version", 1):
        return ["schema_version_mismatch"]

    risk_flags = shard.get("risk_flags") or []
    blocked_flags = set(get("shards.quarantine_if_risk_flags_include", []))
    if any(flag in blocked_flags for flag in risk_flags):
        return ["blocked_risk_flag"]

    if get("shards.force_abstract_resolution_patterns", True):
        for step in shard.get("resolution_pattern", []):
            if not isinstance(step, str):
                return ["non_string_resolution_step"]
            raw_markers = ["rm -rf", "del /f", "format ", "sudo ", "powershell -enc", "curl http"]
            lowered = step.lower()
            if any(marker in lowered for marker in raw_markers):
                return ["raw_command_marker"]

    return []


def validate_learned_shard(shard: dict[str, Any]) -> bool:
    return not learned_shard_validation_errors(shard)


def outbound_shard_validation_errors(
    shard: dict[str, Any],
    *,
    share_scope: str,
    restricted_terms: list[str] | None = None,
) -> list[str]:
    if not share_scope_is_public(share_scope):
        return ["share_scope_not_public"]

    errors = learned_shard_validation_errors(shard)
    if errors:
        return errors

    privacy_risks = shard_privacy_risks(shard, restricted_terms=restricted_terms)
    if not privacy_risks:
        return []

    blocked: list[str] = []
    for reason in privacy_risks:
        if (reason == "filesystem_path" and not bool(get("shards.allow_paths_in_shards", False))) or (reason in {"secret_assignment", "openai_key", "github_token", "aws_access_key", "slack_token"} and not bool(
            get("shards.allow_tokens_in_shards", False)
        )) or (reason in {"email", "phone_number", "postal_address"} and not bool(get("shards.allow_contact_details_in_shards", False))) or (reason in {"identity_marker", "name_disclosure", "location_disclosure"} and not bool(
            get("shards.allow_identity_facts_in_shards", False)
        )) or reason.startswith("restricted_term:"):
            blocked.append(reason)
    return list(dict.fromkeys(blocked))


def validate_outbound_shard(
    shard: dict[str, Any],
    *,
    share_scope: str,
    restricted_terms: list[str] | None = None,
) -> bool:
    return not outbound_shard_validation_errors(
        shard,
        share_scope=share_scope,
        restricted_terms=restricted_terms,
    )
