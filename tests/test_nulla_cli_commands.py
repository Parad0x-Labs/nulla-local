from __future__ import annotations

import json
from unittest import mock

from apps.nulla_cli import (
    WEB_DISABLED_MESSAGE,
    _explorer_tx_link,
    _quote_target_to_uri,
    _strip_null_suffix,
    cmd_manifest,
    cmd_register,
    cmd_resolve,
    cmd_sell_quote,
    cmd_web,
    format_receipt_row,
    render_manifest_lines,
    render_quote_lines,
    render_receipt_lines,
    render_resolve_lines,
    resolve_record_payload,
)
from core.execution.models import ToolIntentExecution
from core.null_resolver import NullDomainRecord

# --- resolve --------------------------------------------------------------

def _record(
    *,
    name: str = "web0",
    owner: str = "Owner111",
    arweave: str | None = "txid-abc",
    endpoint: str = "https://parad0xlabs.com/x402",
    passport: str | None = None,
) -> NullDomainRecord:
    return NullDomainRecord(
        name=name,
        owner=owner,
        arweave_txid=arweave,
        x402_endpoint=endpoint,
        passport_hash=passport,
    )


def test_cmd_register_refuses_premium_and_bad_names(capsys) -> None:
    # 1-3 char premium names are auction-only; bad charset is refused — both before any wallet/RPC.
    assert cmd_register("ab.null") == 2
    assert "auction" in capsys.readouterr().out.lower()
    assert cmd_register("bad_name!.null") == 2
    assert "a-z" in capsys.readouterr().out


def test_cmd_register_dry_run_never_executes(capsys) -> None:
    fake_plan = mock.Mock(total_sol=0.0112)
    preview = mock.Mock(status="preview", message="Register mysite on Solana MAINNET for ~0.0112 SOL", plan=fake_plan)
    fake_wallet = mock.Mock(pubkey="28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX")
    wallet_cls = mock.Mock(return_value=mock.Mock(exists=lambda: True, load=lambda: fake_wallet))
    with mock.patch("core.null_register_execute.preview_registration", return_value=preview), \
         mock.patch("core.null_register_execute.execute_registration") as exec_mock, \
         mock.patch("core.nulla_wallet.NullaWallet", wallet_cls):
        rc = cmd_register("mysite.null")  # 6 chars (valid), no --allow-spend / --mainnet
    assert rc == 0
    exec_mock.assert_not_called()  # dry run must never sign/broadcast
    assert "Dry run" in capsys.readouterr().out


def test_strip_null_suffix_trims_trailing_null() -> None:
    assert _strip_null_suffix("web0.null") == "web0"
    assert _strip_null_suffix("  web0.NULL ") == "web0"
    assert _strip_null_suffix("web0") == "web0"
    assert _strip_null_suffix("") == ""


def test_resolve_record_payload_for_hit() -> None:
    payload = resolve_record_payload("web0", _record(passport="ab" * 32))
    assert payload["resolved"] is True
    assert payload["owner"] == "Owner111"
    assert payload["arweave_txid"] == "txid-abc"
    assert payload["x402_endpoint"] == "https://parad0xlabs.com/x402"
    assert payload["passport_present"] is True


def test_resolve_record_payload_for_miss() -> None:
    payload = resolve_record_payload("nope", None)
    assert payload == {"name": "nope", "resolved": False}


def test_render_resolve_lines_hit_and_miss() -> None:
    hit = render_resolve_lines(resolve_record_payload("web0", _record()))
    joined = "\n".join(hit)
    assert "Name:           web0.null" in joined
    assert "Owner:          Owner111" in joined
    assert "x402 endpoint:  https://parad0xlabs.com/x402" in joined
    assert "Passport:       none" in joined

    miss = render_resolve_lines(resolve_record_payload("ghost", None))
    assert any("unresolved" in line for line in miss)


def test_cmd_resolve_hit_returns_zero(capsys) -> None:
    with mock.patch("apps.nulla_cli.resolve_null_domain", return_value=_record()) as resolver:
        assert cmd_resolve("web0.null") == 0
    resolver.assert_called_once_with("web0")
    out = capsys.readouterr().out
    assert "NULLA .null resolution" in out
    assert "web0.null" in out


def test_cmd_resolve_miss_returns_one(capsys) -> None:
    with mock.patch("apps.nulla_cli.resolve_null_domain", return_value=None):
        assert cmd_resolve("ghost.null") == 1
    assert "unresolved" in capsys.readouterr().out


