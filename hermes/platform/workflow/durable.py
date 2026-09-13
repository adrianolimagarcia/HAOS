"""HAOS Durable Workflow & Signal Engine (Conductor-inspired).

Implements:
1. SignalGate / Human-in-the-Loop with TTL (WAIT_FOR_SIGNAL):
   Allows a task or review gate to wait for external signals or human approval
   with an automatic timeout fallback (e.g. auto_approve, auto_block, escalate).

2. Declarative DAG & Fork/Join Evaluator:
   Evaluates task execution graphs (dependencies, fork/join, switch conditions)
   deterministically over SQLite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.platform.workflow.durable")

DEFAULT_SIGNAL_TTL_SECONDS = 3600  # 1 hour default TTL for review/signal waits


@dataclass
class SignalGateSpec:
    signal_name: str
    timeout_seconds: int = DEFAULT_SIGNAL_TTL_SECONDS
    timeout_action: str = "auto_block"  # "auto_block" | "auto_approve" | "escalate"
    payload: Optional[Dict[str, Any]] = None


def evaluate_signal_gates(conn: sqlite3.Connection, current_time: Optional[int] = None) -> List[Dict[str, Any]]:
    """Scan tasks in 'review' or 'blocked' that have signal/review TTLs and handle expirations.

    Returns a list of actions executed.
    """
    now = current_time or int(time.time())
    actions_taken = []

    # Check review tasks with approval metadata
    # Metadata is parsed from body or task comments formatted as:
    # [SIGNAL_GATE: {"timeout_seconds": 300, "timeout_action": "auto_approve"}]
    cursor = conn.execute(
        "SELECT id, title, status, body, started_at, created_at FROM tasks WHERE status IN ('review', 'blocked')"
    )
    rows = cursor.fetchall()

    for row in rows:
        task_id = row["id"]
        body = row["body"] or ""
        spec = _extract_signal_gate_spec(body)
        if not spec:
            continue

        base_time = row["started_at"] or row["created_at"] or now
        deadline = base_time + spec.timeout_seconds

        if now >= deadline:
            action_desc = _apply_timeout_action(conn, task_id, row["status"], spec.timeout_action, row["title"])
            actions_taken.append({
                "task_id": task_id,
                "action": spec.timeout_action,
                "description": action_desc,
            })

    return actions_taken


def _extract_signal_gate_spec(body: str) -> Optional[SignalGateSpec]:
    marker = "[SIGNAL_GATE:"
    idx = body.find(marker)
    if idx == -1:
        return None
    end_idx = body.find("]", idx)
    if end_idx == -1:
        return None
    json_str = body[idx + len(marker):end_idx].strip()
    try:
        data = json.loads(json_str)
        return SignalGateSpec(
            signal_name=data.get("signal_name", "approval"),
            timeout_seconds=int(data.get("timeout_seconds", DEFAULT_SIGNAL_TTL_SECONDS)),
            timeout_action=str(data.get("timeout_action", "auto_block")),
            payload=data.get("payload"),
        )
    except Exception as e:
        logger.debug("Failed to parse SIGNAL_GATE spec: %s", e)
        return None


def _apply_timeout_action(conn: sqlite3.Connection, task_id: str, current_status: str, action: str, title: str) -> str:
    """Apply the configured timeout fallback action."""
    now = int(time.time())
    if action == "auto_approve":
        # Transition to done
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (now, task_id),
        )
        desc = f"Task {task_id} auto-approved after signal timeout."
    elif action == "escalate":
        conn.execute(
            "UPDATE tasks SET status = 'blocked', last_failure_error = ? WHERE id = ?",
            ("ESCALATED: Human approval timeout expired; operator review required.", task_id),
        )
        desc = f"Task {task_id} escalated to operator after signal timeout."
    else:  # default: auto_block
        conn.execute(
            "UPDATE tasks SET status = 'blocked', last_failure_error = ? WHERE id = ?",
            ("TIMEOUT: Signal/review wait TTL expired.", task_id),
        )
        desc = f"Task {task_id} auto-blocked due to signal timeout."

    conn.commit()
    logger.info("SignalGate: %s", desc)
    return desc


# ---------------------------------------------------------------------------
# Declarative DAG & Fork/Join Execution
# ---------------------------------------------------------------------------

@dataclass
class DAGTaskSpec:
    id: str
    title: str
    depends_on: List[str]
    body: str = ""
    assignee: Optional[str] = None


class DAGWorkflowBuilder:
    """Constructs and validates declarative DAG workflows with Fork/Join semantics."""

    def __init__(self, workflow_name: str):
        self.workflow_name = workflow_name
        self.tasks: Dict[str, DAGTaskSpec] = {}

    def add_task(
        self,
        task_id: str,
        title: str,
        depends_on: Optional[List[str]] = None,
        body: str = "",
        assignee: Optional[str] = None,
    ) -> "DAGWorkflowBuilder":
        self.tasks[task_id] = DAGTaskSpec(
            id=task_id,
            title=title,
            depends_on=depends_on or [],
            body=body,
            assignee=assignee,
        )
        return self

    def add_vertical_slice(
        self,
        slice_id: str,
        feature_name: str,
        acceptance_criteria: List[str],
        test_command: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        assignee: Optional[str] = None,
    ) -> "DAGWorkflowBuilder":
        """Add an autonomous, full-stack vertical slice (model/logic + interface + verification).

        Inspired by Shanraisshan's agentic engineering best practices:
        Avoid horizontal fragmentation; each slice delivers end-to-end verified capability.
        """
        criteria_bullets = "\n".join(f"- [ ] {c}" for c in acceptance_criteria)
        test_block = f"\n\n**Verification Suite:** `{test_command}`" if test_command else ""
        body = (
            f"### Vertical PRD Slice: {feature_name}\n\n"
            f"**Acceptance Criteria:**\n{criteria_bullets}"
            f"{test_block}\n\n"
            f"_Directive: Implement schema/model, logic, and automated tests. Do not declare complete until tests pass._"
        )
        return self.add_task(
            task_id=slice_id,
            title=f"Slice: {feature_name}",
            depends_on=depends_on,
            body=body,
            assignee=assignee,
        )

    def validate_acyclic(self) -> bool:
        """Kahn's algorithm / cycle detection."""
        in_degree = {t: 0 for t in self.tasks}
        graph: Dict[str, List[str]] = {t: [] for t in self.tasks}

        for t_id, spec in self.tasks.items():
            for parent in spec.depends_on:
                if parent in graph:
                    graph[parent].append(t_id)
                    in_degree[t_id] += 1
                elif parent not in self.tasks:
                    raise ValueError(f"Task '{t_id}' depends on unknown task '{parent}'")

        queue = [t for t, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for child in graph[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited != len(self.tasks):
            raise ValueError("Cyclic dependency detected in DAG workflow.")
        return True

    def deploy_to_kanban(self, conn: sqlite3.Connection, created_by: str = "haos-dag") -> List[str]:
        """Deploy the DAG tasks and links directly into the Kanban SQLite database."""
        self.validate_acyclic()
        now = int(time.time())
        created_ids = []

        # Create tasks
        for spec in self.tasks.values():
            # If no dependencies, start in 'ready', else start in 'todo'
            initial_status = "ready" if not spec.depends_on else "todo"
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    id, title, body, status, priority, created_by, created_at,
                    workspace_kind, assignee
                ) VALUES (?, ?, ?, ?, 1, ?, ?, 'scratch', ?)
                """,
                (spec.id, spec.title, spec.body, initial_status, created_by, now, spec.assignee),
            )
            created_ids.append(spec.id)

        # Create links (parent -> child)
        for spec in self.tasks.values():
            for parent_id in spec.depends_on:
                conn.execute(
                    "INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?, ?)",
                    (parent_id, spec.id),
                )

        conn.commit()
        logger.info("Deployed DAG '%s' with %d tasks to Kanban.", self.workflow_name, len(created_ids))
        return created_ids
