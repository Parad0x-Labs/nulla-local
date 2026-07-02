from __future__ import annotations

import unittest
from unittest import mock

from core import policy_engine
from core.model_selection_policy import ModelSelectionRequest, select_provider
from storage.model_provider_manifest import ModelProviderManifest


class LocalOnlyPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cache = getattr(policy_engine, "_POLICY_CACHE", None)

    def tearDown(self) -> None:
        policy_engine._POLICY_CACHE = self._old_cache

    def test_local_only_mode_disables_web_and_remote_only(self) -> None:
        policy_engine._POLICY_CACHE = {
            "system": {
                "local_only_mode": True,
                "allow_web_fallback": True,
                "allow_remote_only_without_backend": True,
            }
        }
        self.assertFalse(policy_engine.allow_web_fallback())
        self.assertFalse(policy_engine.allow_remote_only_without_backend())

    def test_local_only_mode_filters_remote_model_providers(self) -> None:
        policy_engine._POLICY_CACHE = {"system": {"local_only_mode": True}}
        local_manifest = ModelProviderManifest(
            provider_name="local-qwen-http",
            model_name="qwen-local",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            capabilities=["summarize", "structured_json"],
            runtime_config={"base_url": "http://127.0.0.1:11434"},
        )
        remote_manifest = ModelProviderManifest(
            provider_name="remote-http",
            model_name="remote-model",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="remote-provider",
            capabilities=["summarize", "structured_json"],
            runtime_config={"base_url": "https://provider.example"},
        )
        selected = select_provider(
            [remote_manifest, local_manifest],
            ModelSelectionRequest(task_kind="summarization", output_mode="summary_block", allow_paid_fallback=True),
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider_id, local_manifest.provider_id)

    def test_outbound_shard_validation_blocks_secret_like_content(self) -> None:
        shard = {
            "schema_version": 1,
            "problem_class": "security_hardening",
            "summary": "Operator email is operator@example.com and the API key is sk-testsecret1234567890.",
            "resolution_pattern": ["identify_sensitive_surfaces", "remove_secret_exposure_paths"],
            "risk_flags": [],
        }
        self.assertFalse(
            policy_engine.validate_outbound_shard(
                shard,
                share_scope="hive_mind",
                restricted_terms=["operator@example.com"],
            )
        )

    def test_outbound_shard_validation_rejects_local_only_scope(self) -> None:
        shard = {
            "schema_version": 1,
            "problem_class": "system_design",
            "summary": "Generic reusable swarm topology pattern.",
            "resolution_pattern": ["review_problem", "compare_topology", "document_tradeoffs"],
            "risk_flags": [],
        }
        self.assertFalse(policy_engine.validate_outbound_shard(shard, share_scope="local_only"))


class WebOptInPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cache = getattr(policy_engine, "_POLICY_CACHE", None)

    def tearDown(self) -> None:
        policy_engine._POLICY_CACHE = self._old_cache

    def test_web_is_on_by_default(self) -> None:
        # No override env set: web is on after a fresh policy load, so live-data
        # questions (weather, prices, news) get a real answer out of the box.
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("NULLA_ENABLE_WEB", None)
            env.pop("NULLA_ALLOW_WEB", None)
            env.pop("NULLA_DISABLE_WEB", None)
            env.pop("NULLA_NO_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_web_default_value_in_code_default_policy_is_on(self) -> None:
        self.assertTrue(policy_engine._DEFAULT_POLICY["system"]["allow_web_fallback"])

    def test_nulla_enable_web_env_turns_web_on(self) -> None:
        with mock.patch.dict("os.environ", {"NULLA_ENABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_nulla_allow_web_alias_turns_web_on(self) -> None:
        with mock.patch.dict("os.environ", {"NULLA_ALLOW_WEB": "true"}, clear=False) as env:
            env.pop("NULLA_ENABLE_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_nulla_disable_web_env_turns_web_off(self) -> None:
        with mock.patch.dict("os.environ", {"NULLA_DISABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_nulla_no_web_alias_turns_web_off(self) -> None:
        with mock.patch.dict("os.environ", {"NULLA_NO_WEB": "true"}, clear=False) as env:
            env.pop("NULLA_DISABLE_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_disable_env_wins_over_enable_env(self) -> None:
        with mock.patch.dict("os.environ", {"NULLA_ENABLE_WEB": "1", "NULLA_DISABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_opt_in_env_still_blocked_by_local_only_mode(self) -> None:
        # Local-only mode is an even stronger guarantee than the web opt-in.
        with mock.patch.dict("os.environ", {"NULLA_ENABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = {
                "system": {"local_only_mode": True, "allow_web_fallback": True}
            }
            self.assertFalse(policy_engine.allow_web_fallback())


if __name__ == "__main__":
    unittest.main()
