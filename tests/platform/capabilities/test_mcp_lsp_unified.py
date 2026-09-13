"""Tests for Marco 4 (MCP Fabric) and Marco 5 (LSP Fabric) under hermes/platform/capabilities/."""

import time
import unittest
from dataclasses import dataclass
from typing import List

from hermes.platform.capabilities.mcp.unified_fabric import (
    CircuitBreakerPolicy,
    MCPCircuitBreaker,
    MCPPack,
    MCPToolFilter,
    MCPTrustTier,
)
from hermes.platform.capabilities.lsp.unified_intelligence import (
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)


@dataclass
class _DummyTaskSpec:
    id: str = "task-1"
    posture: str = "implementer"
    mcp_packs: List[str] = None


@dataclass
class _DummyPosture:
    id: str = "implementer"
    skills_preferred: List[str] = None


class TestMCPUnifiedFabric(unittest.TestCase):
    """Test Marco 4 MCP Fabric: Trust tiers, posture filtering, circuit breakers."""

    def test_trust_tiers_hierarchy(self):
        self.assertTrue(MCPTrustTier.CORE.is_at_least(MCPTrustTier.VERIFIED_PARTNER))
        self.assertTrue(MCPTrustTier.VERIFIED_PARTNER.is_at_least(MCPTrustTier.COMMUNITY))
        self.assertTrue(MCPTrustTier.COMMUNITY.is_at_least(MCPTrustTier.UNTRUSTED))
        self.assertFalse(MCPTrustTier.UNTRUSTED.is_at_least(MCPTrustTier.COMMUNITY))
        self.assertEqual(MCPTrustTier.CORE.value, "core")

    def test_circuit_breaker_tripping_and_cooldown(self):
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=0.1, policy=CircuitBreakerPolicy.FAIL_CLOSED)
        server_key = "server:weather_api"

        self.assertFalse(cb.is_tripped(server_key))
        self.assertTrue(cb.allow_execution(server_key))

        # 1st failure - not tripped yet
        tripped = cb.record_failure(server_key)
        self.assertFalse(tripped)
        self.assertFalse(cb.is_tripped(server_key))

        # 2nd failure - threshold reached, trips
        tripped = cb.record_failure(server_key)
        self.assertTrue(tripped)
        self.assertTrue(cb.is_tripped(server_key))
        self.assertFalse(cb.allow_execution(server_key))

        # Wait for cooldown
        time.sleep(0.12)
        self.assertFalse(cb.is_tripped(server_key))
        self.assertTrue(cb.allow_execution(server_key))

    def test_circuit_breaker_fail_open_policy(self):
        cb = MCPCircuitBreaker(failure_threshold=1, policy=CircuitBreakerPolicy.FAIL_OPEN)
        cb.trip_now("server:fallback_service")
        self.assertTrue(cb.is_tripped("server:fallback_service"))
        # With fail_open, execution is allowed even when tripped
        self.assertTrue(cb.allow_execution("server:fallback_service"))

    def test_tool_filter_posture_and_trust(self):
        core_pack = MCPPack(
            pack_id="core_tools",
            name="Core Agent Tools",
            servers=["core_fs", "core_git"],
            trust_tier=MCPTrustTier.CORE,
            allowed_postures=["implementer", "reviewer"],
        )
        community_pack = MCPPack(
            pack_id="community_ext",
            name="Community Extensions",
            servers=["unverified_search"],
            trust_tier=MCPTrustTier.COMMUNITY,
            allowed_postures=["researcher"],
        )
        untrusted_pack = MCPPack(
            pack_id="untrusted_pack",
            name="Untrusted Scraper",
            servers=["shady_scraper"],
            trust_tier=MCPTrustTier.UNTRUSTED,
            allowed_postures=["researcher"],
        )

        cb = MCPCircuitBreaker()
        tool_filter = MCPToolFilter(
            packs={
                core_pack.pack_id: core_pack,
                community_pack.pack_id: community_pack,
                untrusted_pack.pack_id: untrusted_pack,
            },
            circuit_breaker=cb,
            minimum_trust_tier=MCPTrustTier.COMMUNITY,
        )

        # 1. Posture: implementer -> should only get core_pack
        task_impl = _DummyTaskSpec(posture="implementer")
        resolved = tool_filter.resolve_packs(task=task_impl)
        self.assertEqual([p.pack_id for p in resolved], ["core_tools"])

        # 2. Posture: researcher -> gets community_pack, but not untrusted_pack (min_trust=COMMUNITY)
        task_res = _DummyTaskSpec(posture="researcher")
        resolved_res = tool_filter.resolve_packs(task=task_res)
        self.assertEqual([p.pack_id for p in resolved_res], ["community_ext"])

        # 3. Dynamic tool filtering by server attribution
        raw_tools = [
            {"name": "core_fs__read_file", "server": "core_fs"},
            {"name": "core_git__commit", "server": "core_git"},
            {"name": "unverified_search__query", "server": "unverified_search"},
            {"name": "shady_scraper__scrape", "server": "shady_scraper"},
        ]

        filtered_tools = tool_filter.filter_tools(task=task_impl, raw_tools=raw_tools)
        tool_names = [t["name"] for t in filtered_tools]
        self.assertIn("core_fs__read_file", tool_names)
        self.assertIn("core_git__commit", tool_names)
        self.assertNotIn("unverified_search__query", tool_names)
        self.assertNotIn("shady_scraper__scrape", tool_names)

        # 4. Trip core_fs circuit breaker -> core_fs tool removed dynamically
        cb.trip_now("server:core_fs")
        filtered_tools_tripped = tool_filter.filter_tools(task=task_impl, raw_tools=raw_tools)
        tool_names_tripped = [t["name"] for t in filtered_tools_tripped]
        self.assertNotIn("core_fs__read_file", tool_names_tripped)
        self.assertIn("core_git__commit", tool_names_tripped)


