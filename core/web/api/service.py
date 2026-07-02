from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.adaptation_autopilot import get_adaptation_autopilot_status, schedule_adaptation_autopilot_tick
from core.control_plane_workspace import collect_control_plane_status
from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.persistent_memory import augment_history_from_session_log
from core.runtime_capabilities import runtime_capability_snapshot
from core.runtime_operator_snapshot import build_runtime_operator_snapshot
from core.runtime_task_events import list_runtime_session_events, list_runtime_sessions
from core.runtime_task_rail import render_runtime_task_rail_html
from core.web0_project_grounding import web0_null_project_response
from storage.adaptation_store import (
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
)
from core.web.api.response_control import apply_exact_response_control

from .runtime import (
    RuntimeServices,
    extract_user_message,
    normalize_chat_history,
    ollama_chat_response,
    ollama_stream_chunks,
    openai_chat_response,
    openai_sse_stream_from_ollama_chunks,
    parameter_count_for_model,
    parameter_size_for_model,
    run_agent,
    runtime_headers,
    stable_openclaw_session_id,
    stream_agent_with_events,
)


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes | None = None
    stream: Iterable[bytes] | None = None
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: Any, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=dict(headers or {}),
    )


def text_response(status: int, text: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/plain; charset=utf-8",
        body=str(text).encode("utf-8"),
        headers=dict(headers or {}),
    )


def html_response(status: int, html: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/html; charset=utf-8",
        body=str(html).encode("utf-8"),
        headers=dict(headers or {}),
    )


def stream_response(
    status: int,
    stream: Iterable[bytes],
    *,
    content_type: str,
    headers: dict[str, str] | None = None,
) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type=content_type,
        stream=stream,
        headers=dict(headers or {}),
    )


# Task-completion self-credit: minted only against an internally-consistent work
# receipt, paid only to the LOCAL peer (no cross-peer transfer is possible here), and
# capped per rolling window so a flood of requests cannot inflate the balance.
_TASK_AWARD_WINDOW_SEC = 60
_TASK_AWARD_MAX_PER_WINDOW = 30
_task_award_window: dict[int, int] = {}


def _award_task_completion_credit(receipt: Any) -> dict[str, Any]:
    """Award the local node's task-completion credit — gated + rate-limited.

    Returns ``{"awarded": bool, "reason": str}``. The receipt's proof must recompute
    and bind the same result (no minting on a malformed/forged receipt); the recipient
    is always the local peer; and awards are bounded per window against spam-inflation.
    """
    import time as _time

    from core.proof_of_execution import verify_proof_receipt

    try:
        if not verify_proof_receipt(receipt.proof):
            return {"awarded": False, "reason": "invalid_proof"}
        if receipt.result_hash != receipt.proof.result_hash:
            return {"awarded": False, "reason": "result_binding_mismatch"}
    except Exception:
        return {"awarded": False, "reason": "proof_check_error"}

    window = int(_time.time()) // _TASK_AWARD_WINDOW_SEC
    for w in [w for w in _task_award_window if w < window]:
        _task_award_window.pop(w, None)
    if _task_award_window.get(window, 0) >= _TASK_AWARD_MAX_PER_WINDOW:
        return {"awarded": False, "reason": "rate_limited"}

    try:
        from core.credit_ledger import award_credits
        from network.signer import get_local_peer_id
        ok = award_credits(get_local_peer_id(), amount=1.0, reason="task_completion",
                           receipt_id=receipt.receipt_id)
    except Exception:
        return {"awarded": False, "reason": "ledger_error"}
    if ok:
        _task_award_window[window] = _task_award_window.get(window, 0) + 1
    return {"awarded": bool(ok), "reason": "awarded" if ok else "ledger_declined"}


