import unittest
from hermes.platform.execution.backpressure import ConcurrencyGuard, BackpressureController
from hermes.platform.tasks.spec import TaskSpec, AcceptanceCriterion, ReviewStage
from hermes.platform.tasks.review_pipeline import (
    AntiAnchoringContextBuilder,
    ReviewPipeline,
    ReviewVerdict,
)
from hermes.platform.tasks.acceptance import AcceptanceEngine, CompositeAcceptanceEngine


class TestBackpressureAndConcurrencyGuard(unittest.TestCase):
    def test_global_limit(self):
        guard = ConcurrencyGuard(max_global_concurrency=2)
        self.assertTrue(guard.can_acquire())
        self.assertTrue(guard.acquire("t1"))
        self.assertTrue(guard.acquire("t2"))
        
        # Idempotente para mesma task
        self.assertTrue(guard.acquire("t1"))

        # Bloqueado por limite global
        self.assertFalse(guard.can_acquire())
        self.assertFalse(guard.acquire("t3"))

        # Release libera
        guard.release("t1")
        self.assertTrue(guard.can_acquire())
        self.assertTrue(guard.acquire("t3"))

    def test_provider_limits(self):
        # A6API: 4, OpenAI: 4, fallback: 2
        guard = ConcurrencyGuard(
            max_global_concurrency=8,
            provider_limits={"A6API": 2, "OpenAI": 1, "fallback": 1},
        )
        self.assertTrue(guard.acquire("t1", provider_id="OpenAI"))
        self.assertFalse(guard.can_acquire(provider_id="OpenAI"))
        self.assertFalse(guard.acquire("t2", provider_id="OpenAI"))

        # Outro provider ainda pode
        self.assertTrue(guard.can_acquire(provider_id="A6API"))
        self.assertTrue(guard.acquire("t3", provider_id="a6api")) # case-insensitive
        self.assertTrue(guard.acquire("t4", provider_id="A6API"))
        self.assertFalse(guard.acquire("t5", provider_id="A6API"))

        # Provider desconhecido cai no fallback (1)
        self.assertTrue(guard.acquire("t6", provider_id="unknown_p"))
        self.assertFalse(guard.acquire("t7", provider_id="unknown_p"))

        # Release
        guard.release("t1")
        self.assertTrue(guard.acquire("t2", provider_id="OpenAI"))

    def test_model_limits(self):
        guard = ConcurrencyGuard(
            max_global_concurrency=8,
            model_limits={"deepseek-v4": 2},
        )
        self.assertTrue(guard.acquire("t1", model_id="deepseek-v4"))
        self.assertTrue(guard.acquire("t2", model_id="deepseek-v4"))
        self.assertFalse(guard.can_acquire(model_id="deepseek-v4"))
        self.assertFalse(guard.acquire("t3", model_id="deepseek-v4"))

        # Outro modelo sem limite explícito é admitido
        self.assertTrue(guard.acquire("t4", model_id="gpt-4o"))

        guard.release("t1")
        self.assertTrue(guard.acquire("t3", model_id="deepseek-v4"))

    def test_stats(self):
        guard = BackpressureController(max_global_concurrency=4)
        guard.acquire("t1", provider_id="a6api", model_id="m1")
        s = guard.stats()
        self.assertEqual(s["global"]["active"], 1)
        self.assertEqual(s["global"]["max"], 4)
        self.assertEqual(s["global"]["available"], 3)
        self.assertEqual(s["providers"]["a6api"]["active"], 1)
        self.assertEqual(s["models"]["m1"]["active"], 1)
        self.assertIn("t1", s["active_task_ids"])


