"""Integration tests for LSP Impact Analysis with Kilo Worktrees & AutoMerge Gate.

Validates:
1. Blast radius detects modified files and symbols from git diff and generates targeted test commands.
2. Failing affected tests properly block the AutoMergeGate.
3. Passing affected tests permit the merge cleanly.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hermes.platform.capabilities.lsp.unified_intelligence import (
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)
from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


class TestLSPAutoMergeIntegration(unittest.TestCase):
    def setUp(self):
        self.mock_worktree_mgr = MagicMock(spec=GitWorktreeManager)
        self.mock_worktree_mgr.worktrees_dir = Path("/tmp/fake_worktrees")

        # Build symbol graph:
        # file: hermes/service.py -> def process_order()
        # caller: hermes/api.py -> def handle_request()
        # test: tests/test_service.py -> calls process_order()
        self.graph = CodeSymbolGraph()
        self.sym_service = SymbolNode(
            name="process_order",
            kind="function",
            file_path="hermes/service.py",
            location=SymbolLocation(file_path="hermes/service.py", line=10, character=0),
        )
        self.sym_caller = SymbolNode(
            name="handle_request",
            kind="function",
            file_path="hermes/api.py",
            location=SymbolLocation(file_path="hermes/api.py", line=20, character=0),
        )
        self.sym_test = SymbolNode(
            name="test_process_order",
            kind="function",
            file_path="tests/test_service.py",
            location=SymbolLocation(file_path="tests/test_service.py", line=5, character=0),
        )
        self.graph.add_symbol(self.sym_service)
        self.graph.add_symbol(self.sym_caller)
        self.graph.add_symbol(self.sym_test)

        self.graph.add_call(self.sym_caller.id, self.sym_service.id)
        self.graph.add_call(self.sym_test.id, self.sym_service.id)

        self.analyzer = ImpactAnalyzer(self.graph)
        self.gate = AutoMergeGate(
            worktree_manager=self.mock_worktree_mgr,
            symbol_graph=self.graph,
            impact_analyzer=self.analyzer,
        )

    def test_blast_radius_detects_modified_files_and_generates_targeted_tests(self):
        """Test that diff parsing extracts modified files/symbols and blast radius identifies targeted tests."""
        fake_diff = """diff --git a/hermes/service.py b/hermes/service.py
index abc1234..def5678 100644
--- a/hermes/service.py
+++ b/hermes/service.py
@@ -10,3 +10,4 @@ def process_order():
+    print("processing")
"""
        self.mock_worktree_mgr.get_diff.return_value = fake_diff

        mod_files, mod_syms = self.gate.parse_diff_impact(fake_diff)
        self.assertIn("hermes/service.py", mod_files)
        self.assertIn("process_order", mod_syms)

        blast = self.analyzer.analyze_impact(
            modified_symbols=mod_syms,
            modified_files=mod_files,
        )

        self.assertIn("hermes/service.py", blast.modified_files)
        self.assertIn("tests/test_service.py", blast.affected_test_suites)
        self.assertIn(self.sym_caller.id, blast.affected_callers)

    @patch.object(AutoMergeGate, "run_tests_in_worktree")
    def test_failing_affected_tests_block_automerge_gate(self, mock_run_tests):
        """Test that when standard contracts pass but affected unit tests fail, merge is blocked."""
        fake_diff = """diff --git a/hermes/service.py b/hermes/service.py
--- a/hermes/service.py
+++ b/hermes/service.py
@@ -10,3 +10,4 @@ def process_order():
"""
        self.mock_worktree_mgr.get_diff.return_value = fake_diff

        # Mock run_tests_in_worktree:
        # First call (contracts) -> passes
        # Second call (affected tests) -> fails
        def side_effect(task_id, test_command):
            if "test_contracts.py" in test_command:
                return {"success": True, "returncode": 0, "stdout_tail": "Contracts OK"}
            elif "tests/test_service.py" in test_command:
                return {"success": False, "returncode": 1, "stderr_tail": "AssertionError in test_process_order"}
            return {"success": True, "returncode": 0}

        mock_run_tests.side_effect = side_effect

        with patch("pathlib.Path.exists", return_value=True):
            result = self.gate.verify_and_merge("task-blast-fail", target_branch="haos-fork")

        self.assertFalse(result["merged"])
        self.assertIn("Affected unit tests failed", result["reason"])
        self.assertIn("tests/test_service.py", result.get("affected_tests", []))
        self.mock_worktree_mgr.merge_worktree.assert_not_called()
        self.mock_worktree_mgr.remove_worktree.assert_not_called()

    @patch.object(AutoMergeGate, "run_tests_in_worktree")
    def test_passing_affected_tests_permit_merge_cleanly(self, mock_run_tests):
        """Test that when both standard contracts and affected unit tests pass, merge proceeds."""
        fake_diff = """diff --git a/hermes/service.py b/hermes/service.py
--- a/hermes/service.py
+++ b/hermes/service.py
@@ -10,3 +10,4 @@ def process_order():
"""
        self.mock_worktree_mgr.get_diff.return_value = fake_diff
        self.mock_worktree_mgr.merge_worktree.return_value = "Merged successfully into haos-fork"

        executed_commands = []

        def side_effect(task_id, test_command):
            executed_commands.append(test_command)
            return {"success": True, "returncode": 0, "stdout_tail": "All tests passed"}

        mock_run_tests.side_effect = side_effect

        with patch("pathlib.Path.exists", return_value=True):
            result = self.gate.verify_and_merge("task-blast-pass", target_branch="haos-fork")

        self.assertTrue(result["merged"])
        self.assertEqual(result["output"], "Merged successfully into haos-fork")
        self.assertIn("tests/test_service.py", result.get("affected_tests", []))

        # Check that both test runs were invoked
        self.assertEqual(len(executed_commands), 2)
        self.assertTrue(any("test_contracts.py" in cmd for cmd in executed_commands))
        self.assertTrue(any("tests/test_service.py" in cmd for cmd in executed_commands))

        # Worktree merged and cleaned up
        self.mock_worktree_mgr.merge_worktree.assert_called_once_with("task-blast-pass", target_branch="haos-fork")
        self.mock_worktree_mgr.remove_worktree.assert_called_once_with("task-blast-pass")


if __name__ == "__main__":
    unittest.main()
