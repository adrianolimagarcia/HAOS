"""Contract tests for HAOS Loop Hygiene (repeat-tool-reminder)."""

from agent.loop_hygiene import RepeatToolGuard, attach_repetition_reminder_if_needed


def test_repeat_tool_guard_first_call():
    guard = RepeatToolGuard(threshold=2, max_strikes=4)
    is_rep, reminder = guard.record_and_check("terminal", {"command": "pytest"})
    assert is_rep is False
    assert reminder is None


def test_repeat_tool_guard_different_call():
    guard = RepeatToolGuard(threshold=2, max_strikes=4)
    guard.record_and_check("terminal", {"command": "pytest"})
    is_rep, reminder = guard.record_and_check("terminal", {"command": "pytest -v"})
    assert is_rep is False
    assert reminder is None


def test_repeat_tool_guard_triggers_warning_on_consecutive():
    guard = RepeatToolGuard(threshold=2, max_strikes=4)
    guard.record_and_check("terminal", {"command": "pytest"})
    is_rep, reminder = guard.record_and_check("terminal", {"command": "pytest"})
    assert is_rep is True
    assert reminder is not None
    assert "LOOP GUARD WARNING" in reminder
    assert "Consecutive identical call #2" in reminder


def test_repeat_tool_guard_triggers_critical_on_max_strikes():
    guard = RepeatToolGuard(threshold=2, max_strikes=4)
    for _ in range(3):
        guard.record_and_check("terminal", {"command": "pytest"})
    is_rep, reminder = guard.record_and_check("terminal", {"command": "pytest"})
    assert is_rep is True
    assert reminder is not None
    assert "LOOP GUARD CRITICAL" in reminder
    assert "You MUST STOP repeating this call immediately" in reminder


def test_attach_repetition_reminder_helper():
    class DummyAgent:
        pass

    agent = DummyAgent()
    base_result = "Command failed with exit code 1"
    res1 = attach_repetition_reminder_if_needed(agent, "bash", {"cmd": "ls"}, base_result)
    assert res1 == base_result

    res2 = attach_repetition_reminder_if_needed(agent, "bash", {"cmd": "ls"}, base_result)
    assert "LOOP GUARD WARNING" in res2
    assert base_result in res2


def test_target_failure_streak_patch_target():
    from agent.loop_hygiene import TargetFailureStreakGuard, attach_target_failure_reminder_if_needed

    class DummyAgent:
        pass

    agent = DummyAgent()
    guard = TargetFailureStreakGuard()
    
    # 3 failures on the same file with different patch arguments (pseudo-loop)
    res, msg = guard.record_and_check("patch", {"path": "foo.py", "old_string": "a"}, "Error: could not find old_string")
    assert not res
    assert msg is None

    res, msg = guard.record_and_check("patch", {"path": "foo.py", "old_string": "b"}, "Error: could not find old_string")
    assert not res
    assert msg is None

    res, msg = guard.record_and_check("patch", {"path": "foo.py", "old_string": "c"}, "Error: could not find old_string")
    assert not res
    assert msg is None

    # 4th failure triggers the target-level reminder!
    res, msg = guard.record_and_check("patch", {"path": "foo.py", "old_string": "d"}, "Error: could not find old_string")
    assert res
    assert "TARGET FAILURE STREAK" in msg
    assert "foo.py" in msg

    # Successful call resets streak
    res, msg = guard.record_and_check("patch", {"path": "foo.py", "old_string": "d"}, "File patched successfully")
    assert not res
    assert guard._file_patch_failures.get("foo.py", 0) == 0


def test_target_failure_streak_test_command():
    from agent.loop_hygiene import TargetFailureStreakGuard

    guard = TargetFailureStreakGuard()
    cmd = "pytest tests/gateway/test_run.py"

    # 2 failures do not trigger
    res, _ = guard.record_and_check("terminal", {"command": cmd}, "FAILED (exit code 1): AssertionError")
    assert not res
    res, _ = guard.record_and_check("terminal", {"command": cmd}, "FAILED (exit code 1): AssertionError")
    assert not res

    # 3rd consecutive failing test run triggers reminder!
    res, msg = guard.record_and_check("terminal", {"command": cmd}, "FAILED (exit code 1): AssertionError")
    assert res
    assert "TEST FAILURE STREAK" in msg
    assert cmd in msg


def test_attach_target_failure_reminder_helper():
    from agent.loop_hygiene import attach_target_failure_reminder_if_needed

    class DummyAgent:
        pass

    agent = DummyAgent()
    base_res = "Error: could not find old_string"

    # Calls 1 to 3 do not attach reminder
    for i in range(3):
        out = attach_target_failure_reminder_if_needed(agent, "patch", {"path": "bar.py", "old_string": f"v{i}"}, base_res)
        assert out == base_res

    # 4th failure attaches target failure reminder
    out = attach_target_failure_reminder_if_needed(agent, "patch", {"path": "bar.py", "old_string": "v4"}, base_res)
    assert base_res in out
    assert "TARGET FAILURE STREAK" in out
    assert "bar.py" in out
