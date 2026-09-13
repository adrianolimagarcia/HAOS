import unittest
from hermes.platform.execution.step_lifecycle_guard import (
    StepLifecycleGuard,
    StepContext,
    StepVerdict,
)


class TestStepLifecycleGuard(unittest.TestCase):
    def test_before_step_valid(self):
        ctx = StepContext(
            tool_name="terminal",
            tool_args={"command": "ls"},
            step_index=1,
            total_steps=3,
        )
        verdict = StepLifecycleGuard.before_step(ctx)
        self.assertTrue(verdict.proceed)
        self.assertFalse(verdict.abort_remaining)

    def test_before_step_empty_name(self):
        ctx = StepContext(
            tool_name="",
            tool_args={},
            step_index=1,
            total_steps=1,
        )
        verdict = StepLifecycleGuard.before_step(ctx)
        self.assertFalse(verdict.proceed)
        self.assertTrue(verdict.abort_remaining)

    def test_after_step_terminal_success(self):
        ctx = StepContext(
            tool_name="terminal",
            tool_args={"command": "pytest"},
            step_index=1,
            total_steps=2,
        )
        verdict = StepLifecycleGuard.after_step(ctx, {"exit_code": 0, "output": "ok"})
        self.assertTrue(verdict.proceed)
        self.assertFalse(verdict.abort_remaining)

    def test_after_step_terminal_failure_aborts_cascade(self):
        ctx = StepContext(
            tool_name="terminal",
            tool_args={"command": "pytest"},
            step_index=1,
            total_steps=2,
        )
        verdict = StepLifecycleGuard.after_step(
            ctx,
            {"exit_code": 1, "output": "AssertionError: expected 1 got 2", "error": "Failed"}
        )
        self.assertFalse(verdict.proceed)
        self.assertTrue(verdict.abort_remaining)
        self.assertIn("failed with exit code 1", verdict.reason or "")
        self.assertIsNotNone(verdict.reflexion_prompt)
        self.assertIn("AssertionError", verdict.reflexion_prompt or "")

    def test_after_step_patch_failure_aborts(self):
        ctx = StepContext(
            tool_name="patch",
            tool_args={"path": "foo.py"},
            step_index=1,
            total_steps=2,
        )
        verdict = StepLifecycleGuard.after_step(
            ctx,
            "[patch] Error: old_string not found in target file"
        )
        self.assertFalse(verdict.proceed)
        self.assertTrue(verdict.abort_remaining)
        self.assertIn("re-read the file first", verdict.reflexion_prompt or "")


if __name__ == "__main__":
    unittest.main()
