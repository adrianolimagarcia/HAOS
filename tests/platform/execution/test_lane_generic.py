"""Test suite for GenericWorkerLane contract (ADR-010 / Gate 4).

Validates:
1. prepare -> workspace creation
2. spawn -> run_id generation and active status
3. heartbeat -> keep-alive updates
4. collect_result -> structured output and tokens
5. cancel -> task cancellation
6. cleanup -> workspace destruction
"""

import tempfile
import unittest
from pathlib import Path

from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.execution.lane_generic import (
    GenericWorkerLane,
    LaneRunStatus,
    LocalMockWorkerLane,
    WorkerResult,
)
from hermes.platform.tasks.spec import TaskSpec


class TestGenericWorkerLane(unittest.TestCase):
    """Verifies the GenericWorkerLane lifecycle contract."""

    def setUp(self):
        self.lane = LocalMockWorkerLane(lane_id="lane-mock-01")
        self.task = TaskSpec(
            id="t-lane-01",
            title="Lane Test Task",
            goal="Test GenericWorkerLane contract",
            posture="coder",
        )
        self.assignment = AssignmentSpec(
            task_id=self.task.id,
            task_revision=1,
            run_id="run-lane-test-1",
            execution_shape="worker_lane",
            lane="mock",
            agent_mode="ephemeral",
            posture_id="coder",
            model_profile_id="coding-primary",
            resolved_model_family="deepseek-v4",
            resolved_model_variant="flash",
        )

    def test_complete_lane_lifecycle(self):
        # 1. Prepare
        ws_path = self.lane.prepare(self.task)
        self.assertTrue(ws_path.exists())

        # 2. Spawn
        run_id = self.lane.spawn(self.assignment, ws_path)
        self.assertTrue(run_id.startswith("run-t-lane-01-"))

        # 3. Heartbeat
        alive = self.lane.heartbeat(run_id)
        self.assertTrue(alive)

        # 4. Collect Result
        result = self.lane.collect_result(run_id)
        self.assertEqual(result.status, LaneRunStatus.COMPLETED)
        self.assertEqual(result.task_id, self.task.id)
        self.assertEqual(result.exit_code, 0)
        self.assertGreater(result.tokens_consumed, 0)

        # 5. Cleanup
        self.lane.cleanup(ws_path)
        self.assertFalse(ws_path.exists())

    def test_lane_cancellation(self):
        ws_path = self.lane.prepare(self.task)
        run_id = self.lane.spawn(self.assignment, ws_path)

        # Cancel
        cancelled = self.lane.cancel(run_id, reason="Emergency operator abort")
        self.assertTrue(cancelled)

        # Heartbeat should now fail on cancelled run
        self.assertFalse(self.lane.heartbeat(run_id))

        self.lane.cleanup(ws_path)


if __name__ == "__main__":
    unittest.main()
