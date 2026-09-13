import json
import tempfile
import unittest
from pathlib import Path

from hermes.platform.models.model_resolver import ModelResolver, UnknownModelProfileError, DEFAULT_PROFILES_PATH
from hermes.platform.models.provider_router import ExactModelRouter, ModelRouteExhaustedException
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.capabilities.registry import CapabilityRegistry, CapabilityResolver
from hermes.platform.capabilities.lsp.manager import LSPManager

class TestModelsAndCapabilities(unittest.TestCase):
    def test_default_profiles_resolve_from_config_file(self):
        # Concrete models live in config (Emenda 26/D4), not in code.
        self.assertTrue(DEFAULT_PROFILES_PATH.exists())
        resolver = ModelResolver()
        coding = resolver.resolve("coding-primary")
        self.assertEqual(coding.model_identity.family, "deepseek-v4")
        self.assertGreaterEqual(len(coding.routes), 1)
        # Config is the source of truth: a profile absent from the file is unknown.
        with self.assertRaises(UnknownModelProfileError):
            resolver.resolve("not-in-config")
    def test_exact_model_routing_with_breaker_per_model_provider_pair(self):
        resolver = ModelResolver()
        profile = resolver.resolve("coding-primary")
        self.assertIsNotNone(profile)

        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        router = ExactModelRouter(circuit_breaker=cb)

        # Primary route should be priority 1 (a6api)
        r1 = router.select_route(profile)
        self.assertEqual(r1.provider_id, "a6api")

        # Open the circuit for (coding-primary model identity, a6api) only
        a6api_key = cb.route_key("a6api", profile.model_identity)
        cb.record_failure(a6api_key)
        cb.record_failure(a6api_key)
        self.assertTrue(cb.is_open(a6api_key))

        # Fallback to priority 2 (deepseek-direct) WITHOUT changing model identity
        r2 = router.select_route(profile)
        self.assertEqual(r2.provider_id, "deepseek-direct")

        # Open circuit for (coding-primary, deepseek-direct)
        direct_key = cb.route_key("deepseek-direct", profile.model_identity)
        cb.record_failure(direct_key)
        cb.record_failure(direct_key)

        # All routes exhausted for THIS exact model -> raise
        with self.assertRaises(ModelRouteExhaustedException):
            router.select_route(profile)

    def test_breaker_isolation_between_models_on_same_provider(self):
        """HAOS v1.1 Emenda 10: a provider can be down for model A, healthy for model B."""
        resolver = ModelResolver()
        coding = resolver.resolve("coding-primary")
        architecture = resolver.resolve("architecture-primary")

        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        router = ExactModelRouter(circuit_breaker=cb)

        coding_a6api = cb.route_key("a6api", coding.model_identity)
        cb.record_failure(coding_a6api)
        cb.record_failure(coding_a6api)

        # coding-primary must skip a6api...
        self.assertEqual(router.select_route(coding).provider_id, "deepseek-direct")
        # ...but architecture-primary on the SAME a6api provider is unaffected.
        self.assertEqual(router.select_route(architecture).provider_id, "a6api")

    def test_custom_config_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "profiles.json"
            cfg.write_text(json.dumps({
                "model_profiles": {
                    "custom-1": {
                        "model_identity": {"family": "acme", "variant": "x1"},
                        "routes": [{"provider_id": "acme-api", "provider_model_id": "acme-x1", "priority": 1}],
                    }
                }
            }))
            resolver = ModelResolver(profiles_path=cfg)
            profile = resolver.resolve("custom-1")
            self.assertEqual(profile.model_identity.family, "acme")
            self.assertEqual(profile.routes[0].provider_id, "acme-api")
            with self.assertRaises(UnknownModelProfileError):
                resolver.resolve("coding-primary")  # not in this file

    def test_missing_config_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ModelResolver(profiles_path="/nonexistent/profiles.json")

    def test_unknown_profile_raises_fail_fast(self):
        resolver = ModelResolver()
        with self.assertRaises(UnknownModelProfileError):
            resolver.resolve("nonexistent-profile")

    def test_capability_resolver(self):
        registry = CapabilityRegistry()
        resolver = CapabilityResolver(registry)

        caps = resolver.resolve_requirements(["code-intelligence", "git"])
        self.assertIn("code-intelligence", caps)
        self.assertEqual(caps["code-intelligence"].execution_kind, "deterministic")

    def test_lsp_manager(self):
        mgr = LSPManager()
        client = mgr.get_client("/tmp/test_workspace")
        symbols = client.get_document_symbols("test.py")
        self.assertTrue(len(symbols) > 0)
        diag = client.get_diagnostics()
        self.assertEqual(diag["new_errors"], 0)

if __name__ == "__main__":
    unittest.main()
