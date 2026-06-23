from __future__ import annotations

import json
from typing import Any
from unittest import mock

from core.null_dial import try_dial
from core.null_resolver import NullDomainRecord
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

# A public, SSRF-safe endpoint. The IP-level guard is stubbed per-test so these
# never touch the network or DNS.
_SAFE_ENDPOINT = "https://pay.parad0xlabs.com/x402"


def _record(endpoint: str = _SAFE_ENDPOINT, owner: str = "OwNeRwAlLeT1111111111111111111111111111111") -> NullDomainRecord:
    return NullDomainRecord(
        name="web0",
        owner=owner,
        arweave_txid=None,
        x402_endpoint=endpoint,
        passport_hash=None,
    )


# ---------------------------------------------------------------------------
# try_dial — direct unit coverage
# ---------------------------------------------------------------------------

def test_try_dial_posts_task_and_returns_remote_result() -> None:
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, *, body=None, headers=None, timeout=15.0):
        calls.append({"method": method, "url": url, "body": body})
        return {"result": "remote answer", "confidence": 1.0}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "do the thing",
            record=_record(),
            wallet=None,
            allow_spend=False,
            http=fake_http,
        )

    assert out == {"result": "remote answer", "confidence": 1.0}
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == _SAFE_ENDPOINT
    assert calls[0]["body"]["prompt"] == "do the thing"
    assert calls[0]["body"]["uri"] == "null://web0/task"


def test_try_dial_returns_none_when_no_endpoint() -> None:
    http_calls: list[Any] = []
    out = try_dial(
        "null://web0/task",
        "task",
        record=_record(endpoint=""),
        http=lambda *a, **k: http_calls.append(1) or {},
    )
    assert out is None
    assert http_calls == []  # no endpoint -> no network call


def test_try_dial_returns_none_when_endpoint_ssrf_unsafe() -> None:
    http_calls: list[Any] = []
    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=False):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            http=lambda *a, **k: http_calls.append(1) or {},
        )
    assert out is None
    assert http_calls == []  # unsafe -> never dialed


def test_try_dial_returns_none_on_remote_error() -> None:
    def erroring_http(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "message": "boom"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial("null://web0/task", "task", record=_record(), http=erroring_http)
    assert out is None  # non-payment error -> caller falls back to local


def test_try_dial_returns_none_on_http_raise() -> None:
    def raising_http(method, url, *, body=None, headers=None, timeout=15.0):
        raise OSError("connection refused")

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial("null://web0/task", "task", record=_record(), http=raising_http)
    assert out is None


def test_try_dial_402_without_allow_spend_returns_preview_no_spend() -> None:
    pay_calls: list[Any] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.02}

    def fake_pay(*a, **k):
        pay_calls.append((a, k))
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=None,
            allow_spend=False,
            http=http_402,
            pay=fake_pay,
        )

    assert out is not None
    assert out["status"] == "user_action_required"
    assert out["amount_usdc"] == 0.02
    assert pay_calls == []  # allow_spend off -> never paid


def test_try_dial_402_with_allow_spend_pays_within_cap() -> None:
    pay_calls: list[dict[str, Any]] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.02}

    def fake_pay(resource_url, wallet, *, max_spend_usdc=1.0, allow_spend=False, **k):
        pay_calls.append({"resource": resource_url, "max_spend_usdc": max_spend_usdc, "allow_spend": allow_spend})
        return {"status": "paid", "resource_response": "unlocked", "amount_paid_usdc": 0.02}

    sentinel_wallet = object()
    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=sentinel_wallet,
            allow_spend=True,
            max_spend_usdc=0.05,
            http=http_402,
            pay=fake_pay,
        )

    assert out == {"status": "paid", "resource_response": "unlocked", "amount_paid_usdc": 0.02}
    assert len(pay_calls) == 1
    assert pay_calls[0]["resource"] == _SAFE_ENDPOINT
    assert pay_calls[0]["allow_spend"] is True
    assert pay_calls[0]["max_spend_usdc"] == 0.05  # the caller cap, within the 1.0 ceiling


def test_try_dial_cap_is_clamped_to_one_usdc_ceiling() -> None:
    pay_calls: list[dict[str, Any]] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.5}

    def fake_pay(resource_url, wallet, *, max_spend_usdc=1.0, allow_spend=False, **k):
        pay_calls.append({"max_spend_usdc": max_spend_usdc})
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=object(),
            allow_spend=True,
            max_spend_usdc=999.0,  # absurd cap must be clamped
            http=http_402,
            pay=fake_pay,
        )

    assert pay_calls[0]["max_spend_usdc"] == 1.0  # clamped to the 1.0 USDC ceiling


def test_try_dial_402_amount_over_cap_returns_preview_not_pay() -> None:
    pay_calls: list[Any] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.50}

    def fake_pay(*a, **k):
        pay_calls.append(1)
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=object(),
            allow_spend=True,
            max_spend_usdc=0.05,  # quote 0.50 > cap 0.05
            http=http_402,
            pay=fake_pay,
        )

    assert out["status"] == "user_action_required"
    assert pay_calls == []  # over the cap -> never paid


# ---------------------------------------------------------------------------
# /api/null service route — dial gated by the policy flag
# ---------------------------------------------------------------------------

def _runtime() -> RuntimeServices:
    return RuntimeServices(display_name="NULLA")


