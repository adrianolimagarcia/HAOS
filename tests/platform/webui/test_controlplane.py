"""Test suite for Phase 5 — Comprehensive Control Plane & Team Graph UI.

Validates:
1. TeamGraphNode hierarchical tree assembly and JSON serialization.
2. ControlPlaneService overview metrics aggregation.
3. Operator interventions (steer, pause, resume, interrupt) with EventStore audit logging.
"""

import unittest
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.webui.controlplane import (
    ControlPlaneService,
    NodeStatus,
    TeamGraphNode,
)


class TestPhase5ControlPlane(unittest.TestCase):
    """Verifies Control Plane backend and Team Graph data modeling."""

    def setUp(self):
        self.event_store = EventStore(db_path=":memory:")
        self.service = ControlPlaneService(event_store=self.event_store)

    def test_team_graph_hierarchy_construction(self):
        """Constructs a full TeamGraph with Town Mayor, Sub-Orchestrator, and Workers."""
        sub_orchestrators = [
            {
                "id": "so-software",
                "domain": "software",
                "workers": [
                    {
                        "id": "w-coder-1",
                        "label": "Polecat #1",
                        "role": "worker",
                        "posture": "coder",
                        "task": "Implement TokenBucket",
                        "status": "completed",
                        "tokens": 450,
                        "cost": 0.00045,
                    },
                    {
                        "id": "w-witness-1",
                        "label": "Witness Reviewer",
                        "role": "reviewer",
                        "posture": "reviewer",
                        "task": "Architecture Validation",
                        "status": "running",
                        "tokens": 120,
                        "cost": 0.00012,
                    },
                ],
            }
        ]

        graph = self.service.build_team_graph(
            mission_id="m-test-01",
            goal="Build TokenBucket",
            sub_orchestrators=sub_orchestrators,
        )

        self.assertEqual(graph.role, "mayor")
        self.assertEqual(graph.posture, "executive")
        self.assertEqual(len(graph.children), 1)

        so_node = graph.children[0]
        self.assertEqual(so_node.role, "sub_orchestrator")
        self.assertEqual(len(so_node.children), 2)

        w1 = so_node.children[0]
        self.assertEqual(w1.label, "Polecat #1")
        self.assertEqual(w1.status, NodeStatus.COMPLETED)
        self.assertEqual(w1.tokens_consumed, 450)

        # Test dictionary serialization
        data = graph.to_dict()
        self.assertEqual(data["node_id"], "mayor-m-test-01")
        self.assertEqual(len(data["children"][0]["children"]), 2)

    def test_control_plane_overview_and_interventions(self):
        """Tests overview counters and operator intervention logging."""
        # Inject sample events
        self.event_store.append(Event(name="team.formed", payload={"mission_id": "m1"}))
        self.event_store.append(Event(name="task.completed", payload={"tokens": 1500}))

        overview = self.service.get_overview()
        self.assertEqual(overview.total_missions, 1)
        self.assertEqual(overview.total_tokens, 1500)

        # Execute intervention
        self.service.record_intervention(
            target_id="w-coder-1",
            action="steer",
            reason="Operator requested strict type annotations",
        )
        self.assertEqual(self.service.get_pending_intervention("w-coder-1"), "steer")

        events = self.event_store.read_events(name="controlplane.intervention")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["action"], "steer")
        self.assertEqual(events[0].payload["target_id"], "w-coder-1")

    def test_team_graph_idle_state_when_no_active_tasks(self):
        """When system has no running tasks or empty kanban, all nodes must be IDLE, not RUNNING."""
        snapshot = self.service.get_team_graph_snapshot()
        self.assertEqual(snapshot["role"], "mayor")
        self.assertEqual(snapshot["status"], "idle")
        self.assertGreaterEqual(len(snapshot["children"]), 1)
        sub_orch = snapshot["children"][0]
        self.assertEqual(sub_orch["status"], "idle")
        for worker in sub_orch["children"]:
            self.assertEqual(worker["status"], "idle", f"Worker {worker['node_id']} should be idle")

    def test_team_graph_ready_tasks_do_not_trigger_running_status(self):
        """Kanban tasks in 'ready' state must be shown as IDLE/pending, never as RUNNING."""
        class MockKanban:
            def list_tasks(self):
                return [
                    {"id": "T-READY-1", "title": "Implement Cache", "status": "ready", "assignee": "specialist-coder-01"}
                ]
        service = ControlPlaneService(self.event_store, kanban=MockKanban())
        snapshot = service.get_team_graph_snapshot()
        self.assertEqual(snapshot["status"], "idle")
        sub_orch = snapshot["children"][0]
        self.assertEqual(sub_orch["status"], "idle")
        coder = next(w for w in sub_orch["children"] if w["node_id"] == "specialist-coder-01")
        self.assertEqual(coder["status"], "idle")

    def test_team_graph_in_progress_tasks_trigger_running_status(self):
        """Only worker executing in_progress task is RUNNING; idle reviewer remains IDLE."""
        class MockKanban:
            def list_tasks(self):
                return [
                    {"id": "T-RUN-1", "title": "Coding Phase", "status": "in_progress", "assignee": "specialist-coder-01"},
                    {"id": "T-REV-1", "title": "Review Phase", "status": "ready", "assignee": "specialist-reviewer-01"}
                ]
        service = ControlPlaneService(self.event_store, kanban=MockKanban())
        snapshot = service.get_team_graph_snapshot()
        self.assertEqual(snapshot["status"], "running")
        sub_orch = snapshot["children"][0]
        self.assertEqual(sub_orch["status"], "running")
        coder = next(w for w in sub_orch["children"] if w["node_id"] == "specialist-coder-01")
        reviewer = next(w for w in sub_orch["children"] if w["node_id"] == "specialist-reviewer-01")
        self.assertEqual(coder["status"], "running")
        self.assertEqual(reviewer["status"], "idle")

    def test_team_graph_completed_tasks_set_completed_status(self):
        """When all tasks are done, mission and workers transition to COMPLETED."""
        class MockKanban:
            def list_tasks(self):
                return [
                    {"id": "T-DONE-1", "title": "Done Task", "status": "done", "assignee": "specialist-coder-01"}
                ]
        service = ControlPlaneService(self.event_store, kanban=MockKanban())
        snapshot = service.get_team_graph_snapshot()
        self.assertEqual(snapshot["status"], "completed")
        sub_orch = snapshot["children"][0]
        self.assertEqual(sub_orch["status"], "completed")
        for worker in sub_orch["children"]:
            self.assertEqual(worker["status"], "completed")

    def test_team_graph_intervention_pause_and_resume(self):
        """Operator pausing a worker changes its status to PAUSED."""
        class MockKanban:
            def list_tasks(self):
                return [
                    {"id": "T-RUN-1", "title": "Coding Phase", "status": "in_progress", "assignee": "specialist-coder-01"}
                ]
        service = ControlPlaneService(self.event_store, kanban=MockKanban())
        # Record pause intervention
        service.record_intervention("specialist-coder-01", "pause", "Waiting for human review")
        snapshot = service.get_team_graph_snapshot()
        sub_orch = snapshot["children"][0]
        coder = next(w for w in sub_orch["children"] if w["node_id"] == "specialist-coder-01")
        self.assertEqual(coder["status"], "paused")

        # Resume intervention
        service.record_intervention("specialist-coder-01", "resume", "Review approved")
        snapshot2 = service.get_team_graph_snapshot()
        coder2 = next(w for w in snapshot2["children"][0]["children"] if w["node_id"] == "specialist-coder-01")
        self.assertEqual(coder2["status"], "running")


if __name__ == "__main__":
    unittest.main()
