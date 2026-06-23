from __future__ import annotations

import argparse
import getpass
import json
import os

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import shutil
import sys as _bootstrap_sys
from pathlib import Path

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)

from apps.nulla_agent import NullaAgent
from apps.nulla_daemon import DaemonConfig, NullaDaemon
from core import policy_engine
from core.adaptation_autopilot import (
    get_adaptation_autopilot_status,
    schedule_adaptation_autopilot_tick,
    score_adaptation_corpus,
)
from core.adaptation_dataset import build_adaptation_corpus
from core.control_plane_workspace import sync_control_plane_workspace
from core.credit_ledger import credit_purchases_enabled, reconcile_ledger
from core.dna_payment_bridge import dna_bridge
from core.dna_wallet_manager import DNAWalletManager
from core.identity_lifecycle import identity_lifecycle_snapshot
from core.identity_manager import load_active_persona
from core.install_recommendations import build_install_recommendation_truth
from core.local_worker_pool import resolve_local_worker_capacity
from core.lora_training_pipeline import promote_adaptation_job, run_adaptation_job
from core.null_protocol import NullProtocolError, resolve_null_request
from core.null_resolver import NullDomainRecord, resolve_null_domain
from core.nulla_user_summary import build_user_summary, render_user_summary
from core.release_channel import release_manifest_snapshot
from core.runtime_backbone import build_provider_registry_snapshot, build_runtime_backbone
from core.runtime_bootstrap import (
    bootstrap_storage_environment,
)
from core.runtime_context import build_runtime_context
from core.runtime_install_profiles import (
    INSTALL_PROFILE_CHOICES,
    PUBLIC_INSTALL_PROFILE_CHOICES,
    active_install_profile_id,
    build_install_profile_truth,
    format_install_profile_id,
    install_profile_display_choices,
    installed_profile_id,
    normalize_install_profile_id,
    persist_install_profile_record,
)
from core.runtime_paths import data_path
from core.trainable_base_manager import stage_trainable_base, trainable_base_status
from core.web0_capability_broadcast import build_manifest_from_env
from network.signer import get_local_peer_id
from storage.adaptation_store import (
    create_adaptation_corpus,
    create_adaptation_job,
    get_adaptation_corpus,
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
    update_corpus_build,
)


def _bootstrap_cli_storage() -> None:
    bootstrap_storage_environment(context=build_runtime_context(mode="cli_storage"))


def cmd_up() -> int:
    # 1. Boot the canonical runtime environment first.
    try:
        backbone = build_runtime_backbone(
            mode="cli_up",
            resolve_backend=True,
        )
    except RuntimeError as exc:
        print(f"Nulla could not start: {exc}")
        return 1

    # 2. Surface provider warnings after policy/bootstrap is loaded.
    provider_warnings = list(backbone.provider_snapshot.warnings)
    if provider_warnings:
        print("Model provider warnings:")
        for warning in provider_warnings:
            print(f" - {warning}")

    # 3. Load local-only persona
    persona = load_active_persona("default")

    # 4. Ensure node identity exists
    peer_id = get_local_peer_id()

    # 5. Auto-detect backend
    selection = backbone.boot.backend_selection
    if selection is None:
        print("Nulla could not start: no supported backend found.")
        print("Install at least one supported runtime: mlx, torch, or onnxruntime.")
        return 1
    hw = selection.hardware
    if selection.backend_name == "remote_only":
        print("No local model backend found. Running in remote-only mode.")

    # 6. Start local agent + local node
    agent = NullaAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    agent_runtime = agent.start()

    pool_hard_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    policy_target = int(policy_engine.get("orchestration.local_worker_pool_target", 0) or 0)
    env_override_raw = str(os.environ.get("NULLA_DAEMON_CAPACITY", "")).strip()
    requested_capacity: int | None = None
    if env_override_raw:
        try:
            requested_capacity = max(1, int(env_override_raw))
        except Exception:
            print(f"Invalid NULLA_DAEMON_CAPACITY='{env_override_raw}', ignoring override.")
            requested_capacity = None
    elif policy_target > 0:
        requested_capacity = policy_target

    daemon_capacity, recommended_capacity = resolve_local_worker_capacity(
        requested=requested_capacity if requested_capacity is not None else None,
        hard_cap=pool_hard_cap,
    )
    if daemon_capacity > recommended_capacity:
        print(
            "WARNING: Local helper capacity override is above recommended "
            f"({daemon_capacity} > {recommended_capacity}). This can degrade stability."
        )

    daemon = NullaDaemon(
        DaemonConfig(
            capacity=int(daemon_capacity),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
        )
    )
    node_runtime = daemon.start()

    print("======================================")
    print("Nulla is yours and running.")
    print("======================================")
    print(f"OS:            {hw.os_name}")
    print(f"Machine:       {hw.machine}")
    print(f"Backend:       {agent_runtime.backend_name}")
    print(f"Device:        {agent_runtime.device}")
    print(f"Persona:       {persona.display_name} ({persona.persona_id})")
    print(f"Tone:          {persona.tone}")
    print(f"Spirit lock:   {'enabled' if persona.personality_locked else 'disabled'}")
    print(f"Swarm:         {'enabled' if agent_runtime.swarm_enabled else 'disabled'}")
    print(f"Node:          {node_runtime.host}:{node_runtime.port}")
    print(f"Public Node:   {node_runtime.public_host}:{node_runtime.public_port}")
    print(f"Helper Pool:   {daemon_capacity} (recommended: {recommended_capacity})")
    print(f"Peer ID:       {peer_id[:24]}...")
    print(f"Reason:        {selection.reason}")
    print(f"Safety mode:   {policy_engine.get('execution.default_mode')}")
    print("Identity:      local-only / never synced")
    print("Mode:          standalone-capable / optional sidecars")
    return 0


def cmd_summary(json_mode: bool = False, limit: int = 5) -> int:
    _bootstrap_cli_storage()
    report = build_user_summary(limit_recent=max(1, min(int(limit), 20)))
    if json_mode:
        import json

        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_user_summary(report))
    return 0


def _strip_null_suffix(name: str) -> str:
    """Normalize a .null name: trim whitespace and a single trailing '.null'."""
    value = str(name or "").strip()
    if value.lower().endswith(".null"):
        value = value[: -len(".null")]
    return value.strip()


def _explorer_tx_link(signature: str) -> str:
    """Copy-pasteable mainnet explorer link for a Solana tx signature."""
    return f"https://explorer.solana.com/tx/{signature}"


def resolve_record_payload(name: str, record: NullDomainRecord | None) -> dict[str, object]:
    """Pure: shape a resolved .null record (or a miss) into a serializable payload."""
    if record is None:
        return {"name": name, "resolved": False}
    return {
        "name": name,
        "resolved": True,
        "owner": record.owner,
        "arweave_txid": record.arweave_txid,
        "x402_endpoint": record.x402_endpoint or "",
        "passport_present": record.passport_hash is not None,
    }


def render_resolve_lines(payload: dict[str, object]) -> list[str]:
    """Pure: human-readable lines for a resolve payload."""
    name = str(payload.get("name") or "")
    lines = [
        "NULLA .null resolution",
        "======================",
        f"Name:           {name}.null",
    ]
    if not payload.get("resolved"):
        lines.append("Status:         unresolved (no on-chain record)")
        return lines
    arweave = payload.get("arweave_txid")
    endpoint = str(payload.get("x402_endpoint") or "")
    lines.extend(
        [
            f"Owner:          {payload.get('owner')}",
            f"Arweave txid:   {arweave if arweave else 'none'}",
            f"x402 endpoint:  {endpoint if endpoint else 'unset'}",
            f"Passport:       {'present' if payload.get('passport_present') else 'none'}",
        ]
    )
    return lines


def cmd_resolve(name: str, *, json_mode: bool = False) -> int:
    clean = _strip_null_suffix(name)
    if not clean:
        print("usage: nulla resolve <name>.null")
        return 2
    record = resolve_null_domain(clean)
    payload = resolve_record_payload(clean, record)
    if json_mode:
        _emit_json(payload)
        return 0 if payload.get("resolved") else 1
    print("\n".join(render_resolve_lines(payload)))
    return 0 if payload.get("resolved") else 1


