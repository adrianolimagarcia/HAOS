"""Test suite for Phase 3 — Adaptive Intelligence Platform.

Validates:
1. FailurePatternDetector: Identifies recurrent failure clusters across tasks.
2. AdaptiveRoutingOptimizer: Recommends model/provider route improvements based on cost and success rate.
3. AgentTaskAffinity: Bayesian affinity scoring for specialists.
4. ExperimentFramework: A/B candidate evaluation with automated rollback of underperforming changes.
5. AdaptiveIntelligenceCoordinator: Integrated processing with EventStore audit logging.
"""

import unittest
from hermes.platform.evolution.adaptive_intelligence import (
    AdaptiveIntelligenceCoordinator,
    AdaptiveRoutingOptimizer,
    AgentTaskAffinity,
    ExperimentFramework,
    FailureCategory,
    FailureIncident,
    FailurePatternDetector,
)
from hermes.platform.observability.event_store import EventStore


class TestPhase3AdaptiveIntelligence(unittest.TestCase):
    """Verifies all Phase 3 Adaptive Intelligence capabilities."""

    def setUp(self):
        self.event_store = EventStore(db_path=":memory:")
        self.coordinator = AdaptiveIntelligenceCoordinator(event_store=self.event_store)

    def test_failure_pattern_detection(self):
        """FailurePatternDetector detects recurrent failure clusters and suggests remediation."""
        detector = FailurePatternDetector(min_cluster_size=2)

        detector.record_failure(
            FailureIncident(
                task_id="task-01",
                category=FailureCategory.SYNTAX_ERROR,
                error_message="SyntaxError: invalid syntax",
                posture="coder",
                model_id="deepseek-v4-flash",
            )
        )
        # Not enough for a cluster yet
        self.assertEqual(len(detector.detect_patterns()), 0)

        detector.record_failure(
            FailureIncident(
                task_id="task-02",
                category=FailureCategory.SYNTAX_ERROR,
                error_message="IndentationError: unexpected indent",
                posture="coder",
                model_id="deepseek-v4-flash",
            )
        )

        patterns = detector.detect_patterns()
        self.assertEqual(len(patterns), 1)
        pat = patterns[0]
        self.assertEqual(pat.category, FailureCategory.SYNTAX_ERROR)
        self.assertEqual(pat.frequency, 2)
        self.assertIn("AST linter", pat.suggested_remediation)
        self.assertTrue(pat.confidence >= 0.70)

    def test_adaptive_routing_optimizer(self):
        """AdaptiveRoutingOptimizer recommends switching to cheaper route when success rate is equal/better."""
        optimizer = AdaptiveRoutingOptimizer()

        # Route A: Expensive provider ($0.05 per run), 100% success
        for _ in range(3):
            optimizer.record_run(
                posture="coder",
                provider_id="expensive-cloud",
                model_id="claude-3-7-sonnet",
                success=True,
                tokens=1000,
                cost_usd=0.05,
                latency_sec=2.0,
            )

        # Route B: A6API DeepSeek-V4-Flash ($0.005 per run), 100% success
        for _ in range(3):
            optimizer.record_run(
                posture="coder",
                provider_id="a6api",
                model_id="deepseek-v4-flash",
                success=True,
                tokens=1000,
                cost_usd=0.005,
                latency_sec=1.1,
            )

        recs = optimizer.recommend_optimizations(posture="coder")
        self.assertTrue(len(recs) >= 1)
        rec = recs[0]
        self.assertEqual(rec.current_route, "expensive-cloud:claude-3-7-sonnet")
        self.assertEqual(rec.recommended_route, "a6api:deepseek-v4-flash")
        self.assertTrue(rec.projected_savings_pct >= 80.0)

    def test_agent_task_affinity(self):
        """AgentTaskAffinity tracks historical success and computes Laplace-smoothed affinity."""
        affinity = AgentTaskAffinity()

        # Specialist 1 excels in 'software'
        affinity.record_outcome(worker_id="worker-coder-01", domain="software", success=True)
        affinity.record_outcome(worker_id="worker-coder-01", domain="software", success=True)
        score_1 = affinity.get_affinity_score(worker_id="worker-coder-01", domain="software")

        # Specialist 2 fails in 'software'
        affinity.record_outcome(worker_id="worker-coder-02", domain="software", success=False)
        affinity.record_outcome(worker_id="worker-coder-02", domain="software", success=False)
        score_2 = affinity.get_affinity_score(worker_id="worker-coder-02", domain="software")

        self.assertTrue(score_1 > score_2)
        self.assertAlmostEqual(score_1, 3 / 4)  # (2 + 1) / (2 + 2) = 0.75
        self.assertAlmostEqual(score_2, 1 / 4)  # (0 + 1) / (2 + 2) = 0.25

    def test_experiment_framework_adoption_and_rollback(self):
        """ExperimentFramework adopts superior candidates and rolls back underperforming candidates."""
        framework = ExperimentFramework(min_improvement_delta=0.05)
        adopted_state = {"active_version": "v1.0.0"}

        def adopt():
            adopted_state["active_version"] = "v1.1.0"

        def rollback():
            adopted_state["active_version"] = "v1.0.0"

        # Case 1: Candidate is significantly better -> Adopted
        res1 = framework.run_experiment(
            target="prompt-prefix",
            baseline_eval_fn=lambda: 0.80,
            candidate_eval_fn=lambda: 0.88,  # delta = +0.08 >= 0.05
            adoption_fn=adopt,
            rollback_fn=rollback,
        )
        self.assertTrue(res1.adopted)
        self.assertFalse(res1.rolled_back)
        self.assertEqual(adopted_state["active_version"], "v1.1.0")

        # Case 2: Candidate regresses or does not meet threshold -> Automatic Rollback
        res2 = framework.run_experiment(
            target="prompt-prefix",
            baseline_eval_fn=lambda: 0.88,
            candidate_eval_fn=lambda: 0.82,  # Regression
            adoption_fn=adopt,
            rollback_fn=rollback,
        )
        self.assertFalse(res2.adopted)
        self.assertTrue(res2.rolled_back)
        self.assertEqual(adopted_state["active_version"], "v1.0.0")

    def test_adaptive_intelligence_coordinator_end_to_end(self):
        """Coordinator processes task telemetry, records failure patterns, and emits audit events."""
        # Process 2 failure tasks to trigger pattern detection
        self.coordinator.process_task_result(
            task_id="t-1",
            posture="coder",
            domain="software",
            worker_id="w-1",
            provider_id="a6api",
            model_id="deepseek-v4-flash",
            success=False,
            tokens=500,
            cost_usd=0.002,
            latency_sec=1.0,
            error_message="SyntaxError",
            failure_category=FailureCategory.SYNTAX_ERROR,
        )
        self.coordinator.process_task_result(
            task_id="t-2",
            posture="coder",
            domain="software",
            worker_id="w-1",
            provider_id="a6api",
            model_id="deepseek-v4-flash",
            success=False,
            tokens=520,
            cost_usd=0.002,
            latency_sec=1.1,
            error_message="SyntaxError",
            failure_category=FailureCategory.SYNTAX_ERROR,
        )

        events = self.event_store.read_events()
        event_names = [e.name for e in events]
        self.assertIn("intelligence.failure_recorded", event_names)
        self.assertIn("intelligence.pattern_detected", event_names)


if __name__ == "__main__":
    unittest.main()
