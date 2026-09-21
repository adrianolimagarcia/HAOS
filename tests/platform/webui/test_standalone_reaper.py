"""Testes para o auto-reparo de tarefas (reap_stale_tasks) no HAOS Standalone WebUI.

Valida que:
1. Tarefas 'in_progress' ou 'running' orfas (sem PID, sem claim, iniciadas ha > 600s)
   sao devidamente reparadas pelo supervisor sem levantar NameError (logger).
2. Tarefa com resultado valido em disco e marcada como 'done' e haos_task_meta refletido.
3. Tarefa sem resultado e marcada como 'failed' com razao explicita.
4. Tarefas ativas e recentes (started_at recente) NAO sao reparadas.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from hermes.platform.webui.standalone import HAOSStandaloneState


def test_reap_stale_orphan_task_without_nameerror():
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        state = HAOSStandaloneState(p)

        conn = state.kanban._connect()
        now = time.time()
        stale_time = int(now - 1200)  # 20 minutos atras

        # 1. Tarefa orfa em 'in_progress' sem resultado -> deve falhar
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, status, priority, created_by, created_at, started_at
            ) VALUES ('t_stale_orphan', 'Stale Task', 'in_progress', 1, 'haos-mayor', ?, ?)
            """,
            (stale_time, stale_time),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO haos_task_meta (task_id, spec_id, phase, posture, updated_at)
            VALUES ('t_stale_orphan', 't_stale_orphan', 'execution', 'worker', ?)
            """,
            (stale_time,),
        )

        # 2. Tarefa recente -> NAO deve ser reparada
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, status, priority, created_by, created_at, started_at
            ) VALUES ('t_recent_active', 'Active Task', 'in_progress', 1, 'haos-mayor', ?, ?)
            """,
            (int(now), int(now)),
        )

        conn.commit()

        # Executa reap_stale_tasks
        reaped = state.reap_stale_tasks()
        assert reaped == 1

        # Verifica t_stale_orphan
        row = conn.execute("SELECT status, last_failure_error FROM tasks WHERE id = 't_stale_orphan'").fetchone()
        assert row["status"] == "failed"
        assert "terminated unexpectedly" in (row["last_failure_error"] or "")

        meta = conn.execute("SELECT phase FROM haos_task_meta WHERE task_id = 't_stale_orphan'").fetchone()
        assert meta["phase"] == "failed"

        # Verifica t_recent_active
        row_recent = conn.execute("SELECT status FROM tasks WHERE id = 't_recent_active'").fetchone()
        assert row_recent["status"] == "in_progress"


def test_reap_stale_task_with_result_json_becomes_done():
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        state = HAOSStandaloneState(p)

        ws = p / "workspaces" / "t_with_res"
        (ws / ".haos").mkdir(parents=True, exist_ok=True)
        (ws / ".haos" / "result.json").write_text(json.dumps({"summary": "Sucesso total"}), encoding="utf-8")

        conn = state.kanban._connect()
        stale_time = int(time.time() - 2000)

        conn.execute(
            """
            INSERT INTO tasks (
                id, title, status, priority, created_by, created_at, started_at, workspace_path
            ) VALUES ('t_with_res', 'Done Task', 'in_progress', 1, 'haos-mayor', ?, ?, ?)
            """,
            (stale_time, stale_time, str(ws)),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO haos_task_meta (task_id, spec_id, phase, posture, updated_at)
            VALUES ('t_with_res', 't_with_res', 'execution', 'worker', ?)
            """,
            (stale_time,),
        )
        conn.commit()

        reaped = state.reap_stale_tasks()
        assert reaped == 1

        row = conn.execute("SELECT status, completed_at FROM tasks WHERE id = 't_with_res'").fetchone()
        assert row["status"] == "done"
        assert row["completed_at"] is not None

        meta = conn.execute("SELECT phase FROM haos_task_meta WHERE task_id = 't_with_res'").fetchone()
        assert meta["phase"] == "done"
