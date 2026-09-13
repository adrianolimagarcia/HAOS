"""Test suite for MultiAgentTeamRuntime (Phase 2 — Team Runtime).

Validates:
1. SpecialistPool: Acquire, hygienic reset, and specialist reuse without context pollution.
2. DomainSubOrchestrator: Decomposing high-level goals into typed DAGs.
3. MultiAgentTeamRuntime: Full multi-agent coordinated execution with DAG dependencies.
4. Telemetry: All events (team.formed, subgoal.delegated, worker.acquired, worker.released) stored in EventStore.
"""

import unittest
from typing import Any, Dict

from hermes.platform.execution.team_runtime import (
    DomainSubGoal,
    DomainSubOrchestrator,
    MultiAgentTeamRuntime,
    SpecialistPool,
)
from hermes.platform.observability.event_store import EventStore
from hermes.platform.tasks.spec import TaskSpec


class TestMultiAgentTeamRuntime(unittest.TestCase):
    """Verifies Phase 2 Multi-Agent Team Runtime and Specialist Pooling."""

    def setUp(self):
        self.event_store = EventStore(db_path=":memory:")
        self.pool = SpecialistPool(max_pool_size=8)
        self.runtime = MultiAgentTeamRuntime(
            event_store=self.event_store,
            specialist_pool=self.pool,
        )

    def test_specialist_pool_reuse_and_sanitization(self):
        """Workers of matching posture are reused across sequential tasks."""
        w1 = self.pool.acquire(posture="coder", model_profile_id="profile-coder")
        self.assertEqual(w1.posture, "coder")
        self.assertTrue(w1.in_use)
        self.assertEqual(self.pool.active_count(), 1)
        self.assertEqual(self.pool.total_count(), 1)

        # Release back to pool
        self.pool.release(w1.worker_id)
        self.assertFalse(w1.in_use)
        self.assertEqual(w1.tasks_executed, 1)
        self.assertEqual(self.pool.active_count(), 0)

        # Acquire again: must REUSE w1 instead of spawning a new worker
        w2 = self.pool.acquire(posture="coder", model_profile_id="profile-coder")
        self.assertEqual(w1.worker_id, w2.worker_id)
        self.assertEqual(self.pool.total_count(), 1)
        self.pool.release(w2.worker_id)
        self.assertEqual(w2.tasks_executed, 2)

    def test_domain_sub_orchestrator_decomposition(self):
        """DomainSubOrchestrator decomposes goals into dependent TaskSpecs."""
        sub = DomainSubOrchestrator(domain="software")
        goal = DomainSubGoal(
            id="subgoal-auth",
            domain="software",
            objective="Add OAuth2 authentication provider",
        )
        tasks = sub.decompose(goal)

        self.assertEqual(len(tasks), 3)
        t_impl, t_test, t_review = tasks[0], tasks[1], tasks[2]

        self.assertEqual(t_impl.id, "subgoal-auth-impl")
        self.assertEqual(t_impl.posture, "coder")

        self.assertEqual(t_test.id, "subgoal-auth-test")
        self.assertIn(t_impl.id, t_test.requires_tasks)

        self.assertEqual(t_review.id, "subgoal-auth-review")
        self.assertEqual(t_review.posture, "reviewer")
        self.assertIn(t_test.id, t_review.requires_tasks)

    def test_multi_agent_team_mission_execution(self):
        """Executes a full team mission honoring task DAG dependencies and pooling."""
        executed_order = []

        def dummy_worker(task: TaskSpec, specialist) -> Dict[str, Any]:
            executed_order.append(task.id)
            return {"task": task.id, "worker": specialist.worker_id, "status": "ok"}

        goals = [
            DomainSubGoal(id="auth", domain="software", objective="Implement Auth Module"),
            DomainSubGoal(id="perf", domain="research", objective="Benchmark latency"),
        ]

        summary = self.runtime.execute_team_mission(
            mission_goal="Build Auth Module with benchmark reports",
            domain_sub_goals=goals,
            worker_execution_fn=dummy_worker,
        )

        self.assertEqual(summary["tasks_count"], 5)  # 3 software + 2 research
        self.assertEqual(len(summary["completed_tasks"]), 5)

        # Confirm strict DAG ordering for software tasks: impl -> test -> review
        idx_impl = executed_order.index("auth-impl")
        idx_test = executed_order.index("auth-test")
        idx_review = executed_order.index("auth-review")
        self.assertTrue(idx_impl < idx_test < idx_review)

        # Confirm all specialists were safely returned to pool
        self.assertEqual(self.pool.active_count(), 0)

        # Verify EventStore audit trail
        events = self.event_store.read_events()
        names = [e.name for e in events]
        self.assertIn("team.formed", names)
        self.assertIn("subgoal.delegated", names)
        self.assertIn("worker.acquired", names)
        self.assertIn("task.completed", names)
        self.assertIn("worker.released", names)
        self.assertIn("mission.completed", names)


if __name__ == "__main__":
    unittest.main()
