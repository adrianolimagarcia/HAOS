import unittest

from hermes.platform.execution.execution_contract import (
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionEvent,
    ExecutionStatus,
    TaskExecutionRequest,
    TaskExecutionResult,
)


class ExecutionContractTests(unittest.TestCase):
    def test_request_round_trips_and_validates_required_fields(self):
        request = TaskExecutionRequest("fix bug", "/tmp/ws", "model-x", 30)
        self.assertEqual(TaskExecutionRequest.from_dict(request.to_dict()), request)
        with self.assertRaises(ValueError):
            TaskExecutionRequest("", "/tmp/ws")
        with self.assertRaises(ValueError):
            TaskExecutionRequest("task", "/tmp/ws", timeout_seconds=0)

    def test_status_terminal_and_error_codes_cover_timeout_cancel(self):
        self.assertFalse(ExecutionStatus.RUNNING.terminal)
        self.assertTrue(ExecutionStatus.TIMED_OUT.terminal)
        timeout = ExecutionError(ExecutionErrorCode.TIMEOUT, "deadline exceeded")
        cancelled = ExecutionError(ExecutionErrorCode.CANCELLED, "cancel requested")
        self.assertEqual(
            ExecutionEvent("e1", ExecutionStatus.TIMED_OUT, 1, error=timeout).to_dict()["status"],
            "timed_out",
        )
        self.assertEqual(
            ExecutionEvent("e2", ExecutionStatus.CANCELLED, 1, error=cancelled).to_dict()["error"]["code"],
            "cancelled",
        )
        with self.assertRaises(ValueError):
            ExecutionEvent("e3", ExecutionStatus.TIMED_OUT, 1)

    def test_events_require_errors_for_failure_states(self):
        with self.assertRaises(ValueError):
            ExecutionEvent("e1", ExecutionStatus.FAILED, 1)
        with self.assertRaises(ValueError):
            ExecutionEvent(
                "e2", ExecutionStatus.CANCELLED, 1,
                error=ExecutionError(ExecutionErrorCode.TIMEOUT, "wrong"),
            )

    def test_result_contains_artifact_refs_and_terminal_error(self):
        result = TaskExecutionResult(
            "e1", ExecutionStatus.COMPLETED, "done",
            artifacts=[ExecutionArtifact("artifact://out", "report")],
        )
        payload = result.to_dict()
        self.assertEqual(payload["artifacts"][0]["ref"], "artifact://out")
        self.assertEqual(payload["status"], "completed")
        failed = TaskExecutionResult(
            "e2", ExecutionStatus.FAILED,
            error=ExecutionError(ExecutionErrorCode.BACKEND_ERROR, "worker failed"),
        )
        self.assertEqual(failed.to_dict()["error"]["code"], "backend_error")
        with self.assertRaises(ValueError):
            TaskExecutionResult("e3", ExecutionStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
