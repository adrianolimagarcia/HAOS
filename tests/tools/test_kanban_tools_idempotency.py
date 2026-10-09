import json
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from tools import kanban_tools as kt


@pytest.fixture
def isolated_kanban_env(monkeypatch, tmp_path):
    """Isolated HERMES_HOME and kanban DB for idempotency tests."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "orchestrator")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return tmp_path


def test_kanban_complete_idempotency(isolated_kanban_env, monkeypatch):
    """Calling kanban_complete on an already 'done' task returns idempotent success."""
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="complete-test", assignee="worker")
        kb.claim_task(conn, tid)
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)

    # First completion
    out1 = kt._handle_complete({"summary": "Finished work on task"})
    d1 = json.loads(out1)
    assert d1["ok"] is True
    assert d1["task_id"] == tid
    assert d1.get("idempotent") is not True

    conn = kbc.connect()
    try:
        task1 = kb.get_task(conn, tid)
        assert task1.status == "done"
        run1 = kb.latest_run(conn, tid)
        assert run1 is not None
        assert run1.outcome == "completed"
    finally:
        conn.close()

    # Second completion (retry)
    out2 = kt._handle_complete({"summary": "Retrying completion"})
    d2 = json.loads(out2)
    assert d2["ok"] is True
    assert d2["task_id"] == tid
    assert d2["run_id"] == run1.id
    assert d2["idempotent"] is True
    assert d2["message"] == f"Task {tid} is already completed"

    conn = kbc.connect()
    try:
        task2 = kb.get_task(conn, tid)
        assert task2.status == "done"
        run2 = kb.latest_run(conn, tid)
        assert run2.id == run1.id
    finally:
        conn.close()


def test_kanban_link_idempotency(isolated_kanban_env):
    """Calling kanban_link when the link already exists returns idempotent success."""
    conn = kbc.connect()
    try:
        parent_id = kb.create_task(conn, title="Parent task", assignee="worker")
        child_id = kb.create_task(conn, title="Child task", assignee="worker")
    finally:
        conn.close()

    # First link
    out1 = kt._handle_link({"parent_id": parent_id, "child_id": child_id})
    d1 = json.loads(out1)
    assert d1["ok"] is True
    assert d1["link_id"] == f"{parent_id}:{child_id}"
    assert d1["parent_id"] == parent_id
    assert d1["child_id"] == child_id
    assert d1.get("idempotent") is not True

    # Second link (idempotent duplicate)
    out2 = kt._handle_link({"parent_id": parent_id, "child_id": child_id})
    d2 = json.loads(out2)
    assert d2["ok"] is True
    assert d2["link_id"] == f"{parent_id}:{child_id}"
    assert d2["parent_id"] == parent_id
    assert d2["child_id"] == child_id
    assert d2["idempotent"] is True


def test_kanban_block_idempotency(isolated_kanban_env, monkeypatch):
    """Calling kanban_block on an already 'blocked' task returns idempotent success."""
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="block-test", assignee="worker")
        kb.claim_task(conn, tid)
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)

    # First block
    out1 = kt._handle_block({"reason": "waiting for user clarification", "kind": "needs_input"})
    d1 = json.loads(out1)
    assert d1["ok"] is True
    assert d1["task_id"] == tid
    assert d1["status"] == "blocked"
    assert d1.get("idempotent") is not True

    conn = kbc.connect()
    try:
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()

    # Second block (already blocked)
    out2 = kt._handle_block({"reason": "still waiting", "kind": "needs_input"})
    d2 = json.loads(out2)
    assert d2["ok"] is True
    assert d2["task_id"] == tid
    assert d2["idempotent"] is True

    conn = kbc.connect()
    try:
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()


def test_kanban_unblock_idempotency(isolated_kanban_env):
    """Calling kanban_unblock on a task that is not 'blocked' returns idempotent success."""
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="unblock-test", assignee="worker")
        # Task is created in 'ready' status by default
        assert kb.get_task(conn, tid).status == "ready"
    finally:
        conn.close()

    # Calling unblock on a task that is already ready (not blocked)
    out1 = kt._handle_unblock({"task_id": tid})
    d1 = json.loads(out1)
    assert d1["ok"] is True
    assert d1["task_id"] == tid
    assert d1["idempotent"] is True

    # Now block it, unblock it once, and unblock it again
    conn = kbc.connect()
    try:
        kb.block_task(conn, tid, reason="testing unblock")
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()

    # First unblock (transitions blocked -> ready)
    out2 = kt._handle_unblock({"task_id": tid})
    d2 = json.loads(out2)
    assert d2["ok"] is True
    assert d2["task_id"] == tid
    assert d2["status"] == "ready"
    assert d2.get("idempotent") is not True

    # Second unblock (now status is 'ready', not 'blocked')
    out3 = kt._handle_unblock({"task_id": tid})
    d3 = json.loads(out3)
    assert d3["ok"] is True
    assert d3["task_id"] == tid
    assert d3["idempotent"] is True
