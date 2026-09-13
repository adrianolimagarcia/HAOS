"""End-to-End Integration Tests for Autonomous Multi-Agent Mission Demo.

Validates the full 8-stage autonomous lifecycle:
1. Text agent receives input pointing to diagram image/spec.
2. MultimodalDispatchPattern.intercept_and_perceive() spawns vision worker,
   extracting typed PerceptionArtifact with OCR and observations.
3. Architect posture generates an ADR and stores it in FederatedMemoryCoordinator
   (syncing Obsidian + GraphRAG + DecisionStore).
4. Coder posture provisions worktree via GitWorktreeManager and implements code changes.
5. LSP ImpactAnalyzer analyzes git diff, extracts blast radius, and identifies test suites.
6. Reviewer posture runs verification gates.
7. AutoMergeGate & MergeQueue merge the worktree cleanly.
8. OuroborosLifecycleManager.simulate_evolution_cycle() captures trace and synthesizes
   candidate SkillSpec.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hermes.platform.capabilities.lsp.unified_intelligence import (
    BlastRadius,
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)
from hermes.platform.capabilities.modality.unified_fabric import (
    MultimodalDispatchPattern,
    PerceptionArtifact,
    PerceptionType,
)
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.evolution.ouroboros_lifecycle import (
    OuroborosLifecycleManager,
    RunTrace,
    TraceSpan,
)
from hermes.platform.execution.mission_demo import (
    ADRArtifact,
    MergeArtifact,
    MissionDemoResult,
    MissionDemoRunner,
    MissionInput,
    ReviewVerdict,
    WorktreeArtifact,
)
from hermes.platform.posture.resolver import PostureResolver
from hermes.platform.skills.procedural_engine import (
    SkillGenerator,
    SkillLifecyclePipeline,
    SkillRegistry,
)
from hermes.platform.skills.spec import SkillSpec
from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.workspaces.git_worktree import GitWorktreeManager
from hermes.platform.workspaces.merge_queue import MergeQueue, MergeStatus


class TestMissionDemoE2E(unittest.TestCase):
    """Integration test suite exercising the 8-stage autonomous multi-agent mission."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="haos_mission_demo_"))
        self.repo_dir = self.test_dir / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.vault_dir = self.test_dir / "vault"
        self.vault_dir.mkdir(parents=True, exist_ok=True)

        # Initialize real git repository
        self._run_git(["init", "-b", "main"], cwd=self.repo_dir)
        self._run_git(["config", "user.name", "HAOS Tester"], cwd=self.repo_dir)
        self._run_git(["config", "user.email", "tester@haos.local"], cwd=self.repo_dir)
        self._run_git(["commit", "--allow-empty", "-m", "chore: initial main commit"], cwd=self.repo_dir)
        self._run_git(["branch", "haos-fork"], cwd=self.repo_dir)

        # Create a sample diagram file
        self.diagram_dir = self.test_dir / "diagrams"
        self.diagram_dir.mkdir(parents=True, exist_ok=True)
        self.diagram_file = self.diagram_dir / "event_pipeline.png"
        self.diagram_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake_diagram_bytes")

        # Initialize MissionDemoRunner
        self.runner = MissionDemoRunner(
            repo_path=self.repo_dir,
            vault_path=self.vault_dir,
            base_branch="main",
            target_branch="main",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _run_git(self, args: list[str], cwd: Path) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def test_full_e2e_mission_demo_lifecycle(self) -> None:
        """Execute full 8-stage autonomous mission and verify all artifacts and transitions."""
        mission_input = MissionInput(
            task_id="event-buffer-042",
            instruction="Implement high-throughput buffered event stream per system diagram",
            diagram_uri=str(self.diagram_file),
            mime_type="image/png",
            base_branch="main",
            target_branch="main",
            metadata={"priority": "high", "environment": "integration-test"},
        )

        result = self.runner.run_mission(mission_input)

        # 1. Overall Mission Success
        self.assertTrue(result.success, f"Mission demo failed: {result.error}")
        self.assertIsNone(result.error)
        self.assertEqual(result.task_id, "event-buffer-042")

        # 2. Stage 1 & 2: Text Agent Input & Multimodal Perception Interception
        self.assertIsNotNone(result.perception)
        self.assertIsInstance(result.perception, PerceptionArtifact)
        self.assertEqual(result.perception.type, PerceptionType.IMAGE.value)
        self.assertGreater(result.perception.confidence, 0.90)
        self.assertIsNotNone(result.perception.ocr_text)
        self.assertIn("OCR_EXTRACTED", result.perception.ocr_text)
        self.assertGreater(len(result.perception.structured_observations), 1)

        # 3. Stage 3: Architect Posture ADR & Federated Memory Sync
        self.assertIsNotNone(result.adr)
        self.assertIsInstance(result.adr, ADRArtifact)
        self.assertTrue(result.adr.adr_id.startswith("ADR-"))
        self.assertTrue(result.adr.stored_in_decisions)
        # Verify sync in DecisionStore
        self.assertIn(
            result.adr.fact_record_id,
            self.runner.memory_coordinator.decisions._decisions,
        )
        decision = self.runner.memory_coordinator.decisions._decisions[result.adr.fact_record_id]
        self.assertEqual(decision.status, "accepted")
        self.assertIn("ADR", decision.content)
        # Verify sync in Obsidian Vault file
        obsidian_file = self.vault_dir / result.adr.obsidian_path
        self.assertTrue(obsidian_file.exists(), f"Obsidian note not found at {obsidian_file}")
        obsidian_text = obsidian_file.read_text(encoding="utf-8")
        self.assertIn("ADR", obsidian_text)
        self.assertIn("EventBuffer", obsidian_text)

        # 4. Stage 4: Coder Posture Worktree Provisioning & Implementation
        self.assertIsNotNone(result.worktree)
        self.assertIsInstance(result.worktree, WorktreeArtifact)
        self.assertEqual(result.worktree.branch_name, "haos/task-event-buffer-042")
        self.assertIn("service.py", result.worktree.files_modified)
        self.assertIn("test_service.py", result.worktree.files_modified)
        self.assertIsNotNone(result.worktree.commit_sha)
        self.assertIn("EventBuffer", result.worktree.diff_text)

        # 5. Stage 5: LSP ImpactAnalyzer Diff Analysis & Blast Radius
        self.assertIsNotNone(result.blast_radius)
        self.assertIn("service.py", result.blast_radius.affected_files)
        self.assertIn("test_service.py", result.blast_radius.affected_test_suites)
        self.assertIn(result.blast_radius.severity, ("low", "medium"))

        # 6. Stage 6: Reviewer Posture Verification Gates
        self.assertIsNotNone(result.review)
        self.assertIsInstance(result.review, ReviewVerdict)
        self.assertTrue(result.review.approved)
        self.assertEqual(result.review.reviewer_posture, "reviewer")
        self.assertTrue(result.review.gates_evaluated["test_suite_execution"])
        self.assertTrue(result.review.gates_evaluated["blast_radius_safe"])
        self.assertTrue(result.review.gates_evaluated["adr_compliance"])
        self.assertTrue(result.review.test_results.get("success"))

        # 7. Stage 7: AutoMergeGate & MergeQueue Clean Merge
        self.assertIsNotNone(result.merge)
        self.assertIsInstance(result.merge, MergeArtifact)
        self.assertTrue(result.merge.merged)
        self.assertEqual(result.merge.status, MergeStatus.MERGED.value)
        self.assertTrue(result.merge.rebase_success)
        # Verify git log on target branch contains the merged commit
        log_output = self._run_git(["log", "--oneline", "main"], cwd=self.repo_dir)
        self.assertIn("feat(event-buffer-042)", log_output)
        # Verify ephemeral worktree directory was cleaned up
        worktree_target = self.repo_dir / ".worktrees" / "task-event-buffer-042"
        self.assertFalse(worktree_target.exists())

        # 8. Stage 8: Ouroboros Lifecycle Evolution Cycle & Skill Synthesis
        self.assertIsNotNone(result.evolution)
        self.assertTrue(result.evolution.success)
        self.assertTrue(result.evolution.promoted)
        self.assertIsNotNone(result.candidate_skill)
        self.assertIsInstance(result.candidate_skill, SkillSpec)
        # Verify candidate skill has actions matching mission steps
        self.assertGreater(len(result.candidate_skill.name), 0)
        self.assertGreater(len(result.candidate_skill.description), 0)
        self.assertIn("action_pattern", result.candidate_skill.metadata)
        # Verify skill registered in Ouroboros SkillRegistry
        registered_skill = self.runner.ouroboros_mgr.skill_registry.get(
            result.candidate_skill.name,
            result.candidate_skill.version,
        )
        self.assertIsNotNone(registered_skill)
        self.assertTrue(result.candidate_skill.name.startswith("auto-skill-"))

        # 9. Observability & Trace Verification
        self.assertIsNotNone(result.trace)
        self.assertIsInstance(result.trace, RunTrace)
        span_names = [s.name for s in result.trace.spans]
        expected_spans = [
            "step_1_text_agent_input",
            "step_2_multimodal_perception",
            "step_3_architect_adr",
            "step_4_coder_worktree",
            "step_5_lsp_impact_analysis",
            "step_6_reviewer_verification",
            "step_7_automerge_queue",
            "step_8_ouroboros_evolution",
        ]
        for exp in expected_spans:
            self.assertIn(exp, span_names)
        self.assertTrue(all(s.status == "ok" for s in result.trace.spans))

        # 10. Audit Trail Serialization
        res_dict = result.to_dict()
        serialized = json.dumps(res_dict)
        self.assertIn("mission_id", serialized)
        self.assertIn("event-buffer-042", serialized)
        self.assertIn("OCR_EXTRACTED", serialized)

    def test_multimodal_vision_worker_interception(self) -> None:
        """Step 2: Verify typed perception artifact with OCR text and observations."""
        trace = RunTrace(trace_id="t1", task_id="t-vis", posture="agent")
        mission_input = MissionInput(
            task_id="t-vis",
            instruction="Examine diagram spec",
            diagram_uri=str(self.diagram_file),
        )
        artifact = self.runner.step_2_intercept_and_perceive(mission_input, trace)

        self.assertEqual(artifact.type, PerceptionType.IMAGE.value)
        self.assertIn("OCR_EXTRACTED", artifact.ocr_text)
        self.assertTrue(any("EventBus" in obs for obs in artifact.structured_observations))
        self.assertEqual(trace.spans[-1].name, "step_2_multimodal_perception")
        self.assertEqual(trace.spans[-1].status, "ok")

    def test_architect_posture_and_federated_sync(self) -> None:
        """Step 3: Verify ADR generation and synchronous multi-store federation."""
        trace = RunTrace(trace_id="t2", task_id="t-arch", posture="architect")
        mission_input = MissionInput(
            task_id="t-arch",
            instruction="Generate ADR",
            diagram_uri=str(self.diagram_file),
        )
        perception = self.runner.step_2_intercept_and_perceive(mission_input, trace)
        adr = self.runner.step_3_generate_and_store_adr(mission_input, perception, trace)

        self.assertIn("ADR-T-ARCH", adr.adr_id)
        self.assertTrue(adr.stored_in_decisions)

        # Check Obsidian file written
        obs_file = self.vault_dir / adr.obsidian_path
        self.assertTrue(obs_file.exists())
        self.assertIn("ADR", obs_file.read_text(encoding="utf-8"))

        # Check DecisionStore
        self.assertIn(adr.fact_record_id, self.runner.memory_coordinator.decisions._decisions)

    def test_coder_worktree_and_lsp_impact_analysis(self) -> None:
        """Steps 4 & 5: Verify worktree provisioning, code commit, and blast radius calculation."""
        trace = RunTrace(trace_id="t3", task_id="t-code", posture="coder")
        mission_input = MissionInput(
            task_id="t-code",
            instruction="Implement feature",
            diagram_uri=str(self.diagram_file),
        )
        adr = ADRArtifact(
            adr_id="ADR-T-CODE",
            title="ADR Title",
            content="Decision content",
            fact_record_id="fact-1",
        )
        wt = self.runner.step_4_provision_and_implement(mission_input, adr, trace)

        self.assertTrue(wt.worktree_path.exists())
        self.assertIsNotNone(wt.commit_sha)
        self.assertTrue((wt.worktree_path / "service.py").exists())
        self.assertTrue((wt.worktree_path / "test_service.py").exists())

        blast = self.runner.step_5_analyze_impact(mission_input, wt, trace)
        self.assertIn("service.py", blast.affected_files)
        self.assertIn("test_service.py", blast.affected_test_suites)

        # Clean up worktree
        self.runner.worktree_manager.remove_worktree(mission_input.task_id, force=True)

    def test_reviewer_rejection_on_failing_gate(self) -> None:
        """Step 6: Reviewer gate blocks merge when unit tests fail."""
        trace = RunTrace(trace_id="t4", task_id="t-fail", posture="reviewer")
        mission_input = MissionInput(
            task_id="t-fail",
            instruction="Implement failing code",
            diagram_uri=str(self.diagram_file),
        )
        adr = ADRArtifact(
            adr_id="ADR-FAIL",
            title="ADR Title",
            content="Decision content",
            fact_record_id="fact-fail",
        )

        def bad_code_writer(wt_path: Path) -> list[str]:
            svc = wt_path / "service.py"
            svc.write_text("def broken(): return False\n", encoding="utf-8")
            tst = wt_path / "test_service.py"
            tst.write_text(
                "import unittest\nclass Test(unittest.TestCase):\n"
                "    def test_broken(self): self.fail('Deliberate fail')\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            return ["service.py", "test_service.py"]

        wt = self.runner.step_4_provision_and_implement(
            mission_input=mission_input,
            adr=adr,
            trace=trace,
            code_writer_callback=bad_code_writer,
        )
        blast = self.runner.step_5_analyze_impact(mission_input, wt, trace)
        review = self.runner.step_6_review_and_verify(mission_input, wt, blast, adr, trace)

        self.assertFalse(review.approved)
        self.assertFalse(review.gates_evaluated["test_suite_execution"])

        # Step 7 should block merge
        merge_res = self.runner.step_7_automerge_and_queue(mission_input, wt, blast, review, trace)
        self.assertFalse(merge_res.merged)
        self.assertEqual(merge_res.status, MergeStatus.REJECTED.value)

        self.runner.worktree_manager.remove_worktree(mission_input.task_id, force=True)

    def test_ouroboros_trace_skill_synthesis(self) -> None:
        """Step 8: Verify Ouroboros synthesis of candidate SkillSpec from successful trace."""
        trace = RunTrace(trace_id="t5", task_id="t-skill", posture="pipeline")
        for stage in [
            "step_1_text_agent_input",
            "step_2_multimodal_perception",
            "step_3_architect_adr",
            "step_4_coder_worktree",
            "step_5_lsp_impact_analysis",
            "step_6_reviewer_verification",
            "step_7_automerge_queue",
        ]:
            span = TraceSpan(span_id=f"s-{stage}", name=stage)
            span.finish(status="ok")
            trace.add_span(span)

        mission_input = MissionInput(
            task_id="t-skill",
            instruction="Evolve skill",
            diagram_uri=str(self.diagram_file),
        )

        evo_res, candidate_skill = self.runner.step_8_evolve_and_synthesize_skill(mission_input, trace)

        self.assertTrue(evo_res.success)
        self.assertIsNotNone(candidate_skill)
        self.assertIsInstance(candidate_skill, SkillSpec)
        self.assertTrue(evo_res.promoted)
        self.assertTrue(candidate_skill.name.startswith("auto-skill-"))


if __name__ == "__main__":
    unittest.main()