class TestAntiAnchoringAndReviewPipeline(unittest.TestCase):
    def test_anti_anchoring_context_builder(self):
        spec = TaskSpec(
            id="TASK-1",
            title="Fix bug",
            goal="Resolve issue in core",
            description="Detailed desc",
            risk_level="high",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-1", description="desc", type="structural")
            ],
            expected_artifacts=["patch.diff"],
        )

        artifacts = [
            {"path": "file.py", "diff": "+print(1)"},
            {"transcript": "Worker: thinking out loud...", "thought": "secret reasoning"},
        ]
        evidence = {
            "tests": {"passed": True, "failed": 0},
            "chain_of_thought": "Should not leak",
            "intermediate_steps": ["step 1", "step 2"],
            "lsp": {"new_errors": 0},
        }
        residual_risk = ["Edge cases not fully tested"]
        summary = "Completed bug fix"
        decisions = ["Used approach X"]

        ctx = AntiAnchoringContextBuilder.build_reviewer_context(
            spec=spec,
            artifacts=artifacts,
            evidence=evidence,
            residual_risk=residual_risk,
            summary=summary,
            decisions=decisions,
        )

        # Invariants: no transcripts or CoT in context
        self.assertNotIn("transcript", ctx["artifacts"][1])
        self.assertNotIn("thought", ctx["artifacts"][1])
        self.assertNotIn("chain_of_thought", ctx["evidence"])
        self.assertNotIn("intermediate_steps", ctx["evidence"])
        self.assertEqual(ctx["evidence"]["tests"]["passed"], True)
        self.assertEqual(ctx["task_id"], "TASK-1")
        self.assertEqual(ctx["summary"], "Completed bug fix")
        self.assertEqual(ctx["decisions"], ["Used approach X"])
        self.assertEqual(ctx["residual_risk"], ["Edge cases not fully tested"])

    def test_review_pipeline_execution_and_when_conditions(self):
        spec = TaskSpec(
            id="TASK-2",
            title="Feature X",
            goal="Ship feature",
            risk_level="medium",
            review_stages=[
                ReviewStage(id="stage-always", when="always"),
                ReviewStage(id="stage-high", when="risk>=high"),
                ReviewStage(id="stage-critical", when="risk==critical"),
            ],
        )

        pipeline = ReviewPipeline()
        verdicts = pipeline.run(
            spec=spec,
            artifacts=[{"id": "art-1"}],
            evidence={"tests": {"passed": True, "failed": 0}, "lsp": {"new_errors": 0}},
            residual_risk=[],
            summary="Feature done",
        )

        # Risk medium: apenas stage-always roda
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].stage_id, "stage-always")
        self.assertTrue(verdicts[0].approved)
        self.assertGreaterEqual(verdicts[0].score, 0.8)

        # Agora com risco high: stage-always e stage-high rodam
        spec.risk_level = "high"
        verdicts_high = pipeline.run(
            spec=spec,
            artifacts=[{"id": "art-1"}],
            evidence={"tests": {"passed": True, "failed": 0}, "lsp": {"new_errors": 0}},
            residual_risk=[],
            summary="Feature done",
        )
        self.assertEqual(len(verdicts_high), 2)
        self.assertEqual([v.stage_id for v in verdicts_high], ["stage-always", "stage-high"])

    def test_review_pipeline_rejection_on_failure(self):
        spec = TaskSpec(
            id="TASK-3",
            title="Feature Y",
            goal="Ship feature",
            risk_level="high",
            review_stages=[ReviewStage(id="stage-1", when="always")],
        )
        pipeline = ReviewPipeline()
        verdicts = pipeline.run(
            spec=spec,
            artifacts=[],
            evidence={"tests": {"passed": False, "failed": 2}, "lsp": {"new_errors": 3}},
            residual_risk=["risco 1", "risco 2"],
            summary="Feature with issues",
        )
        self.assertEqual(len(verdicts), 1)
        self.assertFalse(verdicts[0].approved)
        self.assertIn("Test suite reported 2 failed tests", verdicts[0].rationale)
        self.assertIn("LSP reported 3 new errors", verdicts[0].rationale)


