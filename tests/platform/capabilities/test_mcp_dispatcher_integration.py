"""Integration tests for MCP Fabric integration with CapabilityResolver and LaneExecutor.

Validates:
1. Tool filtering dynamically by posture (architect vs coder vs reviewer).
2. Ensuring the agent NEVER receives unbounded hundreds of raw tools.
3. Suppressing tool exposure when circuit breakers are tripped for servers or packs.
4. Circuit breaker failures do not crash the agent turn.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from hermes.platform.capabilities.mcp.unified_fabric import (
    CircuitBreakerPolicy,
    MCPCircuitBreaker,
    MCPPack,
    MCPToolFilter,
    MCPTrustTier,
)
from hermes.platform.capabilities.resolver import (
    CapabilityResolver,
    DEFAULT_MCP_PACKS,
)
from hermes.platform.execution.lane_executor import HermesCliLaneWorker


class TestMCPDispatcherIntegration(unittest.TestCase):
    def setUp(self):
        self.raw_tools: List[Dict[str, Any]] = [
            # Coder / dev tools
            {"name": "git__diff", "server": "git", "description": "View git diff"},
            {"name": "terminal__bash", "server": "terminal", "description": "Run shell command"},
            {"name": "lsp__definition", "server": "lsp", "description": "Go to definition"},
            # Reviewer tools
            {"name": "diff_inspector__inspect", "server": "diff_inspector", "description": "Review git diff"},
            {"name": "test_runner__pytest", "server": "test_runner", "description": "Run pytest suite"},
            # Architect tools
            {"name": "graphrag__query", "server": "graphrag", "description": "Query knowledge graph"},
            {"name": "obsidian__adr_lookup", "server": "obsidian", "description": "Look up ADR note"},
            # Core tools
            {"name": "core_fs__read_file", "server": "core_fs", "description": "Read file content"},
            # Dangerous / untrusted
            {"name": "untrusted_eval__exec", "server": "untrusted_eval", "description": "Exec code"},
        ]

    def test_tool_filtering_by_posture_coder(self):
        """Coder posture gets dev_tools and core, but not reviewer-only or architect-only packs."""
        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="coder",
        )
        tool_names = [t["name"] for t in filtered]

        self.assertIn("git__diff", tool_names)
        self.assertIn("terminal__bash", tool_names)
        self.assertIn("lsp__definition", tool_names)
        self.assertIn("core_fs__read_file", tool_names)
        # Should not include reviewer-only or architect-only tools
        self.assertNotIn("diff_inspector__inspect", tool_names)
        self.assertNotIn("graphrag__query", tool_names)
        self.assertNotIn("obsidian__adr_lookup", tool_names)
        self.assertNotIn("untrusted_eval__exec", tool_names)

    def test_tool_filtering_by_posture_reviewer(self):
        """Reviewer posture gets review_tools and core, but not dev mutation tools or architect tools."""
        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="reviewer",
        )
        tool_names = [t["name"] for t in filtered]

        self.assertIn("diff_inspector__inspect", tool_names)
        self.assertIn("test_runner__pytest", tool_names)
        self.assertIn("core_fs__read_file", tool_names)
        # Should not include coder terminal or architect graphrag
        self.assertNotIn("terminal__bash", tool_names)
        self.assertNotIn("graphrag__query", tool_names)
        self.assertNotIn("obsidian__adr_lookup", tool_names)

    def test_tool_filtering_by_posture_architect(self):
        """Architect posture gets arch_tools and core, but not dev tools or reviewer-only tools."""
        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="architect",
        )
        tool_names = [t["name"] for t in filtered]

        self.assertIn("graphrag__query", tool_names)
        self.assertIn("obsidian__adr_lookup", tool_names)
        self.assertIn("core_fs__read_file", tool_names)
        # Should not include coder terminal/lsp or reviewer diff_inspector
        self.assertNotIn("terminal__bash", tool_names)
        self.assertNotIn("lsp__definition", tool_names)
        self.assertNotIn("diff_inspector__inspect", tool_names)

    def test_agent_never_receives_unbounded_hundreds_of_raw_tools(self):
        """Ensure strict cap so hundreds of discovered raw tools are bounded."""
        huge_toolset: List[Dict[str, Any]] = [
            {"name": f"core_fs__read_file_{i}", "server": "core_fs"} for i in range(250)
        ]
        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=huge_toolset,
            posture="coder",
            max_tools=30,
        )
        self.assertLessEqual(len(filtered), 30)
        self.assertEqual(len(filtered), 30)

        # Default bounding cap (50) applies even when max_tools is not explicitly given
        filtered_default = MCPToolFilter.filter_tools_for_task(
            tool_schemas=huge_toolset,
            posture="coder",
        )
        self.assertLessEqual(len(filtered_default), 50)
        self.assertEqual(len(filtered_default), 50)

    def test_tripped_circuit_breaker_suppresses_tool_exposure(self):
        """When an MCP server or pack trips its circuit breaker, its tools are suppressed."""
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=60)

        # Record failures to trip circuit breaker on terminal server
        cb.record_failure("server:terminal")
        cb.record_failure("server:terminal")
        self.assertTrue(cb.is_tripped("server:terminal"))

        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="coder",
            circuit_breaker=cb,
        )
        tool_names = [t["name"] for t in filtered]

        # terminal__bash must be suppressed because server:terminal is tripped
        self.assertNotIn("terminal__bash", tool_names)
        # Sibling healthy tools in coder pack must still be present
        self.assertIn("git__diff", tool_names)
        self.assertIn("lsp__definition", tool_names)
        self.assertIn("core_fs__read_file", tool_names)

    def test_tripped_pack_circuit_breaker_suppresses_entire_pack(self):
        """When an entire pack's circuit breaker trips, all tools in that pack are suppressed."""
        cb = MCPCircuitBreaker(failure_threshold=1)
        cb.record_failure("pack:dev_tools")
        self.assertTrue(cb.is_tripped("pack:dev_tools"))

        filtered = MCPToolFilter.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="coder",
            circuit_breaker=cb,
        )
        tool_names = [t["name"] for t in filtered]

        self.assertNotIn("git__diff", tool_names)
        self.assertNotIn("terminal__bash", tool_names)
        self.assertNotIn("lsp__definition", tool_names)
        # Core pack tools are untouched
        self.assertIn("core_fs__read_file", tool_names)

    def test_capability_resolver_integration_with_mcp_filter(self):
        """CapabilityResolver exposes filter_tools_for_task and respects posture and circuit breakers."""
        resolver = CapabilityResolver()
        filtered = resolver.filter_tools_for_task(
            tool_schemas=self.raw_tools,
            posture="architect",
        )
        tool_names = [t["name"] for t in filtered]
        self.assertIn("graphrag__query", tool_names)
        self.assertNotIn("terminal__bash", tool_names)

    def test_lane_executor_prepare_tools_integration(self):
        """LaneWorker.prepare_tools filters tools dynamically based on task spec posture and mcp_packs."""
        worker = HermesCliLaneWorker()
        task_spec = {
            "id": "task-architect-123",
            "posture": "architect",
            "title": "Design Unified Architecture",
        }
        filtered = worker.prepare_tools(
            tool_schemas=self.raw_tools,
            spec=task_spec,
        )
        tool_names = [t["name"] for t in filtered]
        self.assertIn("graphrag__query", tool_names)
        self.assertIn("obsidian__adr_lookup", tool_names)
        self.assertNotIn("terminal__bash", tool_names)
        self.assertNotIn("diff_inspector__inspect", tool_names)

    def test_lane_executor_prepare_tools_with_explicit_required_packs(self):
        """LaneWorker.prepare_tools filters tools restricting to explicit required_packs when provided."""
        worker = HermesCliLaneWorker()
        task_spec = {
            "id": "task-review-456",
            "posture": "reviewer",
            "mcp_packs": ["core"],
        }
        filtered = worker.prepare_tools(
            tool_schemas=self.raw_tools,
            spec=task_spec,
        )
        tool_names = [t["name"] for t in filtered]
        # Only core tools allowed because mcp_packs specifically requested "core"
        self.assertIn("core_fs__read_file", tool_names)
        self.assertNotIn("diff_inspector__inspect", tool_names)
        self.assertNotIn("test_runner__pytest", tool_names)


if __name__ == "__main__":
    unittest.main()
