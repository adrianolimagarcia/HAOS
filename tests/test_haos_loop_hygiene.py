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
