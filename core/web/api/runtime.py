from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.nulla_agent import NullaAgent
from apps.nulla_daemon import DaemonConfig, NullaDaemon
from core import policy_engine
from core.compute_mode import ComputeModeDaemon
from core.hardware_tier import probe_machine
from core.identity_manager import load_active_persona
from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
from core.local_worker_pool import resolve_local_worker_capacity
from core.model_registry import ModelRegistry
from core.onboarding import (
    ensure_bootstrap_identity,
    ensure_openclaw_registration,
    get_agent_display_name,
    is_first_boot,
)
from core.provider_env import merge_provider_env
from core.public_hive_bridge import ensure_public_hive_auth
from core.release_channel import release_manifest_snapshot
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_bootstrap import bootstrap_runtime_mode
from core.runtime_install_profiles import active_install_profile_id, required_ollama_models_for_profile
from core.runtime_paths import active_config_home_dir, resolve_workspace_root
from core.runtime_provider_defaults import default_runtime_model_tag, ensure_default_runtime_providers
from core.runtime_task_events import (
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from network.signer import get_local_peer_id

logger = logging.getLogger("nulla.api")

MODEL_NAME = "nulla"
BUILD_SOURCE_PATH = Path("config") / "build-source.json"
_OPENCLAW_SENDER_WRAPPER_RE = re.compile(
    r"^Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*\[[^\]]+\]\s*(.*)$",
    re.DOTALL,
)


@dataclass
class RuntimeServices:
    agent: NullaAgent | None = None
    daemon: NullaDaemon | None = None
    display_name: str = "NULLA"
    runtime_model_tag: str = field(default_factory=default_runtime_model_tag)
    runtime_parameter_size: str = field(
        default_factory=lambda: parameter_size_for_model(default_runtime_model_tag())
    )
    runtime_started_at: str = ""
    runtime_home: str = ""
    runtime_version_stamp: dict[str, Any] = field(default_factory=dict)
    public_hive_auth: dict[str, Any] = field(default_factory=dict)
    provider_capability_truth: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def shutdown(self) -> None:
        if self.daemon:
            self.daemon.stop()


def git_output(project_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def build_source_metadata(project_root: Path) -> dict[str, Any]:
    metadata_path = project_root / BUILD_SOURCE_PATH
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key in ("ref", "branch", "commit", "source_url", "source_kind"):
        value = str(payload.get(key) or "").strip()
        if value:
            metadata[key] = value
    dirty_state = _coerce_optional_bool(payload.get("dirty_state"))
    if dirty_state is not None:
        metadata["dirty_state"] = dirty_state
    return metadata


def git_checkout_state(project_root: Path) -> dict[str, Any]:
    commit_full = git_output(project_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit_full):
        return {"valid": False, "branch": "", "commit": "", "dirty": False}
    branch = git_output(project_root, "branch", "--show-current")
    short_commit = git_output(project_root, "rev-parse", "--short=12", "HEAD") or commit_full[:12]
    dirty = bool(git_output(project_root, "status", "--short"))
    return {
        "valid": True,
        "branch": branch,
        "commit": short_commit,
        "dirty": dirty,
    }


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid integer env override %s=%r", name, raw)
        return int(default)


def env_text(name: str, default: str) -> str:
    return str(os.environ.get(name, default) or default).strip() or str(default)


def daemon_runtime_config(*, capacity: int, local_worker_threads: int) -> DaemonConfig:
    return DaemonConfig(
        bind_host=env_text("NULLA_DAEMON_BIND_HOST", "0.0.0.0"),
        bind_port=env_int("NULLA_DAEMON_BIND_PORT", 49152),
        advertise_host=env_text("NULLA_DAEMON_ADVERTISE_HOST", "127.0.0.1"),
        health_bind_host=env_text("NULLA_DAEMON_HEALTH_BIND_HOST", "127.0.0.1"),
        health_bind_port=max(0, env_int("NULLA_DAEMON_HEALTH_PORT", 0)),
        capacity=int(capacity),
        local_worker_threads=max(2, int(local_worker_threads)),
    )


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.rsplit(":", 1)
    return size.upper()


def parameter_count_for_model(model_tag: str) -> int:
    label = parameter_size_for_model(model_tag).rstrip("B")
    try:
        return int(float(label) * 1_000_000_000)
    except ValueError:
        return 7_000_000_000


def build_runtime_version_stamp(*, project_root: Path, runtime_model_tag: str, workstation_version: str) -> dict[str, Any]:
    release = dict(release_manifest_snapshot())
    build_source = build_source_metadata(project_root)
    git_state = git_checkout_state(project_root)
    if bool(git_state.get("valid")):
        branch = str(git_state.get("branch") or build_source.get("branch") or build_source.get("ref") or "")
        commit = str(git_state.get("commit") or "").strip()
        dirty = bool(git_state.get("dirty"))
    else:
        branch = str(build_source.get("branch") or build_source.get("ref") or "")
        commit = str(build_source.get("commit") or "").strip()[:12]
        dirty = bool(build_source.get("dirty_state"))
    release_version = str(release.get("release_version") or "").strip() or "unknown-release"
    build_parts = [release_version]
    if commit:
        build_parts.append(commit)
    build_id = "+".join(build_parts)
    if dirty:
        build_id = f"{build_id}.dirty"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "release_version": release_version,
        "minimum_compatible_release": str(release.get("minimum_compatible_release") or "").strip(),
        "protocol_version": int(release.get("protocol_version") or 0),
        "rollout_stage": str(release.get("rollout_stage") or "").strip(),
        "channel_name": str(release.get("channel_name") or "").strip(),
        "branch": branch,
        "commit": commit,
        "dirty": dirty,
        "build_id": build_id,
        "started_at": started_at,
        "pid": os.getpid(),
        "workstation_version": workstation_version,
        "model_tag": runtime_model_tag,
    }


def ensure_ollama_model(model_tag: str | None = None) -> None:
    active_model = str(model_tag or "").strip() or default_runtime_model_tag()
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if active_model in result.stdout:
            return
    except Exception:
        pass
    logger.info(
        "Ollama model '%s' missing — pulling now (this may take a few minutes on first run)...",
        active_model,
    )
    try:
        subprocess.run(
            ["ollama", "pull", active_model],
            timeout=1200,
            capture_output=True,
        )
        logger.info("Ollama model '%s' pulled successfully.", active_model)
    except Exception as exc:
        logger.warning(
            "Failed to pull Ollama model '%s': %s — LLM responses will fall back to planning mode.",
            active_model,
            exc,
        )


def ensure_default_provider(
    registry: ModelRegistry,
    model_tag: str,
    *,
    env: dict[str, str] | None = None,
    install_profile: str | None = None,
    runtime_home: str | None = None,
) -> None:
    for provider_id in ensure_default_runtime_providers(
        registry,
        model_tag=model_tag,
        env=env,
        install_profile=install_profile,
        runtime_home=runtime_home,
    ):
        logger.info("Auto-registered default provider: %s", provider_id)


def log_prewarm_results(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
) -> None:
    if str(os.environ.get("NULLA_SKIP_PROVIDER_PREWARM") or "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.info("Provider prewarm skipped by NULLA_SKIP_PROVIDER_PREWARM.")
        return
    try:
        snapshot = build_provider_registry_snapshot(
            registry,
            model_tag=model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            honor_install_profile=bool(requested_profile or runtime_home),
            run_prewarm=True,
        )
        raw_results = snapshot.prewarm_results
    except Exception as exc:
        logger.warning("Provider prewarm enumeration failed: %s", exc)
        return
    if not isinstance(raw_results, (list, tuple)):
        return
    for result in raw_results:
        provider_id = str(result.get("provider_id") or "unknown-provider")
        status = str(result.get("status") or "unknown").strip() or "unknown"
        if result.get("ok") and status == "prewarmed":
            logger.info(
                "Provider prewarmed: %s | keep_alive=%s | load_duration=%s | total_duration=%s",
                provider_id,
                result.get("keep_alive"),
                result.get("load_duration"),
                result.get("total_duration"),
            )
            continue
        if result.get("ok") and status == "timed_out":
            logger.info(
                "Provider prewarm timed out; continuing without background warming: %s | reason=%s | keep_alive=%s | timeout_seconds=%s",
                provider_id,
                result.get("reason") or "unspecified",
                result.get("keep_alive"),
                result.get("timeout_seconds"),
            )
            continue
        if result.get("ok"):
            logger.info(
                "Provider prewarm skipped: %s | status=%s | reason=%s",
                provider_id,
                status,
                result.get("reason") or "unspecified",
            )
            continue
        logger.warning(
            "Provider prewarm failed: %s | status=%s | error=%s",
            provider_id,
            status,
            result.get("error") or "unknown_error",
        )


def startup_provider_capability_truth(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    try:
        snapshot = build_provider_registry_snapshot(
            registry,
            model_tag=model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            honor_install_profile=bool(requested_profile or runtime_home),
            env=env,
        )
    except Exception as exc:
        logger.warning("Startup provider capability snapshot failed: %s", exc)
        return tuple()
    return tuple(item.to_dict() for item in hydrate_capability_truth_with_benchmarks(snapshot.capability_truth))


def public_hive_auth_snapshot(auth_result: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(auth_result or {})
    status = str(payload.get("status") or "unknown").strip() or "unknown"
    snapshot: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "status": status,
    }
    requires_auth = payload.get("requires_auth")
    if requires_auth is not None:
        snapshot["requires_auth"] = bool(requires_auth)
    watch_host = str(payload.get("watch_host") or "").strip()
    if watch_host:
        snapshot["watch_host"] = watch_host
    suggested_remote_config_path = str(payload.get("suggested_remote_config_path") or "").strip()
    if suggested_remote_config_path:
        snapshot["remote_config_path"] = suggested_remote_config_path
    suggested_command = str(payload.get("suggested_command") or "").strip()
    if suggested_command:
        snapshot["next_step"] = suggested_command
    return snapshot


def bootstrap_runtime_services(*, project_root: Path, workstation_version: str) -> RuntimeServices:
    boot = bootstrap_runtime_mode(
        mode="api_server",
        workspace_root=resolve_workspace_root(),
        force_policy_reload=True,
        configure_logging=True,
        resolve_backend=True,
    )

    if is_first_boot():
        ensure_bootstrap_identity(
            default_agent_name="NULLA",
            privacy_pact="Store memory locally by default. Never share secrets or personal identity without explicit approval.",
        )
    peer_id = get_local_peer_id()
    from core.credit_ledger import ensure_starter_credits

    if ensure_starter_credits(peer_id):
        logger.info("Starter credits seeded for peer %s...", peer_id[:24])

    auth_target_path = active_config_home_dir() / "agent-bootstrap.json"
    auth_result = ensure_public_hive_auth(
        project_root=project_root,
        target_path=auth_target_path,
    )
    auth_snapshot = public_hive_auth_snapshot(auth_result)
    if not auth_result.get("ok"):
        auth_status = str(auth_result.get("status") or "unknown").strip() or "unknown"
        suggested_command = str(auth_result.get("suggested_command") or "").strip()
        suggested_remote_config_path = str(auth_result.get("suggested_remote_config_path") or "").strip()
        watch_host = str(auth_result.get("watch_host") or "").strip()
        if auth_status in {"missing_remote_config_path", "missing_watch_host", "missing_ssh_key"}:
            detail_parts = []
            if watch_host:
                detail_parts.append(f"watch_host={watch_host}")
            if suggested_remote_config_path:
                detail_parts.append(f"remote_config_path={suggested_remote_config_path}")
            if suggested_command:
                detail_parts.append(f"next_step={suggested_command}")
            detail_text = " | ".join(detail_parts) if detail_parts else "set Public Hive auth config explicitly"
            logger.info("Public Hive writes are not hydrated yet: %s | %s", auth_status, detail_text)
        else:
            logger.warning("Public Hive auth is not wired for writes: %s", auth_status)

    probe = probe_machine()
    runtime_home = (
        str(boot.context.paths.runtime_home)
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None
    )
    provider_env = merge_provider_env(runtime_home)
    requested_profile = active_install_profile_id(runtime_home=runtime_home, env=provider_env) if runtime_home else None
    profile_default_model = _profile_fast_default_model(
        requested_profile=requested_profile,
        runtime_home=runtime_home,
        env=provider_env,
    )
    runtime_model_tag = env_text(
        "NULLA_OLLAMA_MODEL",
        profile_default_model or default_runtime_model_tag(env=provider_env),
    )
    runtime_parameter_size = parameter_size_for_model(runtime_model_tag)
    ensure_ollama_model(runtime_model_tag)
    logger.info(
        "Hardware: %s | GPU: %s | Primary local model: %s",
        probe.accelerator,
        probe.gpu_name or "none",
        runtime_model_tag,
    )
    runtime_version_stamp = build_runtime_version_stamp(
        project_root=project_root,
        runtime_model_tag=runtime_model_tag,
        workstation_version=workstation_version,
    )
    runtime_started_at = str(runtime_version_stamp.get("started_at") or "")
    logger.info(
        "Runtime build: %s | branch=%s | commit=%s | dirty=%s",
        runtime_version_stamp.get("build_id") or "unknown",
        runtime_version_stamp.get("branch") or "unknown",
        runtime_version_stamp.get("commit") or "unknown",
        runtime_version_stamp.get("dirty"),
    )

    compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
    compute_daemon.start()

    model_registry = ModelRegistry()
    ensure_default_provider(
        model_registry,
        runtime_model_tag,
        env=provider_env,
        install_profile=requested_profile,
        runtime_home=runtime_home,
    )
    for warning in model_registry.startup_warnings():
        logger.warning("Model warning: %s", warning)
    log_prewarm_results(
        model_registry,
        model_tag=runtime_model_tag,
        runtime_home=runtime_home,
        requested_profile=requested_profile,
    )

    selection = boot.backend_selection
    if selection is None:
        raise RuntimeError("API bootstrap did not resolve a backend selection.")
    if selection.backend_name == "remote_only":
        logger.warning("No local backend found. Continuing in remote-only mode.")

    persona = load_active_persona("default")
    display_name = get_agent_display_name()
    if ensure_openclaw_registration(display_name=display_name, model_tag=runtime_model_tag):
        logger.info("OpenClaw registration ensured for agent '%s'.", display_name)
    else:
        logger.warning("OpenClaw registration could not be refreshed automatically.")

    agent = NullaAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    agent.start()

    pool_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    daemon_capacity, _ = resolve_local_worker_capacity(requested=None, hard_cap=pool_cap)
    daemon = NullaDaemon(
        daemon_runtime_config(
            capacity=int(daemon_capacity),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
        )
    )
    daemon.start()

    logger.info("%s API server ready.", display_name)
    logger.info("Peer ID: %s...", peer_id[:24])
    logger.info("Backend: %s | Device: %s", selection.backend_name, selection.device)
    logger.info("Mesh daemon: active on UDP %s", daemon.config.bind_port)

    return RuntimeServices(
        agent=agent,
        daemon=daemon,
        display_name=display_name,
        runtime_model_tag=runtime_model_tag,
        runtime_parameter_size=runtime_parameter_size,
        runtime_started_at=runtime_started_at,
        runtime_home=str(runtime_home or ""),
        runtime_version_stamp=runtime_version_stamp,
        public_hive_auth=auth_snapshot,
        provider_capability_truth=startup_provider_capability_truth(
            model_registry,
            model_tag=runtime_model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            env=provider_env,
        ),
    )


def _profile_fast_default_model(
    *,
    requested_profile: str | None,
    runtime_home: str | None,
    env: dict[str, str],
) -> str:
    default_model = default_runtime_model_tag(env=env)
    required_models = required_ollama_models_for_profile(
        profile_id=requested_profile or "local-only",
        model_tag=default_model,
        runtime_home=runtime_home,
        env=env,
    )
    fast_model = "nulla-qwen3-30b-a3b:nothink"
    return fast_model if fast_model in required_models else ""


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return strip_openclaw_sender_wrapper(str(content).strip())
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip().lower() == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        return strip_openclaw_sender_wrapper("\n".join(parts).strip())
    return strip_openclaw_sender_wrapper(str(content or "").strip())


def strip_openclaw_sender_wrapper(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("Sender (untrusted metadata):"):
        return stripped
    match = _OPENCLAW_SENDER_WRAPPER_RE.match(stripped)
    if not match:
        return stripped
    return match.group(1).strip() or stripped


def normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        content = message_text(message.get("content", ""))
        if not content:
            continue
        history.append({"role": role, "content": content})
    return history


def extract_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(normalize_chat_history(messages)):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def stable_openclaw_session_id(
    *,
    body: dict[str, Any],
    history: list[dict[str, str]],
    headers: dict[str, Any],
) -> str:
    for key in (
        "session_id",
        "sessionId",
        "session",
        "conversation_id",
        "conversationId",
        "chat_id",
        "chatId",
        "thread_id",
        "threadId",
    ):
        value = str(body.get(key) or "").strip()
        if value:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
            return f"openclaw:{digest}"

    for header_name in ("X-Session-Id", "X-Conversation-Id", "X-Thread-Id", "X-OpenClaw-Session"):
        value = str(headers.get(header_name) or "").strip()
        if value:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
            return f"openclaw:{digest}"

    seed = {
        "model": str(body.get("model") or MODEL_NAME),
        "history": history[:4],
    }
    digest = hashlib.sha256(json.dumps(seed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    return f"openclaw:{digest}"


def runtime_headers(runtime: RuntimeServices) -> dict[str, str]:
    stamp = dict(runtime.runtime_version_stamp or {})
    return {
        "X-Nulla-Runtime-Version": str(stamp.get("release_version") or "unknown"),
        "X-Nulla-Runtime-Build": str(stamp.get("build_id") or "unknown"),
        "X-Nulla-Runtime-Started-At": str(stamp.get("started_at") or ""),
        "X-Nulla-Runtime-Commit": str(stamp.get("commit") or ""),
        "X-Nulla-Runtime-Dirty": "1" if bool(stamp.get("dirty")) else "0",
    }


def default_workspace_root() -> str:
    return str(resolve_workspace_root())


def run_agent(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> dict[str, Any]:
    if not runtime.agent or not user_text:
        return {"response": "", "confidence": 0.0}
    base_context = {
        "surface": "channel",
        "platform": "openclaw",
        "allow_remote_fetch": policy_engine.allow_web_fallback(),
        "allow_cold_context": True,
    }
    if source_context:
        base_context.update(source_context)
    default_workspace = workspace_root_provider()
    base_context.setdefault("workspace", default_workspace)
    base_context.setdefault("workspace_root", default_workspace)
    if session_id:
        base_context["runtime_session_id"] = session_id
    if runtime.runtime_home:
        base_context.setdefault("runtime_home", runtime.runtime_home)
    memory_recall = _memory_recall_response(runtime, user_text=user_text, source_context=base_context)
    if memory_recall is not None:
        return memory_recall
    result = runtime.agent.run_once(
        user_text,
        session_id_override=session_id,
        source_context=base_context,
    )
    schedule_memory_extraction(
        runtime,
        user_text=user_text,
        assistant_output=str(dict(result or {}).get("response") or ""),
        session_id=session_id,
        source_context=base_context,
    )
    return result


def schedule_memory_extraction(
    runtime: RuntimeServices,
    *,
    user_text: str,
    assistant_output: str,
    session_id: str | None,
    source_context: dict[str, Any] | None,
) -> None:
    if not _memory_capture_allowed(source_context):
        return
    messages = _messages_for_memory_extraction(
        user_text=user_text,
        assistant_output=assistant_output,
        source_context=source_context,
    )
    if not messages:
        return
    try:
        from core.fact_extractor import FactExtractor
        from core.nulla_memory import NullaMemory

        runtime_home = str(runtime.runtime_home or (source_context or {}).get("runtime_home") or "").strip() or None
        memory = NullaMemory(runtime_home=runtime_home, agent_id=str((source_context or {}).get("agent_id") or "nulla"))
        extractor = FactExtractor(memory=memory, close_memory_on_finish=True)
        extractor.trigger_async(messages)
    except Exception:
        return


def _memory_capture_allowed(source_context: dict[str, Any] | None) -> bool:
    context = dict(source_context or {})
    platform = str(context.get("platform") or "").strip().lower()
    surface = str(context.get("surface") or "").strip().lower()
    group_like = (
        platform in {"discord", "telegram", "slack", "whatsapp", "group"}
        or surface in {"discord", "telegram", "slack", "whatsapp", "group"}
        or bool(context.get("is_group"))
        or bool(context.get("group_id"))
        or bool(context.get("channel_is_group"))
    )
    if group_like:
        return False
    explicit = context.get("memory_capture_enabled")
    if isinstance(explicit, bool):
        return explicit
    return platform in {"api", "openclaw", "web_companion", "cli", ""} or surface in {"api", "openclaw", "channel", "cli", ""}


def _memory_recall_response(
    runtime: RuntimeServices,
    *,
    user_text: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _memory_capture_allowed(source_context):
        return None
    if not _looks_like_private_memory_recall(user_text):
        return None
    try:
        from core.nulla_memory import NullaMemory

        context = dict(source_context or {})
        runtime_home = str(runtime.runtime_home or context.get("runtime_home") or "").strip() or None
        agent_id = str(context.get("agent_id") or "nulla").strip() or "nulla"
        with NullaMemory(runtime_home=runtime_home, agent_id=agent_id) as memory:
            values = _private_memory_values(memory)
    except Exception:
        return None
    if not any(values.values()):
        return None
    response = _format_private_memory_recall(values)
    if not response:
        return None
    return {
        "response": response,
        "confidence": 0.95,
        "source": "local_private_memory",
    }


def _looks_like_private_memory_recall(user_text: str) -> bool:
    text = " ".join(str(user_text or "").lower().split())
    if not text:
        return False
    direct_markers = (
        "profile recall",
        "personal profile",
        "persistent memory",
        "stored about me",
        "remember about me",
        "what do you remember",
        "what is my name",
        "what's my name",
        "who am i",
        "who is the user",
        "answer style",
        "response style",
        "preferred style",
        "preference",
        "project codename",
        "active codename",
        "codename",
    )
    return any(marker in text for marker in direct_markers)


def _private_memory_values(memory: Any) -> dict[str, str]:
    blocks = {
        "user_profile": str(memory.block_read("user_profile") or ""),
        "preferences": str(memory.block_read("preferences") or ""),
        "project_context": str(memory.block_read("project_context") or ""),
        "constraints": str(memory.block_read("constraints") or ""),
    }
    return {
        "name": _first_block_value(blocks["user_profile"], ("Name",)),
        "answer_style": _first_block_value(blocks["preferences"], ("Answer style", "Response style", "Preferred answer style")),
        "project_codename": _first_block_value(blocks["project_context"], ("Active project codename", "Project codename")),
        "constraints": _compact_block_lines(blocks["constraints"]),
    }


def _first_block_value(block_text: str, labels: tuple[str, ...]) -> str:
    for raw_line in str(block_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for label in labels:
            prefix = f"{label}:"
            if line.lower().startswith(prefix.lower()):
                return line[len(prefix) :].strip()
    return ""


def _compact_block_lines(block_text: str, *, max_lines: int = 3) -> str:
    lines = [line.strip() for line in str(block_text or "").splitlines() if line.strip()]
    return "; ".join(lines[:max_lines])


def _format_private_memory_recall(values: dict[str, str]) -> str:
    parts: list[str] = []
    if values.get("name"):
        parts.append(f"user: {values['name']}")
    if values.get("answer_style"):
        parts.append(f"preferred answer style: {values['answer_style']}")
    if values.get("project_codename"):
        parts.append(f"active project codename: {values['project_codename']}")
    if values.get("constraints"):
        parts.append(f"constraints: {values['constraints']}")
    if not parts:
        return ""
    return "Stored local profile: " + "; ".join(parts) + "."


def _messages_for_memory_extraction(
    *,
    user_text: str,
    assistant_output: str,
    source_context: dict[str, Any] | None,
) -> list[dict[str, str]]:
    context = dict(source_context or {})
    history = list(context.get("conversation_history") or context.get("client_conversation_history") or [])
    messages: list[dict[str, str]] = []
    for item in history[-18:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = message_text(item.get("content") or "")
        if content:
            messages.append({"role": role, "content": content})
    clean_user = str(user_text or "").strip()
    if clean_user and (not messages or messages[-1] != {"role": "user", "content": clean_user}):
        messages.append({"role": "user", "content": clean_user})
    clean_assistant = str(assistant_output or "").strip()
    if clean_assistant:
        messages.append({"role": "assistant", "content": clean_assistant})
    return messages[-20:]


def openai_chat_response(result: dict[str, Any], model: str) -> dict[str, Any]:
    response_text = str(result.get("response") or "").strip()
    return {
        "id": f"chatcmpl-{hashlib.sha256(response_text.encode()).hexdigest()[:12]}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(response_text.split()),
            "total_tokens": len(response_text.split()),
        },
    }


def ollama_chat_response(result: dict[str, Any], model: str, runtime: RuntimeServices) -> dict[str, Any]:
    response_text = str(result.get("response") or "").strip()
    return {
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "message": {"role": "assistant", "content": response_text},
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_count": len(response_text.split()),
        "eval_duration": 0,
    }


def ollama_stream_chunk(*, model: str, content: str, created_at: str, done: bool, eval_count: int = 0) -> bytes:
    payload: dict[str, Any] = {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": content},
        "done": done,
    }
    if done:
        payload.update(
            {
                "done_reason": "stop",
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": 0,
                "prompt_eval_duration": 0,
                "eval_count": eval_count,
                "eval_duration": 0,
            }
        )
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def ollama_stream_chunks(result: dict[str, Any], model: str) -> list[bytes]:
    full_text = str(result.get("response") or "").strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    chunks: list[bytes] = []
    words = full_text.split(" ") if full_text else []
    for index, word in enumerate(words):
        token = word if index == 0 else " " + word
        chunks.append(ollama_stream_chunk(model=model, content=token, created_at=now, done=False))
    chunks.append(ollama_stream_chunk(model=model, content="", created_at=now, done=True, eval_count=len(words)))
    return chunks


def openai_sse_stream_from_ollama_chunks(stream: Iterable[bytes], model: str) -> Iterator[bytes]:
    chunk_id = f"chatcmpl-{hashlib.sha256(f'{model}:{datetime.now(timezone.utc).timestamp()}'.encode()).hexdigest()[:12]}"
    created = int(datetime.now(timezone.utc).timestamp())
    emitted_role = False
    for raw_chunk in stream:
        raw_text = raw_chunk.decode("utf-8", errors="replace").strip()
        if not raw_text:
            continue
        payload = json.loads(raw_text)
        message = dict(payload.get("message") or {})
        content = str(message.get("content") or "")
        done = bool(payload.get("done"))
        delta: dict[str, Any] = {}
        if not emitted_role:
            delta["role"] = "assistant"
            emitted_role = True
        if content:
            delta["content"] = content
        event = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": "stop" if done else None,
                }
            ],
        }
        yield b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n"
        if done:
            yield b"data: [DONE]\n\n"
            break


def format_runtime_event_text(event: dict[str, Any]) -> str:
    if str(event.get("event_type") or "").strip() == "model_output_chunk":
        return str(event.get("message") or "")
    event_type = str(event.get("event_type") or "").strip()
    if event_type.startswith("model_"):
        visible = {
            key: value
            for key, value in dict(event or {}).items()
            if key
            in {
                "event_type",
                "message",
                "schema",
                "turn_id",
                "session_id",
                "task_class",
                "task_kind",
                "output_mode",
                "complexity",
                "lane",
                "lane_type",
                "provider_id",
                "model_id",
                "model_name",
                "planned_provider_id",
                "planned_model_id",
                "actual_adapter_provider_id",
                "actual_adapter_model_id",
                "backend",
                "role",
                "provider_role",
                "queue_depth",
                "tokens_per_second",
                "measurement_source",
                "phase",
                "fallback_reason",
                "rejection_reason",
                "failure_reason",
                "selected_provider_id",
                "selected_model",
                "ranked_candidates",
                "attempted",
                "error",
                "failover_used",
                "verifier_status",
                "verifier_provider_id",
                "verifier_model_id",
                "kv_cache_status",
                "backend_cache_proof",
                "speculative_status",
                "speculative_proof",
                "eagle_status",
                "eagle_proof",
                "mismatch",
            }
        }
        return "NULLA_RUNTIME_EVENT " + json.dumps(visible, separators=(",", ":"), sort_keys=True) + "\n"
    message = str(event.get("message") or "").strip()
    return message + "\n" if message else ""


def stream_agent_with_events(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
    run_agent_provider: Callable[..., dict[str, Any]] | None = None,
) -> Iterator[bytes]:
    event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
    stream_context = dict(source_context or {})
    stream_id = ""
    saw_model_output = False
    stream_id = new_runtime_event_stream_id()
    stream_context["runtime_event_stream_id"] = stream_id

    def sink(event: dict[str, Any]) -> None:
        event_queue.put(("event", dict(event)))

    def worker() -> None:
        try:
            agent_runner = run_agent_provider or run_agent
            result = agent_runner(runtime, user_text, session_id=session_id, source_context=stream_context)
            event_queue.put(("result", result))
        except Exception as exc:
            event_queue.put(("error", str(exc)))

    if stream_id:
        register_runtime_event_sink(stream_id, sink)
    thread = threading.Thread(target=worker, name="nulla-openclaw-stream", daemon=True)
    thread.start()

    try:
        while True:
            kind, payload = event_queue.get()
            if kind == "event":
                event_payload = dict(payload or {})
                if str(event_payload.get("event_type") or "").strip() == "model_output_chunk":
                    saw_model_output = True
                if not include_runtime_events and str(event_payload.get("event_type") or "").strip() != "model_output_chunk":
                    continue
                content = format_runtime_event_text(event_payload)
                if content:
                    yield ollama_stream_chunk(
                        model=model,
                        content=content,
                        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        done=False,
                    )
                continue
            if kind == "error":
                for chunk in ollama_stream_chunks({"response": f"Runtime error: {payload}"}, model):
                    yield chunk
                break
            if kind == "result":
                if saw_model_output:
                    response_text = str(dict(payload or {}).get("response") or "").strip()
                    eval_count = len(response_text.split()) if response_text else 0
                    yield ollama_stream_chunk(
                        model=model,
                        content="",
                        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        done=True,
                        eval_count=eval_count,
                    )
                else:
                    for chunk in ollama_stream_chunks(dict(payload or {}), model):
                        yield chunk
                break
    finally:
        if stream_id:
            unregister_runtime_event_sink(stream_id)
        thread.join(timeout=0.1)
