import unittest

from hermes.platform.context.evals.utilization import (
    ContextUtilizationEvaluator,
    ContextUtilizationMetrics,
)
from hermes.platform.context.primitives.item import ContextItem, TrustLevel, AuthorityLevel
from hermes.platform.context.primitives.package import ContextPackage
from hermes.platform.evolution.ledger import EvolutionLedger
from hermes.platform.observability.event_store import EventStore


class TestContextUtilization(unittest.TestCase):
    def setUp(self):
        self.evaluator = ContextUtilizationEvaluator(low_utilization_threshold=0.25)

    def test_metrics_evaluation_and_utilization_calc(self):
        # 1 item referenced, 1 item not referenced
        item1 = ContextItem(
            id="item_dec_1",
            item_type="architecture_decision",
            source_uri="decisions://sqlite",
            content="Decision: We use SQLite for EventStore.",
            title="SQLite Decision",
            token_cost=50,
            metadata={"symbol": "EventStore", "key_terms": ["sqlite_store"]},
        )
        item2 = ContextItem(
            id="item_mem_2",
            item_type="memory_note",
            source_uri="obsidian://notes/groceries",
            content="Irrelevant user note about bananas.",
            title="Grocery list",
            token_cost=150,
            metadata={"key_terms": ["bananas", "fruit"]},
        )

        package = ContextPackage(
            id="pkg_test_1",
            task_id="task_abc",
            task_revision=1,
            posture_id="coder",
            budget_limit=1000,
            sections={
                "decisions": [item1],
                "memory": [item2],
            },
        )

        tool_calls = [
            {
                "function": {
                    "name": "query_db",
                    "arguments": '{"target": "EventStore"}',
                }
            }
        ]
        final_response = "I will use SQLite for the database."

        metrics = self.evaluator.evaluate_usage(package, tool_calls, final_response)

        self.assertIsInstance(metrics, ContextUtilizationMetrics)
        self.assertEqual(metrics.total_items_count, 2)
        self.assertEqual(metrics.cited_items_count, 1)
        self.assertEqual(metrics.total_context_tokens, 200)
        self.assertEqual(metrics.utilized_tokens, 50)
        self.assertEqual(metrics.utilization_ratio, 0.25)
        self.assertEqual(metrics.stable_prefix_tokens, 50)  # item1 in 'decisions'
        self.assertEqual(metrics.cache_hit_efficiency, 0.25)
        self.assertEqual(metrics.cited_item_ids, ["item_dec_1"])
        self.assertEqual(len(metrics.recommendations), 0)

    def test_low_utilization_recommendations(self):
        item1 = ContextItem(
            id="item_used",
            item_type="code_snippet",
            source_uri="file://src/main.py",
            content="Target file is src/main.py",
            title="Main File",
            token_cost=20,
        )
        item2 = ContextItem(
            id="item_unused_1",
            item_type="code_snippet",
            source_uri="file://src/unrelated.py",
            content="Huge code dump of irrelevant subsystem...",
            title="Unrelated",
            token_cost=80,
        )
        item3 = ContextItem(
            id="item_unused_2",
            item_type="code_snippet",
            source_uri="file://src/unrelated2.py",
            content="Another huge code dump...",
            title="Unrelated 2",
            token_cost=100,
        )

        package = ContextPackage(
            id="pkg_test_2",
            task_id="task_xyz",
            task_revision=1,
            posture_id="coder",
            budget_limit=2000,
            sections={
                "code": [item1, item2, item3],
            },
        )

        tool_calls = []
        final_response = "Editing src/main.py now."

        metrics = self.evaluator.evaluate_usage(package, tool_calls, final_response)
        # 20 / 200 = 0.10 < 0.25
        self.assertLess(metrics.utilization_ratio, 0.25)
        self.assertTrue(len(metrics.recommendations) > 0)
        self.assertTrue(any("coder" in r for r in metrics.recommendations))
        self.assertTrue(any("code" in r for r in metrics.recommendations))

    def test_record_to_ledger(self):
        event_store = EventStore(":memory:")
        ledger = EvolutionLedger(event_store)

        metrics = ContextUtilizationMetrics(
            total_context_tokens=1000,
            utilized_tokens=100,
            utilization_ratio=0.10,
            cited_items_count=1,
            total_items_count=10,
            stable_prefix_tokens=200,
            cache_hit_efficiency=0.20,
            recommendations=["coder posture receiving too many irrelevant memory items"],
            cited_item_ids=["it_1"],
        )

        proposal = self.evaluator.record_to_ledger(
            metrics=metrics,
            task_id="task_001",
            posture_id="coder",
            ledger=ledger,
        )

        self.assertIn("proposal_id", proposal)
        self.assertEqual(proposal["task_id"], "task_001")
        self.assertEqual(proposal["posture_id"], "coder")
        self.assertEqual(proposal["action"], "reduce_budget")

        # Verifica se a proposta existe no ledger
        pending = ledger.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_id"], proposal["proposal_id"])
        self.assertEqual(pending[0]["type"], "context_utilization_optimization")
        self.assertEqual(pending[0]["task_id"], "task_001")


if __name__ == "__main__":
    unittest.main()
