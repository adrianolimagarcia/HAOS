"""Test Ouroboros Context Utilization proposals."""

import unittest
from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event


class TestEvolutionContextOptimization(unittest.TestCase):
    def test_analyzer_proposes_context_budget_tuning_on_low_utilization(self):
        store = EventStore(":memory:")
        store.append(
            Event(
                name="context.utilization.eval",
                payload={
                    "task_id": "task-auth-99",
                    "utilization_ratio": 0.22,
                    "allocated_tokens": 12000,
                    "metric": "context_utilization",
                },
                trace_id="tr-cxt-1",
            )
        )

        analyzer = OuroborosAnalyzer()
        proposals = analyzer.analyze_execution_history(event_store=store)

        targets = [p["target"] for p in proposals]
        self.assertIn("context:budget", targets)
        p = next(p for p in proposals if p["target"] == "context:budget")
        self.assertEqual(p["proposed_profile"], "aggressive_progressive_disclosure")
        self.assertIn("22.00%", p["rationale"])
        self.assertEqual(p["evidence"]["task_id"], "task-auth-99")


if __name__ == "__main__":
    unittest.main()

