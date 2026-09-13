"""End-to-end integration tests for Ouroboros Live Simulation (Auto-Evolução End-to-End).

Validates:
1. Full evolution cycle from task history trace to sandbox verification, eval gate, and merge queue approval.
2. Rejection when evaluation score does not meet threshold.
3. Dry run mode execution.
4. Failure handling on insufficient patterns or invalid traces.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hermes.platform.evolution.ouroboros_lifecycle import (
    OuroborosLifecycleManager,
    RunTrace,
    TraceSpan,
)
from hermes.platform.skills.procedural_engine import (
    SkillGenerator,
    SkillLifecyclePipeline,
    SkillRegistry,
    TaskExecutionRecord,
)
from hermes.platform.skills.spec import SkillSpec
from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.capabilities.lsp.unified_intelligence import (
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)
from hermes.platform.workspaces.merge_queue import MergeQueue, MergeStatus


class TestOuroborosLiveSimulation(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.generator = SkillGenerator(min_pattern_frequency=2)
        self.pipeline = SkillLifecyclePipeline(
            registry=self.registry,
            available_capabilities={"terminal", "docker", "k8s"},
            min_eval_score=0.80,
        )

        # Configura SymbolGraph e ImpactAnalyzer
        self.symbol_graph = CodeSymbolGraph()
        self.sym_service = SymbolNode(
            name="deploy",
            kind="function",
            file_path="service.py",
            location=SymbolLocation(file_path="service.py", line=10, character=0),
        )
        self.sym_test = SymbolNode(
            name="test_deploy",
            kind="function",
            file_path="tests/test_deploy.py",
            location=SymbolLocation(file_path="tests/test_deploy.py", line=5, character=0),
        )
        self.symbol_graph.add_symbol(self.sym_service)
        self.symbol_graph.add_symbol(self.sym_test)
        self.symbol_graph.add_call(self.sym_test.id, self.sym_service.id)

        self.impact_analyzer = ImpactAnalyzer(self.symbol_graph)

        # Mock worktree manager
        self.mock_worktree_mgr = MagicMock()
        self.mock_worktree_mgr.worktrees_dir = Path("/tmp/mock-worktree")
        self.mock_worktree_mgr.create_worktree.return_value = Path("/tmp/mock-worktree/task-evo")
        self.mock_worktree_mgr.get_diff.return_value = (
            "diff --git a/service.py b/service.py\n"
            "--- a/service.py\n"
            "+++ b/service.py\n"
            "+def deploy():\n"
            "+    pass\n"
        )
        self.mock_worktree_mgr.remove_worktree.return_value = True

        # AutoMerge Gate
        self.automerge_gate = AutoMergeGate(
            worktree_manager=self.mock_worktree_mgr,
            symbol_graph=self.symbol_graph,
            impact_analyzer=self.impact_analyzer,
        )

        # Mock MergeQueue to avoid executing real git subprocesses in unit integration test
        self.mock_merge_queue = MagicMock(spec=MergeQueue)
        self.mock_merge_candidate = MagicMock()
        self.mock_merge_candidate.status = MergeStatus.MERGED
        self.mock_merge_candidate.rejection_reason = None
        self.mock_merge_queue.enqueue.return_value = self.mock_merge_candidate
        self.mock_merge_queue.process_next.return_value = self.mock_merge_candidate

        # Manager
        self.manager = OuroborosLifecycleManager(
            skill_registry=self.registry,
            skill_generator=self.generator,
            skill_pipeline=self.pipeline,
            automerge_gate=self.automerge_gate,
            impact_analyzer=self.impact_analyzer,
            merge_queue=self.mock_merge_queue,
            worktree_manager=self.mock_worktree_mgr,
            promotion_threshold=0.80,
            min_improvement_pct=0.05,
        )

    def _generate_synthetic_task_history(self) -> list[TaskExecutionRecord]:
        return [
            TaskExecutionRecord(
                task_name="deploy_service",
                action_sequence=["git_pull", "build_docker", "k8s_apply", "health_check"],
                success=True,
                context_keys=["cluster_env", "image_tag"],
                capabilities_used=["terminal", "docker", "k8s"],
                duration_sec=45.0,
                metadata={"token_cost": 0.02},
            ),
            TaskExecutionRecord(
                task_name="deploy_service",
                action_sequence=["git_pull", "build_docker", "k8s_apply", "health_check"],
                success=True,
                context_keys=["cluster_env", "image_tag"],
                capabilities_used=["terminal", "docker", "k8s"],
                duration_sec=40.0,
                metadata={"token_cost": 0.018},
            ),
            TaskExecutionRecord(
                task_name="deploy_service",
                action_sequence=["git_pull", "build_docker", "k8s_apply", "health_check"],
                success=True,
                context_keys=["cluster_env", "image_tag"],
                capabilities_used=["terminal", "docker", "k8s"],
                duration_sec=42.0,
                metadata={"token_cost": 0.019},
            ),
        ]

    def test_full_evolution_cycle_success(self):
        """Tests full cycle: task history -> candidate spec -> isolated worktree -> sandbox -> eval -> merge queue -> merged."""
        history = self._generate_synthetic_task_history()

        result = self.manager.simulate_evolution_cycle(
            task_history=history,
            target_task_name="deploy_service",
            skill_name="auto_deploy_pipeline",
            candidate_eval_score=0.92,
            baseline_score=0.70,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.stage, "completed")
        self.assertTrue(result.promoted)
        self.assertTrue(result.merged)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.proposal.status, "adopted")
        self.assertIsNotNone(result.candidate_skill)
        self.assertEqual(result.candidate_skill.name, "auto_deploy_pipeline")
        self.assertEqual(result.candidate_skill.status, "active")

        # Verifica registro de skill
        active_skill = self.registry.get("auto_deploy_pipeline")
        self.assertIsNotNone(active_skill)
        self.assertEqual(active_skill.status, "active")

        # Verifica worktree calls
        self.mock_worktree_mgr.create_worktree.assert_called()
        self.assertIsNotNone(result.worktree_path)

        # Verifica blast radius e affected tests
        self.assertIsNotNone(result.blast_radius)
        self.assertIn("service.py", result.blast_radius.get("modified_files", []))
        self.assertIn("tests/test_deploy.py", result.affected_tests)

        # Verifica MergeQueue candidate
        self.assertIsNotNone(result.merge_candidate)
        self.assertEqual(result.merge_candidate.status, MergeStatus.MERGED)

    def test_evolution_cycle_rejected_below_threshold(self):
        """Tests rejection when evaluation score does not meet promotion threshold."""
        history = self._generate_synthetic_task_history()

        result = self.manager.simulate_evolution_cycle(
            task_history=history,
            target_task_name="deploy_service",
            skill_name="auto_deploy_pipeline",
            candidate_eval_score=0.65,  # Abaixo do threshold 0.80
            baseline_score=0.60,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, "eval_gate")
        self.assertFalse(result.promoted)
        self.assertFalse(result.merged)
        self.assertEqual(result.eval_score, 0.65)
        self.assertIn("failed threshold", result.error)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.proposal.status, "rejected")

        # Verifica que não foi registrado como ativo no registry
        active_skill = self.registry.get("auto_deploy_pipeline")
        self.assertIsNone(active_skill)

        # Worktree deve ter sido limpo no cleanup
        self.mock_worktree_mgr.remove_worktree.assert_called()

    def test_evolution_cycle_rejected_no_improvement(self):
        """Tests rejection when candidate score does not improve upon baseline."""
        history = self._generate_synthetic_task_history()

        # Score meeting 0.80 threshold, but baseline is 0.85 (delta negative)
        result = self.manager.simulate_evolution_cycle(
            task_history=history,
            target_task_name="deploy_service",
            skill_name="auto_deploy_pipeline",
            candidate_eval_score=0.82,
            baseline_score=0.85,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, "eval_gate")
        self.assertFalse(result.promoted)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.proposal.status, "rejected")

    def test_evolution_cycle_from_run_traces(self):
        """Tests evolution cycle using RunTrace objects instead of TaskExecutionRecord."""
        traces = []
        for i in range(3):
            trace = RunTrace(trace_id=f"tr-{i}", task_id="trace_task", posture="coder")
            s1 = TraceSpan(span_id=f"s1-{i}", name="scan_code", provider="local", model_name="fast")
            s1.finish("ok")
            s2 = TraceSpan(span_id=f"s2-{i}", name="fix_bugs", provider="local", model_name="fast")
            s2.finish("ok")
            trace.add_span(s1)
            trace.add_span(s2)
            traces.append(trace)

        result = self.manager.simulate_evolution_cycle(
            task_history=traces,
            target_task_name="trace_task",
            skill_name="code_fixer_skill",
            candidate_eval_score=0.95,
            baseline_score=0.60,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.stage, "completed")
        self.assertTrue(result.promoted)
        flow = (
            result.candidate_skill.metadata.get("action_pattern")
            or result.candidate_skill.metadata.get("flow")
        )
        self.assertEqual(flow, ["scan_code", "fix_bugs"])

    def test_evolution_cycle_dry_run(self):
        """Tests that dry_run advances evaluation and promotion without merging to repository."""
        history = self._generate_synthetic_task_history()

        result = self.manager.simulate_evolution_cycle(
            task_history=history,
            target_task_name="deploy_service",
            skill_name="dry_run_skill",
            candidate_eval_score=0.90,
            baseline_score=0.70,
            dry_run=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.stage, "dry_run_completed")
        self.assertTrue(result.promoted)
        self.assertFalse(result.merged)
        self.assertIsNone(result.merge_candidate)

    def test_evolution_cycle_no_recurring_pattern_fails(self):
        """Tests cycle failure when history does not meet minimum frequency."""
        history = [
            TaskExecutionRecord(
                task_name="single_task",
                action_sequence=["step_unique"],
                success=True,
            )
        ]
        result = self.manager.simulate_evolution_cycle(
            task_history=history,
            target_task_name="single_task",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.stage, "pattern_detection")
        self.assertIn("No repetitive successful procedural pattern", result.error)


if __name__ == "__main__":
    unittest.main()
