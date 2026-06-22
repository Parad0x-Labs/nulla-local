from __future__ import annotations

import unittest
from unittest import mock

from core.execution.payment_tools import _MAX_SPEND_CEILING_USDC, execute_payment_tool
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent, runtime_tool_specs


def _offline_http(*args, **kwargs):
    # No network in tests: dna_get_quote returns an error envelope, so the
    # preview quote stays empty and no spend path is reachable.
    return {"error": True, "message": "offline"}


class SellQuoteTests(unittest.TestCase):
    def test_sell_quote_is_read_only_and_returns_a_quote(self) -> None:
        result = execute_payment_tool("sell.quote", {"resource": "null://task/code-review"})

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "quoted")
        self.assertEqual(result.mode, "tool_executed")
        quote = result.details["quote"]
        self.assertGreater(float(quote["amount_usdc"]), 0.0)
        self.assertTrue(str(quote["quote_hash"]))
        self.assertEqual(quote["service"], "task")
        self.assertEqual(result.details["observation"]["tool_surface"], "x402_market")

    def test_sell_quote_resolves_null_target_endpoint(self) -> None:
        result = execute_payment_tool(
            "sell.quote",
            {"resource": "null://task/render", "null_name": "studio.null"},
            resolve_x402_endpoint_fn=lambda name, **kw: "https://studio.example.test/x402",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.details["quote"]["resolved_x402_endpoint"], "https://studio.example.test/x402")


class PayX402GateTests(unittest.TestCase):
    def test_pay_refuses_to_spend_without_allow_spend(self) -> None:
        spy = mock.Mock()
        result = execute_payment_tool(
            "pay.x402",
            {"resource": "https://compute.example.test/job", "max_spend_usdc": 0.02, "approve": True},
            source_context={"nulla_wallet": object()},
            dna_pay_and_unlock_fn=spy,
            dna_get_quote_fn=_offline_http,
        )

        self.assertTrue(result.handled)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "user_action_required")
        self.assertEqual(result.mode, "tool_preview")
        spy.assert_not_called()
        self.assertFalse(result.details["allow_spend"])
        self.assertIn("allow_spend", result.details["action_required"]["confirm_arguments"])

    def test_pay_refuses_without_approval_even_with_allow_spend(self) -> None:
        spy = mock.Mock()
        result = execute_payment_tool(
            "pay.x402",
            {"resource": "https://compute.example.test/job", "allow_spend": True, "max_spend_usdc": 0.02},
            source_context={"nulla_wallet": object()},
            dna_pay_and_unlock_fn=spy,
            dna_get_quote_fn=_offline_http,
        )

        self.assertEqual(result.status, "user_action_required")
        spy.assert_not_called()

    def test_pay_respects_the_cap_when_authorized(self) -> None:
        captured: dict[str, object] = {}

        def fake_pay(resource, wallet, *, max_spend_usdc, privacy_path, allow_spend):
            captured["max_spend_usdc"] = max_spend_usdc
            captured["allow_spend"] = allow_spend
            return {
                "status": "paid",
                "amount_paid_usdc": max_spend_usdc,
                "receipt_id": "rcpt-1",
            }

        # An over-large opt-in is clamped to the hard ceiling, never above it.
        result = execute_payment_tool(
            "pay.x402",
            {
                "resource": "https://compute.example.test/job",
                "allow_spend": True,
                "approve": True,
                "max_spend_usdc": 999.0,
            },
            source_context={"nulla_wallet": object()},
            dna_pay_and_unlock_fn=fake_pay,
            dna_get_quote_fn=_offline_http,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "paid")
        self.assertTrue(captured["allow_spend"])
        self.assertLessEqual(float(captured["max_spend_usdc"]), _MAX_SPEND_CEILING_USDC)
        self.assertEqual(float(captured["max_spend_usdc"]), _MAX_SPEND_CEILING_USDC)

    def test_pay_authorized_buy_surfaces_quote_exceeds_cap_error(self) -> None:
        def fake_pay(resource, wallet, *, max_spend_usdc, privacy_path, allow_spend):
            return {"error": "quote_exceeds_max_spend", "amount_usdc": 5.0, "max_spend_usdc": max_spend_usdc}

        result = execute_payment_tool(
            "pay.x402",
            {
                "resource": "https://compute.example.test/job",
                "allow_spend": True,
                "approve": True,
                "max_spend_usdc": 0.02,
            },
            source_context={"nulla_wallet": object()},
            dna_pay_and_unlock_fn=fake_pay,
            dna_get_quote_fn=_offline_http,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "quote_exceeds_max_spend")


class PaymentToolWiringTests(unittest.TestCase):
    def test_runtime_specs_advertise_both_payment_tools(self) -> None:
        specs = {item["intent"]: item for item in runtime_tool_specs()}

        self.assertIn("sell.quote", specs)
        self.assertIn("pay.x402", specs)
        self.assertTrue(specs["sell.quote"]["read_only"])
        self.assertFalse(specs["pay.x402"]["read_only"])

    def test_executor_dispatches_pay_x402_into_safe_user_action_required(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch("core.execution.payment_tools.dna_pay_and_unlock") as paid, mock.patch(
            "core.execution.payment_tools.dna_get_quote",
            side_effect=_offline_http,
        ):
            result = execute_tool_intent(
                {"intent": "pay.x402", "arguments": {"resource": "https://compute.example.test/job"}},
                task_id="task-1",
                session_id="session-1",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.handled)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "user_action_required")
        self.assertEqual(result.mode, "tool_preview")
        paid.assert_not_called()

    def test_executor_dispatches_sell_quote_read_only(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        result = execute_tool_intent(
            {"intent": "sell.quote", "arguments": {"resource": "null://embed/search"}},
            task_id="task-1",
            session_id="session-1",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "quoted")
        self.assertEqual(result.tool_name, "sell.quote")
        self.assertEqual(result.details["quote"]["service"], "embed")


if __name__ == "__main__":
    unittest.main()
