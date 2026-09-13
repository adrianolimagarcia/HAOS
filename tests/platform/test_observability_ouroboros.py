"""Tests for Observability Fabric and Ouroboros Lifecycle."""

import unittest
from hermes.platform.evolution.ouroboros_lifecycle import (
    EvolutionProposal,
    OuroborosLifecycleManager,
    RunTrace,
    TraceSpan,
)


class TestObservabilityAndOuroboros(unittest.TestCase):
    def test_run_trace_and_spans_metrics(self):
        trace = RunTrace(trace_id="tr-001", task_id="task-42", posture="coder")
        span1 = TraceSpan(
            span_id="sp-1",
            name="llm_call",
            model_name="claude-3-7-sonnet",
            provider="anthropic",
            tokens_input=1200,
            tokens_output=350,
            cost_usd=0.008,
        )
        span2 = TraceSpan(
            span_id="sp-2",
            name="tool_call_pytest",
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
        )
        trace.add_span(span1)
        trace.add_span(span2)

        self.assertEqual(trace.total_cost(), 0.008)
        tokens = trace.total_tokens()
        self.assertEqual(tokens["input"], 1200)
        self.assertEqual(tokens["output"], 350)
        self.assertEqual(tokens["total"], 1550)

    def test_ouroboros_proposal_lifecycle_gates(self):
        mgr = OuroborosLifecycleManager()
        prop = mgr.submit_proposal(
            target="context:budget",
            changes={"elide_threshold": 1200},
            rationale="Reduces context waste by 25%",
        )
        self.assertEqual(prop.status, "proposed")

        # Avança para sandbox
        self.assertTrue(mgr.sandbox_proposal(prop.proposal_id))
        self.assertEqual(prop.status, "sandboxed")

        # Avaliação com ganho insuficiente (< 5%) -> Rejeitada
        rejection = mgr.evaluate_proposal(
            prop.proposal_id,
            baseline_score=0.80,
            candidate_score=0.81,  # ganho de apenas 1.25%
        )
        self.assertFalse(rejection)
        self.assertEqual(prop.status, "rejected")

        # Nova proposta com ganho superior (> 5%) -> Adotada
        prop2 = mgr.submit_proposal(
            target="model:routing",
            changes={"primary": "deepseek-v3"},
            rationale="High pass rate with lower cost",
        )
        mgr.sandbox_proposal(prop2.proposal_id)
        adopted = mgr.evaluate_proposal(
            prop2.proposal_id,
            baseline_score=0.75,
            candidate_score=0.85,  # ganho de 13.3%
        )
        self.assertTrue(adopted)
        self.assertEqual(prop2.status, "adopted")


if __name__ == "__main__":
    unittest.main()