def _agent(runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
    return {"response": f"local: {text}", "confidence": 1.0}


def test_service_flag_off_runs_local_and_never_dials(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: False)
    http_calls: list[Any] = []

    def boom_dial(*a, **k):
        http_calls.append(1)
        raise AssertionError("try_dial must not be called when the flag is off")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=boom_dial,
    )

    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["result"] == "local: review"  # local path ran
    assert data.get("dialed") is None
    assert http_calls == []  # dial provider never invoked


def test_service_flag_on_returns_remote_result(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)
    seen: list[dict[str, Any]] = []

    def fake_dial(uri, task_text, *, record, wallet, allow_spend, **k):
        seen.append({"uri": uri, "task": task_text, "endpoint": record.x402_endpoint, "allow_spend": allow_spend})
        return {"result": "remote answer"}

    def agent_must_not_run(*a, **k):
        raise AssertionError("local agent must not run when dial returns a result")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=agent_must_not_run,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=fake_dial,
    )

    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["dialed"] is True
    assert data["result"] == {"result": "remote answer"}
    assert seen[0]["task"] == "review"
    assert seen[0]["endpoint"] == _SAFE_ENDPOINT
    assert seen[0]["allow_spend"] is False  # the service never spends


def test_service_flag_on_but_resolution_miss_falls_back_local(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)

    def boom_dial(*a, **k):
        raise AssertionError("try_dial must not be called when the name does not resolve")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: None,  # miss
        try_dial_provider=boom_dial,
    )

    data = json.loads(resp.body)
    assert data["result"] == "local: review"


def test_service_flag_on_dial_returns_none_falls_back_local(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=lambda *a, **k: None,  # remote miss / error
    )

    data = json.loads(resp.body)
    assert data["result"] == "local: review"  # graceful local fallback


def test_service_threads_resolved_owner_into_quote_when_dial_on(monkeypatch) -> None:
    from core import policy_engine

    # Owner resolution is a live on-chain read, so it only runs when remote dial
    # is opted in. With the flag ON the quote carries the REAL on-chain owner.
    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)
    owner = "RealOwnerWallet22222222222222222222222222222"

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(owner=owner),
        try_dial_provider=lambda *a, **k: None,  # no remote result -> local run, quote still built
    )

    data = json.loads(resp.body)
    assert data["quote"]["recipient_wallet"] == owner


def test_service_keeps_stub_wallet_when_dial_off(monkeypatch) -> None:
    from core import policy_engine

    # With the flag OFF the route is fully local: NO resolution, so the injected
    # provider is never called and the quote keeps the default wallet (zero network).
    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: False)
    calls: list[str] = []

    def _resolver(name: str):
        calls.append(name)
        return _record(owner="RealOwnerWallet22222222222222222222222222222")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task"},
        headers={},
        runtime=_runtime(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=_resolver,
    )

    data = json.loads(resp.body)
    assert data["quote"]["recipient_wallet"] == "stub-wallet"
    assert calls == []  # resolver never invoked when dial is off


# ── canonical x402 pay path (the re-pointed default) ────────────────────────

def test_amount_and_requirements_from_canonical_402() -> None:
    from core.null_dial import _amount_from_402, _requirements_from_402
    resp = {"error": True, "status": 402, "accepts": [
        {"network": "solana-devnet", "maxAmountRequired": "1500", "payTo": "R", "asset": "M"}]}
    assert _amount_from_402(resp) == 0.0015          # atomic 1500 -> 0.0015 USDC
    assert _requirements_from_402(resp)["payTo"] == "R"
    assert _requirements_from_402({"status": 402}) is None


def test_dial_pay_x402_settles_then_unlocks(monkeypatch) -> None:
    from core import null_dial

    class _Receipt:
        payment_tx = "SIG_DIAL_123"
        amount_usdc = 0.001
        recipient_wallet = "R"

    class _FakeClient:
        def __init__(self, cfg, signer=None):
            self.cfg = cfg

        def pay_requirements(self, req, session_id=None):
            return _Receipt()

    monkeypatch.setattr("core.x402.client.X402Client", _FakeClient)
    monkeypatch.setattr("core.x402.client.wallet_signer", lambda w: w)

    calls = []

    def fake_http(method, url, *, body=None, headers=None, timeout=15):
        calls.append({"url": url, "headers": headers or {}})
        return {"result": "unlocked-resource"}

    req = {"network": "solana-devnet", "asset": "MINT", "maxAmountRequired": "1000",
           "payTo": "R", "extra": {"feePayer": "F"}}
    out = null_dial._dial_pay_x402(
        "https://agent.example/x402", object(), max_spend_usdc=1.0, allow_spend=True,
        requirements=req, task_text="do-it", http=fake_http,
    )
    assert out["status"] == "paid"
    assert out["payment_tx"] == "SIG_DIAL_123"
    assert out["resource_response"] == {"result": "unlocked-resource"}
    # the unlock re-request carries the settlement proof
    assert calls[0]["headers"]["X-PAYMENT-RECEIPT"] == "SIG_DIAL_123"


def test_dial_pay_x402_not_attempted_guards() -> None:
    from core.null_dial import _dial_pay_x402
    assert _dial_pay_x402("u", object(), allow_spend=False, requirements={"a": 1}).get("error")
    assert _dial_pay_x402("u", object(), allow_spend=True, requirements=None).get("error")
    assert _dial_pay_x402("u", None, allow_spend=True, requirements={"a": 1}).get("error")