def _attach_work_receipt(
    payload: dict[str, Any],
    *,
    result: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Issue a Web0WorkReceipt for the completed turn; return payload unchanged on failure."""
    try:
        import os

        from core.web0_work_receipt import issue_work_receipt
        response_text = str(result.get("response") or "").strip()
        if not response_text:
            return payload
        worker_id = str(os.environ.get("NULLA_WORKER_ID") or "nulla")
        # Wire live wallet pubkey as payment recipient when available
        recipient_wallet = "stub-wallet"
        try:
            from core.nulla_wallet import get_or_create_wallet
            recipient_wallet = get_or_create_wallet().pubkey
        except Exception:
            pass
        receipt = issue_work_receipt(
            task_id=session_id,
            result=response_text,
            worker_id=worker_id,
            recipient_wallet=recipient_wallet,
        )
        payload = dict(payload)
        payload["web0_receipt"] = receipt.to_dict()
        # Award the local node a bounded, proof-gated task-completion credit.
        with contextlib.suppress(Exception):
            _award_task_completion_credit(receipt)
        # Anchor receipt hash on Solana when anchoring is opted in (shared gate)
        from core.solana_anchor import anchor_enabled
        if anchor_enabled():
            try:
                from core.solana_anchor import anchor_vault_proof
                anchor_vault_proof(
                    parent_task_id=session_id,
                    final_response_hash=receipt.result_hash,
                    confidence=1.0,
                )
            except Exception:
                pass
    except Exception:
        pass
    return payload


def apply_runtime_headers(response: ApiResponse, runtime: RuntimeServices) -> ApiResponse:
    headers = runtime_headers(runtime)
    headers.update(response.headers)
    response.headers = headers
    return response


def _normalize_web0_name(raw: str) -> str:
    """A user-typed address -> the bare .null name the resolver expects.

    Accepts 'web0.null', 'web0', 'null://web0.null/path', 'web0://web0', with any
    trailing path/query stripped, lowercased.
    """
    name = str(raw or "").strip().lower()
    for prefix in ("null://", "web0://", "https://", "http://"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.split("/", 1)[0].split("?", 1)[0].strip()
    if name.endswith(".null"):
        name = name[: -len(".null")]
    return name.strip()


def _web0_gateway_urls(txid: str) -> list[str]:
    # Try more than one Arweave gateway: right after a publish a given gateway can
    # 404 for ~30-60s while another already serves it (AGENTS.md pitfall #5).
    tx = str(txid or "").strip()
    if not tx:
        return []
    return [f"https://arweave.net/{tx}", f"https://gateway.irys.xyz/{tx}", f"https://{tx}.ar-io.net"]


def _web0_resolve_response(query: dict[str, list[str]]) -> ApiResponse:
    """Read-only: resolve a .null NAME to its Arweave content URL for the /web0 browser.

    Public on-chain read + a public gateway URL. No key, no signing, no payment.
    Honors local_only_mode (a hard no-remote switch).
    """
    from core import policy_engine

    values = query.get("name") or query.get("q") or []
    name = _normalize_web0_name(str(values[0]) if values else "")
    if not name:
        return json_response(400, {"ok": False, "error": "missing 'name' query parameter"})
    if policy_engine.local_only_mode():
        return json_response(
            403,
            {"ok": False, "name": name, "error": "local_only_mode is on; remote .null resolution is disabled"},
        )
    try:
        from core.null_resolver import resolve_null_domain

        record = resolve_null_domain(name)
    except Exception as exc:  # noqa: BLE001 - surface resolver failures as a clean 502
        return json_response(502, {"ok": False, "name": name, "error": f"resolver error: {exc}"})
    if record is None:
        return json_response(404, {"ok": False, "name": name, "error": "unregistered or RPC unreachable"})
    txid = getattr(record, "arweave_txid", None) or ""
    gateways = _web0_gateway_urls(txid)
    return json_response(
        200,
        {
            "ok": True,
            "name": name,
            "owner": record.owner,
            "arweave_txid": txid,
            "gateway_url": gateways[0] if gateways else "",
            "gateways": gateways,
            "x402_endpoint": getattr(record, "x402_endpoint", "") or "",
            "has_content": bool(txid),
        },
    )


def _looks_like_runtime_model_status_question(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if any(term in clean for term in ("recommend", "should i download", "which model should")):
        return False
    has_model_term = any(term in clean for term in ("llm", "model", "model lane"))
    has_status_term = any(
        term in clean
        for term in (
            "active",
            "current",
            "standard",
            "using now",
            "what are you using",
            "what model",
            "which model",
        )
    )
    return has_model_term and has_status_term


def _runtime_text_model_lanes(runtime: RuntimeServices) -> list[str]:
    lanes: list[str] = []
    for item in tuple(runtime.provider_capability_truth or ()):
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        if not provider_id.lower().startswith("ollama-local:"):
            continue
        model_id = str(item.get("model_id") or "").strip()
        if model_id and model_id not in lanes:
            lanes.append(model_id)
    default_model = str(runtime.runtime_model_tag or "").strip()
    if default_model and default_model not in lanes:
        lanes.insert(0, default_model)
    return lanes


def runtime_model_status_response(text: str, runtime: RuntimeServices) -> dict[str, Any] | None:
    if not _looks_like_runtime_model_status_question(text):
        return None
    default_model = str(runtime.runtime_model_tag or "").strip() or "unknown"
    lanes = _runtime_text_model_lanes(runtime)
    largest = max(lanes, key=parameter_count_for_model) if lanes else default_model
    lane_text = ", ".join(f"`{item}`" for item in lanes) if lanes else "`none`"
    if largest and largest != default_model:
        router_line = f"- Routing can select `{largest}` for heavier local turns; no larger local text model is installed right now."
    else:
        router_line = "- No separate larger local text model is installed right now."
    return {
        "response": (
            f"- Boot/default local model: `{default_model}` ({parameter_size_for_model(default_model)}).\n"
            f"- Visible local text lanes: {lane_text}.\n"
            f"{router_line}"
        ),
        "confidence": 1.0,
        "source": "runtime_model_status",
        "deterministic": True,
        "runtime_model_tag": default_model,
        "local_text_lanes": lanes,
    }


def capability_snapshot_with_runtime(
    runtime: RuntimeServices,
    capability_snapshot_provider: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(capability_snapshot_provider() or {})
    public_hive_auth = dict(runtime.public_hive_auth or {})
    runtime_provider_truth = tuple(
        dict(item)
        for item in tuple(runtime.provider_capability_truth or ())
        if isinstance(item, dict)
    )
    if runtime_provider_truth:
        payload["provider_capability_truth"] = list(runtime_provider_truth)
        model_lane_defaults = dict(payload.get("model_lane_defaults") or {})
        fast_model = "nulla-qwen3-30b-a3b:nothink"
        if any(str(item.get("model_id") or "").strip() == fast_model for item in runtime_provider_truth):
            model_lane_defaults["default_model"] = fast_model
            model_lane_defaults["fast_local_preferred_model"] = fast_model
            model_lane_defaults["fast_local_installed"] = True
            model_lane_defaults["no_think_default"] = True
            payload["model_lane_defaults"] = model_lane_defaults
    capabilities = [dict(item) for item in list(payload.get("capabilities") or []) if isinstance(item, dict)]
    feature_flags = dict(payload.get("feature_flags") or {})

    if feature_flags.get("helper_mesh_enabled"):
        for item in capabilities:
            if str(item.get("name") or "").strip() == "helper_mesh":
                item["state"] = "implemented"
                item["reason"] = "Helper coordination lanes are enabled for this runtime."
                break

    if public_hive_auth:
        payload["public_hive_auth"] = public_hive_auth
        status = str(public_hive_auth.get("status") or "").strip()
        ok = bool(public_hive_auth.get("ok"))
        for item in capabilities:
            if str(item.get("name") or "").strip() != "public_hive_surface":
                continue
            if ok or status in {"already_configured", "hydrated_from_bundle", "hydrated_from_local_cluster", "synced_from_ssh", "no_auth_required", "disabled"}:
                item["state"] = "implemented"
                item["reason"] = f"Public Hive surface is live for this runtime ({status or 'ready'})."
            else:
                item["state"] = "blocked_by_configuration"
                item["reason"] = f"Public Hive surface is enabled but not ready for writes ({status or 'unknown'})."
            break
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def _health_capability_snapshot(
    runtime: RuntimeServices,
    capability_snapshot_provider: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if capability_snapshot_provider is runtime_capability_snapshot:
        return capability_snapshot_with_runtime(runtime, lambda: {})
    return capability_snapshot_with_runtime(runtime, capability_snapshot_provider)


def _augment_history_from_session_log(
    history: list[dict[str, str]],
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    return augment_history_from_session_log(
        history,
        session_id=session_id,
        user_text=user_text,
        limit=limit,
    )


def _inbound_source_context(body: dict[str, Any]) -> dict[str, Any]:
    payload = body.get("source_context")
    return dict(payload) if isinstance(payload, dict) else {}


def _runtime_model_catalog(
    *,
    capability_snapshot: dict[str, Any],
    default_model_name: str,
) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(model_id: str, *, owned_by: str) -> None:
        normalized = str(model_id or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        catalog.append({"id": normalized, "owned_by": str(owned_by or "nulla-runtime").strip() or "nulla-runtime"})

    _add(default_model_name, owned_by="nulla-runtime")
    for item in list(capability_snapshot.get("provider_capability_truth") or []):
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        model_id = str(item.get("model_id") or "").strip()
        owned_by = provider_id or "nulla-runtime"
        if model_id:
            _add(model_id, owned_by=owned_by)
        if provider_id:
            _add(provider_id, owned_by=owned_by)
    return catalog


def _ollama_tag_parameter_size(model_id: str, *, model_name: str, runtime: RuntimeServices) -> str:
    normalized_model_id = str(model_id or "").strip()
    normalized_default = str(model_name or "").strip()
    if normalized_model_id in {normalized_default, f"{normalized_default}:latest"}:
        return str(runtime.runtime_parameter_size or "").strip() or parameter_size_for_model(runtime.runtime_model_tag)
    return parameter_size_for_model(normalized_model_id)


def _ollama_tag_payload(*, capability_snapshot: dict[str, Any], model_name: str, runtime: RuntimeServices) -> dict[str, Any]:
    models = []
    for entry in _runtime_model_catalog(capability_snapshot=capability_snapshot, default_model_name=model_name):
        parameter_size = _ollama_tag_parameter_size(entry["id"], model_name=model_name, runtime=runtime)
        models.append(
            {
                "name": entry["id"],
                "model": entry["id"],
                "modified_at": datetime.now(timezone.utc).isoformat(),
                "size": 0,
                "digest": "nulla-runtime",
                "details": {
                    "parent_model": "",
                    "format": "nulla",
                    "family": "qwen",
                    "parameter_size": parameter_size,
                    "quantization_level": "runtime",
                },
            }
        )
    return {"models": models}


def _openai_models_payload(*, capability_snapshot: dict[str, Any], model_name: str) -> dict[str, Any]:
    data = []
    for entry in _runtime_model_catalog(capability_snapshot=capability_snapshot, default_model_name=model_name):
        data.append(
            {
                "id": entry["id"],
                "object": "model",
                "created": 0,
                "owned_by": entry["owned_by"],
            }
        )
    return {"object": "list", "data": data}


def dispatch_get(
    *,
    path: str,
    query: dict[str, list[str]],
    runtime: RuntimeServices,
    model_name: str,
    capability_snapshot_provider: Callable[[], dict[str, Any]] = runtime_capability_snapshot,
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    if normalized_path in {"/task-rail", "/trace"}:
        return apply_runtime_headers(
            html_response(
                200,
                render_runtime_task_rail_html(),
                headers={
                    "X-Nulla-Workstation-Version": NULLA_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Nulla-Workstation-Surface": "trace-rail",
                },
            ),
            runtime,
        )

    if normalized_path in {"/web0", "/null-browser"}:
        # The .null browser: a normal browser can't open a .null site (Arweave, behind
        # the resolver), so NULLA serves this entry page. Served here on the always-on
        # API server (11435) - the Meet server (8766) is not guaranteed to be running -
        # and the page's own JS talks back to this same origin.
        from core.null_browser_page import render_null_browser_html

        return apply_runtime_headers(
            html_response(
                200,
                render_null_browser_html(),
                headers={
                    "X-Nulla-Workstation-Version": NULLA_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Nulla-Workstation-Surface": "web0-browser",
                },
            ),
            runtime,
        )

    if normalized_path == "/api/web0/resolve":
        # Read-only: a .null NAME -> its Arweave content URL, so the /web0 browser can
        # load the live site. This is a public on-chain read + a public Arweave gateway
        # URL; no key, no signing, no payment (distinct from the /api/null dial path).
        return apply_runtime_headers(_web0_resolve_response(query), runtime)

    if normalized_path == "/":
        return apply_runtime_headers(text_response(200, "Ollama is running"), runtime)

    if normalized_path == "/api/tags":
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        payload = _ollama_tag_payload(capability_snapshot=capability_snapshot, model_name=model_name, runtime=runtime)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/v1/models":
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        payload = _openai_models_payload(capability_snapshot=capability_snapshot, model_name=model_name)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/healthz", "/v1/healthz"}:
        capability_snapshot = _health_capability_snapshot(runtime, capability_snapshot_provider)
        payload = {
            "ok": True,
            "agent": runtime.display_name,
            "daemon": runtime.daemon is not None,
            "runtime": dict(runtime.runtime_version_stamp or {}),
            "capabilities": capability_snapshot,
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/api/runtime/version", "/v1/runtime/version"}:
        return apply_runtime_headers(json_response(200, dict(runtime.runtime_version_stamp or {})), runtime)

    if normalized_path in {"/api/runtime/capabilities", "/v1/runtime/capabilities"}:
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        return apply_runtime_headers(json_response(200, capability_snapshot), runtime)

    if normalized_path == "/api/runtime/sessions":
        return apply_runtime_headers(json_response(200, {"sessions": list_runtime_sessions(limit=24)}), runtime)

    if normalized_path == "/api/runtime/events":
        session_id = str((query.get("session") or [""])[0] or "").strip()
        after_seq = int(str((query.get("after") or ["0"])[0] or "0"))
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        events = list_runtime_session_events(session_id, after_seq=after_seq, limit=limit)
        next_after = after_seq
        if events:
            next_after = max(int(item.get("seq") or 0) for item in events)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "session_id": session_id,
                    "events": events,
                    "next_after": next_after,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/runtime/control-plane/status":
        return apply_runtime_headers(json_response(200, collect_control_plane_status()), runtime)

    if normalized_path == "/api/runtime/operator-snapshot":
        session_id = str((query.get("session") or [""])[0] or "").strip()
        query_text = str((query.get("query") or [""])[0] or "").strip()
        topic_hints = [str(item).strip() for item in list(query.get("topic_hint") or []) if str(item).strip()]
        return apply_runtime_headers(
            json_response(
                200,
                build_runtime_operator_snapshot(
                    session_id=session_id,
                    query_text=query_text,
                    topic_hints=topic_hints,
                ),
            ),
            runtime,
        )

    if normalized_path in {"/api/adaptation/status", "/api/adaptation/loop"}:
        return apply_runtime_headers(json_response(200, get_adaptation_autopilot_status()), runtime)

    if normalized_path == "/api/adaptation/jobs":
        limit = int(str((query.get("limit") or ["24"])[0] or "24"))
        return apply_runtime_headers(json_response(200, {"jobs": list_adaptation_jobs(limit=max(1, min(limit, 200)))}), runtime)

    if normalized_path == "/api/adaptation/job-events":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "events": list_adaptation_job_events(job_id, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    if normalized_path == "/api/adaptation/evals":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "evals": list_adaptation_eval_runs(job_id=job_id or None, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    if normalized_path in {"/v1/wallet/info", "/api/wallet/info"}:
        try:
            from core.nulla_wallet import get_or_create_wallet
            w = get_or_create_wallet()
            payload = w.export_safe(include_balances=True)
        except Exception as exc:
            payload = {"error": str(exc)}
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/v1/credits/balance", "/api/credits/balance"}:
        try:
            from core.credit_ledger import get_credit_balance, list_credit_ledger_entries
            from network.signer import get_local_peer_id
            raw_limit = str((query.get("limit") or ["20"])[0] or "20")
            limit = int(raw_limit) if raw_limit.isdigit() else 20
            peer_id = str((query.get("peer_id") or [""])[0] or get_local_peer_id())
            payload = {
                "peer_id": peer_id,
                "balance": get_credit_balance(peer_id),
                "entries": list_credit_ledger_entries(peer_id, limit=limit),
            }
        except Exception as exc:
            payload = {"error": str(exc)}
        return apply_runtime_headers(json_response(200, payload), runtime)

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)


def dispatch_post(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    workspace_root_provider,
    normalize_chat_history_provider: Callable[[list[dict[str, Any]]], list[dict[str, str]]] = normalize_chat_history,
    extract_user_message_provider: Callable[[list[dict[str, Any]]], str] = extract_user_message,
    stable_openclaw_session_id_provider: Callable[..., str] = stable_openclaw_session_id,
    run_agent_provider: Callable[..., dict[str, Any]] = run_agent,
    stream_agent_with_events_provider: Callable[..., Iterable[bytes]] = stream_agent_with_events,
    resolve_null_domain_provider: Callable[[str], Any] | None = None,
    try_dial_provider: Callable[..., Any] | None = None,
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    if normalized_path in {"/api/null", "/v1/null"}:
        uri_str = str(body.get("uri") or "").strip()
        prompt = str(body.get("prompt") or body.get("task") or "").strip()
        if not uri_str:
            return apply_runtime_headers(json_response(400, {"error": "missing 'uri' field"}), runtime)
        try:
            from core.null_protocol import NullResponse, parse_null_uri
        except Exception as exc:
            return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)

        # When remote dial is opted in, resolve the .null name carried by the URI
        # (the service segment) so the quote shows the REAL recipient wallet (the
        # on-chain owner) instead of the default. A resolution miss or any error
        # leaves recipient + record unset and the path behaves as before.
        from core import policy_engine

        null_record = None
        recipient_wallet = "stub-wallet"
        # Owner resolution does a live on-chain read, so it ONLY runs when remote
        # dial is opted in — with the flag off this route stays fully local (no
        # network), byte-identical to before this feature.
        if policy_engine.null_dial_enabled():
            resolve_domain = resolve_null_domain_provider
            if resolve_domain is None:
                from core.null_resolver import resolve_null_domain as resolve_domain
            try:
                parsed_uri = parse_null_uri(uri_str)
                null_record = resolve_domain(parsed_uri.service)
                if null_record is not None and getattr(null_record, "owner", ""):
                    recipient_wallet = null_record.owner
            except Exception:
                null_record = None

        try:
            from core.null_protocol import resolve_null_request
            null_req = resolve_null_request(uri_str, recipient_wallet=recipient_wallet)
        except Exception as exc:
            return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
        task_text = prompt or null_req.uri.path or null_req.uri.service

        # Remote dial is opt-in and off by default. When enabled and the resolved
        # record carries a safe x402 endpoint, reach the named agent; otherwise
        # fall through to the unchanged local run.
        dial_result = None
        try:
            if policy_engine.null_dial_enabled() and null_record is not None:
                dial_fn = try_dial_provider
                if dial_fn is None:
                    from core.null_dial import try_dial as dial_fn
                dial_result = dial_fn(
                    uri_str,
                    task_text,
                    record=null_record,
                    wallet=None,
                    allow_spend=False,
                )
        except Exception:
            dial_result = None

        if dial_result is not None:
            null_resp = NullResponse(
                session_id=null_req.session_id,
                service=null_req.uri.service,
                path=null_req.uri.path,
                result=dial_result,
            )
            dial_payload: dict[str, Any] = {
                "session_id": null_resp.session_id,
                "service":    null_resp.service,
                "path":       null_resp.path,
                "result":     null_resp.result,
                "receipt_id": None,
                "zk_proof":   null_resp.zk_proof,
                "dialed":     True,
                "quote": {
                    "amount_usdc":      null_req.quote.amount_usdc,
                    "recipient_wallet": null_req.quote.recipient_wallet,
                } if null_req.quote else None,
            }
            return apply_runtime_headers(json_response(200, dial_payload), runtime)

        try:
            null_result = run_agent_provider(
                runtime,
                task_text,
                session_id=null_req.session_id,
                source_context={"surface": "null_protocol", "null_uri": uri_str, "service": null_req.uri.service},
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": str(exc)}), runtime)
        response_text = str(null_result.get("response") or "").strip()
        null_receipt = None
        try:
            import os

            from core.web0_work_receipt import issue_work_receipt
            null_receipt = issue_work_receipt(
                task_id=null_req.session_id,
                result=response_text or task_text,
                worker_id=str(os.environ.get("NULLA_WORKER_ID") or "nulla"),
            )
        except Exception:
            pass
        null_resp = NullResponse(
            session_id=null_req.session_id,
            service=null_req.uri.service,
            path=null_req.uri.path,
            result=response_text,
            receipt_id=null_receipt.receipt_id if null_receipt else None,
        )
        null_payload: dict[str, Any] = {
            "session_id": null_resp.session_id,
            "service":    null_resp.service,
            "path":       null_resp.path,
            "result":     null_resp.result,
            "receipt_id": null_resp.receipt_id,
            "zk_proof":   null_resp.zk_proof,
            "quote": {
                "amount_usdc":      null_req.quote.amount_usdc,
                "recipient_wallet": null_req.quote.recipient_wallet,
            } if null_req.quote else None,
        }
        return apply_runtime_headers(json_response(200, null_payload), runtime)

    if normalized_path == "/gate/unlock":
        from core.web0_gated_html import NullaGateHandler, gate_cors_headers
        from core.web0_tools import web0_gate_key_store

        result = NullaGateHandler(web0_gate_key_store()).handle(body)
        status = 200 if "aes_key" in result else 403
        if result.get("error") in {"missing_fields", "invalid_wallet_pubkey", "invalid_nonce"}:
            status = 400
        return apply_runtime_headers(json_response(status, result, headers=gate_cors_headers()), runtime)

    if normalized_path in {"/api/chat", "/v1/chat/completions"}:
        messages = list(body.get("messages", []) or [])
        client_history = normalize_chat_history_provider(messages)
        user_text = extract_user_message_provider(messages)
        if not user_text:
            return apply_runtime_headers(json_response(400, {"error": "no user message found"}), runtime)

        model = body.get("model", model_name)
        stream = body.get("stream", False)
        include_runtime_events = bool(body.get("stream_runtime_events") or body.get("include_runtime_events"))
        session_id = stable_openclaw_session_id_provider(body=body, history=client_history, headers=headers)
        history = _augment_history_from_session_log(
            client_history,
            session_id=session_id,
            user_text=user_text,
        )
        inbound_source_context = _inbound_source_context(body)
        requested_workspace = str(
            body.get("workspace")
            or body.get("workspace_root")
            or body.get("cwd")
            or body.get("projectRoot")
            or inbound_source_context.get("workspace")
            or inbound_source_context.get("workspace_root")
            or inbound_source_context.get("cwd")
            or inbound_source_context.get("projectRoot")
            or ""
        ).strip()
        default_workspace = workspace_root_provider()
        source_context = {
            **inbound_source_context,
            "surface": str(inbound_source_context.get("surface") or body.get("surface") or "api").strip() or "api",
            "platform": str(inbound_source_context.get("platform") or body.get("platform") or "api").strip() or "api",
            "client_conversation_history": client_history,
            "client_history_message_count": len(client_history),
            "conversation_history": history,
            "history_message_count": len(history),
            "workspace": requested_workspace or default_workspace,
            "workspace_root": requested_workspace or default_workspace,
        }
        requested_model = str(model or "").strip()
        if requested_model and requested_model not in {str(model_name or "").strip(), f"{str(model_name or '').strip()}:latest"}:
            source_context["requested_model"] = requested_model

        model_status_result = runtime_model_status_response(user_text, runtime)
        if model_status_result is not None:
            result = apply_exact_response_control(dict(model_status_result), user_text)
            if stream:
                stream_iter = ollama_stream_chunks(result, str(model))
                if normalized_path.startswith("/v1/"):
                    return apply_runtime_headers(
                        stream_response(
                            200,
                            openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                            content_type="text/event-stream; charset=utf-8",
                            headers={"Cache-Control": "no-cache"},
                        ),
                        runtime,
                    )
                return apply_runtime_headers(
                    stream_response(200, stream_iter, content_type="application/x-ndjson"),
                    runtime,
                )
            payload = openai_chat_response(result, model) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime)
            payload = _attach_work_receipt(payload, result=result, session_id=session_id)
            return apply_runtime_headers(json_response(200, payload), runtime)

        grounded_result = web0_null_project_response(user_text)
        if grounded_result is not None:
            result = apply_exact_response_control(dict(grounded_result), user_text)
            if stream:
                stream_iter = ollama_stream_chunks(result, str(model))
                if normalized_path.startswith("/v1/"):
                    return apply_runtime_headers(
                        stream_response(
                            200,
                            openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                            content_type="text/event-stream; charset=utf-8",
                            headers={"Cache-Control": "no-cache"},
                        ),
                        runtime,
                    )
                return apply_runtime_headers(
                    stream_response(200, stream_iter, content_type="application/x-ndjson"),
                    runtime,
                )
            payload = openai_chat_response(result, model) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime)
            payload = _attach_work_receipt(payload, result=result, session_id=session_id)
            return apply_runtime_headers(json_response(200, payload), runtime)

        if stream:
            stream_iter = stream_agent_with_events_provider(
                runtime,
                user_text,
                session_id=session_id,
                source_context=source_context,
                model=model,
                include_runtime_events=include_runtime_events,
            )
            if normalized_path.startswith("/v1/"):
                return apply_runtime_headers(
                    stream_response(
                        200,
                        openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                        content_type="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache"},
                    ),
                    runtime,
                )
            return apply_runtime_headers(
                stream_response(200, stream_iter, content_type="application/x-ndjson"),
                runtime,
            )

        try:
            result = run_agent_provider(
                runtime,
                user_text,
                session_id=session_id,
                source_context=source_context,
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": str(exc)}), runtime)
        result = apply_exact_response_control(dict(result or {}), user_text)
        payload = openai_chat_response(result, model) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime)
        payload = _attach_work_receipt(payload, result=result, session_id=session_id)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/generate":
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            return apply_runtime_headers(json_response(400, {"error": "no prompt"}), runtime)
        model = body.get("model", model_name)
        try:
            result = run_agent_provider(
                runtime,
                prompt,
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": str(exc)}), runtime)
        result = apply_exact_response_control(dict(result or {}), prompt)
        response_text = str(result.get("response") or "").strip()
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "model": model,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "response": response_text,
                    "done": True,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/show":
        name = str(body.get("name") or body.get("model") or "").strip()
        if name and name not in {model_name, f"{model_name}:latest"}:
            return apply_runtime_headers(json_response(404, {"error": f"model '{name}' not found"}), runtime)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = {
            "modelfile": f"# NULLA runtime model\nFROM {model_name}",
            "parameters": "stop <|im_end|>",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "nulla",
                "family": "qwen",
                "families": ["qwen"],
                "parameter_size": runtime.runtime_parameter_size,
                "quantization_level": "runtime",
            },
            "model_info": {
                "general.architecture": "qwen2",
                "general.parameter_count": parameter_count_for_model(runtime.runtime_model_tag),
                "general.file_type": 0,
            },
            "modified_at": now,
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/adaptation/loop/tick":
        return apply_runtime_headers(json_response(200, schedule_adaptation_autopilot_tick(force=True, wait=True)), runtime)

    if normalized_path in {"/v1/credits/settle", "/api/credits/settle"}:
        try:
            from core.credit_ledger import reconcile_ledger
            from network.signer import get_local_peer_id
            peer_id = str(body.get("peer_id") or get_local_peer_id())
            result = reconcile_ledger(peer_id)
            resp_payload: dict[str, Any] = {
                "peer_id": result.peer_id,
                "balance": result.balance,
                "entries": result.entries,
                "mode": result.mode,
            }
        except Exception as exc:
            resp_payload = {"error": str(exc)}
        return apply_runtime_headers(json_response(200, resp_payload), runtime)

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)
