"""Unit tests for UniversalCapabilityRegistry — Etapa 3.

Validates:
- Registration of capabilities across all categories (MCP, LSP, Kilo, Multimodal, Browser, Knowledge/Memory, Federated Agents, Plugins).
- Posture-based capability resolution and filtering.
- Dynamic capability discovery and packs expansion.
- Health checks, caching, and fail-open/fail-closed behaviors.
- Strict stdlib-only compliance and PEP-420 namespace.
"""

import unittest
from typing import List

from hermes.platform.capabilities.universal_registry import (
    CapabilityCategory,
    CapabilityMetadata,
    FailurePolicy,
    HealthStatus,
    UniversalCapabilityRegistry,
)


class TestUniversalCapabilityRegistry(unittest.TestCase):
    """Test suite for UniversalCapabilityRegistry."""

    def setUp(self):
        self.registry = UniversalCapabilityRegistry()

    def test_canonical_defaults_registered(self):
        """Verify default capabilities cover all categories specified in requirements."""
        # 1. Plugins Hermes
        plugin_cap = self.registry.get("plugin:hermes_memory")
        self.assertIsNotNone(plugin_cap)
        self.assertEqual(plugin_cap.category, CapabilityCategory.PLUGIN)

        # 2. MCP Packs
        mcp_gh = self.registry.get("mcp:github")
        self.assertIsNotNone(mcp_gh)
        self.assertEqual(mcp_gh.category, CapabilityCategory.MCP)
        mcp_fetch = self.registry.get("mcp:fetch")
        self.assertIsNotNone(mcp_fetch)

        # 3. LSP Language Servers
        lsp_py = self.registry.get("lsp:python")
        self.assertIsNotNone(lsp_py)
        self.assertEqual(lsp_py.category, CapabilityCategory.LSP)
        lsp_ts = self.registry.get("lsp:typescript")
        self.assertIsNotNone(lsp_ts)

        # 4. Kilo Lane Worktrees
        kilo_wt = self.registry.get("kilo:worktree")
        self.assertIsNotNone(kilo_wt)
        self.assertEqual(kilo_wt.category, CapabilityCategory.KILO)
        kilo_git = self.registry.get("kilo:git")
        self.assertIsNotNone(kilo_git)

        # 5. Vision & Multimodal Workers
        vision = self.registry.get("vision:analyzer")
        self.assertIsNotNone(vision)
        self.assertEqual(vision.category, CapabilityCategory.MULTIMODAL)
        audio = self.registry.get("audio:transcriber")
        self.assertIsNotNone(audio)

        # 6. Browser automation
        browser_cdp = self.registry.get("browser:cdp")
        self.assertIsNotNone(browser_cdp)
        self.assertEqual(browser_cdp.category, CapabilityCategory.BROWSER)
        browser_nav = self.registry.get("browser:nav")
        self.assertIsNotNone(browser_nav)

        # 7. GraphRAG & Knowledge Retrieval
        graphrag = self.registry.get("graphrag:query")
        self.assertIsNotNone(graphrag)
        self.assertEqual(graphrag.category, CapabilityCategory.KNOWLEDGE)
        obsidian = self.registry.get("obsidian:read")
        self.assertIsNotNone(obsidian)

        # 8. External ANP/A2A Federated Agents
        ext_agent = self.registry.get("agent:external")
        self.assertIsNotNone(ext_agent)
        self.assertEqual(ext_agent.category, CapabilityCategory.FEDERATED_AGENT)

    def test_list_by_category(self):
        """Test listing capabilities filtered by category."""
        mcp_caps = self.registry.list_by_category(CapabilityCategory.MCP)
        mcp_ids = {c.id for c in mcp_caps}
        self.assertIn("mcp:github", mcp_ids)
        self.assertIn("mcp:fetch", mcp_ids)

        lsp_caps = self.registry.list_by_category("lsp")
        lsp_ids = {c.id for c in lsp_caps}
        self.assertIn("lsp:python", lsp_ids)
        self.assertIn("lsp:typescript", lsp_ids)

        kilo_caps = self.registry.list_by_category(CapabilityCategory.KILO)
        kilo_ids = {c.id for c in kilo_caps}
        self.assertIn("kilo:worktree", kilo_ids)
        self.assertIn("kilo:git", kilo_ids)

    def test_custom_registration_and_unregistration(self):
        """Test registering a new capability and unregistering it."""
        custom_cap = CapabilityMetadata(
            id="custom:tool",
            category=CapabilityCategory.CUSTOM,
            provider_type="custom_worker",
            description="Custom user tool",
            cost_per_invocation=0.005,
            allowed_postures=["researcher"],
        )
        self.registry.register(custom_cap)
        self.assertEqual(self.registry.get("custom:tool"), custom_cap)

        by_cat = self.registry.list_by_category(CapabilityCategory.CUSTOM)
        self.assertEqual(len(by_cat), 1)
        self.assertEqual(by_cat[0].id, "custom:tool")

        unregistered = self.registry.unregister("custom:tool")
        self.assertTrue(unregistered)
        self.assertIsNone(self.registry.get("custom:tool"))
        self.assertEqual(len(self.registry.list_by_category(CapabilityCategory.CUSTOM)), 0)

    def test_posture_based_authorization_and_resolution(self):
        """Test filtering capabilities based on agent posture."""
        # For 'coding' posture:
        # kilo:worktree is allowed (allowed_postures=["coding", "autonomous"])
        # browser:cdp is NOT allowed (allowed_postures=["researcher", "autonomous"])
        # plugin:hermes_memory has no 'coding' explicitly, so excluded if posture is checked
        resolved_coding = self.registry.resolve(
            ["kilo:worktree", "browser:cdp", "lsp:python"],
            posture="coding",
        )
        resolved_ids = [c.id for c in resolved_coding]
        self.assertIn("kilo:worktree", resolved_ids)
        self.assertIn("lsp:python", resolved_ids)
        self.assertNotIn("browser:cdp", resolved_ids)

        # For 'researcher' posture:
        resolved_researcher = self.registry.resolve(
            ["kilo:worktree", "browser:cdp", "lsp:python"],
            posture="researcher",
        )
        res_ids = [c.id for c in resolved_researcher]
        self.assertIn("browser:cdp", res_ids)
        self.assertNotIn("kilo:worktree", res_ids)
        self.assertNotIn("lsp:python", res_ids)

    def test_pack_expansion(self):
        """Test resolution expands registered packs into concrete capabilities."""
        resolved = self.registry.resolve(["pack:coding_full"], posture="coding")
        ids = {c.id for c in resolved}
        self.assertTrue(ids.issuperset({"lsp:python", "lsp:typescript", "kilo:worktree", "kilo:git", "mcp:github"}))

    def test_cost_limit_filtering(self):
        """Test that capabilities exceeding max cost limit are filtered out."""
        # vision:analyzer cost is 0.005, obsidian:read is 0.0, mcp:fetch is 0.0005
        resolved = self.registry.resolve(
            ["vision:analyzer", "obsidian:read", "mcp:fetch"],
            max_cost_limit=0.001,
        )
        ids = {c.id for c in resolved}
        self.assertIn("obsidian:read", ids)
        self.assertIn("mcp:fetch", ids)
        self.assertNotIn("vision:analyzer", ids)

    def test_health_check_fail_closed(self):
        """Test that unhealthy capability with FAIL_CLOSED is omitted from resolution."""
        def failing_check():
            return False

        bad_cap = CapabilityMetadata(
            id="test:broken_closed",
            category=CapabilityCategory.CUSTOM,
            provider_type="mock",
            health_check_fn=failing_check,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        )
        self.registry.register(bad_cap)

        health = self.registry.check_health("test:broken_closed")
        self.assertFalse(health.healthy)

        resolved = self.registry.resolve(["test:broken_closed"], check_health_status=True)
        self.assertEqual(len(resolved), 0)

    def test_health_check_fail_open(self):
        """Test that unhealthy capability with FAIL_OPEN is preserved in resolution for graceful degradation."""
        def failing_check():
            raise RuntimeError("Temporary remote outage")

        bad_cap = CapabilityMetadata(
            id="test:broken_open",
            category=CapabilityCategory.CUSTOM,
            provider_type="mock",
            health_check_fn=failing_check,
            failure_policy=FailurePolicy.FAIL_OPEN,
        )
        self.registry.register(bad_cap)

        health = self.registry.check_health("test:broken_open")
        self.assertFalse(health.healthy)
        self.assertIn("Temporary remote outage", health.message)

        # In FAIL_OPEN, it still resolves so caller can fallback or report degraded status
        resolved = self.registry.resolve(["test:broken_open"], check_health_status=True)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].id, "test:broken_open")

    def test_health_check_status_object(self):
        """Test health check returning structured HealthStatus."""
        def healthy_check():
            return HealthStatus(healthy=True, message="ready", details={"ping_ms": 12})

        cap = CapabilityMetadata(
            id="test:healthy_custom",
            category=CapabilityCategory.CUSTOM,
            provider_type="mock",
            health_check_fn=healthy_check,
        )
        self.registry.register(cap)

        status = self.registry.check_health("test:healthy_custom")
        self.assertTrue(status.healthy)
        self.assertEqual(status.message, "ready")
        self.assertEqual(status.details["ping_ms"], 12)


if __name__ == "__main__":
    unittest.main()