WEB_DISABLED_MESSAGE = "Web access is off. Enable it with NULLA_ENABLE_WEB=1 (web is opt-in and off by default)."


def _run_web_intent(intent: str, arguments: dict[str, object]) -> object:
    """Dispatch a single web tool intent through the canonical executor.

    Only reached when web is enabled; the caller gates on the policy flag so no
    network call happens while web is off.
    """
    from core.hive_activity_tracker import HiveActivityTracker
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": dict(arguments)},
        task_id="cli-web",
        session_id="cli-web",
        source_context={"surface": "cli", "platform": "nulla-cli"},
        hive_activity_tracker=HiveActivityTracker(),
    )


def cmd_web(
    *,
    query: str = "",
    fetch_url: str = "",
    render_url: str = "",
    limit: int = 5,
    json_mode: bool = False,
) -> int:
    """Run a live web search/fetch/browse, only when web access is opted in."""
    if fetch_url:
        intent, arguments, requested = "web.fetch", {"url": fetch_url}, fetch_url
    elif render_url:
        intent, arguments, requested = "browser.render", {"url": render_url}, render_url
    else:
        clean_query = str(query or "").strip()
        if not clean_query:
            print("usage: nulla web <query> | --fetch <url> | --browse <url>")
            return 2
        intent, arguments, requested = (
            "web.search",
            {"query": clean_query, "limit": max(1, min(int(limit or 5), 5))},
            clean_query,
        )

    if not policy_engine.allow_web_fallback():
        if json_mode:
            _emit_json({"intent": intent, "enabled": False, "message": WEB_DISABLED_MESSAGE})
        else:
            print(WEB_DISABLED_MESSAGE)
        return 2

    _bootstrap_cli_storage()
    execution = _run_web_intent(intent, arguments)
    response_text = str(getattr(execution, "response_text", "") or "").strip()
    ok = bool(getattr(execution, "ok", False))
    status = str(getattr(execution, "status", "") or "").strip()
    if json_mode:
        _emit_json(
            {
                "intent": intent,
                "enabled": True,
                "requested": requested,
                "ok": ok,
                "status": status,
                "response_text": response_text,
            }
        )
        return 0 if ok else 1
    print(response_text or f"web {intent} returned no output (status={status or 'unknown'}).")
    return 0 if ok else 1


_RECEIPT_EVENT_TYPES = ("solana_proof_anchored", "parent_output_finalized")


def _load_receipt_rows(limit: int = 50) -> list[dict[str, object]]:
    """Local SQLite read of anchored / finalized proof events from audit_log."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT event_type, target_id, details_json, created_at
                FROM audit_log
                WHERE event_type IN (?, ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (_RECEIPT_EVENT_TYPES[0], _RECEIPT_EVENT_TYPES[1], max(1, int(limit))),
            ).fetchall()
        ]
    finally:
        conn.close()
    return rows


def format_receipt_row(row: dict[str, object]) -> dict[str, object]:
    """Pure: shape one audit_log row into a receipt entry with an explorer link."""
    details = row.get("details") if isinstance(row.get("details"), dict) else None
    if details is None:
        raw = row.get("details_json")
        try:
            details = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            details = {}
    signature = str(details.get("signature") or "")
    entry: dict[str, object] = {
        "event_type": str(row.get("event_type") or ""),
        "task_id": str(row.get("target_id") or ""),
        "confidence": float(details.get("confidence") or 0.0),
        "signature": signature,
        "created_at": str(row.get("created_at") or ""),
    }
    entry["explorer_link"] = _explorer_tx_link(signature) if signature else ""
    return entry


def render_receipt_lines(entries: list[dict[str, object]]) -> list[str]:
    """Pure: human-readable receipt block."""
    lines = ["NULLA receipts", "=============="]
    if not entries:
        lines.append("No anchored or finalized proofs recorded yet.")
        return lines
    for entry in entries:
        lines.append(f"{entry['event_type']}")
        lines.append(f"  Task:      {entry['task_id'] or 'unknown'}")
        lines.append(f"  Confidence:{' '}{float(entry['confidence']):.2f}")
        if entry.get("signature"):
            lines.append(f"  Signature: {entry['signature']}")
            lines.append(f"  Explorer:  {entry['explorer_link']}")
        else:
            lines.append("  Signature: none (local finalize, not yet anchored)")
        lines.append(f"  When:      {entry['created_at']}")
    return lines


def cmd_receipts(*, limit: int = 20, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = _load_receipt_rows(limit=max(1, min(int(limit), 200)))
    entries = [format_receipt_row(row) for row in rows]
    if json_mode:
        _emit_json(entries)
        return 0
    print("\n".join(render_receipt_lines(entries)))
    return 0


def render_manifest_lines(manifest_dict: dict[str, object]) -> list[str]:
    """Pure: human-readable capability+price card for this node's manifest."""
    provider_ids = list(manifest_dict.get("provider_ids") or [])
    tools = list(manifest_dict.get("tools") or [])
    return [
        "NULLA capability manifest",
        "=========================",
        f"Worker ID:      {manifest_dict.get('worker_id')}",
        f"Top tier:       {manifest_dict.get('top_tier')}",
        f"Top TPS:        {float(manifest_dict.get('top_tps') or 0.0):.2f}",
        f"Context window: {int(manifest_dict.get('context_window') or 0)}",
        f"Providers:      {', '.join(provider_ids) if provider_ids else 'none registered'}",
        f"Tools:          {', '.join(tools) if tools else 'none'}",
        f"Price/token:    {float(manifest_dict.get('price_per_token_usdc') or 0.0):.8f} USDC",
        f"Privacy mode:   {manifest_dict.get('privacy_mode')}",
    ]


def cmd_manifest(*, json_mode: bool = False) -> int:
    manifest = build_manifest_from_env()
    payload = manifest.to_dict()
    if json_mode:
        _emit_json(payload)
        return 0
    print("\n".join(render_manifest_lines(payload)))
    return 0


def _quote_target_to_uri(target: str) -> str:
    """Map a `null://...` URI or a `<name>.null` into a null:// task URI."""
    value = str(target or "").strip()
    if value.lower().startswith("null://"):
        return value
    name = _strip_null_suffix(value)
    return f"null://task/{name}" if name else ""


def render_quote_lines(payload: dict[str, object]) -> list[str]:
    """Pure: invoice preview for a resolved null:// request."""
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    quote = quote or {}
    return [
        "NULLA sell-quote preview",
        "========================",
        f"URI:            {payload.get('uri')}",
        f"Service:        {payload.get('service')}",
        f"Path:           {payload.get('path') or '(none)'}",
        f"Session ID:     {payload.get('session_id')}",
        f"Amount:         {float(quote.get('amount_usdc') or 0.0):.8f} USDC",
        f"Recipient:      {quote.get('recipient_wallet') or 'unset'}",
        f"USDC mint:      {quote.get('usdc_mint') or 'unset'}",
        f"Quote hash:     {quote.get('quote_hash') or 'unset'}",
    ]


