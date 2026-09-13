"""Integration Test for the 7 Real Adapters (Phase 1 — Kernel Adapters).

Validates:
1. A6API as primary provider (ExactModelClient).
2. Second provider (OpenRouter) for exact-model failover on circuit trip.
3. LSP as deterministic capability (CodeSymbolGraph & ImpactAnalyzer).
4. Obsidian as human-readable knowledge adapter (ObsidianAdapter).
5. GraphRAG as derived memory with entities & relations (GraphRAGAdapter).
6. Kilo as worker lane with git worktree isolation (GitWorktreeManager).
7. MCP simple tool for Capability -> MCP resolution (MCPFabric & MCPToolFilter).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from hermes.platform.capabilities.lsp.unified_intelligence import (
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)
from hermes.platform.capabilities.mcp.fabric import MCPCapabilityProvider, MCPFabric
from hermes.platform.capabilities.mcp.unified_fabric import MCPPack, MCPToolFilter, MCPTrustTier
from hermes.platform.capabilities.universal_registry import (
    CapabilityCategory,
    CapabilityMetadata,
    FailurePolicy,
    UniversalCapabilityRegistry,
)
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.client import ChatMessage, ExactModelClient
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


class TestSevenRealAdapters(unittest.TestCase):
    """Integrates and verifies the 7 canonical adapters in Phase 1."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="haos_seven_adapters_"))
        self.repo_dir = self.test_dir / "repo"
        self.vault_dir = self.test_dir / "vault"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.vault_dir.mkdir(parents=True, exist_ok=True)

        os.system(f"git -C {self.repo_dir} init -b main > /dev/null 2>&1")
        os.system(f"git -C {self.repo_dir} config user.name 'Adapter Tester'")
        os.system(f"git -C {self.repo_dir} config user.email 'adapters@haos.local'")
        os.system(f"git -C {self.repo_dir} commit --allow-empty -m 'initial root' > /dev/null 2>&1")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_adapter_1_and_2_a6api_and_exact_failover(self):
        """1 & 2: A6API as primary provider + OpenRouter exact-model failover."""
        cb = CircuitBreaker()
        identity = ModelIdentity(family="deepseek-v3", variant="default")
        profile = ModelProfile(
            id="coding-primary",
            model_identity=identity,
            routes=[
                ProviderRoute(provider_id="a6api", provider_model_id="deepseek-v3", priority=1),
                ProviderRoute(provider_id="openrouter", provider_model_id="deepseek/deepseek-chat", priority=2),
            ],
        )

        # First call: A6API is healthy
        def healthy_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": "Code from A6API"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }

        client = ExactModelClient(circuit_breaker=cb, transport_fn=healthy_transport)
        res1 = client.complete(profile, [ChatMessage("user", "Write fn")])
        self.assertEqual(res1.provider_id, "a6api")
        self.assertEqual(res1.content, "Code from A6API")

        # Second call: A6API fails with 429 -> Failover to OpenRouter for exact model
        def failover_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            if "a6api" in url:
                return 429, {"error": "Rate limit"}
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": "Code from OpenRouter failover"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
            }

        client_failover = ExactModelClient(circuit_breaker=cb, transport_fn=failover_transport)
        res2 = client_failover.complete(profile, [ChatMessage("user", "Write fn")])
        self.assertEqual(res2.provider_id, "openrouter")
        self.assertEqual(res2.content, "Code from OpenRouter failover")
        self.assertEqual(res2.model_family, "deepseek-v3")

    def test_adapter_3_lsp_deterministic_intelligence(self):
        """3: LSP CodeSymbolGraph and ImpactAnalyzer blast radius computation."""
        graph = CodeSymbolGraph()
        graph.add_symbol(
            SymbolNode(
                name="execute_order",
                kind="function",
                file_path="src/order.py",
                location=SymbolLocation("src/order.py", 10, 4),
            )
        )
        graph.add_symbol(
            SymbolNode(
                name="checkout_view",
                kind="function",
                file_path="src/views.py",
                location=SymbolLocation("src/views.py", 42, 8),
            )
        )
        graph.add_call("src/views.py::checkout_view", "src/order.py::execute_order")

        analyzer = ImpactAnalyzer(graph)
        blast = analyzer.analyze_impact(modified_files=["src/order.py"])

        self.assertIn("src/order.py", blast.modified_files)
        self.assertIn("src/views.py", blast.affected_files)
        self.assertIn("src/views.py::checkout_view", blast.affected_callers)

    def test_adapter_4_obsidian_knowledge_adapter(self):
        """4: Obsidian human-readable Markdown vault read/write."""
        obsidian = ObsidianAdapter(vault_path=self.vault_dir)
        item = obsidian.write_note(
            relative_path="20-Architecture/ADR-005.md",
            title="ADR-005 Payment Gateway",
            content="## Decision\nUse Stripe API v3.",
            metadata={"tags": "finance, architecture"},
        )
        self.assertEqual(item.title, "ADR-005 Payment Gateway")
        self.assertTrue((self.vault_dir / "20-Architecture/ADR-005.md").exists())

        loaded = obsidian.read_note("20-Architecture/ADR-005.md")
        self.assertIsNotNone(loaded)
        self.assertIn("Use Stripe API v3", loaded.content)

    def test_adapter_5_graphrag_derived_memory(self):
        """5: GraphRAG derived memory with entities, relations, and communities."""
        graph = GraphRAGAdapter()
        graph.register_entity("PaymentGateway", "ArchitectureComponent", "Core payment component", community="billing")
        graph.register_entity("StripeSDK", "ThirdPartyLibrary", "Stripe API SDK client", community="billing")
        graph.register_relation("PaymentGateway", "StripeSDK", "DEPENDS_ON", "Gateway depends on Stripe SDK")

        # Query local sub-graph
        item = graph.query_local("PaymentGateway")
        self.assertIsNotNone(item)
        self.assertIn("PaymentGateway", item.content)
        self.assertIn("StripeSDK", item.content)
        self.assertIn("DEPENDS_ON", item.content)

    def test_adapter_6_kilo_worker_lane(self):
        """6: Kilo isolated Git worktree provisioning and diff extraction."""
        kilo = GitWorktreeManager(repo_root=self.repo_dir)
        wt_path = kilo.create_worktree(task_id="kilo-adapter-001", base_branch="main")
        self.assertTrue(wt_path.exists())

        test_file = wt_path / "hello.txt"
        test_file.write_text("Hello Kilo!\n", encoding="utf-8")
        os.system(f"git -C {wt_path} add hello.txt > /dev/null 2>&1")
        os.system(f"git -C {wt_path} commit -m 'feat: add hello' > /dev/null 2>&1")

        diff = kilo.get_diff("kilo-adapter-001", base_branch="main")
        self.assertIn("Hello Kilo!", diff)

        cleaned = kilo.remove_worktree("kilo-adapter-001")
        self.assertTrue(cleaned)
        self.assertFalse(wt_path.exists())

    def test_adapter_7_mcp_capability_resolution(self):
        """7: MCP simple tool resolution via MCPFabric & MCPToolFilter."""
        fabric = MCPFabric()
        packs = {
            "dev_tools": MCPPack(
                pack_id="dev_tools",
                name="Development Tools",
                servers=["github", "bash"],
                trust_tier=MCPTrustTier.VERIFIED,
                allowed_postures=["coder"],
            )
        }

        tool_schemas = [
            {"name": "github__create_pr", "_server": "github", "description": "Creates a PR"},
            {"name": "bash__exec", "_server": "bash", "description": "Executes bash"},
            {"name": "admin__drop_db", "_server": "admin", "description": "Admin command"},
        ]

        class FakeTask:
            mcp_packs = ["dev_tools"]

        class FakePosture:
            id = "coder"

        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=tool_schemas,
            task=FakeTask(),
            posture=FakePosture(),
            packs=packs,
        )

        names = [t["name"] for t in filtered]
        self.assertIn("github__create_pr", names)
        self.assertIn("bash__exec", names)
        self.assertNotIn("admin__drop_db", names)


if __name__ == "__main__":
    unittest.main()
