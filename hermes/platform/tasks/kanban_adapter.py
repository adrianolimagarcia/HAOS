"""KanbanAdapter v2 — HAOS extension layer over the UPSTREAM Hermes Kanban store.

v1.1 decision D2: **there is no second ledger.** The canonical task lifecycle —
``tasks``, ``task_links``, ``task_runs``, ``task_events``, atomic claims
(WAL + BEGIN IMMEDIATE + CAS), heartbeat, stale-claim reclaim, review and
completion — is owned by upstream ``hermes_cli.kanban_db`` /
``hermes_cli.kanban_db_connect``. This adapter only:

* maps a HAOS ``TaskSpec`` onto an upstream task card (``create_task`` with
  parent links and idempotency, then claim/heartbeat/complete/review through
  the upstream state machine), and
* keeps HAOS-ONLY metadata — spec JSON, phase/posture/revision, and the HAOS
  ``TaskRun``/``TaskResult`` presentation snapshots — in ONE additive table
  (``haos_task_meta``) *inside the same canonical database file*.

It never owns status/heartbeat/lease state of its own. Task ids returned are
the canonical upstream ids (``t_...``); lookups accept either the upstream id
or the HAOS spec id (``T-...``), resolved through the meta table.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.execution_plan import ExecutionPlan
from hermes.platform.tasks.failure_classifier import FailureClassifier
from hermes.platform.execution.runs import TaskRun, TaskResult

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd

# HAOS workspace_type -> upstream workspace_kind.
_WORKSPACE_KIND = {"git_worktree": "worktree", "worktree": "worktree", "scratch": "scratch", "local": "dir"}


class KanbanAdapter:
    """Thin HAOS extension over the canonical upstream Kanban store."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        *,
        board: Optional[str] = None,
        event_sink: Optional[Any] = None,
    ):
        """Open the canonical Kanban DB.

        ``db_path`` explicit file path (default: upstream resolution: board /
        ``HERMES_KANBAN_BOARD`` / current / default). ``":memory:"`` is not a
        real Kanban; tests must pass a temp file path.
        ``event_sink`` (opcional) EventStoreSink para projeção de eventos no EventStore HAOS.
        """
        if db_path == ":memory:":
            raise ValueError(
                "KanbanAdapter wraps the canonical upstream Kanban DB, which is "
                "file-backed; pass a temp file path for tests (:memory: removed)."
            )
        self.db_path = Path(db_path) if db_path is not None else None
        self.board = board
        self.event_sink = event_sink
        self._local = threading.local()  # conexão por thread (threadpool-safe)

    # ------------------------------------------------------------------ #
    # connection / schema
    # ------------------------------------------------------------------ #
    def _connect(self):
        # Uma conexão POR THREAD (não uma única conexão global): um dashboard
        # FastAPI roda handlers sync num threadpool, e o sqlite3 upstream é
        # thread-bound (check_same_thread). O plugin kanban do próprio upstream
        # abre uma conexão por request; aqui cacheamos por thread para o mesmo
        # efeito com menos open/close. O registro sqlite_safe_read é um
        # contador — várias conexões vivas ao mesmo arquivo são suportadas.
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self.db_path is not None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = kbc.connect(db_path=self.db_path, board=self.board)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_task_meta (
                    task_id     TEXT PRIMARY KEY,
                    spec_id     TEXT,
                    spec_json   TEXT,
                    phase       TEXT,
                    posture     TEXT,
                    revision    INTEGER,
                    run_json    TEXT,
                    result_json TEXT,
                    plan_json   TEXT,
                    updated_at  REAL
                )
            """)
            try:
                conn.execute("ALTER TABLE haos_task_meta ADD COLUMN plan_json TEXT")
            except Exception:
                pass  # Já existe a coluna em bases prévias
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_meta_spec ON haos_task_meta(spec_id)")
            # Tabelas aditivas para histórico de runs, artefatos duráveis e eventos de execução
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_task_runs (
                    run_uid     TEXT PRIMARY KEY,
                    task_id     TEXT NOT NULL,
                    attempt     INTEGER DEFAULT 1,
                    worker_id   TEXT,
                    posture_id  TEXT,
                    model       TEXT,
                    provider    TEXT,
                    lane        TEXT,
                    status      TEXT DEFAULT 'running',
                    exit_reason TEXT,
                    assignment_snapshot TEXT,
                    started_at  REAL,
                    ended_at    REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_runs_task ON haos_task_runs(task_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_task_artifacts (
                    artifact_id  TEXT PRIMARY KEY,
                    task_id      TEXT NOT NULL,
                    run_uid      TEXT,
                    type         TEXT NOT NULL,
                    uri          TEXT NOT NULL,
                    summary      TEXT,
                    metadata_json TEXT,
                    created_at   REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_artifacts_task ON haos_task_artifacts(task_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_run_events (
                    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     TEXT NOT NULL,
                    run_uid     TEXT,
                    event_type  TEXT NOT NULL,
                    payload_json TEXT,
                    created_at  REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_events_task ON haos_run_events(task_id)")
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ------------------------------------------------------------------ #
    # id resolution: upstream t_ id  <->  HAOS spec id
    # ------------------------------------------------------------------ #
    def _resolve_task_id(self, key: str) -> Optional[str]:
        conn = self._connect()
        if kb.get_task(conn, key) is not None:
            return key
        row = conn.execute("SELECT task_id FROM haos_task_meta WHERE spec_id = ?", (key,)).fetchone()
        return row["task_id"] if row else None

    # ------------------------------------------------------------------ #
    # task creation / spec persistence
    # ------------------------------------------------------------------ #
    def save_task(self, spec: TaskSpec, status: str = "READY", phase: str = "triage",
                  assignee: Optional[str] = None) -> str:
        """Create the upstream card (ready/todo/triage per upstream rules) and
        persist the HAOS spec. Returns the canonical upstream task id."""
        conn = self._connect()
        existing = self._resolve_task_id(spec.id)

        if existing is not None:
            # Spec revision update only — canonical status lives upstream.
            self._upsert_meta(existing, spec, phase=phase)
            return existing

        workspace_kind = _WORKSPACE_KIND.get(spec.workspace_type, "scratch")
        # Map HAOS spec ids -> canonical upstream task ids (drop any unresolved).
        parents = [
            resolved for p in (spec.requires_tasks or [])
            if (resolved := self._resolve_task_id(p)) is not None
        ]
        if len(parents) != len(spec.requires_tasks or []):
            # Guard: upstream create_task rejects unknown parent ids.
            unresolved = sorted(
                set(spec.requires_tasks or [])
                - {p for p in (spec.requires_tasks or []) if self._resolve_task_id(p)}
            )
            raise ValueError(
                f"requires_tasks of {spec.id} reference tasks not present in the "
                f"Kanban DB: {unresolved}"
            )

        body = spec.goal or spec.description or spec.title
        task_id = kb.create_task(
            conn,
            title=spec.title,
            body=body,
            priority=spec.priority,
            created_by=spec.created_by,
            assignee=assignee,
            parents=parents,
            workspace_kind=workspace_kind,
            skills=spec.preferred_skills or None,
            idempotency_key=f"haos:{spec.id}:v{spec.version}",
            triage=(status == "TRIAGE"),
            initial_status="blocked" if status == "BLOCKED" else "running",
            max_runtime_seconds=int(spec.max_runtime_minutes * 60) if spec.max_runtime_minutes else None,
        )
        self._upsert_meta(task_id, spec, phase=phase)
        return task_id

    upsert_task = save_task

    def _upsert_meta(self, task_id: str, spec: TaskSpec, phase: str = "triage") -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO haos_task_meta (task_id, spec_id, spec_json, phase, posture, revision, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                spec_id=excluded.spec_id,
                spec_json=excluded.spec_json,
                phase=excluded.phase,
                posture=excluded.posture,
                revision=excluded.revision,
                updated_at=excluded.updated_at
            """,
            (
                task_id, spec.id,
                json.dumps(_spec_dict(spec)),
                phase, spec.posture, spec.version,
                time.time(),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # lifecycle (canonical state machine, HAOS run/result snapshots)
    # ------------------------------------------------------------------ #
    def claim_task(
        self,
        task_id_or_spec: str,
        worker_id: Optional[str] = None,
        lease_duration_sec: Optional[int] = None,
        run: Optional[TaskRun] = None,
    ) -> bool:
        """Atomically claim (ready -> running). Records the HAOS TaskRun
        snapshot for the canonical run on success."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        claimed = kb.claim_task(conn, task_id, claimer=worker_id, ttl_seconds=lease_duration_sec)
        if claimed is None:
            return False
        run = run or TaskRun(task_id=task_id, worker_id=worker_id)
        run.run_id = claimed.current_run_id
        run.started_at = float(claimed.started_at or time.time())
        self._store_run(task_id, run)
        return True

    def heartbeat(self, task_id_or_spec: str, worker_id: Optional[str] = None) -> bool:
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        result = kb.heartbeat_claim(conn, task_id, claimer=worker_id)
        ok = bool(result)
        if ok:
            self._touch_run(task_id)
        return ok

    def record_run_start(self, task_id_or_spec: str, *, run_id: Optional[int],
                         worker_id: Optional[str] = None) -> TaskRun:
        """Registra o snapshot HAOS de um run canônico recém-claimado (usado
        por caminhos que claimam via upstream diretamente, ex.: claim_tick)."""
        task_id = self._require_resolved(task_id_or_spec)
        spec = (self.get_task(task_id) or {}).get("spec") or {}
        run = TaskRun(
            task_id=task_id,
            run_id=run_id,
            worker_id=worker_id,
            posture_id=spec.get("posture"),
            model_profile_id=spec.get("model_profile"),
            started_at=float(time.time()),
        )
        self._store_run(task_id, run)
        return run

    def release_stale_claims(self) -> int:
        return kb.release_stale_claims(self._connect())

    def record_review_verdict(
        self,
        task_id_or_spec: str,
        verdict: str,
        *,
        approver: str,
        rationale: Optional[str] = None,
        acceptance_status: Optional[str] = None,
    ) -> TaskResult:
        """Registra o veredito de review humano num card (Task != Run != Result).

        Atualiza o ``TaskResult`` canônico associado ao card com o veredito
        (ex.: ``approved`` | ``changes_requested``) e estágio de aceite
        (``passed`` quando ``approved`` por padrão), mantendo summary, evidence
        e artifacts originais.
        """
        if verdict not in ("approved", "changes_requested"):
            raise ValueError(f"Veredito de review inválido: {verdict!r} (esperado approved|changes_requested)")
        if not approver or not str(approver).strip():
            raise ValueError("approver é obrigatório para registrar veredito de review")
        task_id = self._require_resolved(task_id_or_spec)
        res = self._load_result(task_id)
        if res is None:
            # Cria TaskResult vazio caso o card ainda não tenha resultado registrado
            res = TaskResult(task_id=task_id, summary="", completed_at=time.time())
        res.reviewer_verdict = verdict
        acc_status = acceptance_status or ("passed" if verdict == "approved" else "failed")
        acceptance = list(res.acceptance or [])
        acceptance.append({
            "stage": "human_review",
            "approver": str(approver).strip(),
            "status": acc_status,
            "rationale": rationale or "",
            "timestamp": time.time(),
        })
        res.acceptance = acceptance
        self._store_result(task_id, res)

        if self.event_sink is not None and getattr(self.event_sink, "available", lambda: True)():
            try:
                from hermes.platform.observability.events import Event
                self.event_sink.append_from_sync(Event(
                    name="task.run.reviewed",
                    payload={
                        "task_id": task_id,
                        "verdict": verdict,
                        "approver": approver,
                        "rationale": rationale or "",
                        "acceptance_status": acc_status,
                    },
                    correlation_id=task_id,
                    trust_level="internal",
                ))
            except Exception:
                pass

        return res

    def complete_task(
        self,
        task_id_or_spec: str,
        *,
        result: Optional[str] = None,
        summary: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        artifacts: Optional[Iterable[str]] = None,
        residual_risk: Optional[Iterable[str]] = None,
        acceptance: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Complete the task (running|review -> done) and record the HAOS
        TaskResult + ended TaskRun snapshots."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        ok = kb.complete_task(
            conn, task_id,
            result=result or summary or "",
            summary=summary or result or "",
            metadata={
                "haos_evidence": evidence or {},
                "haos_acceptance": acceptance or [],
            },
        )
        if ok:
            run = self._load_run(task_id)
            if run is not None:
                run.end("accepted")
                self._store_run(task_id, run)
            task_result = TaskResult(
                task_id=task_id,
                run_id=run.run_id if run else None,
                summary=summary or result or "",
                artifacts=list(artifacts or []),
                evidence=evidence or {},
                residual_risk=list(residual_risk or []),
                acceptance=acceptance or [],
                completed_at=time.time(),
            )
            # Check evidence before auto-approving
            test_ev = task_result.evidence.get("tests", {}) if isinstance(task_result.evidence, dict) else {}
            has_failed_tests = isinstance(test_ev, dict) and (test_ev.get("failed", 0) > 0 or test_ev.get("errors", 0) > 0)

            if not acceptance and task_result.reviewer_verdict != "rejected":
                if has_failed_tests:
                    task_result.reviewer_verdict = "rejected"
                    task_result.acceptance = [{
                        "stage": "auto_accept",
                        "status": "failed",
                        "approver": "engine:auto",
                        "timestamp": task_result.completed_at,
                        "rationale": f"Rejected due to failing tests in evidence: {test_ev}",
                    }]
                    self._store_result(task_id, task_result)
                    return False
                else:
                    task_result.reviewer_verdict = "approved"
                    task_result.acceptance = [{
                        "stage": "auto_accept",
                        "status": "passed",
                        "approver": "engine:auto",
                        "timestamp": task_result.completed_at,
                    }]
            self._store_result(task_id, task_result)

            if self.event_sink is not None and getattr(self.event_sink, "available", lambda: True)():
                try:
                    from hermes.platform.observability.events import Event
                    self.event_sink.append_from_sync(Event(
                        name="task.run.completed",
                        payload={
                            "task_id": task_id,
                            "summary": task_result.summary,
                            "artifacts": task_result.artifacts,
                            "model": run.model_profile_id if run else None,
                            "posture": run.posture_id if run else None,
                        },
                        correlation_id=task_id,
                        trust_level="internal",
                    ))
                except Exception:
                    pass
        return ok

    def record_task_failure(
        self,
        task_id_or_spec: str,
        error: str,
        *,
        outcome: str = "worker_crash",
        failure_limit: Optional[int] = None,
    ) -> bool:
        """Registra falha de execução com orçamento do kernel (Fase 1 hardening).

        Delega ao ``_record_task_failure`` do dispatcher upstream com
        ``release_claim=True, end_run=True``: restaura a fase de origem (ou
        ``blocked`` quando o breaker trip), libera o claim, incrementa o
        contador unificado ``consecutive_failures`` (auto-block ao atingir o
        limite) e fecha o run canônico. Fecha também o snapshot HAOS TaskRun
        com o exit_reason. Retorna True quando o card foi auto-bloqueado.
        """
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        blocked = kbd._record_task_failure(
            conn, task_id, error[:500],
            outcome=outcome, failure_limit=failure_limit,
            release_claim=True, end_run=True,
        )
        run = self._load_run(task_id)
        if run is not None:
            run.end(outcome)
            self._store_run(task_id, run)

        # Emissão de evento estruturado com classificação de falhas (11 categorias) no EventStore
        if self.event_sink is not None and getattr(self.event_sink, "available", lambda: True)():
            try:
                from hermes.platform.observability.events import Event
                failure_cat = FailureClassifier.classify(error, metadata={"outcome": outcome})
                retry_action = FailureClassifier.retry_policy_for(failure_cat)
                self.event_sink.append_from_sync(Event(
                    name="task.run.failure",
                    payload={
                        "task_id": task_id,
                        "error": str(error)[:500],
                        "outcome": outcome,
                        "failure_category": failure_cat,
                        "retry_action": retry_action,
                        "blocked": blocked,
                        "model": run.model_profile_id if run else None,
                        "posture": run.posture_id if run else None,
                    },
                    correlation_id=task_id,
                    trust_level="internal",
                ))
            except Exception:
                pass

        return blocked

    def record_task_blocked(
        self,
        task_id_or_spec: str,
        *,
        blocker_kind: str,
        reason: str,
        evidence: Optional[List[str]] = None,
    ) -> bool:
        """Registra bloqueio formal e tipado de uma tarefa (inspirado em ByteDance DeerFlow 2.0).

        Transiciona a tarefa para status 'blocked' com categoria tipada
        (missing_evidence, needs_user_input, run_failed, external_wait, goal_not_met_yet)
        e evidências rastreáveis.
        """
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        from hermes.platform.execution.goal_blockers import GoalBlockerEvaluator
        typed_kind = GoalBlockerEvaluator.classify_blocker_reason(blocker_kind)
        ev_list = list(evidence or [])
        blocker_meta = {
            "kind": typed_kind.value,
            "reason": reason,
            "evidence": ev_list,
            "blocked_at": time.time(),
        }

        # Atualiza status no Kanban upstream
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status='blocked' WHERE id=?", (task_id,))
        conn.commit()

        # Atualiza haos_task_meta com blocker estruturado
        cursor.execute("SELECT spec_json FROM haos_task_meta WHERE task_id=?", (task_id,))
        row = cursor.fetchone()
        if row and row[0]:
            try:
                spec_data = json.loads(row[0])
                spec_data["blocker"] = blocker_meta
                cursor.execute(
                    "UPDATE haos_task_meta SET spec_json=?, phase='blocked', updated_at=? WHERE task_id=?",
                    (json.dumps(spec_data), time.time(), task_id),
                )
                conn.commit()
            except Exception:
                pass

        if self.event_sink is not None and getattr(self.event_sink, "available", lambda: True)():
            try:
                from hermes.platform.observability.events import Event
                self.event_sink.append_from_sync(Event(
                    name="task.run.blocked",
                    payload={
                        "task_id": task_id,
                        "blocker_kind": typed_kind.value,
                        "reason": reason,
                        "evidence": ev_list,
                    },
                    correlation_id=task_id,
                    trust_level="internal",
                ))
            except Exception:
                pass

        return True

    def request_review(self, task_id_or_spec: str, *, reviewer: Optional[str] = None,
                       summary: Optional[str] = None) -> bool:
        """running/ready -> review (canonical review state machine)."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        return bool(kb.request_review(conn, task_id, reviewer=reviewer, summary=summary))

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def get_task(self, task_id_or_spec: str) -> Optional[Dict[str, Any]]:
        """Canonical card + HAOS spec/phase/run/result, as a plain dict."""
        task_id = self._require_resolved(task_id_or_spec)
        task = kb.get_task(self._connect(), task_id)
        if task is None:
            return None
        meta = self._meta_row(task_id)
        run = self._load_run(task_id)
        result = self._load_result(task_id)
        evidence = (result.evidence if result and hasattr(result, "evidence") else {}) or {}
        tokens = evidence.get("tokens") or (run.snapshot.get("tokens") if run and hasattr(run, "snapshot") and run.snapshot else 0) or 0
        cost = evidence.get("cost") or (run.snapshot.get("cost") if run and hasattr(run, "snapshot") and run.snapshot else 0.0) or 0.0
        created_at = getattr(task, "created_at", 0.0) or 0.0
        started_at = getattr(task, "started_at", None) or (run.started_at if run else None) or created_at
        completed_at = getattr(task, "completed_at", None) or (result.completed_at if result else None)
        now = time.time()
        if completed_at and completed_at > 0:
            elapsed_seconds = max(0.0, completed_at - (started_at or created_at))
        elif started_at and started_at > 0 and str(task.status).lower() in ("in_progress", "running"):
            elapsed_seconds = max(0.0, now - started_at)
        else:
            elapsed_seconds = 0.0

        return {
            "id": task.id,
            "spec_id": (meta or {}).get("spec_id"),
            "title": task.title,
            "status": task.status,
            "assignee": task.assignee,
            "priority": task.priority,
            "phase": (meta or {}).get("phase", "triage"),
            "posture": (meta or {}).get("posture"),
            "workspace_kind": task.workspace_kind,
            "workspace_path": getattr(task, "workspace_path", None),
            "current_run_id": task.current_run_id,
            "claim_lock": getattr(task, "claim_lock", None),
            "consecutive_failures": getattr(task, "consecutive_failures", 0),
            "spec": json.loads((meta or {}).get("spec_json") or "{}"),
            "plan": self.load_execution_plan(task_id),
            "run": run,
            "result": result,
            "created_at": created_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(elapsed_seconds, 1),
            "tokens": int(tokens),
            "cost": float(cost),
            "blocker": json.loads((meta or {}).get("spec_json") or "{}").get("blocker"),
        }

    def list_tasks(self, *, status: Optional[str] = None,
                   include_archived: bool = False) -> List[Dict[str, Any]]:
        conn = self._connect()
        tasks = kb.list_tasks(conn, status=status, include_archived=include_archived)
        res = []
        for t in tasks:
            meta = self._meta_row(t.id) or {}
            spec_dict = json.loads(meta.get("spec_json") or "{}")
            res.append({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "assignee": t.assignee,
                "priority": t.priority,
                "phase": meta.get("phase"),
                "blocker": spec_dict.get("blocker"),
            })
        return res

    def get_run(self, task_id_or_spec: str) -> Optional[TaskRun]:
        return self._load_run(self._require_resolved(task_id_or_spec))

    def get_result(self, task_id_or_spec: str) -> Optional[TaskResult]:
        return self._load_result(self._require_resolved(task_id_or_spec))

    def set_phase(self, task_id_or_spec: str, phase: str) -> None:
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        conn.execute("UPDATE haos_task_meta SET phase = ?, updated_at = ? WHERE task_id = ?",
                     (phase, time.time(), task_id))
        conn.commit()

    def clear_completed_tasks(self, include_failed: bool = False) -> int:
        """Limpa tarefas concluídas, arquivadas ou com falhas do banco Kanban."""
        conn = self._connect()
        statuses = ["done", "completed", "archived", "cancelled"]
        if include_failed:
            statuses.extend(["failed", "error"])
        placeholders = ",".join(f"'{s}'" for s in statuses)
        cur = conn.execute(f"SELECT id FROM tasks WHERE LOWER(status) IN ({placeholders})")
        tids = [r[0] for r in cur.fetchall()]
        count = 0
        for tid in tids:
            try:
                kb.delete_task(conn, tid)
                count += 1
            except Exception:
                pass
        try:
            conn.execute("DELETE FROM haos_task_meta WHERE task_id NOT IN (SELECT id FROM tasks)")
            conn.execute("DELETE FROM haos_task_runs WHERE task_id NOT IN (SELECT id FROM tasks)")
            conn.execute("DELETE FROM haos_task_artifacts WHERE task_id NOT IN (SELECT id FROM tasks)")
            conn.execute("DELETE FROM haos_run_events WHERE task_id NOT IN (SELECT id FROM tasks)")
            conn.commit()
        except Exception:
            pass
        return count

    def delete_task(self, task_id_or_spec: str) -> bool:
        """Exclui um card específico do Kanban e seus metadados associados."""
        try:
            task_id = self._resolve_task_id(task_id_or_spec) or task_id_or_spec
            conn = self._connect()
            try:
                kb.delete_task(conn, task_id)
            except Exception:
                pass
            conn.execute("DELETE FROM haos_task_meta WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM haos_task_runs WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM haos_task_artifacts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM haos_run_events WHERE task_id = ?", (task_id,))
            conn.commit()
            return True
        except Exception:
            return False

    def reset_all_tasks(self) -> int:
        """Limpa absolutamente todas as tarefas do Kanban (reset completo)."""
        conn = self._connect()
        cur = conn.execute("SELECT id FROM tasks")
        tids = [r[0] for r in cur.fetchall()]
        count = 0
        for tid in tids:
            try:
                kb.delete_task(conn, tid)
                count += 1
            except Exception:
                pass
        try:
            conn.execute("DELETE FROM haos_task_meta")
            conn.execute("DELETE FROM haos_task_runs")
            conn.execute("DELETE FROM haos_task_artifacts")
            conn.execute("DELETE FROM haos_run_events")
            conn.commit()
        except Exception:
            pass
        return count

    # ------------------------------------------------------------------ #
    # meta internals
    # ------------------------------------------------------------------ #
    def _require_resolved(self, key: str) -> str:
        task_id = self._resolve_task_id(key)
        if task_id is None:
            raise KeyError(f"Unknown task '{key}' (not in canonical Kanban DB nor haos_task_meta)")
        return task_id

    def _meta_row(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._connect().execute(
            "SELECT * FROM haos_task_meta WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def _store_run(self, task_id: str, run: TaskRun) -> None:
        conn = self._connect()
        run_data = run.to_dict()
        conn.execute(
            "UPDATE haos_task_meta SET run_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(run_data), time.time(), task_id),
        )
        # Adicionalmente persiste no histórico de runs com chave única determinística/uid
        run_uid = f"{task_id}:run:{run.run_id or int(run.started_at)}"
        attempt = getattr(run, "attempt", 1)
        conn.execute(
            """
            INSERT INTO haos_task_runs (
                run_uid, task_id, attempt, worker_id, posture_id, model, provider,
                lane, status, exit_reason, assignment_snapshot, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_uid) DO UPDATE SET
                status=excluded.status,
                exit_reason=excluded.exit_reason,
                ended_at=excluded.ended_at
            """,
            (
                run_uid, task_id, attempt, run.worker_id, run.posture_id,
                run.resolved_model,
                (run.provider_chain[0].get("provider") if run.provider_chain else None),
                run.lane, run.status, run.exit_reason,
                json.dumps(run.snapshot or {}), run.started_at,
                time.time() if run.status == "ended" else None,
            )
        )
        conn.commit()

    def record_artifact(self, task_id_or_spec: str, artifact_id: str, artifact_type: str,
                        uri: str, summary: str = "", metadata: Optional[Dict[str, Any]] = None,
                        run_uid: Optional[str] = None) -> None:
        """Salva um artefato produzido pela tarefa no ledger durável."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO haos_task_artifacts (
                artifact_id, task_id, run_uid, type, uri, summary, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                uri=excluded.uri, summary=excluded.summary, metadata_json=excluded.metadata_json
            """,
            (
                artifact_id, task_id, run_uid, artifact_type, uri,
                summary, json.dumps(metadata or {}), time.time()
            )
        )
        conn.commit()

    def list_artifacts(self, task_id_or_spec: str) -> List[Dict[str, Any]]:
        """Lista artefatos associados à tarefa."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM haos_task_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def record_run_event(self, task_id_or_spec: str, event_type: str,
                         payload: Optional[Dict[str, Any]] = None,
                         run_uid: Optional[str] = None) -> None:
        """Registra um evento durável de execução de tarefa/run na tabela aditiva e opcionalmente no EventStore."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO haos_run_events (task_id, run_uid, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, run_uid, event_type, json.dumps(payload or {}), time.time())
        )
        conn.commit()

        # Projeção fail-safe no EventStore via sink se disponível
        if self.event_sink is not None and getattr(self.event_sink, "available", lambda: True)():
            try:
                from hermes.platform.observability.events import Event
                self.event_sink.append_from_sync(Event(
                    name=f"task.run.{event_type}",
                    payload=payload or {},
                    correlation_id=task_id,
                    causation_id=run_uid or "",
                    trust_level="internal",
                ))
            except Exception:
                pass

    def list_run_events(self, task_id_or_spec: str) -> List[Dict[str, Any]]:
        """Recupera a linha do tempo de eventos de uma tarefa."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM haos_run_events WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _store_result(self, task_id: str, result: TaskResult) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE haos_task_meta SET result_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(result.to_dict()), time.time(), task_id),
        )
        conn.commit()

    def _load_run(self, task_id: str) -> Optional[TaskRun]:
        meta = self._meta_row(task_id)
        if not meta or not meta.get("run_json"):
            return None
        return TaskRun.from_dict(json.loads(meta["run_json"]))

    def _load_result(self, task_id: str) -> Optional[TaskResult]:
        meta = self._meta_row(task_id)
        if not meta or not meta.get("result_json"):
            return None
        raw = json.loads(meta["result_json"])
        if isinstance(raw, dict) and not raw.get("task_id"):
            raw["task_id"] = task_id
        return TaskResult.from_dict(raw)

    def _load_plan(self, task_id: str) -> Optional[ExecutionPlan]:
        meta = self._meta_row(task_id)
        if not meta or not meta.get("plan_json"):
            return None
        return ExecutionPlan.from_dict(json.loads(meta["plan_json"]))

    def _touch_run(self, task_id: str) -> None:
        run = self._load_run(task_id)
        if run is not None:
            run.heartbeat_at = time.time()
            self._store_run(task_id, run)

    # ------------------------------------------------------------------ #
    # execution plan persistence
    # ------------------------------------------------------------------ #
    def store_execution_plan(self, task_id_or_spec: str, plan: ExecutionPlan) -> None:
        """Salva o plano de execução associado à tarefa."""
        task_id = self._require_resolved(task_id_or_spec)
        conn = self._connect()
        conn.execute(
            "UPDATE haos_task_meta SET plan_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(plan.to_dict()), time.time(), task_id),
        )
        conn.commit()

    def load_execution_plan(self, task_id_or_spec: str) -> Optional[ExecutionPlan]:
        """Carrega o plano de execução associado à tarefa se existir."""
        task_id = self._require_resolved(task_id_or_spec)
        meta = self._meta_row(task_id)
        if not meta or not meta.get("plan_json"):
            return None
        return ExecutionPlan.from_dict(json.loads(meta["plan_json"]))


def _spec_dict(spec: TaskSpec) -> Dict[str, Any]:
    """JSON-safe TaskSpec payload (keep nested dataclasses simple)."""
    out = {
        "id": spec.id, "title": spec.title, "goal": spec.goal,
        "description": spec.description, "team_id": spec.team_id,
        "created_by": spec.created_by, "parent_id": spec.parent_id,
        "priority": spec.priority, "risk_level": spec.risk_level,
        "task_class": spec.task_class, "posture": spec.posture,
        "strategy": spec.strategy, "model_profile": spec.model_profile,
        "required_capabilities": list(spec.required_capabilities),
        "preferred_capabilities": list(spec.preferred_capabilities),
        "required_modalities": list(spec.required_modalities),
        "preferred_skills": list(spec.preferred_skills),
        "mcp_packs": list(spec.mcp_packs),
        "context_files": list(spec.context_files),
        "context_decisions": list(spec.context_decisions),
        "context_symbols": list(spec.context_symbols),
        "requires_tasks": list(spec.requires_tasks),
        "informs_tasks": list(spec.informs_tasks),
        "typed_dependencies": [d.__dict__ for d in getattr(spec, "typed_dependencies", [])],
        "optional_modalities": list(getattr(spec, "optional_modalities", [])),
        "context_memory_queries": list(getattr(spec, "context_memory_queries", [])),
        "workspace_type": spec.workspace_type,
        "acceptance_criteria": [a.__dict__ for a in spec.acceptance_criteria],
        "review_stages": [r.__dict__ for r in spec.review_stages],
        "max_cost_usd": spec.max_cost_usd,
        "max_runtime_minutes": spec.max_runtime_minutes,
        "max_runs": spec.max_runs,
        "max_child_tasks": getattr(spec, "max_child_tasks", 8),
        "allow_posture_switch": getattr(spec, "allow_posture_switch", True),
        "allow_delegation": getattr(spec, "allow_delegation", True),
        "allow_child_tasks": getattr(spec, "allow_child_tasks", True),
        "expected_artifacts": list(getattr(spec, "expected_artifacts", [])),
        "tags": list(getattr(spec, "tags", [])),
        "version": spec.version,
        # Contrato tipado de I/O (Fase 1): JSON round-trippable (None == vazio).
        "task_contract": spec.task_contract.to_dict() if spec.task_contract else None,
        # Emendas 8/9 (persistência para o claim path reconstruir).
        "required_agents": list(spec.required_agents),
        "preferred_agents": list(spec.preferred_agents),
        "model_profile_preferred": spec.model_profile_preferred,
        # A4: forma de execução pedida (none|persistent|orchestrator).
        "reuse": spec.reuse,
    }
    return out
