"""
tests/test_launch_readiness.py
Tests covering all 6 gaps closed for the NULLA launch:
  1. Earnings page
  2. Wallet API routes
  3. Task market routes (queue / claim / complete / settle)
  4. Receipt wallet wiring + credit award
  5. Solana anchor hook (env-gated)
  6. Background task poll loop (idempotency)
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from core.meet_and_greet_service import MeetAndGreetService
from core.web.api.runtime import RuntimeServices
from core.web.api.service import _attach_work_receipt, dispatch_get, dispatch_post
from core.web.meet.routes import dispatch_request, resolve_static_route


@pytest.fixture(autouse=True)
def _isolated_nulla_home(tmp_path, monkeypatch):
    """Give each test its own NULLA_HOME.

    The default test NULLA_HOME (conftest.py) is session-shared, so the agent wallet + its
    node signing key are global across the process. If any test earlier in the shard
    regenerates the node key, the shared wallet can no longer be decrypted and these
    wallet-info tests fail with "Solana wallet decryption failed". A fresh per-test home
    makes get_or_create_wallet create the wallet + key together, so the tests are
    order-independent.
    """
    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    try:
        from core import runtime_paths

        monkeypatch.setattr(runtime_paths, "_NULLA_HOME_OVERRIDE", None, raising=False)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc() -> MeetAndGreetService:
    return MeetAndGreetService()


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="NULLA")


def _meet_get(path: str, query: dict | None = None) -> tuple[int, dict]:
    return dispatch_request("GET", path, query or {}, {}, _svc())


def _meet_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    return dispatch_request("POST", path, {}, body or {}, _svc())


# ---------------------------------------------------------------------------
# Gap 1 — Earnings / Task Queue panel
# ---------------------------------------------------------------------------

def test_earnings_static_route_returns_200() -> None:
    result = resolve_static_route("/earnings")
    assert result is not None
    assert result[0] == 200
    assert "text/html" in result[1]


def test_earnings_page_contains_key_elements() -> None:
    result = resolve_static_route("/earnings")
    assert result is not None
    body = result[2].decode("utf-8")
    assert "Earnings" in body
    assert "Task Queue" in body or "task-tbody" in body
    assert "/v1/wallet/info" in body
    assert "/v1/credits/balance" in body
    assert "/v1/tasks/queue" in body
    assert "/v1/workers" in body


def test_earnings_trailing_slash() -> None:
    result = resolve_static_route("/earnings/")
    assert result is not None
    assert result[0] == 200


# ---------------------------------------------------------------------------
# Gap 2 — Wallet live wiring (meet server routes)
# ---------------------------------------------------------------------------

def test_get_wallet_info_returns_200() -> None:
    status, data = _meet_get("/v1/wallet/info")
    assert status == 200
    assert "pubkey" in data["result"]


def test_get_wallet_info_pubkey_is_nonempty_string() -> None:
    _, data = _meet_get("/v1/wallet/info")
    pubkey = data["result"].get("pubkey") or ""
    assert isinstance(pubkey, str) and len(pubkey) > 10


def test_get_credits_balance_returns_200() -> None:
    status, data = _meet_get("/v1/credits/balance")
    assert status == 200
    result = data["result"]
    assert "balance" in result
    assert "peer_id" in result
    assert "entries" in result


def test_get_credits_balance_entries_is_list() -> None:
    _, data = _meet_get("/v1/credits/balance")
    assert isinstance(data["result"]["entries"], list)


# ---------------------------------------------------------------------------
# Gap 2 — Wallet routes on NULLA API (:11435 service.py)
# ---------------------------------------------------------------------------

def test_service_wallet_info_returns_200() -> None:
    r = dispatch_get(path="/v1/wallet/info", query={}, runtime=_rt(), model_name="nulla")
    assert r.status == 200
    body = json.loads(r.body)
    assert "pubkey" in body


def test_service_credits_balance_returns_200() -> None:
    r = dispatch_get(path="/v1/credits/balance", query={}, runtime=_rt(), model_name="nulla")
    assert r.status == 200
    body = json.loads(r.body)
    assert "balance" in body


def test_service_credits_settle_returns_200() -> None:
    r = dispatch_post(
        path="/v1/credits/settle",
        body={},
        headers={},
        runtime=_rt(),
        model_name="nulla",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=None,
    )
    assert r.status == 200
    body = json.loads(r.body)
    assert "balance" in body
    assert "mode" in body


# ---------------------------------------------------------------------------
# Gap 3 — Task market routes
# ---------------------------------------------------------------------------

def test_get_tasks_queue_returns_200() -> None:
    status, data = _meet_get("/v1/tasks/queue")
    assert status == 200
    assert isinstance(data["result"], list)


def test_post_task_claim_nonexistent_returns_409() -> None:
    status, _ = _meet_post("/v1/tasks/nonexistent-task-xyz/claim", {})
    assert status == 409


def test_post_credits_settle_meet_returns_200() -> None:
    status, data = _meet_post("/v1/credits/settle", {})
    assert status == 200
    assert "balance" in data["result"]


def test_post_task_complete_nonexistent_returns_200() -> None:
    # complete on missing task still returns 200 (no-op on DB, returns status)
    status, data = _meet_post("/v1/tasks/nonexistent-task-xyz/complete", {"result_hash": "abc123"})
    assert status == 200
    assert data["result"]["status"] == "complete"


# ---------------------------------------------------------------------------
# Gap 3b — task_offer_store unit tests
# ---------------------------------------------------------------------------

def test_task_offer_store_list_returns_list() -> None:
    from storage.task_offer_store import list_open_task_offers
    rows = list_open_task_offers(limit=5)
    assert isinstance(rows, list)


def test_task_offer_store_get_nonexistent_returns_none() -> None:
    from storage.task_offer_store import get_task_offer
    assert get_task_offer("does-not-exist-xyz") is None


def test_task_offer_store_claim_nonexistent_returns_false() -> None:
    from storage.task_offer_store import claim_task_offer
    assert claim_task_offer("not-a-real-task", "peer-1") is False


# ---------------------------------------------------------------------------
# Gap 4 — Receipt wallet wiring + credit award
# ---------------------------------------------------------------------------

def test_attach_receipt_uses_wallet_pubkey() -> None:
    payload = _attach_work_receipt(
        {"response": "result text"},
        result={"response": "result text"},
        session_id="wiring-test",
    )
    receipt = payload.get("web0_receipt") or {}
    payment = receipt.get("payment") or {}
    wallet = payment.get("recipient_wallet") or ""
    # Must NOT be the old stub default
    assert wallet != "stub-wallet", f"Still using stub-wallet, wiring failed. Got: {wallet}"
    assert len(wallet) > 10


def test_attach_receipt_awards_credits_to_local_peer() -> None:
    from core.credit_ledger import get_credit_balance
    from network.signer import get_local_peer_id
    peer_id = get_local_peer_id()
    balance_before = get_credit_balance(peer_id)
    _attach_work_receipt(
        {"response": "earn some credits"},
        result={"response": "earn some credits"},
        session_id="credit-award-test",
    )
    balance_after = get_credit_balance(peer_id)
    assert balance_after >= balance_before + 1.0


# ---------------------------------------------------------------------------
# Gap 4b — Solana anchor hook (env-gated)
# ---------------------------------------------------------------------------

def test_anchor_hook_fires_when_env_set() -> None:
    with mock.patch.dict("os.environ", {"NULLA_ANCHOR_RECEIPTS": "1"}):
        with mock.patch("core.solana_anchor.anchor_vault_proof") as mock_anchor:
            mock_anchor.return_value = "mock-sig-test"
            _attach_work_receipt(
                {"response": "anchor test"},
                result={"response": "anchor test"},
                session_id="anchor-env-test",
            )
            mock_anchor.assert_called_once()
            call_kwargs = mock_anchor.call_args.kwargs
            assert call_kwargs["parent_task_id"] == "anchor-env-test"
            assert call_kwargs["confidence"] == 1.0


def test_anchor_hook_silent_when_env_not_set() -> None:
    with mock.patch.dict("os.environ", {}, clear=False):
        os_env = dict(__import__("os").environ)
        os_env.pop("NULLA_ANCHOR_RECEIPTS", None)
        with mock.patch.dict("os.environ", os_env, clear=True):
            with mock.patch("core.solana_anchor.anchor_vault_proof") as mock_anchor:
                _attach_work_receipt(
                    {"response": "no anchor"},
                    result={"response": "no anchor"},
                    session_id="no-anchor-test",
                )
                mock_anchor.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 6 — Background task poll loop idempotency
# ---------------------------------------------------------------------------

def test_start_web0_background_workers_is_idempotent() -> None:
    import threading

    from core.runtime_backbone import start_web0_background_workers
    count_before = threading.active_count()
    start_web0_background_workers()
    start_web0_background_workers()
    start_web0_background_workers()
    # Should start at most 1 new daemon thread
    assert threading.active_count() <= count_before + 1
