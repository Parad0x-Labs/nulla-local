from __future__ import annotations

import unittest
import uuid

import core.solana_anchor as anchor
from core.final_response_store import (
    get_final_response,
    set_anchored_signature,
    store_final_response,
)
from storage.db import get_connection
from storage.migrations import run_migrations


class AnchoredSignatureRoundTripTests(unittest.TestCase):
    """#4 — the captured anchor tx signature persists on the finalized row."""

    def setUp(self) -> None:
        run_migrations()

    def _new_task_id(self) -> str:
        return f"task-{uuid.uuid4().hex}"

    def test_signature_round_trips_on_finalized_row(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="raw text",
            rendered="rendered text",
            status="finalized",
            confidence=0.9,
        )
        # Before capture the column exists but is null (additive, no default).
        before = get_final_response(task_id)
        assert before is not None
        self.assertIn("anchored_signature", before)
        self.assertIsNone(before["anchored_signature"])

        sig = "5" + "z" * 80  # plausible base58 tx signature
        updated = set_anchored_signature(task_id, sig)
        self.assertTrue(updated)

        after = get_final_response(task_id)
        assert after is not None
        self.assertEqual(after["anchored_signature"], sig)
        # The rest of the row is untouched by the additive update.
        self.assertEqual(after["rendered_persona_text"], "rendered text")
        self.assertEqual(after["status_marker"], "finalized")

    def test_set_signature_is_noop_for_unknown_task(self) -> None:
        self.assertFalse(set_anchored_signature(self._new_task_id(), "deadbeef"))

    def test_set_signature_rejects_empty_inputs(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="r",
            rendered="r",
            status="finalized",
            confidence=0.5,
        )
        self.assertFalse(set_anchored_signature(task_id, ""))
        self.assertFalse(set_anchored_signature("", "sig"))

    def test_column_migration_is_idempotent(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="r",
            rendered="r",
            status="finalized",
            confidence=0.5,
        )
        # Repeated reads/writes must not error or duplicate the column.
        self.assertIsNotNone(get_final_response(task_id))
        self.assertTrue(set_anchored_signature(task_id, "sig-1"))
        self.assertTrue(set_anchored_signature(task_id, "sig-2"))
        conn = get_connection()
        try:
            cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(finalized_responses)").fetchall()]
        finally:
            conn.close()
        self.assertEqual(cols.count("anchored_signature"), 1)


class ConfirmSignatureParserTests(unittest.TestCase):
    """#14 — the optional confirm helper parses a getSignatureStatuses fixture."""

    # A representative getSignatureStatuses RPC result for a landed tx.
    _LANDED_FIXTURE = {
        "context": {"slot": 82_493_733},
        "value": [
            {
                "slot": 72_191_500,
                "confirmations": None,
                "err": None,
                "confirmationStatus": "finalized",
            }
        ],
    }

    def test_parses_landed_status(self) -> None:
        status = anchor.parse_signature_status(self._LANDED_FIXTURE)
        assert status is not None
        self.assertEqual(status["confirmationStatus"], "finalized")
        self.assertIsNone(status["err"])
        self.assertEqual(status["slot"], 72_191_500)

    def test_unknown_signature_value_null(self) -> None:
        self.assertIsNone(anchor.parse_signature_status({"context": {}, "value": [None]}))

    def test_empty_or_bad_shapes(self) -> None:
        self.assertIsNone(anchor.parse_signature_status(None))
        self.assertIsNone(anchor.parse_signature_status({}))
        self.assertIsNone(anchor.parse_signature_status({"value": []}))
        self.assertIsNone(anchor.parse_signature_status({"value": "nope"}))

    def test_confirm_signature_uses_helper_without_hot_path(self) -> None:
        calls: list[tuple[str, list]] = []

        def fake_rpc(method: str, params: list) -> object:
            calls.append((method, params))
            return self._LANDED_FIXTURE

        original = anchor._rpc_call
        anchor._rpc_call = fake_rpc  # type: ignore[assignment]
        try:
            out = anchor.confirm_signature("somesig", commitment="finalized")
        finally:
            anchor._rpc_call = original  # type: ignore[assignment]

        assert out is not None
        self.assertEqual(out["confirmationStatus"], "finalized")
        self.assertEqual(out["requested_commitment"], "finalized")
        # Exactly one light status call; searchTransactionHistory requested.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "getSignatureStatuses")
        self.assertEqual(calls[0][1][0], ["somesig"])
        self.assertTrue(calls[0][1][1]["searchTransactionHistory"])

    def test_confirm_signature_empty_returns_none(self) -> None:
        self.assertIsNone(anchor.confirm_signature(""))

    def test_confirm_signature_swallows_rpc_failure(self) -> None:
        def boom(method: str, params: list) -> object:
            raise RuntimeError("rpc down")

        original = anchor._rpc_call
        anchor._rpc_call = boom  # type: ignore[assignment]
        try:
            self.assertIsNone(anchor.confirm_signature("somesig"))
        finally:
            anchor._rpc_call = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
