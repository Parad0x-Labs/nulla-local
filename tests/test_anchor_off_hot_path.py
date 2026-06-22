from __future__ import annotations

import threading
import unittest
import uuid
from unittest import mock

import core.solana_anchor as anchor
from core.final_response_store import get_final_response, store_final_response
from core.solana_anchor import dispatch_anchor_in_background
from storage.migrations import run_migrations


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex}"


class DispatchAnchorBackgroundTests(unittest.TestCase):
    """#8 — the gated anchor broadcast runs off the finalize hot path."""

    def setUp(self) -> None:
        run_migrations()

    def test_disabled_is_strict_noop(self) -> None:
        # No env opt-in: no thread, no network call, returns None.
        env = {k: v for k, v in __import__("os").environ.items() if k != "NULLA_ANCHOR_RECEIPTS"}
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch.object(anchor, "anchor_vault_proof") as mock_anchor:
                thread = dispatch_anchor_in_background("task-x", "hash-x", 0.9)
        self.assertIsNone(thread)
        mock_anchor.assert_not_called()

    def test_dispatch_returns_before_anchor_completes(self) -> None:
        # The blocking broadcast is held open; dispatch must return immediately
        # with a live worker thread rather than waiting on the RPC.
        release = threading.Event()
        entered = threading.Event()

        def blocking_anchor(parent_task_id: str, final_response_hash: str, confidence: float) -> str:
            entered.set()
            # Stand in for the serial blocking RPC (getLatestBlockhash +
            # sendTransaction). If dispatch were synchronous, the caller would
            # hang here until release fires.
            release.wait(timeout=5)
            return "5" + "a" * 80

        with mock.patch.dict("os.environ", {"NULLA_ANCHOR_RECEIPTS": "1"}):
            with mock.patch.object(anchor, "anchor_vault_proof", side_effect=blocking_anchor):
                thread = dispatch_anchor_in_background("task-y", "hash-y", 0.5)
                # Returned a started worker, and the anchor is still in-flight.
                assert thread is not None
                self.assertTrue(thread.is_alive())
                self.assertTrue(entered.wait(timeout=5))
                self.assertTrue(thread.is_alive())
                # Now let the broadcast finish and confirm the worker joins.
                release.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_signature_persists_after_thread_joins(self) -> None:
        task_id = _new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="raw",
            rendered="rendered",
            status="finalized",
            confidence=0.9,
        )
        sig = "5" + "b" * 80

        with mock.patch.dict("os.environ", {"NULLA_ANCHOR_RECEIPTS": "1"}):
            with mock.patch.object(anchor, "anchor_vault_proof", return_value=sig) as mock_anchor:
                thread = dispatch_anchor_in_background(task_id, "result-hash", 0.9)
                assert thread is not None
                thread.join(timeout=5)

        mock_anchor.assert_called_once_with(task_id, "result-hash", 0.9)
        row = get_final_response(task_id)
        assert row is not None
        self.assertEqual(row["anchored_signature"], sig)

    def test_failed_broadcast_leaves_signature_null(self) -> None:
        task_id = _new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="raw",
            rendered="rendered",
            status="finalized",
            confidence=0.9,
        )

        with mock.patch.dict("os.environ", {"NULLA_ANCHOR_RECEIPTS": "1"}):
            with mock.patch.object(anchor, "anchor_vault_proof", return_value=None):
                thread = dispatch_anchor_in_background(task_id, "result-hash", 0.9)
                assert thread is not None
                thread.join(timeout=5)

        row = get_final_response(task_id)
        assert row is not None
        self.assertIsNone(row["anchored_signature"])


class FinalizerOffHotPathTests(unittest.TestCase):
    """End-to-end: finalize_parent_response dispatches the anchor off-thread."""

    def setUp(self) -> None:
        run_migrations()

    def _plan(self, task_id: str):
        from core.task_reassembler import ReassembledPlan

        return ReassembledPlan(
            parent_task_id=task_id,
            is_complete=True,
            merged_summary="assembled answer",
            merged_evidence=["signal a"],
            merged_steps=["step a"],
            pending_subtasks=0,
            confidence=0.9,
            completeness_score=0.9,
            result_hash="result-hash-" + task_id,
        )

    def test_finalize_returns_without_waiting_then_persists_signature(self) -> None:
        task_id = _new_task_id()
        release = threading.Event()
        entered = threading.Event()
        sig = "5" + "c" * 80

        def blocking_anchor(parent_task_id: str, final_response_hash: str, confidence: float) -> str:
            entered.set()
            release.wait(timeout=5)
            return sig

        from core import finalizer

        with mock.patch.dict("os.environ", {"NULLA_ANCHOR_RECEIPTS": "1"}):
            with mock.patch.object(finalizer, "reassemble_parent_task", return_value=self._plan(task_id)):
                with mock.patch.object(finalizer, "export_task_bundle"):
                    with mock.patch.object(anchor, "anchor_vault_proof", side_effect=blocking_anchor):
                        result = finalizer.finalize_parent_response(task_id)

                        # finalize returned a result while the anchor broadcast
                        # is still blocked in the background worker.
                        assert result is not None
                        self.assertEqual(result.parent_task_id, task_id)
                        self.assertTrue(entered.wait(timeout=5))

                        # Signature not yet persisted (worker is still blocked).
                        pre = get_final_response(task_id)
                        assert pre is not None
                        self.assertIsNone(pre["anchored_signature"])

                        # Let the broadcast finish, then drain the worker thread.
                        release.set()
                        _join_anchor_workers(timeout=5)

        post = get_final_response(task_id)
        assert post is not None
        self.assertEqual(post["anchored_signature"], sig)

    def test_finalize_does_not_anchor_when_disabled(self) -> None:
        task_id = _new_task_id()
        env = {k: v for k, v in __import__("os").environ.items() if k != "NULLA_ANCHOR_RECEIPTS"}

        from core import finalizer

        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch.object(finalizer, "reassemble_parent_task", return_value=self._plan(task_id)):
                with mock.patch.object(finalizer, "export_task_bundle"):
                    with mock.patch.object(anchor, "anchor_vault_proof") as mock_anchor:
                        result = finalizer.finalize_parent_response(task_id)

        assert result is not None
        mock_anchor.assert_not_called()
        row = get_final_response(task_id)
        assert row is not None
        self.assertIsNone(row["anchored_signature"])


def _join_anchor_workers(*, timeout: float) -> None:
    deadline = timeout
    for t in list(threading.enumerate()):
        if t is threading.current_thread():
            continue
        # The anchor worker targets the module-level _anchor_and_persist helper.
        target = getattr(t, "_target", None)
        if target is anchor._anchor_and_persist:
            t.join(timeout=deadline)


if __name__ == "__main__":
    unittest.main()