class TestCompositeAcceptanceEngine(unittest.TestCase):
    def test_composite_all_of_success(self):
        spec = TaskSpec(
            id="T-COMP-1",
            title="Composite test",
            goal="Pass all",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-1", description="Structural", type="structural"),
                AcceptanceCriterion(id="AC-2", description="LSP clean", type="lsp"),
                AcceptanceCriterion(id="AC-3", description="Review Score", type="review_score"),
            ],
        )
        ctx = {
            "structural_ok": True,
            "lsp_diagnostics": {"new_errors": 0},
            "reviewer_score": 0.9,
        }
        res = CompositeAcceptanceEngine.evaluate_composite(spec, ctx, composition_mode="all_of")
        self.assertTrue(res["passed"])
        self.assertEqual(res["failed_count"], 0)
        self.assertEqual(res["passed_count"], 3)

    def test_composite_all_of_failure(self):
        spec = TaskSpec(
            id="T-COMP-2",
            title="Composite test fail",
            goal="Fail on LSP",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-1", description="Structural", type="structural"),
                AcceptanceCriterion(id="AC-2", description="LSP clean", type="lsp"),
            ],
        )
        ctx = {
            "structural_ok": True,
            "lsp_diagnostics": {"new_errors": 2},
        }
        res = CompositeAcceptanceEngine.evaluate_composite(spec, ctx, composition_mode="all_of")
        self.assertFalse(res["passed"])
        self.assertEqual(res["failed_count"], 1)

    def test_composite_any_of(self):
        criteria = [
            AcceptanceCriterion(id="AC-1", description="Structural", type="structural"),
            AcceptanceCriterion(id="AC-2", description="LSP clean", type="lsp"),
        ]
        # Structural fails, LSP passes
        ctx = {
            "structural_ok": False,
            "lsp_diagnostics": {"new_errors": 0},
        }
        res = CompositeAcceptanceEngine.evaluate_composite(criteria, ctx, composition_mode="any_of")
        self.assertTrue(res["passed"])
        self.assertEqual(res["passed_count"], 1)
        self.assertEqual(res["failed_count"], 1)

    def test_composite_with_review_verdicts(self):
        spec = TaskSpec(
            id="T-COMP-3",
            title="Composite with review",
            goal="Review evaluation",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-REV", description="Review", type="review_score"),
            ],
        )
        verdicts = [
            ReviewVerdict(stage_id="s1", posture="rev", approved=True, rationale="ok", score=0.85),
            ReviewVerdict(stage_id="s2", posture="rev", approved=True, rationale="good", score=0.95),
        ]
        ctx = {}
        res = CompositeAcceptanceEngine.evaluate_composite(
            spec, ctx, composition_mode="all_of", review_verdicts=verdicts
        )
        self.assertTrue(res["passed"])
        self.assertAlmostEqual(res["results"][0]["score"], 0.9)

    def test_composite_rejects_when_verdict_approved_is_false_even_with_high_score(self):
        # F7: Se score >= 0.8 mas approved=False, deve reprovar!
        spec = TaskSpec(
            id="T-COMP-4",
            title="Composite with rejected review",
            goal="Review evaluation",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-REV", description="Review", type="review_score"),
            ],
        )
        verdicts = [
            ReviewVerdict(stage_id="s1", posture="rev", approved=False, rationale="security veto", score=0.95),
        ]
        ctx = {}
        res = CompositeAcceptanceEngine.evaluate_composite(
            spec, ctx, composition_mode="all_of", review_verdicts=verdicts
        )
        self.assertFalse(res["passed"])
        self.assertFalse(res["results"][0]["passed"])
        self.assertFalse(res["results"][0]["review_approved"])

    def test_schema_acceptance_criterion_and_fail_closed_on_missing_evidence(self):
        # F4 e F5: Schema validation real e fail-closed
        from hermes.platform.execution.contracts import FieldContract
        spec = TaskSpec(
            id="T-SCHEMA-1",
            title="Schema task",
            goal="Ensure valid contract",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-SCHEMA", description="Validate output", type="schema"),
                AcceptanceCriterion(id="AC-STRUCT", description="Require structure", type="structural"),
            ],
        )

        # 1. Sem evidência -> fail-closed (ambos falham!)
        empty_ctx = {}
        res_empty = CompositeAcceptanceEngine.evaluate_composite(spec, empty_ctx)
        self.assertFalse(res_empty["passed"])
        self.assertEqual(res_empty["failed_count"], 2)

        # 2. Com evidência correta -> passa
        valid_ctx = {
            "structural_ok": True,
            "output_data": {"user_id": 123, "email": "test@example.com"},
            "field_contracts": [
                FieldContract(name="user_id", required=True, type="int"),
                FieldContract(name="email", required=True, type="str"),
            ],
        }
        res_valid = CompositeAcceptanceEngine.evaluate_composite(spec, valid_ctx)
        self.assertTrue(res_valid["passed"])
        self.assertEqual(res_valid["passed_count"], 2)

        # 3. Com violação de schema -> reprova
        invalid_ctx = {
            "structural_ok": True,
            "output_data": {"user_id": "not_an_int"},
            "field_contracts": [
                FieldContract(name="user_id", required=True, type="int"),
                FieldContract(name="email", required=True, type="str"),
            ],
        }
        res_invalid = CompositeAcceptanceEngine.evaluate_composite(spec, invalid_ctx)
        self.assertFalse(res_invalid["passed"])
        self.assertEqual(res_invalid["failed_count"], 1)

    def test_review_pipeline_has_risks_and_never_conditions(self):
        # F6 e F8: when="has_risks" e when="never"
        spec = TaskSpec(
            id="T-COND-1",
            title="Condition test",
            goal="Verify when conditions",
            risk_level="low",
            review_stages=[
                ReviewStage(id="stage-never", when="never"),
                ReviewStage(id="stage-risks", when="has_risks"),
            ],
        )
        pipeline = ReviewPipeline()

        # Sem riscos residuais -> nenhum estágio roda
        verdicts_no_risk = pipeline.run(
            spec=spec,
            artifacts=[],
            evidence={},
            residual_risk=[],
            summary="Clean run",
        )
        self.assertEqual(len(verdicts_no_risk), 0)

        # Com riscos residuais -> stage-risks roda, stage-never NUNCA roda
        verdicts_with_risk = pipeline.run(
            spec=spec,
            artifacts=[],
            evidence={},
            residual_risk=["Potential memory leak"],
            summary="Run with risk",
        )
        self.assertEqual(len(verdicts_with_risk), 1)
        self.assertEqual(verdicts_with_risk[0].stage_id, "stage-risks")


if __name__ == "__main__":
    unittest.main()
