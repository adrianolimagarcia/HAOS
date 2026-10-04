"""ADR-021: delegate_tool attaches the civilization recovery suggestion to the
failure entry (timeout/exception paths), informational for the parent.

No LLM involved: civ plumbing is monkeypatched at the import point inside
tools.delegate_tool, so this tests the tool-side wiring only.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from hermes.platform.civilization.feature_flags import reset_feature_flags


def _make_mock_parent():
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "test"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "cli"
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    return parent


_CIV_TASK = {
    "bot_id": "worker-bot",
    "leaf_id": "shadow-worker-bot-abc",
    "identity_version": 1,
    "goal": "Fix flaky integration test",
}


@pytest.fixture
def civ_recovery(monkeypatch):
    """Fake the civ delegation surface and force the recovery flag ON."""
    import tools.delegate_tool as dt

    monkeypatch.setenv("HAOS_CIV_RECOVERY", "1")
    reset_feature_flags()
    calls = {"record": [], "suggest": []}

    fake = MagicMock()
    fake.record_task_result = lambda task, result, **kw: calls["record"].append((task, result))

    def _fake_suggest(task, result, **kw):
        # Mirror the real contract: HAOS_CIV_RECOVERY=0 -> no suggestion.
        from hermes.platform.civilization.feature_flags import get_feature_flags
        if not get_feature_flags().recovery_enabled:
            return None
        return {
            "strategy": "halt", "reason": "max_retries_exceeded", "attempt": 3,
            "bot_id": task["bot_id"], "leaf_id": task["leaf_id"], "goal": task["goal"],
        }

    fake.suggest_recovery = _fake_suggest
    import sys
    monkeypatch.setitem(sys.modules, "hermes.platform.civilization.delegation", fake)
    yield calls, fake
    reset_feature_flags()


def _child_that_raises():
    child = MagicMock()
    child._credential_pool = None
    child._civ_task = dict(_CIV_TASK)
    child.run_conversation.side_effect = RuntimeError("boom")
    return child


def test_failure_entry_carries_recovery(civ_recovery):
    from tools.delegate_tool import _run_single_child

    calls, _fake = civ_recovery
    entry = _run_single_child(task_index=0, goal="Fix flaky integration test",
                              child=_child_that_raises(), parent_agent=_make_mock_parent())

    assert entry["status"] == "error"
    # record_task_result still ran first (leaf closed), then the suggestion.
    assert len(calls["record"]) == 1
    civ_block = entry["civilization"]
    assert civ_block["bot_id"] == "worker-bot"
    assert civ_block["recovery"]["strategy"] == "halt"
    assert civ_block["recovery"]["reason"] == "max_retries_exceeded"


def test_recovery_flag_off_leaves_entry_unchanged(civ_recovery, monkeypatch):
    monkeypatch.setenv("HAOS_CIV_RECOVERY", "0")
    reset_feature_flags()
    from tools.delegate_tool import _run_single_child

    calls, _fake = civ_recovery
    entry = _run_single_child(task_index=0, goal="Fix flaky integration test",
                              child=_child_that_raises(), parent_agent=_make_mock_parent())

    assert len(calls["record"]) == 1  # leaf still closed
    assert "recovery" not in entry["civilization"]  # pre-ADR shape


def test_suggestion_failure_never_breaks_the_entry(civ_recovery):
    """A raising suggest_recovery must not corrupt the parent-visible entry."""
    import sys
    from tools.delegate_tool import _run_single_child

    _calls, fake = civ_recovery
    fake.suggest_recovery = MagicMock(side_effect=RuntimeError("store on fire"))
    entry = _run_single_child(task_index=0, goal="Fix flaky integration test",
                              child=_child_that_raises(), parent_agent=_make_mock_parent())
    assert entry["status"] == "error"
    assert "recovery" not in entry["civilization"]