def cmd_sell_quote(target: str, *, json_mode: bool = False) -> int:
    uri = _quote_target_to_uri(target)
    if not uri:
        print("usage: nulla sell-quote [null://service/path | <name>.null]")
        return 2
    try:
        request = resolve_null_request(uri)
    except NullProtocolError as exc:
        print(f"Invalid null:// request: {exc}")
        return 2
    quote = request.quote
    payload: dict[str, object] = {
        "uri": request.uri.raw,
        "service": request.uri.service,
        "path": request.uri.path,
        "session_id": request.session_id,
        "quote": None
        if quote is None
        else {
            "amount_usdc": quote.amount_usdc,
            "recipient_wallet": quote.recipient_wallet,
            "usdc_mint": quote.usdc_mint,
            "quote_hash": quote.quote_hash,
        },
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("\n".join(render_quote_lines(payload)))
    return 0


DIAL_DISABLED_MESSAGE = (
    "Remote dial is opt-in; enable with NULLA_ENABLE_NULL_DIAL=1 "
    "(remote dial is off by default). Payment is separately gated by "
    "--allow-spend within a cap."
)


def cmd_dial(
    name: str,
    task: str,
    *,
    allow_spend: bool = False,
    max_spend_usdc: float = 1.0,
    json_mode: bool = False,
) -> int:
    """Reach a named .null agent's endpoint and return its result.

    Gated on the null-dial policy flag: when off, this prints how to enable it and
    makes ZERO network calls.
    """
    if not policy_engine.null_dial_enabled():
        if json_mode:
            _emit_json({"enabled": False, "message": DIAL_DISABLED_MESSAGE})
        else:
            print(DIAL_DISABLED_MESSAGE)
        return 2

    clean = _strip_null_suffix(name)
    task_text = str(task or "").strip()
    if not clean or not task_text:
        print('usage: nulla dial <name>.null "<task>" [--allow-spend --max-spend <usdc>]')
        return 2

    from core.null_dial import try_dial

    record = resolve_null_domain(clean)
    if record is None:
        payload = {"name": clean, "resolved": False, "dialed": False}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null did not resolve to an on-chain record.")
        return 1
    if not record.x402_endpoint:
        payload = {"name": clean, "resolved": True, "dialed": False, "reason": "no_endpoint"}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null has no x402 endpoint set; nothing to dial.")
        return 1

    wallet = None
    if allow_spend:
        from core.nulla_wallet import NullaWallet

        candidate = NullaWallet()
        if candidate.exists():
            wallet = candidate.load()

    uri = _quote_target_to_uri(clean)
    result = try_dial(
        uri,
        task_text,
        record=record,
        wallet=wallet,
        allow_spend=bool(allow_spend),
        max_spend_usdc=float(max_spend_usdc),
    )
    if result is None:
        payload = {"name": clean, "resolved": True, "dialed": False, "reason": "no_remote_result"}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null could not be reached; run it locally instead.")
        return 1

    payload = {"name": clean, "resolved": True, "dialed": True, "endpoint": record.x402_endpoint, "result": result}
    if json_mode:
        _emit_json(payload)
    else:
        print(f"NULLA dial -> {clean}.null ({record.x402_endpoint})")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_x402_pay(
    amount_usdc: float,
    recipient: str,
    *,
    keypair_path: str = "",
    mainnet: bool = False,
    asset_mint: str = "",
    allow_spend: bool = False,
    json_mode: bool = False,
) -> int:
    """Settle one x402 "exact" payment on Solana via the PayAI facilitator.

    Devnet by default. Gated on ``--allow-spend``: without it this is a DRY RUN
    (no network, no spend) that prints what it would pay; with it, it settles a
    real on-chain payment and prints the transaction signature. The recipient's
    associated token account for the asset must already exist.
    """
    from core.x402.client import X402Client, X402Config, X402Mode

    recipient = str(recipient or "").strip()
    if amount_usdc <= 0 or not recipient:
        print('usage: nulla x402-pay <amount_usdc> <recipient_pubkey> '
              '--keypair <path> [--mainnet --asset <mint>] --allow-spend')
        return 2

    mode = X402Mode.MAINNET if mainnet else X402Mode.DEVNET
    network = "mainnet" if mainnet else "devnet"

    if not allow_spend:
        msg = (f"Dry run — no spend. Would settle {amount_usdc} (asset: "
               f"{asset_mint or 'cluster USDC'}) to {recipient} on {network}. "
               f"Re-run with --allow-spend to settle for real.")
        if json_mode:
            _emit_json({"would_pay": True, "amount_usdc": amount_usdc,
                        "recipient": recipient, "network": network,
                        "asset": asset_mint or "cluster-usdc", "message": msg})
        else:
            print(msg)
        return 0

    if not keypair_path:
        print("error: --keypair <solana JSON keypair path> is required to pay.")
        return 2

    cfg = X402Config(mode=mode, keypair_path=keypair_path, asset_mint=(asset_mint or None))
    try:
        receipt = X402Client(cfg).pay(amount_usdc=amount_usdc, recipient_wallet=recipient)
    except Exception as exc:
        if json_mode:
            _emit_json({"error": str(exc), "network": network})
        else:
            print(f"payment failed: {exc}")
        return 1

    explorer = (f"https://explorer.solana.com/tx/{receipt.payment_tx}"
                f"{'' if mainnet else '?cluster=devnet'}")
    if json_mode:
        _emit_json({**receipt.to_dict(), "network": network, "explorer": explorer})
    else:
        print(f"paid {amount_usdc} on {network}")
        print(f"  tx: {receipt.payment_tx}")
        print(f"  {explorer}")
    return 0


def cmd_providers(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    context = build_runtime_context(mode="cli_storage")
    snapshot = build_provider_registry_snapshot(
        runtime_home=str(context.paths.runtime_home),
        honor_install_profile=True,
    )
    rows = list(snapshot.audit_rows)
    if json_mode:
        import json

        print(
            json.dumps(
                [
                    {
                        "provider_id": row.provider_id,
                        "source_type": row.source_type,
                        "license_name": row.license_name,
                        "weight_location": row.weight_location,
                        "redistribution_allowed": row.redistribution_allowed,
                        "warnings": row.warnings,
                    }
                    for row in rows
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not rows:
        print("No model providers are registered.")
        return 0

    print("NULLA model providers")
    print("=====================")
    for row in rows:
        print(f"{row.provider_id}")
        print(f"  Source:          {row.source_type}")
        print(f"  License:         {row.license_name or 'MISSING'}")
        print(f"  License ref:     {row.license_reference or 'MISSING'}")
        print(f"  Runtime dep:     {row.runtime_dependency or 'MISSING'}")
        print(f"  Weight location: {row.weight_location}")
        print(f"  Weights bundled: {row.weights_bundled}")
        print(f"  Redistribution:  {row.redistribution_allowed if row.redistribution_allowed is not None else 'unknown'}")
        if row.warnings:
            print(f"  Warnings:        {'; '.join(row.warnings)}")
        else:
            print("  Warnings:        none")
    return 0


def cmd_install_profile(*, set_profile: str = "", json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    context = build_runtime_context(mode="cli_install_profile")
    runtime_home = str(context.paths.runtime_home)
    requested_profile = str(set_profile or "").strip().lower()
    requested_profile_normalized = normalize_install_profile_id(requested_profile, allow_auto=True)
    stored_profile = installed_profile_id(runtime_home)
    provider_snapshot = build_provider_registry_snapshot(
        runtime_home=runtime_home,
        requested_profile=requested_profile_normalized or requested_profile or None,
        honor_install_profile=False,
    )
    active_profile = active_install_profile_id(runtime_home=runtime_home, allow_auto=True)
    install_recommendation = build_install_recommendation_truth(runtime_home=runtime_home)

    if requested_profile:
        profile = build_install_profile_truth(
            requested_profile=requested_profile_normalized or requested_profile,
            runtime_home=runtime_home,
            provider_capability_truth=provider_snapshot.capability_truth,
        )
        if not profile.ready:
            message = "; ".join(profile.reasons) or "selected install profile is not ready on this machine"
            if json_mode:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "requested_profile": requested_profile,
                            "install_recommendation": install_recommendation.to_dict(),
                            "resolved_profile": profile.to_dict(),
                            "error": message,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(f"Install profile switch blocked: {requested_profile}")
                print(message)
            return 2
        saved_profile = profile.profile_id
        record_kwargs = {
            "selected_model": profile.selected_model,
        }
        if profile.selected_models:
            record_kwargs["selected_models"] = profile.selected_models
        if profile.bundle_id:
            record_kwargs["bundle_id"] = profile.bundle_id
        if profile.bundle_kind:
            record_kwargs["bundle_kind"] = profile.bundle_kind
        record_path = persist_install_profile_record(
            runtime_home,
            saved_profile,
            **record_kwargs,
        )
        if json_mode:
            print(
                json.dumps(
                        {
                            "ok": True,
                            "record_path": str(record_path),
                            "requested_profile": requested_profile,
                            "saved_profile": saved_profile,
                            "install_recommendation": install_recommendation.to_dict(),
                            "resolved_profile": profile.to_dict(),
                            "next_step": "Restart NULLA to apply the new provider mix.",
                        },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Install profile saved: {format_install_profile_id(saved_profile, allow_auto=False)}")
            print(f"Resolved profile:     {format_install_profile_id(profile.profile_id, allow_auto=False)} ({profile.label})")
            print(f"Summary:              {profile.summary}")
            if profile.selected_models:
                print(f"Bundle models:        {', '.join(profile.selected_models)}")
            print(f"Record:               {record_path}")
            print("Next step:            Restart NULLA to apply the new provider mix.")
        return 0

    profile = build_install_profile_truth(
        requested_profile=None,
        runtime_home=runtime_home,
        provider_capability_truth=provider_snapshot.capability_truth,
    )
    if json_mode:
        print(
            json.dumps(
                {
                    "runtime_home": runtime_home,
                    "stored_profile_id": stored_profile or "",
                    "requested_profile_id": active_profile or "",
                    "available_profiles": list(PUBLIC_INSTALL_PROFILE_CHOICES),
                    "all_profiles": list(INSTALL_PROFILE_CHOICES),
                    "install_recommendation": install_recommendation.to_dict(),
                    "resolved_profile": profile.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print("NULLA install profile")
    print("=====================")
    print(f"Runtime home:    {runtime_home}")
    print(f"Stored profile:  {format_install_profile_id(stored_profile, allow_auto=False) if stored_profile else 'none'}")
    resolved_profile_display = format_install_profile_id(profile.profile_id, allow_auto=False)
    print(f"Resolved profile:{' ' if resolved_profile_display else ''}{resolved_profile_display} ({profile.label})")
    print(
        f"Recommended default: {format_install_profile_id(install_recommendation.recommended_default_profile, allow_auto=False)}"
    )
    bundle_models = ", ".join(install_recommendation.recommended_bundle_models)
    if bundle_models:
        print(
            f"Recommended bundle:  {install_recommendation.recommended_bundle_id} "
            f"({install_recommendation.recommended_bundle_kind}) -> {bundle_models}"
        )
    fallback_models = ", ".join(install_recommendation.fallback_bundle_models)
    if fallback_models:
        print(f"Lighter fallback:    {install_recommendation.fallback_bundle_id} -> {fallback_models}")
    optional_profile_display = format_install_profile_id(
        install_recommendation.recommended_optional_profile,
        allow_auto=False,
    )
    if optional_profile_display:
        print(f"Optional stronger:   {optional_profile_display} via {install_recommendation.secondary_local_backend}")
    print(f"Summary:         {profile.summary}")
    print("Available:       " + ", ".join(install_profile_display_choices()))
    if profile.reasons:
        print("Notes:")
        for reason in profile.reasons:
            print(f" - {reason}")
    return 0


def cmd_identity_report(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    report = identity_lifecycle_snapshot()
    import json

    if json_mode:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print("NULLA identity lifecycle")
    print("======================")
    print(f"Active peer: {report['active_local_peer_id']}")
    print(f"Key path:    {report['key_path']}")
    print(f"Revocations: {len(report['revocations'])}")
    print(f"Key history: {len(report['key_history'])}")
    return 0


def cmd_release_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    report = release_manifest_snapshot()
    import json

    if json_mode:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print("NULLA release status")
    print("====================")
    print(f"Channel:       {report['channel_name']}")
    print(f"Version:       {report['release_version']}")
    print(f"Protocol:      {report['protocol_version']}")
    print(f"Schema gen:    {report['schema_generation']}")
    print(f"Min compat:    {report['minimum_compatible_release']}")
    print(f"Rollout stage: {report['rollout_stage']}")
    warnings = list(report.get("warnings") or [])
    print(f"Warnings:      {len(warnings)}")
    for warning in warnings:
        print(f" - {warning}")
    return 0


def _resolve_secret(value: str | None, *, prompt: str) -> str:
    raw = str(value or "").strip()
    if raw:
        return raw
    return getpass.getpass(prompt)


def _emit_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_credits(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    peer_id = get_local_peer_id()
    recon = reconcile_ledger(peer_id)
    if json_mode:
        import json
        print(json.dumps({
            "peer_id": recon.peer_id,
            "balance": recon.balance,
            "entries": recon.entries,
            "mode": recon.mode,
        }, indent=2))
        return 0
    print("NULLA compute credits")
    print("=====================")
    print(f"Peer ID:     {peer_id[:24]}...")
    print(f"Balance:     {recon.balance:.2f} credits")
    print(f"Ledger rows: {recon.entries}")
    print(f"Mode:        {recon.mode}")
    return 0


def cmd_adaptation_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = get_adaptation_autopilot_status()
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA adaptation status")
    print("======================")
    print(f"Deps ok:      {payload['dependency_status']['ok']}")
    print(f"Device:       {payload['dependency_status']['device']}")
    print(f"Modules:      {payload['dependency_status']['modules']}")
    print(f"Loop status:  {(payload.get('loop_state') or {}).get('status') or 'idle'!s}")
    print(f"Decision:     {(payload.get('loop_state') or {}).get('last_decision') or ''!s}")
    print(f"Reason:       {(payload.get('loop_state') or {}).get('last_reason') or ''!s}")
    print(f"Corpora:      {len(payload['recent_corpora'])}")
    print(f"Jobs:         {len(payload['recent_jobs'])}")
    print(f"Evals:        {len(payload['recent_evals'])}")
    print(f"Worker:       {'running' if payload.get('worker_running') else 'idle'}")
    return 0


def cmd_adaptation_corpus(
    *,
    corpus_id: str,
    label: str,
    include_conversations: bool,
    include_final_responses: bool,
    include_hive_posts: bool,
    limit_per_source: int,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    if str(corpus_id or "").strip():
        result = build_adaptation_corpus(str(corpus_id).strip())
        payload = {
            "corpus_id": result.corpus_id,
            "output_path": result.output_path,
            "example_count": result.example_count,
            "source_stats": result.source_stats,
        }
    else:
        corpus = create_adaptation_corpus(
            label=str(label or "").strip() or "default-corpus",
            source_config={
                "include_conversations": bool(include_conversations),
                "include_final_responses": bool(include_final_responses),
                "include_hive_posts": bool(include_hive_posts),
                "limit_per_source": max(1, int(limit_per_source)),
            },
        )
        result = build_adaptation_corpus(str(corpus["corpus_id"]))
        payload = {
            "corpus_id": result.corpus_id,
            "output_path": result.output_path,
            "example_count": result.example_count,
            "source_stats": result.source_stats,
        }
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA adaptation corpus")
    print("=======================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    print(f"Sources:      {payload['source_stats']}")
    return 0


def cmd_adaptation_corpus_import(*, input_path: str, label: str, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    source = Path(str(input_path or "").strip()).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Corpus input does not exist: {source}")
    corpus = create_adaptation_corpus(
        label=str(label or "").strip() or source.stem,
        source_config={"imported": True, "source_path": str(source)},
    )
    dest = data_path("adaptation", "corpora", f"{corpus['corpus_id']}.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    example_count = sum(1 for line in dest.read_text(encoding="utf-8").splitlines() if line.strip())
    update_corpus_build(corpus["corpus_id"], output_path=str(dest), example_count=example_count, source_stats={"imported": example_count})
    score = score_adaptation_corpus(corpus["corpus_id"], str(dest))
    payload = {
        "corpus_id": corpus["corpus_id"],
        "output_path": str(dest),
        "example_count": example_count,
        "quality_score": score.quality_score,
        "content_hash": score.content_hash,
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA imported adaptation corpus")
    print("===============================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    print(f"Quality:      {payload['quality_score']:.4f}")
    return 0


def cmd_adaptation_corpus_export(*, corpus_id: str, output_path: str, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    corpus = get_adaptation_corpus(str(corpus_id or "").strip())
    if not corpus:
        raise ValueError(f"Unknown corpus: {corpus_id}")
    source = Path(str(corpus.get("output_path") or "")).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Corpus file does not exist: {source}")
    target = Path(str(output_path or "").strip()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    payload = {
        "corpus_id": corpus["corpus_id"],
        "source_path": str(source),
        "output_path": str(target),
        "example_count": int(corpus.get("example_count") or 0),
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA exported adaptation corpus")
    print("================================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Source:       {payload['source_path']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    return 0


def cmd_adaptation_job_create(
    *,
    corpus_id: str,
    base_model_ref: str,
    base_provider_name: str,
    base_model_name: str,
    adapter_provider_name: str,
    adapter_model_name: str,
    license_name: str,
    license_reference: str,
    capabilities: list[str],
    target_modules: list[str],
    epochs: int,
    max_steps: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    cutoff_len: int,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    promote: bool = False,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    payload = create_adaptation_job(
        corpus_id=str(corpus_id or "").strip(),
        base_model_ref=str(base_model_ref or "").strip(),
        base_provider_name=str(base_provider_name or "").strip(),
        base_model_name=str(base_model_name or "").strip(),
        adapter_provider_name=str(adapter_provider_name or "").strip(),
        adapter_model_name=str(adapter_model_name or "").strip(),
        training_config={
            "license_name": str(license_name or "").strip(),
            "license_reference": str(license_reference or "").strip(),
            "capabilities": list(capabilities or []),
            "target_modules": list(target_modules or []),
            "epochs": max(1, int(epochs)),
            "max_steps": max(1, int(max_steps)),
            "batch_size": max(1, int(batch_size)),
            "gradient_accumulation_steps": max(1, int(gradient_accumulation_steps)),
            "learning_rate": float(learning_rate),
            "cutoff_len": max(128, int(cutoff_len)),
            "lora_r": max(1, int(lora_r)),
            "lora_alpha": max(1, int(lora_alpha)),
            "lora_dropout": max(0.0, float(lora_dropout)),
        },
    )
    if promote:
        payload["promote_requested"] = True
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA adaptation job")
    print("====================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Base model:   {payload['base_model_ref']}")
    print(f"Status:       {payload['status']}")
    return 0


def cmd_adaptation_jobs(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_jobs(limit=100)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation jobs exist.")
        return 0
    print("NULLA adaptation jobs")
    print("=====================")
    for row in rows:
        print(f"{row['job_id']}")
        print(f"  Corpus:   {row['corpus_id']}")
        print(f"  Base:     {row['base_model_ref']}")
        print(f"  Status:   {row['status']}")
        print(f"  Device:   {row['device'] or 'pending'}")
        if row.get("output_dir"):
            print(f"  Output:   {row['output_dir']}")
        if row.get("error_text"):
            print(f"  Error:    {row['error_text']}")
    return 0


def cmd_adaptation_eval_runs(*, job_id: str = "", json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_eval_runs(job_id=str(job_id or "").strip() or None, limit=100)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation eval runs exist.")
        return 0
    print("NULLA adaptation eval runs")
    print("=========================")
    for row in rows:
        print(f"{row['eval_id']}")
        print(f"  Job:      {row['job_id']}")
        print(f"  Kind:     {row['eval_kind']}")
        print(f"  Status:   {row['status']}")
        print(f"  Delta:    {row['score_delta']:.4f}")
        print(f"  Decision: {row['decision']}")
    return 0


def cmd_adaptation_job_events(job_id: str, *, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_job_events(str(job_id or "").strip(), limit=500)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation job events found.")
        return 0
    print("NULLA adaptation job events")
    print("===========================")
    for row in rows:
        print(f"[{row['seq']:03d}] {row['event_type']}: {row['message']}")
    return 0


def cmd_adaptation_job_run(job_id: str, *, promote: bool = False, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = run_adaptation_job(str(job_id or "").strip(), promote=bool(promote))
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA adaptation run")
    print("====================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    print(f"Device:       {payload.get('device') or 'unknown'}")
    if payload.get("output_dir"):
        print(f"Output:       {payload['output_dir']}")
    if payload.get("error_text"):
        print(f"Error:        {payload['error_text']}")
        return 1
    return 0


def cmd_adaptation_job_promote(job_id: str, *, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = promote_adaptation_job(str(job_id or "").strip())
    if json_mode:
        _emit_json(payload)
        return 0
    manifest = payload.get("registered_manifest") or {}
    print("NULLA adaptation promotion")
    print("==========================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    print(f"Provider:     {manifest.get('provider_name', '')}:{manifest.get('model_name', '')}")
    return 0


def cmd_adaptation_loop_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = get_adaptation_autopilot_status()
    if json_mode:
        _emit_json(payload)
        return 0
    loop_state = dict(payload.get("loop_state") or {})
    print("NULLA adaptation loop")
    print("=====================")
    print(f"Status:       {loop_state.get('status', 'idle')}")
    print(f"Decision:     {loop_state.get('last_decision', '')}")
    print(f"Reason:       {loop_state.get('last_reason', '')}")
    print(f"Active job:   {loop_state.get('active_job_id', '')}")
    print(f"Active model: {loop_state.get('active_provider_name', '')}:{loop_state.get('active_model_name', '')}")
    print(f"Last eval:    {loop_state.get('last_eval_id', '')}")
    print(f"Last canary:  {loop_state.get('last_canary_eval_id', '')}")
    return 0


def cmd_adaptation_loop_tick(*, force: bool = False, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = schedule_adaptation_autopilot_tick(force=bool(force), wait=True)
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA adaptation loop tick")
    print("==========================")
    print(f"Status:       {payload.get('status', '')}")
    print(f"Decision:     {payload.get('last_decision', '')}")
    print(f"Reason:       {payload.get('last_reason', '')}")
    return 0


def cmd_control_plane_sync(*, json_mode: bool = False) -> int:
    payload = sync_control_plane_workspace()
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA control-plane workspace sync")
    print("=================================")
    print(f"Workspace:    {payload['workspace_root']}")
    print(f"Control root: {payload['control_root']}")
    print(f"Templates:    {payload['templates_root']}")
    print(f"Writes:       {payload['writes']}")
    print(f"Open tasks:   {payload['open_task_count']}")
    print(f"Runs:         {payload['runtime_session_count']}")
    print(f"Approvals:    {payload['pending_approval_count']} backlog")
    print(f"Runtime wait: {payload.get('runtime_pending_approval_count', 0)} session(s)")
    return 0


def cmd_trainable_base_status(*, json_mode: bool = False) -> int:
    payload = trainable_base_status()
    if json_mode:
        _emit_json(payload)
        return 0
    active = dict(payload.get("active_policy") or {})
    print("NULLA trainable base status")
    print("===========================")
    print(f"Active base:  {active.get('base_model_name', '')}")
    print(f"Base ref:     {active.get('base_model_ref', '')}")
    print(f"Provider:     {active.get('base_provider_name', '')}")
    print(f"Staged bases: {len(payload.get('staged_bases') or [])}")
    return 0


def cmd_stage_trainable_base(
    *,
    model_ref: str,
    activate: bool,
    verify_load: bool,
    force_download: bool,
    license_name: str,
    license_reference: str,
    trust_remote_code: bool,
    json_mode: bool = False,
) -> int:
    payload = stage_trainable_base(
        model_ref=str(model_ref or "").strip() or "qwen-0.5b",
        activate=bool(activate),
        verify_load=bool(verify_load),
        force_download=bool(force_download),
        license_name=str(license_name or "").strip(),
        license_reference=str(license_reference or "").strip(),
        trust_remote_code=bool(trust_remote_code),
    )
    if json_mode:
        _emit_json(payload)
        return 0
    print("NULLA trainable base")
    print("====================")
    print(f"Model:        {payload['model_name']}")
    print(f"Repo:         {payload['model_id']}")
    print(f"Path:         {payload['local_path']}")
    print(f"Activated:    {payload['activated']}")
    verification = dict(payload.get("verification") or {})
    if verification:
        print(f"Tokenizer:    {verification.get('tokenizer_class', '')}")
        print(f"Parameters:   {verification.get('parameter_count', 0)}")
    return 0


def cmd_adaptation_autopilot(
    *,
    label: str,
    base_model_ref: str,
    base_provider_name: str,
    base_model_name: str,
    adapter_provider_name: str,
    adapter_model_name: str,
    limit_per_source: int,
    epochs: int,
    max_steps: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    cutoff_len: int,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    license_name: str,
    license_reference: str,
    capabilities: list[str],
    target_modules: list[str],
    promote: bool = False,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    corpus = create_adaptation_corpus(
        label=str(label or "").strip() or "autopilot-corpus",
        source_config={
            "include_conversations": True,
            "include_final_responses": True,
            "include_hive_posts": True,
            "limit_per_source": max(1, int(limit_per_source)),
        },
    )
    built = build_adaptation_corpus(str(corpus["corpus_id"]))
    job = create_adaptation_job(
        corpus_id=built.corpus_id,
        base_model_ref=str(base_model_ref or "").strip(),
        base_provider_name=str(base_provider_name or "").strip(),
        base_model_name=str(base_model_name or "").strip(),
        adapter_provider_name=str(adapter_provider_name or "").strip(),
        adapter_model_name=str(adapter_model_name or "").strip(),
        training_config={
            "license_name": str(license_name or "").strip(),
            "license_reference": str(license_reference or "").strip(),
            "capabilities": list(capabilities or []),
            "target_modules": list(target_modules or []),
            "epochs": max(1, int(epochs)),
            "max_steps": max(1, int(max_steps)),
            "batch_size": max(1, int(batch_size)),
            "gradient_accumulation_steps": max(1, int(gradient_accumulation_steps)),
            "learning_rate": float(learning_rate),
            "cutoff_len": max(128, int(cutoff_len)),
            "lora_r": max(1, int(lora_r)),
            "lora_alpha": max(1, int(lora_alpha)),
            "lora_dropout": max(0.0, float(lora_dropout)),
        },
    )
    payload = run_adaptation_job(str(job["job_id"]), promote=bool(promote))
    result = {
        "corpus_id": built.corpus_id,
        "corpus_output_path": built.output_path,
        "corpus_example_count": built.example_count,
        "job": payload,
    }
    if json_mode:
        _emit_json(result)
        return 0
    print("NULLA adaptation autopilot")
    print("==========================")
    print(f"Corpus ID:    {built.corpus_id}")
    print(f"Examples:     {built.example_count}")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    if payload.get("output_dir"):
        print(f"Output:       {payload['output_dir']}")
    if payload.get("error_text"):
        print(f"Error:        {payload['error_text']}")
        return 1
    return 0


def cmd_wallet_init(
    *,
    hot_address: str,
    cold_address: str,
    cold_secret: str | None,
    hot_usdc: float,
    cold_usdc: float,
) -> int:
    _bootstrap_cli_storage()
    manager = DNAWalletManager()
    secret = _resolve_secret(cold_secret, prompt="Set cold-wallet approval secret: ")
    status = manager.configure_wallets(
        hot_wallet_address=str(hot_address),
        cold_wallet_address=str(cold_address),
        cold_secret=secret,
        initial_hot_usdc=float(hot_usdc),
        initial_cold_usdc=float(cold_usdc),
    )
    print("DNA wallet profile initialized.")
    print(f"Hot USDC:  {status.hot_balance_usdc:.6f}")
    print(f"Cold USDC: {status.cold_balance_usdc:.6f}")
    return 0


def cmd_wallet_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    manager = DNAWalletManager()
    status = manager.get_status()
    if status is None:
        print("Wallet profile is not configured.")
        return 1
    if json_mode:
        import json

        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
        return 0
    print("DNA wallet status")
    print("=================")
    print(f"Hot wallet:   {status.hot_wallet_address}")
    print(f"Cold wallet:  {status.cold_wallet_address}")
    print(f"Hot USDC:     {status.hot_balance_usdc:.6f}")
    print(f"Cold USDC:    {status.cold_balance_usdc:.6f}")
    print(f"Hot auto use: {'enabled' if status.hot_auto_spend_enabled else 'disabled'}")
    return 0


def cmd_wallet_topup_hot(*, usdc: float, cold_secret: str | None) -> int:
    _bootstrap_cli_storage()
    manager = DNAWalletManager()
    secret = _resolve_secret(cold_secret, prompt="Cold-wallet approval secret: ")
    status = manager.top_up_hot_from_cold(float(usdc), cold_secret=secret, initiated_by="user")
    print(f"Top-up complete. Hot={status.hot_balance_usdc:.6f} USDC, Cold={status.cold_balance_usdc:.6f} USDC")
    return 0


def cmd_wallet_move_to_cold(*, usdc: float, cold_secret: str | None) -> int:
    _bootstrap_cli_storage()
    manager = DNAWalletManager()
    secret = _resolve_secret(cold_secret, prompt="Cold-wallet approval secret: ")
    status = manager.move_hot_to_cold(float(usdc), cold_secret=secret, initiated_by="user")
    print(f"Transfer complete. Hot={status.hot_balance_usdc:.6f} USDC, Cold={status.cold_balance_usdc:.6f} USDC")
    return 0


def cmd_wallet_buy_credits(*, usdc: float) -> int:
    if not credit_purchases_enabled():
        print("Credit purchases are disabled in this build.")
        print("Credits are earned by contributing work to the Hive; inactive peers just get lower priority.")
        return 2
    _bootstrap_cli_storage()
    result = dna_bridge.purchase_credits(float(usdc), local_peer_id=get_local_peer_id())
    print("Credit purchase complete.")
    print(f"Tx: {result.get('tx_id')}")
    print(f"Credits added: {result.get('credits_added')}")
    if result.get("hot_wallet_balance_usdc") is not None:
        print(f"Hot USDC left: {result.get('hot_wallet_balance_usdc')}")
        print(f"Cold USDC left: {result.get('cold_wallet_balance_usdc')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nulla")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("up", help="Auto-detect hardware and start Nulla")
    summary = sub.add_parser("summary", help="Show what Nulla learned, stored, indexed, and exchanged.")
    summary.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    summary.add_argument("--limit", type=int, default=5, help="Number of recent items to show per section.")

    resolve = sub.add_parser("resolve", help="Resolve a .null name on mainnet (read-only): owner, Arweave, x402 endpoint.")
    resolve.add_argument("name", help="The .null name to resolve, e.g. web0.null")
    resolve.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    web = sub.add_parser(
        "web",
        help="Live web search/fetch/browse. Opt-in: off unless NULLA_ENABLE_WEB=1 is set.",
    )
    web.add_argument("query", nargs="*", help="Search query words.")
    web.add_argument("--fetch", default="", metavar="URL", help="Fetch text from a specific URL instead of searching.")
    web.add_argument("--browse", default="", metavar="URL", help="Render a JS-heavy URL instead of searching.")
    web.add_argument("--limit", type=int, default=5, help="Max search results (1-5).")
    web.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    receipts = sub.add_parser("receipts", help="Show locally recorded anchored/finalized proofs with explorer links.")
    receipts.add_argument("--limit", type=int, default=20, help="Number of recent receipts to show.")
    receipts.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    manifest = sub.add_parser("manifest", help="Show this node's advertised capability + price card.")
    manifest.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    sell_quote = sub.add_parser("sell-quote", help="Preview the x402 invoice for a null:// request or a .null name (read-only).")
    sell_quote.add_argument("target", help="A null:// service URI or a <name>.null")
    sell_quote.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    dial = sub.add_parser(
        "dial",
        help="Reach a named .null agent's endpoint and return its result. Opt-in: off unless NULLA_ENABLE_NULL_DIAL=1.",
    )
    dial.add_argument("name", help="The .null name to dial, e.g. web0.null")
    dial.add_argument("task", help="The task to hand the named agent.")
    dial.add_argument("--allow-spend", action="store_true", help="Permit paying the endpoint via x402 (within --max-spend).")
    dial.add_argument("--max-spend", type=float, default=1.0, metavar="USDC", help="Spend cap in USDC (clamped to 1.0).")
    dial.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    x402pay = sub.add_parser(
        "x402-pay",
        help="Settle a single x402 payment on Solana (devnet by default). Real spend requires --allow-spend.",
    )
    x402pay.add_argument("amount", type=float, help="Amount in USDC, e.g. 0.001")
    x402pay.add_argument("recipient", help="Recipient Solana wallet (base58); its token account for the asset must exist.")
    x402pay.add_argument("--keypair", default="", metavar="PATH", help="Payer Solana JSON keypair (required to actually pay).")
    x402pay.add_argument("--mainnet", action="store_true", help="Settle on mainnet (real funds). Default is devnet.")
    x402pay.add_argument("--asset", default="", metavar="MINT", help="SPL mint to transfer (default: the cluster USDC mint).")
    x402pay.add_argument("--allow-spend", action="store_true", help="Actually spend. Without it this is a dry run.")
    x402pay.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    providers = sub.add_parser("providers", help="Show registered external model providers and declared licenses.")
    providers.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    install_profile = sub.add_parser(
        "install-profile",
        help="Show or switch the active install/provider profile for this runtime home.",
    )
    install_profile.add_argument(
        "--set",
        default="",
        metavar="PROFILE",
        help=(
            "Persist a new install profile and require a restart before it takes effect. "
            "Canonical values: "
            + ", ".join(PUBLIC_INSTALL_PROFILE_CHOICES)
            + ". Friendly aliases: ollama-only, ollama-max."
        ),
    )
    install_profile.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    identities = sub.add_parser("identities", help="Show local identity lifecycle, revocations, and key history.")
    identities.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    release = sub.add_parser("release-status", help="Show the current release/update manifest and compatibility contract.")
    release.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    credits = sub.add_parser("credits", help="Show current compute credit balance.")
    credits.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    adapt_status = sub.add_parser("adaptation-status", help="Show LoRA/adaptation dependency and job status.")
    adapt_status.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    adapt_corpus = sub.add_parser("adapt-corpus", help="Create or rebuild a training corpus from chats, final responses, and Hive.")
    adapt_corpus.add_argument("--corpus-id", default="", help="Rebuild an existing corpus instead of creating a new one.")
    adapt_corpus.add_argument("--label", default="default-corpus")
    adapt_corpus.add_argument("--no-conversations", action="store_true")
    adapt_corpus.add_argument("--no-final-responses", action="store_true")
    adapt_corpus.add_argument("--no-hive-posts", action="store_true")
    adapt_corpus.add_argument("--limit-per-source", type=int, default=250)
    adapt_corpus.add_argument("--json", action="store_true")

    adapt_import = sub.add_parser("adapt-corpus-import", help="Import a corpus JSONL from another node or machine into local adaptation storage.")
    adapt_import.add_argument("--input-path", required=True)
    adapt_import.add_argument("--label", default="imported-corpus")
    adapt_import.add_argument("--json", action="store_true")

    adapt_export = sub.add_parser("adapt-corpus-export", help="Export an existing local adaptation corpus JSONL to a target path.")
    adapt_export.add_argument("--corpus-id", required=True)
    adapt_export.add_argument("--output-path", required=True)
    adapt_export.add_argument("--json", action="store_true")

    adapt_create = sub.add_parser("adapt-job-create", help="Create a LoRA adaptation job definition.")
    adapt_create.add_argument("--corpus-id", required=True)
    adapt_create.add_argument("--base-model-ref", required=True, help="HF model id or local model path for training.")
    adapt_create.add_argument("--base-provider-name", default="")
    adapt_create.add_argument("--base-model-name", default="")
    adapt_create.add_argument("--adapter-provider-name", default="")
    adapt_create.add_argument("--adapter-model-name", default="")
    adapt_create.add_argument("--license-name", default="")
    adapt_create.add_argument("--license-reference", default="")
    adapt_create.add_argument("--capability", action="append", default=[])
    adapt_create.add_argument("--target-module", action="append", default=[])
    adapt_create.add_argument("--epochs", type=int, default=1)
    adapt_create.add_argument("--max-steps", type=int, default=32)
    adapt_create.add_argument("--batch-size", type=int, default=1)
    adapt_create.add_argument("--gradient-accumulation-steps", type=int, default=4)
    adapt_create.add_argument("--learning-rate", type=float, default=2e-4)
    adapt_create.add_argument("--cutoff-len", type=int, default=768)
    adapt_create.add_argument("--lora-r", type=int, default=8)
    adapt_create.add_argument("--lora-alpha", type=int, default=16)
    adapt_create.add_argument("--lora-dropout", type=float, default=0.05)
    adapt_create.add_argument("--promote", action="store_true")
    adapt_create.add_argument("--json", action="store_true")

    adapt_jobs = sub.add_parser("adapt-jobs", help="List adaptation jobs.")
    adapt_jobs.add_argument("--json", action="store_true")

    adapt_evals = sub.add_parser("adapt-evals", help="List adaptation evaluation and canary runs.")
    adapt_evals.add_argument("--job-id", default="")
    adapt_evals.add_argument("--json", action="store_true")

    adapt_events = sub.add_parser("adapt-job-events", help="Show adaptation job event log.")
    adapt_events.add_argument("--job-id", required=True)
    adapt_events.add_argument("--json", action="store_true")

    adapt_run = sub.add_parser("adapt-job-run", help="Run a queued LoRA adaptation job.")
    adapt_run.add_argument("--job-id", required=True)
    adapt_run.add_argument("--promote", action="store_true")
    adapt_run.add_argument("--json", action="store_true")

    adapt_promote = sub.add_parser("adapt-promote", help="Promote a completed LoRA job into the live provider registry.")
    adapt_promote.add_argument("--job-id", required=True)
    adapt_promote.add_argument("--json", action="store_true")

    adapt_loop_status = sub.add_parser("adapt-loop-status", help="Show the closed-loop adaptation controller state.")
    adapt_loop_status.add_argument("--json", action="store_true")

    adapt_loop_tick = sub.add_parser("adapt-loop-tick", help="Run the closed-loop adaptation controller once.")
    adapt_loop_tick.add_argument("--force", action="store_true")
    adapt_loop_tick.add_argument("--json", action="store_true")

    adapt_auto = sub.add_parser("adapt-autopilot", help="One-shot corpus build + LoRA job create + run.")
    adapt_auto.add_argument("--label", default="autopilot")
    adapt_auto.add_argument("--base-model-ref", required=True)
    adapt_auto.add_argument("--base-provider-name", default="")
    adapt_auto.add_argument("--base-model-name", default="")
    adapt_auto.add_argument("--adapter-provider-name", default="")
    adapt_auto.add_argument("--adapter-model-name", default="")
    adapt_auto.add_argument("--license-name", default="")
    adapt_auto.add_argument("--license-reference", default="")
    adapt_auto.add_argument("--capability", action="append", default=[])
    adapt_auto.add_argument("--target-module", action="append", default=[])
    adapt_auto.add_argument("--limit-per-source", type=int, default=250)
    adapt_auto.add_argument("--epochs", type=int, default=1)
    adapt_auto.add_argument("--max-steps", type=int, default=32)
    adapt_auto.add_argument("--batch-size", type=int, default=1)
    adapt_auto.add_argument("--gradient-accumulation-steps", type=int, default=4)
    adapt_auto.add_argument("--learning-rate", type=float, default=2e-4)
    adapt_auto.add_argument("--cutoff-len", type=int, default=768)
    adapt_auto.add_argument("--lora-r", type=int, default=8)
    adapt_auto.add_argument("--lora-alpha", type=int, default=16)
    adapt_auto.add_argument("--lora-dropout", type=float, default=0.05)
    adapt_auto.add_argument("--promote", action="store_true")
    adapt_auto.add_argument("--json", action="store_true")

    control_sync = sub.add_parser("control-sync", help="Mirror real queue/lease/run/budget/approval state into workspace/control.")
    control_sync.add_argument("--json", action="store_true")

    base_status = sub.add_parser("trainable-base-status", help="Show staged real trainable bases and the active adaptation base.")
    base_status.add_argument("--json", action="store_true")

    base_stage = sub.add_parser("stage-trainable-base", help="Download and activate a real trainable Transformers base for LoRA.")
    base_stage.add_argument("--model-ref", default="qwen-0.5b", help="Curated alias or full Hugging Face repo id.")
    base_stage.add_argument("--activate", action="store_true", help="Write the staged base into local adaptation policy.")
    base_stage.add_argument("--skip-verify-load", action="store_true", help="Skip tokenizer/model load verification after download.")
    base_stage.add_argument("--force-download", action="store_true", help="Force a fresh snapshot even if the model already looks staged.")
    base_stage.add_argument("--license-name", default="", help="Required for custom full HF repo ids.")
    base_stage.add_argument("--license-reference", default="", help="Required for custom full HF repo ids.")
    base_stage.add_argument("--trust-remote-code", action="store_true", help="Enable trust_remote_code for custom model layouts.")
    base_stage.add_argument("--json", action="store_true")

    wallet_init = sub.add_parser("wallet-init", help="Configure hot/cold DNA wallets and cold approval secret.")
    wallet_init.add_argument("--hot-address", required=True)
    wallet_init.add_argument("--cold-address", required=True)
    wallet_init.add_argument("--cold-secret", default="")
    wallet_init.add_argument("--hot-usdc", type=float, default=0.0)
    wallet_init.add_argument("--cold-usdc", type=float, default=0.0)

    wallet_status = sub.add_parser("wallet-status", help="Show hot/cold wallet balances.")
    wallet_status.add_argument("--json", action="store_true")

    wallet_topup = sub.add_parser("wallet-topup-hot", help="Move USDC from cold wallet to hot wallet (requires approval secret).")
    wallet_topup.add_argument("--usdc", type=float, required=True)
    wallet_topup.add_argument("--cold-secret", default="")

    wallet_to_cold = sub.add_parser("wallet-move-cold", help="Move USDC from hot wallet to cold wallet (requires approval secret).")
    wallet_to_cold.add_argument("--usdc", type=float, required=True)
    wallet_to_cold.add_argument("--cold-secret", default="")

    wallet_buy = sub.add_parser(
        "wallet-buy-credits",
        help="Disabled by default. Credits are work-earned until purchase rails are enabled.",
    )
    wallet_buy.add_argument("--usdc", type=float, required=True)
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "up":
        return cmd_up()
    if args.command == "summary":
        return cmd_summary(json_mode=bool(args.json), limit=int(args.limit))
    if args.command == "resolve":
        return cmd_resolve(str(args.name or ""), json_mode=bool(args.json))
    if args.command == "web":
        return cmd_web(
            query=" ".join(list(args.query or [])),
            fetch_url=str(args.fetch or ""),
            render_url=str(args.browse or ""),
            limit=int(args.limit),
            json_mode=bool(args.json),
        )
    if args.command == "receipts":
        return cmd_receipts(limit=int(args.limit), json_mode=bool(args.json))
    if args.command == "manifest":
        return cmd_manifest(json_mode=bool(args.json))
    if args.command == "sell-quote":
        return cmd_sell_quote(str(args.target or ""), json_mode=bool(args.json))
    if args.command == "dial":
        return cmd_dial(
            str(args.name or ""),
            str(args.task or ""),
            allow_spend=bool(args.allow_spend),
            max_spend_usdc=float(args.max_spend),
            json_mode=bool(args.json),
        )
    if args.command == "x402-pay":
        return cmd_x402_pay(
            float(args.amount),
            str(args.recipient or ""),
            keypair_path=str(args.keypair or ""),
            mainnet=bool(args.mainnet),
            asset_mint=str(args.asset or ""),
            allow_spend=bool(args.allow_spend),
            json_mode=bool(args.json),
        )
    if args.command == "providers":
        return cmd_providers(json_mode=bool(args.json))
    if args.command == "install-profile":
        return cmd_install_profile(set_profile=str(args.set or ""), json_mode=bool(args.json))
    if args.command == "identities":
        return cmd_identity_report(json_mode=bool(args.json))
    if args.command == "release-status":
        return cmd_release_status(json_mode=bool(args.json))
    if args.command == "credits":
        return cmd_credits(json_mode=bool(args.json))
    if args.command == "adaptation-status":
        return cmd_adaptation_status(json_mode=bool(args.json))
    if args.command == "adapt-corpus":
        return cmd_adaptation_corpus(
            corpus_id=str(args.corpus_id or ""),
            label=str(args.label or ""),
            include_conversations=not bool(args.no_conversations),
            include_final_responses=not bool(args.no_final_responses),
            include_hive_posts=not bool(args.no_hive_posts),
            limit_per_source=int(args.limit_per_source),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-corpus-import":
        return cmd_adaptation_corpus_import(
            input_path=str(args.input_path or ""),
            label=str(args.label or ""),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-corpus-export":
        return cmd_adaptation_corpus_export(
            corpus_id=str(args.corpus_id or ""),
            output_path=str(args.output_path or ""),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-job-create":
        return cmd_adaptation_job_create(
            corpus_id=str(args.corpus_id),
            base_model_ref=str(args.base_model_ref),
            base_provider_name=str(args.base_provider_name or ""),
            base_model_name=str(args.base_model_name or ""),
            adapter_provider_name=str(args.adapter_provider_name or ""),
            adapter_model_name=str(args.adapter_model_name or ""),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            capabilities=list(args.capability or []),
            target_modules=list(args.target_module or []),
            epochs=int(args.epochs),
            max_steps=int(args.max_steps),
            batch_size=int(args.batch_size),
            gradient_accumulation_steps=int(args.gradient_accumulation_steps),
            learning_rate=float(args.learning_rate),
            cutoff_len=int(args.cutoff_len),
            lora_r=int(args.lora_r),
            lora_alpha=int(args.lora_alpha),
            lora_dropout=float(args.lora_dropout),
            promote=bool(args.promote),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-jobs":
        return cmd_adaptation_jobs(json_mode=bool(args.json))
    if args.command == "adapt-evals":
        return cmd_adaptation_eval_runs(job_id=str(args.job_id or ""), json_mode=bool(args.json))
    if args.command == "adapt-job-events":
        return cmd_adaptation_job_events(str(args.job_id), json_mode=bool(args.json))
    if args.command == "adapt-job-run":
        return cmd_adaptation_job_run(str(args.job_id), promote=bool(args.promote), json_mode=bool(args.json))
    if args.command == "adapt-promote":
        return cmd_adaptation_job_promote(str(args.job_id), json_mode=bool(args.json))
    if args.command == "adapt-loop-status":
        return cmd_adaptation_loop_status(json_mode=bool(args.json))
    if args.command == "adapt-loop-tick":
        return cmd_adaptation_loop_tick(force=bool(args.force), json_mode=bool(args.json))
    if args.command == "adapt-autopilot":
        return cmd_adaptation_autopilot(
            label=str(args.label or ""),
            base_model_ref=str(args.base_model_ref),
            base_provider_name=str(args.base_provider_name or ""),
            base_model_name=str(args.base_model_name or ""),
            adapter_provider_name=str(args.adapter_provider_name or ""),
            adapter_model_name=str(args.adapter_model_name or ""),
            limit_per_source=int(args.limit_per_source),
            epochs=int(args.epochs),
            max_steps=int(args.max_steps),
            batch_size=int(args.batch_size),
            gradient_accumulation_steps=int(args.gradient_accumulation_steps),
            learning_rate=float(args.learning_rate),
            cutoff_len=int(args.cutoff_len),
            lora_r=int(args.lora_r),
            lora_alpha=int(args.lora_alpha),
            lora_dropout=float(args.lora_dropout),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            capabilities=list(args.capability or []),
            target_modules=list(args.target_module or []),
            promote=bool(args.promote),
            json_mode=bool(args.json),
        )
    if args.command == "control-sync":
        return cmd_control_plane_sync(json_mode=bool(args.json))
    if args.command == "trainable-base-status":
        return cmd_trainable_base_status(json_mode=bool(args.json))
    if args.command == "stage-trainable-base":
        return cmd_stage_trainable_base(
            model_ref=str(args.model_ref or ""),
            activate=bool(args.activate),
            verify_load=not bool(args.skip_verify_load),
            force_download=bool(args.force_download),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            trust_remote_code=bool(args.trust_remote_code),
            json_mode=bool(args.json),
        )
    if args.command == "wallet-init":
        return cmd_wallet_init(
            hot_address=str(args.hot_address),
            cold_address=str(args.cold_address),
            cold_secret=str(args.cold_secret or ""),
            hot_usdc=float(args.hot_usdc),
            cold_usdc=float(args.cold_usdc),
        )
    if args.command == "wallet-status":
        return cmd_wallet_status(json_mode=bool(args.json))
    if args.command == "wallet-topup-hot":
        return cmd_wallet_topup_hot(usdc=float(args.usdc), cold_secret=str(args.cold_secret or ""))
    if args.command == "wallet-move-cold":
        return cmd_wallet_move_to_cold(usdc=float(args.usdc), cold_secret=str(args.cold_secret or ""))
    if args.command == "wallet-buy-credits":
        return cmd_wallet_buy_credits(usdc=float(args.usdc))

    parser.print_help()
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
