import unittest
import tempfile
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.backpressure import ConcurrencyGuard
from hermes.platform.ui.stats import DashboardStats
from hermes.platform.ui.views import concurrency_view, critical_path_view
from hermes.platform.ui.dashboard import dashboard_payload


class TestDashboardExtendedViews(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.db_path = self.tmp_path / "kanban.db"
        self.adapter = KanbanAdapter(self.db_path)
        self.guard = ConcurrencyGuard(
            max_global_concurrency=4,
            provider_limits={"openai": 2, "a6api": 3},
        )
        self.stats = DashboardStats(kanban=self.adapter, concurrency_guard=self.guard)

    def tearDown(self):
        self.adapter.close()
        self._tmp.cleanup()

    def test_concurrency_view(self):
        self.guard.acquire("T-1", provider_id="openai")
        view = concurrency_view(self.stats)
        self.assertEqual(view["view"], "concurrency")
        self.assertEqual(view["active_global"], 1)
        self.assertEqual(view["max_global"], 4)
        self.assertEqual(view["available_global"], 3)
        self.assertEqual(view["providers"]["openai"]["active"], 1)
        self.assertEqual(view["providers"]["openai"]["limit"], 2)

    def test_critical_path_view(self):
        # Cria DAG de tarefas: T1 -> T2 -> T3
        t1 = self.adapter.save_task(TaskSpec(id="T-1", title="Task 1", goal="g", max_runtime_minutes=10, workspace_type="scratch"))
        t2 = self.adapter.save_task(TaskSpec(id="T-2", title="Task 2", goal="g", max_runtime_minutes=20, requires_tasks=["T-1"], workspace_type="scratch"))
        t3 = self.adapter.save_task(TaskSpec(id="T-3", title="Task 3", goal="g", priority=90, requires_tasks=["T-2"], workspace_type="scratch"))

        view = critical_path_view(self.stats)
        self.assertEqual(view["view"], "critical_path")
        self.assertIn("T-1", view["critical_path_ids"])
        self.assertIn("T-2", view["critical_path_ids"])
        self.assertIn("T-3", view["critical_path_ids"])
        self.assertEqual(view["inherited_priorities"]["T-1"], 90.0)

    def test_dashboard_payload_includes_concurrency_and_critical_path(self):
        payload = dashboard_payload(self.stats)
        self.assertIn("concurrency", payload)
        self.assertIn("critical_path", payload)
        self.assertIn("taskboard", payload)
        self.assertIn("approvals", payload)

    def test_sanitized_review_context(self):
        t1 = self.adapter.save_task(TaskSpec(
            id="T-REV-1",
            title="Review Task",
            goal="Test review context",
            workspace_type="scratch",
            expected_artifacts=["patch.diff"],
        ))
        self.adapter.complete_task(
            t1,
            summary="Implemented fix",
            evidence={"tests": {"passed": True}, "chain_of_thought": "Should be purged"},
        )
        ctx = self.stats.sanitized_review_context(t1)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["task_id"], "T-REV-1")
        self.assertNotIn("chain_of_thought", ctx["evidence"])
        self.assertEqual(ctx["evidence"]["tests"]["passed"], True)


if __name__ == "__main__":
    unittest.main()
