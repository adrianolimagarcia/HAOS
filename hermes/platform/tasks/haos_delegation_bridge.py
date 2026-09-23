"""HAOS Control Plane Bridge for Subagent Delegation.

Bridges Hermes native `delegate_task` executions directly into HAOS Kanban
(`kanban.db`) and EventStore (`events.db`), updating the taskboard and worker status.
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

def _kanban_db_is_real(candidate: Path) -> bool:
    """Um ``kanban.db`` de 0 bytes NÃO é board: SQLite vazio não tem schema nenhum.

    Aceitar o stub vazio é justamente o que permitia o store descartável de /tmp
    vencer o board real e desviar as delegações para um banco que ninguém lê.
    """
    db = candidate / "kanban.db"
    try:
        return db.is_file() and db.stat().st_size > 0
    except OSError:
        return False


def _get_haos_data_dir() -> Optional[Path]:
    """Find the active HAOS data dir where kanban.db and events.db live.

    Home DECLARADO confina a busca. Com ``HAOS_DATA_DIR``/``HAOS_HOME``/
    ``HERMES_HOME`` no ambiente, os candidatos saem só desse home — a função não
    sai adivinhando em ``~/.haos``, ``~/.hermes`` ou ``/tmp``.

    Isso não é preciosismo: ``Path.home()`` é o HOME do processo, e um processo
    com ``HERMES_HOME`` temporário (a suíte de testes) caía no ``~/.haos`` da
    máquina. Nesta VM esse caminho É o board de produção do control plane, então
    a adivinhação fazia os testes gravarem tarefas e eventos REAIS no board real.

    A ordem dentro do home declarado põe o engine dir canônico
    (``<hermes home>/haos``, o MESMO que o plugin do dashboard usa em
    ``_haos_engine_dir``) antes dos próprios homes — é ele que aponta para o
    board que o control plane despacha.

    Os candidatos especulativos (``~/.haos``, ``~/.hermes``, ``/tmp``) só entram
    quando NADA foi declarado.
    """
    candidates: List[Path] = []

    env_dir = (os.environ.get("HAOS_DATA_DIR") or "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    declared_homes = [
        raw for raw in ((os.environ.get(n) or "").strip() for n in ("HAOS_HOME", "HERMES_HOME")) if raw
    ]

    from hermes_constants import get_hermes_home  # function-level: lint A6

    candidates.append(Path(get_hermes_home()) / "haos")
    candidates.extend(Path(raw).expanduser() for raw in declared_homes)

    if not env_dir and not declared_homes:
        candidates.extend([
            Path.home() / ".haos",
            Path.home() / ".hermes",  # haos-legacy-path: cadeia de candidatos: le o store legado de proposito
            Path("/tmp/haos_shared_data"),
        ])

    seen: set[Path] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if _kanban_db_is_real(p):
            return p

    if not env_dir and not declared_homes:
        # Último recurso, e só sem home declarado: o store descartável de /tmp
        # serve se for gravável e, quando já houver kanban.db ali, ele não for o
        # stub vazio (um SQLite de 0 bytes não tem schema nenhum).
        tmp_haos = Path("/tmp/haos_shared_data")
        if tmp_haos.exists() and os.access(tmp_haos, os.W_OK):
            if not (tmp_haos / "kanban.db").exists() or _kanban_db_is_real(tmp_haos):
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
                        ) VALUES (?, ?, ?, ?, 'in_progress', 1, 'haos-the-eye', ?, ?, 'lane')
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
