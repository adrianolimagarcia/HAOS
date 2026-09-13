"""HAOS Control Plane Bridge for Subagent Delegation.

Bridges Hermes native `delegate_task` executions directly into HAOS Kanban
(`kanban.db`) and EventStore (`events.db`), updating the Team Graph and Taskboard
in real-time on http://<host>:8788/.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def _get_haos_data_dir() -> Optional[Path]:
    """Find the active HAOS data dir where kanban.db and events.db live."""
    env_dir = os.environ.get("HAOS_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend([
        Path("/tmp/haos_shared_data"),
        Path.home() / ".haos",
        Path.home() / ".hermes",
    ])
    for p in candidates:
        if (p / "kanban.db").exists():
            return p
    # Fallback to /tmp/haos_shared_data if writable
    tmp_haos = Path("/tmp/haos_shared_data")
    if tmp_haos.exists() and os.access(tmp_haos, os.W_OK):
        return tmp_haos
    return None


def haos_bridge_spawn_batch(batch: Any) -> None:
    """Record spawned subagent tasks into HAOS kanban.db and events.db."""
    try:
        data_dir = _get_haos_data_dir()
        if not data_dir:
            return

        kanban_path = data_dir / "kanban.db"
        events_path = data_dir / "events.db"
        deleg_id = getattr(batch, "live_deleg_id", "del_anon")
        task_list = getattr(batch, "task_list", [])
        now = time.time()
        now_int = int(now)

        if kanban_path.exists():
            conn = sqlite3.connect(str(kanban_path), timeout=3.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                for idx, t in enumerate(task_list):
                    task_id = f"t_{deleg_id[:6]}_{idx+1}"
                    goal = str(t.get("goal") or t.get("task", f"Subagent Task {idx+1}"))
                    assignee = f"polecat-{idx+1}"
                    cursor.execute("""
                        INSERT OR REPLACE INTO tasks (
                            id, title, body, assignee, status, priority,
                            created_by, created_at, started_at, workspace_kind
                        ) VALUES (?, ?, ?, ?, 'in_progress', 1, 'haos-mayor', ?, ?, 'lane')
                    """, (task_id, goal[:120], goal, assignee, now_int, now_int))
                    
                    # Also insert into haos_task_meta if available
                    spec_json = json.dumps({"title": goal, "task_id": task_id, "assignee": assignee})
                    cursor.execute("""
                        INSERT OR REPLACE INTO haos_task_meta (
                            task_id, spec_id, spec_json, phase, posture, updated_at
                        ) VALUES (?, ?, ?, 'execution', 'worker', ?)
                    """, (task_id, task_id, spec_json, now))
                conn.commit()
            finally:
                conn.close()

        if events_path.exists():
            econn = sqlite3.connect(str(events_path), timeout=3.0)
            ec = econn.cursor()
            try:
                for idx, t in enumerate(task_list):
                    task_id = f"t_{deleg_id[:6]}_{idx+1}"
                    goal = str(t.get("goal") or t.get("task", ""))
                    ev_id = str(uuid.uuid4())
                    trace_id = str(uuid.uuid4())
                    payload = json.dumps({
                        "task_id": task_id,
                        "goal": goal,
                        "assignee": f"polecat-{idx+1}",
                        "status": "in_progress",
                    }, ensure_ascii=False)
                    ec.execute("""
                        INSERT INTO events (
                            event_id, seq, name, trace_id, correlation_id,
                            trust_level, schema_version, timestamp, payload
                        ) VALUES (
                            ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM events),
                            'haos.task.spawned', ?, ?, 'internal', 1, ?, ?
                        )
                    """, (ev_id, trace_id, task_id, now, payload))
                econn.commit()
            finally:
                econn.close()
    except Exception as exc:
        logger.debug("haos_bridge_spawn_batch ignored exception: %s", exc)


def haos_bridge_child_done(live_deleg_id: str, task_index: int, entry: Dict[str, Any]) -> None:
    """Record child task completion into HAOS kanban.db and events.db."""
    try:
        data_dir = _get_haos_data_dir()
        if not data_dir:
            return

        kanban_path = data_dir / "kanban.db"
        events_path = data_dir / "events.db"
        now = time.time()
        now_int = int(now)
        task_id = f"t_{live_deleg_id[:6]}_{task_index+1}"
        status = entry.get("status", "completed")
        kb_status = "done" if status == "completed" else "failed"

        if kanban_path.exists():
            conn = sqlite3.connect(str(kanban_path), timeout=3.0)
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE tasks
                    SET status = ?, completed_at = ?
                    WHERE id = ?
                """, (kb_status, now_int, task_id))
                
                result_json = json.dumps(entry, ensure_ascii=False)
                cursor.execute("""
                    UPDATE haos_task_meta
                    SET phase = 'done', result_json = ?, updated_at = ?
                    WHERE task_id = ?
                """, (result_json, now, task_id))
                conn.commit()
            finally:
                conn.close()

        if events_path.exists():
            econn = sqlite3.connect(str(events_path), timeout=3.0)
            ec = econn.cursor()
            try:
                ev_id = str(uuid.uuid4())
                trace_id = str(uuid.uuid4())
                payload = json.dumps({
                    "task_id": task_id,
                    "status": kb_status,
                    "duration_seconds": entry.get("duration_seconds", 0),
                    "summary": str(entry.get("summary", ""))[:300],
                }, ensure_ascii=False)
                ec.execute("""
                    INSERT INTO events (
                        event_id, seq, name, trace_id, correlation_id,
                        trust_level, schema_version, timestamp, payload
                    ) VALUES (
                        ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM events),
                        'haos.task.completed', ?, ?, 'internal', 1, ?, ?
                    )
                """, (ev_id, trace_id, task_id, now, payload))
                econn.commit()
            finally:
                econn.close()
    except Exception as exc:
        logger.debug("haos_bridge_child_done ignored exception: %s", exc)
