"""Unit and Integration Tests for MasterPlanOrchestrator.

Validates that all 10 tasks in the Master Plan execute cleanly and emit EventStore audit logs.
"""

import unittest
from hermes.platform.execution.master_plan_orchestrator import MasterPlanOrchestrator


class TestMasterPlanOrchestrator(unittest.TestCase):
    """Verifies that all 10 Master Plan tasks execute end-to-end."""

    def test_master_plan_orchestrator_execution(self):
        orchestrator = MasterPlanOrchestrator()
        reports = orchestrator.execute_all_tasks()

        self.assertEqual(len(reports), 10)
        for r in reports:
            self.assertEqual(r.status, "success", f"Task {r.task_id} failed: {r.error}")

        # Check EventStore events
        events = orchestrator.event_store.read_events(name="master_plan.task_completed")
        self.assertEqual(len(events), 10)


if __name__ == "__main__":
    unittest.main()
