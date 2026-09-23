import unittest

from hermes.platform.execution.execution_contract import ExecutionStatus
from hermes.platform.execution.opencode_sse import (
    OpenCodeCancellationUnsupported,
    OpenCodeCapabilityUnsupported,
    OpenCodeProtocolError,
    artifacts,
    cancel,
    cancellation_supported,
    events,
    iter_events,
    restart,
    resume,
    status,
)


class OpenCodeSSETests(unittest.TestCase):
    def test_valid_stream_has_lifecycle_and_preserves_sse_boundary(self):
        events = list(iter_events("oc-1", [
            ": heartbeat\n",
            'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": " there"}}]}\n',
            "data: [DONE]\n",
        ], timestamp=7.0))
        self.assertEqual([event.status for event in events], [ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED])
        self.assertEqual(events[0].metadata["source"], "opencode_sse")

    def test_invalid_input_fails_closed(self):
        with self.assertRaises(ValueError):
            list(iter_events("", []))
        with self.assertRaises(OpenCodeProtocolError):
            list(iter_events("oc-1", ["data: not-json\n"]))
        with self.assertRaises(OpenCodeProtocolError):
            list(iter_events("oc-1", ["event: message\n"]))

    def test_upstream_error_is_failed_not_success(self):
        events = list(iter_events("oc-1", ['data: {"error": {"message": "upstream down"}}\n']))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, ExecutionStatus.FAILED)
        self.assertEqual(events[0].error.message, "upstream down")

    def test_post_done_metadata_is_ignored_after_terminal_marker(self):
        events = list(iter_events("oc-1", [
            'data: {"choices": [{"delta": {"content": "ok"}}]}\n',
            "data: [DONE]\n",
            'data: {"choices": [], "cost": "0"}\n',
        ]))
        self.assertEqual(
            [event.status for event in events],
            [ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED],
        )

    def test_disconnect_without_done_does_not_invent_terminal_state(self):
        events = list(iter_events("oc-1", ['data: {"choices": []}\n']))
        self.assertEqual([event.status for event in events], [ExecutionStatus.RUNNING])

    def test_cancellation_is_explicitly_unsupported(self):
        self.assertFalse(cancellation_supported())
        with self.assertRaises(OpenCodeCancellationUnsupported):
            cancel("oc-1")

    def test_unverified_task_controls_are_explicitly_unsupported(self):
        for operation in (status, events, restart, resume, artifacts):
            with self.assertRaises(OpenCodeCapabilityUnsupported):
                operation("oc-1")


if __name__ == "__main__":
    unittest.main()