def test_cmd_resolve_json(capsys) -> None:
    with mock.patch("apps.nulla_cli.resolve_null_domain", return_value=_record()):
        assert cmd_resolve("web0.null", json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is True
    assert payload["name"] == "web0"


def test_cmd_resolve_empty_name() -> None:
    assert cmd_resolve(".null") == 2


# --- receipts -------------------------------------------------------------

def test_explorer_tx_link_is_mainnet_explorer() -> None:
    assert _explorer_tx_link("sig123") == "https://explorer.solana.com/tx/sig123"


def test_format_receipt_row_with_signature() -> None:
    row = {
        "event_type": "solana_proof_anchored",
        "target_id": "task-1",
        "details_json": json.dumps({"signature": "SIGabc", "confidence": 0.91}),
        "created_at": "2026-06-22T00:00:00+00:00",
    }
    entry = format_receipt_row(row)
    assert entry["task_id"] == "task-1"
    assert entry["confidence"] == 0.91
    assert entry["signature"] == "SIGabc"
    assert entry["explorer_link"] == "https://explorer.solana.com/tx/SIGabc"


def test_format_receipt_row_finalized_without_signature() -> None:
    row = {
        "event_type": "parent_output_finalized",
        "target_id": "task-2",
        "details_json": json.dumps({"status": "finalized", "confidence": 0.5}),
        "created_at": "2026-06-22T00:00:01+00:00",
    }
    entry = format_receipt_row(row)
    assert entry["signature"] == ""
    assert entry["explorer_link"] == ""
    assert entry["confidence"] == 0.5


def test_format_receipt_row_handles_bad_json() -> None:
    row = {"event_type": "x", "target_id": "t", "details_json": "{not-json", "created_at": "now"}
    entry = format_receipt_row(row)
    assert entry["confidence"] == 0.0
    assert entry["signature"] == ""


def test_render_receipt_lines_empty_and_populated() -> None:
    assert any("No anchored" in line for line in render_receipt_lines([]))
    entry = format_receipt_row(
        {
            "event_type": "solana_proof_anchored",
            "target_id": "task-1",
            "details_json": json.dumps({"signature": "SIGabc", "confidence": 0.91}),
            "created_at": "now",
        }
    )
    lines = render_receipt_lines([entry])
    joined = "\n".join(lines)
    assert "Task:      task-1" in joined
    assert "Explorer:  https://explorer.solana.com/tx/SIGabc" in joined


# --- manifest -------------------------------------------------------------

def test_render_manifest_lines() -> None:
    manifest_dict = {
        "worker_id": "nulla",
        "top_tier": "drone",
        "top_tps": 12.5,
        "context_window": 32768,
        "provider_ids": ["ollama:qwen"],
        "tools": [],
        "price_per_token_usdc": 0.000001,
        "privacy_mode": "plain",
    }
    joined = "\n".join(render_manifest_lines(manifest_dict))
    assert "Worker ID:      nulla" in joined
    assert "Providers:      ollama:qwen" in joined
    assert "Tools:          none" in joined
    assert "Price/token:    0.00000100 USDC" in joined
    assert "Privacy mode:   plain" in joined


def test_cmd_manifest_json(capsys) -> None:
    assert cmd_manifest(json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "worker_id" in payload
    assert "price_per_token_usdc" in payload


# --- sell-quote -----------------------------------------------------------

def test_quote_target_to_uri() -> None:
    assert _quote_target_to_uri("null://task/code-review") == "null://task/code-review"
    assert _quote_target_to_uri("web0.null") == "null://task/web0"
    assert _quote_target_to_uri("web0") == "null://task/web0"
    assert _quote_target_to_uri("") == ""


def test_render_quote_lines() -> None:
    payload = {
        "uri": "null://task/code-review",
        "service": "task",
        "path": "code-review",
        "session_id": "null-abc",
        "quote": {
            "amount_usdc": 0.0005,
            "recipient_wallet": "Wallet111",
            "usdc_mint": "Mint111",
            "quote_hash": "hash111",
        },
    }
    joined = "\n".join(render_quote_lines(payload))
    assert "URI:            null://task/code-review" in joined
    assert "Service:        task" in joined
    assert "Amount:         0.00050000 USDC" in joined
    assert "Recipient:      Wallet111" in joined


def test_cmd_sell_quote_runs_read_only(capsys) -> None:
    assert cmd_sell_quote("null://task/code-review", json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "task"
    assert payload["quote"]["amount_usdc"] > 0


def test_cmd_sell_quote_empty_target() -> None:
    assert cmd_sell_quote("") == 2


# --- web (opt-in) ---------------------------------------------------------

def test_cmd_web_disabled_prints_opt_in_message_and_makes_no_call(capsys) -> None:
    # Web is off by default. The command must refuse without dispatching anything.
    with mock.patch("apps.nulla_cli.policy_engine.allow_web_fallback", return_value=False), mock.patch(
        "apps.nulla_cli._run_web_intent",
        side_effect=AssertionError("web command must not dispatch while web is off"),
    ):
        assert cmd_web(query="latest qwen release notes") == 2
    out = capsys.readouterr().out
    assert WEB_DISABLED_MESSAGE in out
    assert "NULLA_ENABLE_WEB=1" in out


def test_cmd_web_disabled_json_reports_enabled_false(capsys) -> None:
    with mock.patch("apps.nulla_cli.policy_engine.allow_web_fallback", return_value=False):
        assert cmd_web(query="anything", json_mode=True) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["intent"] == "web.search"


def test_cmd_web_empty_query_returns_usage() -> None:
    assert cmd_web(query="") == 2


def test_cmd_web_enabled_routes_search_through_executor(capsys) -> None:
    execution = ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text='Search results for "qwen":\n- Qwen notes - https://example.test/qwen',
        mode="tool_executed",
        tool_name="web.search",
    )
    with mock.patch("apps.nulla_cli.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "apps.nulla_cli._bootstrap_cli_storage", return_value=None
    ), mock.patch("apps.nulla_cli._run_web_intent", return_value=execution) as runner:
        assert cmd_web(query="qwen") == 0
    runner.assert_called_once()
    intent, arguments = runner.call_args.args
    assert intent == "web.search"
    assert arguments["query"] == "qwen"
    assert "Search results for" in capsys.readouterr().out


def test_cmd_web_enabled_fetch_routes_web_fetch_intent(capsys) -> None:
    execution = ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text="Fetched https://example.test/\n- Status: ok",
        mode="tool_executed",
        tool_name="web.fetch",
    )
    with mock.patch("apps.nulla_cli.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "apps.nulla_cli._bootstrap_cli_storage", return_value=None
    ), mock.patch("apps.nulla_cli._run_web_intent", return_value=execution) as runner:
        assert cmd_web(fetch_url="https://example.test/") == 0
    intent, arguments = runner.call_args.args
    assert intent == "web.fetch"
    assert arguments["url"] == "https://example.test/"
