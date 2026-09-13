import asyncio
import unittest
from hermes.platform.capabilities.registry import (
    Capability, CapabilityRegistry, CapabilityProvider,
)
from hermes.platform.extensions.registry import (
    ExtensionRegistry, ExtensionManifest,
    PENDING, ACTIVE, DISPOSED,
)
from hermes.platform.evals.runner import EvalRunner, EvalSuite, EvalCase, compare_results
from hermes.platform.evals.baselines import BaselineStore


class _DummyProvider(CapabilityProvider):
    """Tiny concrete provider proving the probe/acquire/release contract."""

    provider_id = "dummy-capability"

    async def probe(self):
        return {"status": "available", "provider": self.provider_id}

    async def acquire(self, request):
        return {"handle": f"h-{request.get('id', '?')}", "provider": self.provider_id}

    async def release(self, handle):
        return None


class TestExtensionFabric(unittest.TestCase):
    def test_extension_lifecycle_and_capability_bridge(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)

        manifest = ExtensionManifest(
            id="hermes-lsp-ext",
            version="1.0",
            provides=["capability:code-intelligence-v2"],
            requires=["service:workspace"],
            permissions={"filesystem": "workspace", "network": False},
        )
        registry.register(manifest)
        self.assertEqual(registry.status("hermes-lsp-ext"), PENDING)

        self.assertEqual(registry.activate("hermes-lsp-ext"), ACTIVE)
        self.assertIsNotNone(caps.get("code-intelligence-v2"))

        # Dependency gating: an extension requiring an unprovided capability cannot activate.
        caps2 = CapabilityRegistry()
        registry2 = ExtensionRegistry(capability_registry=caps2)
        needs = ExtensionManifest(id="needs-x", version="1.0", provides=[], requires=["capability:graphrag"])
        registry2.register(needs)
        with self.assertRaises(RuntimeError):
            registry2.activate("needs-x")

        # Deactivate tears the bridged capability down.
        self.assertEqual(registry.deactivate("hermes-lsp-ext"), DISPOSED)
        self.assertIsNone(caps.get("code-intelligence-v2"))


class TestEvalHarness(unittest.TestCase):
    def test_runner_aggregates_and_compare_verdicts(self):
        suite = EvalSuite(
            id="sanity",
            cases=[
                EvalCase(id="c1", input=1, expected=1),
                EvalCase(id="c2", input=2, expected=4),
                EvalCase(id="c3", input=3, expected=6),
            ],
        )
        runner = EvalRunner()

        def run_fn(case):
            # "model A" solves 2/3; "model B" solves all.
            wrong = (case.id == "c2")
            return {"passed": not wrong, "score": 1.0 if not wrong else 0.0}

        base = runner.run_suite(suite, run_fn, label="model-a")
        cand = EvalRunner().run_suite(suite, lambda c: {"passed": True, "score": 1.0}, label="model-b")

        self.assertEqual(base.pass_rate, 2 / 3)
        self.assertEqual(cand.pass_rate, 1.0)
        report = compare_results(base, cand)
        self.assertEqual(report["verdict"], "improved")

    def test_baseline_store_roundtrip(self):
        store = BaselineStore(":memory:")
        store.save("suite-1", "hermes-upstream", {"pass_rate": 0.7})
        store.save("suite-1", "haos-fork", {"pass_rate": 0.85})
        latest = store.latest("suite-1", "haos-fork")
        self.assertEqual(latest["metrics"]["pass_rate"], 0.85)


class TestCapabilityProviderContract(unittest.TestCase):
    def test_provider_probe_acquire_release(self):
        provider = _DummyProvider()
        registry = CapabilityRegistry()
        registry.register(Capability(id="dummy-capability", execution_kind="deterministic", providers=["dummy-capability"]))
        registry.register_provider(provider)
        self.assertIs(registry.get_provider("dummy-capability"), provider)

        async def exercise():
            probe = await provider.probe()
            self.assertEqual(probe["status"], "available")
            handle = await provider.acquire({"id": "abc"})
            self.assertEqual(handle["handle"], "h-abc")
            await provider.release(handle)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
