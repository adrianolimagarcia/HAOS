"""Contract tests for Conductor-inspired Durable Workflows & Signal Gates in HAOS."""

import sqlite3
import pytest
from hermes.platform.workflow.durable import (
    SignalGateSpec,
    _extract_signal_gate_spec,
    evaluate_signal_gates,
    DAGWorkflowBuilder,
)


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            status TEXT,
            priority INTEGER DEFAULT 1,
            created_by TEXT,
            created_at INTEGER,
            started_at INTEGER,
            completed_at INTEGER,
            workspace_kind TEXT,
            assignee TEXT,
            last_failure_error TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE task_links (
            parent_id TEXT,
            child_id TEXT,
            PRIMARY KEY (parent_id, child_id)
        )
    """)
    return conn


def test_extract_signal_gate_spec():
    body = "Review PR #42\n[SIGNAL_GATE: {\"timeout_seconds\": 600, \"timeout_action\": \"auto_approve\"}]"
    spec = _extract_signal_gate_spec(body)
    assert spec is not None
    assert spec.timeout_seconds == 600
    assert spec.timeout_action == "auto_approve"

    # Empty/invalid body
    assert _extract_signal_gate_spec("Regular body without gate") is None


def test_signal_gate_auto_approve_timeout(memory_db):
    base_time = 1000
    body = "Human review needed\n[SIGNAL_GATE: {\"timeout_seconds\": 300, \"timeout_action\": \"auto_approve\"}]"
    memory_db.execute(
        "INSERT INTO tasks (id, title, body, status, started_at) VALUES ('T-1', 'Review X', ?, 'review', ?)",
        (body, base_time),
    )
    memory_db.commit()

    # Before deadline (1200 < 1300): should not trigger
    actions = evaluate_signal_gates(memory_db, current_time=1200)
    assert len(actions) == 0
    row = memory_db.execute("SELECT status FROM tasks WHERE id = 'T-1'").fetchone()
    assert row["status"] == "review"

    # After deadline (1301 >= 1300): should auto-approve
    actions = evaluate_signal_gates(memory_db, current_time=1301)
    assert len(actions) == 1
    assert actions[0]["action"] == "auto_approve"
    row = memory_db.execute("SELECT status FROM tasks WHERE id = 'T-1'").fetchone()
    assert row["status"] == "done"


def test_dag_workflow_builder_validation():
    builder = DAGWorkflowBuilder("Build and Test")
    builder.add_task("task-1", "Compile Code")
    builder.add_task("task-2", "Run Unit Tests", depends_on=["task-1"])
    builder.add_task("task-3", "Deploy Artifact", depends_on=["task-2"])

    assert builder.validate_acyclic() is True


def test_dag_workflow_cycle_detection():
    builder = DAGWorkflowBuilder("Cycle Test")
    builder.add_task("task-A", "Task A", depends_on=["task-B"])
    builder.add_task("task-B", "Task B", depends_on=["task-A"])

    with pytest.raises(ValueError, match="Cyclic dependency"):
        builder.validate_acyclic()


def test_dag_deploy_to_kanban(memory_db):
    builder = DAGWorkflowBuilder("CI Pipeline")
    builder.add_task("compile", "Compile Code")
    builder.add_task("lint", "Run Linter")
    builder.add_task("package", "Package Dist", depends_on=["compile", "lint"])

    deployed = builder.deploy_to_kanban(memory_db)
    assert len(deployed) == 3

    # Tasks with no dependencies start in 'ready'
    compile_task = memory_db.execute("SELECT status FROM tasks WHERE id = 'compile'").fetchone()
    assert compile_task["status"] == "ready"

    # Tasks with dependencies start in 'todo'
    pkg_task = memory_db.execute("SELECT status FROM tasks WHERE id = 'package'").fetchone()
    assert pkg_task["status"] == "todo"

    # Verify task_links
    links = memory_db.execute("SELECT parent_id, child_id FROM task_links WHERE child_id = 'package'").fetchall()
    parents = {row["parent_id"] for row in links}
    assert parents == {"compile", "lint"}