class TestLSPUnifiedIntelligence(unittest.TestCase):
    """Test Marco 5 LSP Fabric: CodeSymbolGraph, call hierarchy, and blast radius."""

    def setUp(self):
        self.graph = CodeSymbolGraph()

        # Build sample symbols
        # File: hermes/core.py -> process_task -> calls validate_input & execute_engine
        self.sym_validate = SymbolNode(
            name="validate_input",
            kind="function",
            file_path="hermes/core.py",
            location=SymbolLocation("hermes/core.py", 10, 4),
        )
        self.sym_execute = SymbolNode(
            name="execute_engine",
            kind="function",
            file_path="hermes/engine.py",
            location=SymbolLocation("hermes/engine.py", 50, 4),
        )
        self.sym_process = SymbolNode(
            name="process_task",
            kind="function",
            file_path="hermes/core.py",
            location=SymbolLocation("hermes/core.py", 30, 4),
        )
        # Higher caller in CLI
        self.sym_cli = SymbolNode(
            name="run_cli",
            kind="function",
            file_path="hermes/cli.py",
            location=SymbolLocation("hermes/cli.py", 100, 0),
        )
        # Test symbol in test suite
        self.sym_test = SymbolNode(
            name="test_process_task",
            kind="function",
            file_path="tests/test_core.py",
            location=SymbolLocation("tests/test_core.py", 20, 4),
        )

        for s in [self.sym_validate, self.sym_execute, self.sym_process, self.sym_cli, self.sym_test]:
            self.graph.add_symbol(s)

        # Setup call hierarchy:
        # run_cli -> calls process_task
        # test_process_task -> calls process_task
        # process_task -> calls validate_input
        # process_task -> calls execute_engine
        self.graph.add_call(self.sym_cli.id, self.sym_process.id)
        self.graph.add_call(self.sym_test.id, self.sym_process.id)
        self.graph.add_call(self.sym_process.id, self.sym_validate.id)
        self.graph.add_call(self.sym_process.id, self.sym_execute.id)

        # Add a reference
        self.graph.add_reference(
            self.sym_validate.id,
            SymbolLocation(file_path="hermes/core.py", line=32, character=8),
        )

        self.analyzer = ImpactAnalyzer(self.graph)

    def test_call_hierarchy(self):
        callers = self.graph.get_callers(self.sym_process.id)
        self.assertEqual(callers, {self.sym_cli.id, self.sym_test.id})

        callees = self.graph.get_callees(self.sym_process.id)
        self.assertEqual(callees, {self.sym_validate.id, self.sym_execute.id})

        # Transitive callers of validate_input -> process_task -> (run_cli, test_process_task)
        transitive, depth = self.graph.get_transitive_callers(self.sym_validate.id)
        self.assertIn(self.sym_process.id, transitive)
        self.assertIn(self.sym_cli.id, transitive)
        self.assertIn(self.sym_test.id, transitive)
        self.assertEqual(depth, 2)

    def test_impact_analyzer_blast_radius_modified_symbol(self):
        # Modifying validate_input:
        # Blast radius must reach process_task, run_cli, test_process_task
        blast = self.analyzer.calculate_blast_radius(modified_symbols=["validate_input"])

        self.assertIn(self.sym_validate.id, blast.modified_symbols)
        self.assertIn(self.sym_process.id, blast.affected_callers)
        self.assertIn(self.sym_cli.id, blast.affected_callers)
        self.assertIn(self.sym_test.id, blast.affected_callers)

        # Affected files must include tests/test_core.py, hermes/cli.py, hermes/core.py
        self.assertIn("tests/test_core.py", blast.affected_files)
        self.assertIn("hermes/cli.py", blast.affected_files)
        self.assertIn("hermes/core.py", blast.affected_files)

        # Affected test suites must identify tests/test_core.py
        self.assertIn("tests/test_core.py", blast.affected_test_suites)
        self.assertGreaterEqual(blast.depth_reached, 2)

    def test_impact_analyzer_blast_radius_modified_file(self):
        # Modifying hermes/engine.py (which defines execute_engine)
        blast = self.analyzer.calculate_blast_radius(modified_files=["hermes/engine.py"])

        self.assertIn("hermes/engine.py", blast.modified_files)
        self.assertIn(self.sym_execute.id, blast.modified_symbols)
        self.assertIn(self.sym_process.id, blast.affected_callers)
        self.assertIn("tests/test_core.py", blast.affected_test_suites)


if __name__ == "__main__":
    unittest.main()
