"""Test suite for Golden Tasks Benchmark Suite (Step 5.4 / ADR-002 / ADR-010).

Validates:
1. All 10 golden tasks (G001 to G010) are formally registered with categories.
2. Token budgets and deterministic assertions are well-formed.
3. GoldenTasksRunner executes suite and aggregates scores cleanly.
"""

import unittest
from hermes.platform.evals.golden_tasks import (
    GOLDEN_TASKS,
    GoldenTaskCategory,
    GoldenTasksRunner,
)


class TestGoldenTasksSuite(unittest.TestCase):
    """Verifies the Golden Tasks benchmark catalog and runner."""

    def test_all_ten_tasks_registered(self):
        expected_ids = [f"G{str(i).zfill(3)}" for i in range(1, 11)]
        self.assertEqual(len(GOLDEN_TASKS), 10)
        for tid in expected_ids:
            self.assertIn(tid, GOLDEN_TASKS)
            task = GOLDEN_TASKS[tid]
            self.assertTrue(len(task.name) > 0)
            self.assertTrue(task.max_tokens_budget > 0)
            self.assertTrue(len(task.deterministic_assertions) > 0)

    def test_task_categories_diversity(self):
        categories = {t.category for t in GOLDEN_TASKS.values()}
        # Must cover at least 7 distinct categories
        self.assertGreaterEqual(len(categories), 7)
        self.assertIn(GoldenTaskCategory.FAULT_TOLERANCE, categories)
        self.assertIn(GoldenTaskCategory.REVIEW_GATE, categories)
        self.assertIn(GoldenTaskCategory.CONTEXT_MANAGEMENT, categories)

    def test_golden_tasks_runner(self):
        runner = GoldenTasksRunner()
        results = runner.run_suite(["G001", "G009"])
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r.success)
            self.assertEqual(r.assertions_passed, r.assertions_total)
            self.assertGreater(r.tokens_consumed, 0)


if __name__ == "__main__":
    unittest.main()
