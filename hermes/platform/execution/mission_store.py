"""Durable additive mission metadata for the canonical Kanban SQLite database."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class MissionStore:
    """Transactional mission metadata store sharing the canonical Kanban DB."""

    def __init__(self, db_path: Union[str, Path], profile_key: str, home_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.profile_key = str(profile_key)
        self.home_path = str(Path(home_path).resolve())
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            self._ensure_schema(conn)

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _json(value: Any) -> Optional[str]:
        return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for key in ("metadata_json", "payload_json", "decision_json", "usage_json"):
            if key in result and result[key] is not None:
                result[key[:-5] if key.endswith("_json") else key] = json.loads(result.pop(key))
        return result

    def _transaction(self):
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mission_control (
                mission_id TEXT PRIMARY KEY,
                profile_key TEXT NOT NULL,
                home_path TEXT NOT NULL,
                title TEXT,
                objective TEXT,
                desired_state TEXT NOT NULL DEFAULT 'planned',
                actual_state TEXT NOT NULL DEFAULT 'planned',
                version INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mission_control_profile
                ON mission_control(profile_key, updated_at);
            CREATE TABLE IF NOT EXISTS mission_tasks (
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                role TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (mission_id, task_id),
                FOREIGN KEY (mission_id) REFERENCES mission_control(mission_id)
            );
            CREATE INDEX IF NOT EXISTS idx_mission_tasks_task ON mission_tasks(task_id);
            CREATE TABLE IF NOT EXISTS approval_gates (
                gate_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                execution_id TEXT,
                run_id TEXT,
                node_id TEXT,
                kind TEXT NOT NULL,
                action TEXT,
                resource_hash TEXT,
                scope TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT,
                decided_by TEXT,
                payload_json TEXT,
                decision_json TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                expires_at REAL,
                idempotency_key TEXT,
                created_at REAL NOT NULL,
                decided_at REAL,
                FOREIGN KEY (mission_id) REFERENCES mission_control(mission_id)
            );
            CREATE INDEX IF NOT EXISTS idx_approval_gates_pending
                ON approval_gates(status, mission_id);
            CREATE INDEX IF NOT EXISTS idx_approval_gates_idempotency
                ON approval_gates(mission_id, idempotency_key);
            CREATE TABLE IF NOT EXISTS execution_usage (
                usage_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                run_id TEXT,
                provider TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cost_usd REAL,
                duration_ms REAL,
                attempt INTEGER DEFAULT 1,
                provider_attempt INTEGER DEFAULT 1,
                dedupe_key TEXT,
                usage_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (mission_id) REFERENCES mission_control(mission_id)
            );
            CREATE INDEX IF NOT EXISTS idx_execution_usage_mission
                ON execution_usage(mission_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_execution_usage_dedupe
                ON execution_usage(mission_id, dedupe_key);
            """
        )
        # Additive column migrations for existing databases
        for col_def in (
            ("approval_gates", "execution_id", "TEXT"),
            ("approval_gates", "run_id", "TEXT"),
            ("approval_gates", "node_id", "TEXT"),
            ("approval_gates", "action", "TEXT"),
            ("approval_gates", "resource_hash", "TEXT"),
            ("approval_gates", "scope", "TEXT"),
            ("approval_gates", "schema_version", "INTEGER NOT NULL DEFAULT 1"),
            ("approval_gates", "expires_at", "REAL"),
            ("approval_gates", "idempotency_key", "TEXT"),
            ("execution_usage", "attempt", "INTEGER DEFAULT 1"),
            ("execution_usage", "provider_attempt", "INTEGER DEFAULT 1"),
            ("execution_usage", "dedupe_key", "TEXT"),
        ):
            table, col, ctype = col_def
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")
            except sqlite3.OperationalError:
                pass  # column already exists

    def _require_owner(self, conn: sqlite3.Connection, mission_id: str) -> None:
        row = conn.execute("SELECT profile_key, home_path FROM mission_control WHERE mission_id = ?", (mission_id,)).fetchone()
        if row is None:
            raise KeyError(mission_id)
        if row["profile_key"] != self.profile_key or row["home_path"] != self.home_path:
            raise PermissionError("mission belongs to another profile")

    def create_or_import(
        self,
        mission_id: str,
        *,
        title: Optional[str] = None,
        objective: Optional[str] = None,
        desired_state: str = "planned",
        actual_state: str = "planned",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a mission or import it idempotently; existing fields are preserved."""
        now = time.time()
        with self._lock:
            conn = self._transaction()
            try:
                existing = conn.execute(
                    "SELECT profile_key, home_path FROM mission_control WHERE mission_id = ?",
                    (mission_id,),
                ).fetchone()
                if existing is not None:
                    if existing["profile_key"] != self.profile_key or existing["home_path"] != self.home_path:
                        raise PermissionError("mission belongs to another profile")

                conn.execute(
                    """INSERT INTO mission_control
                    (mission_id, profile_key, home_path, title, objective, desired_state,
                     actual_state, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mission_id) DO UPDATE SET
                      title=COALESCE(mission_control.title, excluded.title),
                      objective=COALESCE(mission_control.objective, excluded.objective),
                      metadata_json=COALESCE(mission_control.metadata_json, excluded.metadata_json),
                      updated_at=excluded.updated_at""",
                    (mission_id, self.profile_key, self.home_path, title, objective,
                     desired_state, actual_state, self._json(metadata), now, now),
                )
                row = conn.execute("SELECT * FROM mission_control WHERE mission_id = ?", (mission_id,)).fetchone()
                conn.commit()
                return self._row(row) or {}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get(self, mission_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM mission_control WHERE mission_id = ?", (mission_id,)).fetchone()
            if row is not None:
                self._require_owner(conn, mission_id)
            return self._row(row)

    def start(self, mission_id: str, *, expected_version: Optional[int] = None) -> Dict[str, Any]:
        return self._set_state(mission_id, desired_state="running", actual_state="running", expected_version=expected_version)

    def set_desired_state(self, mission_id: str, desired_state: str, *, expected_version: Optional[int] = None) -> Dict[str, Any]:
        return self._set_state(mission_id, desired_state=desired_state, expected_version=expected_version)

    def _set_state(self, mission_id: str, *, desired_state: Optional[str] = None, actual_state: Optional[str] = None, expected_version: Optional[int] = None) -> Dict[str, Any]:
        if desired_state is None and actual_state is None:
            raise ValueError("a state is required")
        with self._lock:
            conn = self._transaction()
            try:
                self._require_owner(conn, mission_id)
                current = conn.execute(
                    "SELECT * FROM mission_control WHERE mission_id = ? AND profile_key = ?",
                    (mission_id, self.profile_key),
                ).fetchone()
                if current is None:
                    raise KeyError(mission_id)

                if expected_version is not None and current["version"] != expected_version:
                    raise RuntimeError("mission missing or version conflict")

                # Idempotent no-op check
                desired_same = (desired_state is None or desired_state == current["desired_state"])
                actual_same = (actual_state is None or actual_state == current["actual_state"])
                if desired_same and actual_same:
                    conn.commit()
                    return self._row(current) or {}

                fields, values = [], []
                if desired_state is not None:
                    fields.append("desired_state = ?"); values.append(desired_state)
                if actual_state is not None:
                    fields.append("actual_state = ?"); values.append(actual_state)
                fields += ["version = version + 1", "updated_at = ?"]
                values.append(time.time()); values.append(mission_id)
                where = "mission_id = ?"
                if expected_version is not None:
                    where += " AND version = ?"; values.append(expected_version)

                cur = conn.execute(f"UPDATE mission_control SET {', '.join(fields)} WHERE {where} AND profile_key = ?", values + [self.profile_key])
                if cur.rowcount != 1:
                    conn.rollback()
                    raise RuntimeError("mission missing or version conflict")
                row = conn.execute("SELECT * FROM mission_control WHERE mission_id = ? AND profile_key = ?", (mission_id, self.profile_key)).fetchone()
                conn.commit(); return self._row(row) or {}
            except Exception:
                conn.rollback(); raise
            finally: conn.close()

    def map_task(self, mission_id: str, task_id: str, *, role: Optional[str] = None, position: int = 0, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            conn = self._transaction()
            try:
                self._require_owner(conn, mission_id)
                conn.execute("""INSERT INTO mission_tasks (mission_id, task_id, role, position, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mission_id, task_id) DO UPDATE SET role=excluded.role, position=excluded.position, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
                    (mission_id, task_id, role, position, self._json(metadata), now, now))
                row = conn.execute("SELECT * FROM mission_tasks WHERE mission_id = ? AND task_id = ?", (mission_id, task_id)).fetchone()
                conn.commit(); return self._row(row) or {}
            except Exception:
                conn.rollback(); raise
            finally: conn.close()

    def list_task_mappings(self, mission_id: str) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            self._require_owner(conn, mission_id)
            return [self._row(r) or {} for r in conn.execute("SELECT * FROM mission_tasks WHERE mission_id = ? ORDER BY position, task_id", (mission_id,))]

    def request_gate(
        self,
        mission_id: str,
        kind: str,
        *,
        task_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_hash: Optional[str] = None,
        scope: Optional[str] = None,
        schema_version: int = 1,
        requested_by: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        expires_at: Optional[float] = None,
        idempotency_key: Optional[str] = None,
        gate_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            conn = self._transaction()
            try:
                self._require_owner(conn, mission_id)
                if idempotency_key:
                    existing = conn.execute(
                        "SELECT * FROM approval_gates WHERE mission_id = ? AND idempotency_key = ?",
                        (mission_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        conn.commit()
                        return self._row(existing) or {}

                gate_id = gate_id or uuid.uuid4().hex
                now = time.time()
                conn.execute(
                    """INSERT INTO approval_gates
                    (gate_id, mission_id, task_id, execution_id, run_id, node_id, kind, action,
                     resource_hash, scope, schema_version, status, requested_by, payload_json,
                     expires_at, idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
                    (gate_id, mission_id, task_id, execution_id, run_id, node_id, kind, action,
                     resource_hash, scope, schema_version, requested_by, self._json(payload),
                     expires_at, idempotency_key, now),
                )
                row = conn.execute(
                    "SELECT * FROM approval_gates WHERE gate_id = ? AND mission_id IN (SELECT mission_id FROM mission_control WHERE profile_key = ?)",
                    (gate_id, self.profile_key),
                ).fetchone()
                conn.commit()
                return self._row(row) or {}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_gate(self, gate_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT g.* FROM approval_gates g
                   JOIN mission_control m ON m.mission_id = g.mission_id
                   WHERE g.gate_id = ? AND m.profile_key = ?""",
                (gate_id, self.profile_key),
            ).fetchone()
            return self._row(row)

    def decide_gate(
        self,
        gate_id: str,
        decision: Optional[str] = None,
        *,
        approved: Optional[bool] = None,
        decided_by: Optional[str] = None,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        if decision is None and approved is not None:
            decision = "approved" if approved else "rejected"
        if decision is None:
            raise ValueError("decision or approved is required")
        if reason:
            if payload is None:
                payload = {"reason": reason}
            elif "reason" not in payload:
                payload["reason"] = reason
        with self._lock:
            conn = self._transaction()
            try:
                gate = conn.execute(
                    """SELECT g.* FROM approval_gates g
                       JOIN mission_control m ON m.mission_id = g.mission_id
                       WHERE g.gate_id = ? AND m.profile_key = ?""",
                    (gate_id, self.profile_key),
                ).fetchone()
                if gate is None:
                    conn.rollback()
                    raise KeyError("gate missing or access denied")

                if gate["status"] != "pending":
                    conn.rollback()
                    raise RuntimeError(f"gate already {gate['status']}")

                now = time.time()
                if gate["expires_at"] is not None and now > gate["expires_at"]:
                    conn.execute(
                        "UPDATE approval_gates SET status = 'expired', decided_at = ? WHERE gate_id = ?",
                        (now, gate_id),
                    )
                    conn.commit()
                    raise RuntimeError("gate expired")

                if expected_version is not None and gate["version"] != expected_version:
                    conn.rollback()
                    raise RuntimeError("version conflict")

                cur = conn.execute(
                    """UPDATE approval_gates
                       SET status = ?, decided_by = ?, decision_json = ?, decided_at = ?, version = version + 1
                       WHERE gate_id = ? AND status = 'pending'""",
                    (decision, decided_by, self._json(payload), now, gate_id),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    raise RuntimeError("gate missing, already decided, or version conflict")

                row = conn.execute(
                    "SELECT * FROM approval_gates WHERE gate_id = ?",
                    (gate_id,),
                ).fetchone()
                conn.commit()
                return self._row(row) or {}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def is_gate_approved(self, gate_id: str) -> bool:
        gate = self.get_gate(gate_id)
        return bool(gate and gate.get("status") == "approved")

    def list_pending_gates(self, mission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            sql = "SELECT g.* FROM approval_gates g JOIN mission_control m ON m.mission_id = g.mission_id AND m.profile_key = ? WHERE g.status = 'pending'"
            args: List[Any] = [self.profile_key]
            if mission_id is not None:
                sql += " AND g.mission_id = ?"
                args.append(mission_id)
            return [self._row(r) or {} for r in conn.execute(sql + " ORDER BY g.created_at, g.gate_id", args)]

    def list_active_missions(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_control WHERE profile_key = ? AND desired_state = 'running'",
                (self.profile_key,),
            ).fetchall()
            return [self._row(r) or {} for r in rows]

    def record_usage(
        self,
        mission_id: str,
        *,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        duration_ms: Optional[float] = None,
        attempt: int = 1,
        provider_attempt: int = 1,
        dedupe_key: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        usage_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            conn = self._transaction()
            try:
                self._require_owner(conn, mission_id)
                if dedupe_key:
                    existing = conn.execute(
                        "SELECT * FROM execution_usage WHERE mission_id = ? AND dedupe_key = ?",
                        (mission_id, dedupe_key),
                    ).fetchone()
                    if existing is not None:
                        conn.commit()
                        return self._row(existing) or {}

                usage_id = usage_id or uuid.uuid4().hex
                now = time.time()
                conn.execute(
                    """INSERT INTO execution_usage
                    (usage_id, mission_id, task_id, run_id, provider, model,
                     input_tokens, output_tokens, total_tokens, cost_usd, duration_ms,
                     attempt, provider_attempt, dedupe_key, usage_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (usage_id, mission_id, task_id, run_id, provider, model,
                     input_tokens, output_tokens, total_tokens, cost_usd, duration_ms,
                     attempt, provider_attempt, dedupe_key, self._json(usage), now),
                )
                row = conn.execute("SELECT * FROM execution_usage WHERE usage_id = ?", (usage_id,)).fetchone()
                conn.commit()
                return self._row(row) or {}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def list_usage(self, mission_id: str, *, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            self._require_owner(conn, mission_id)
            sql = "SELECT * FROM execution_usage WHERE mission_id = ?"; args: List[Any] = [mission_id]
            if task_id is not None: sql += " AND task_id = ?"; args.append(task_id)
            return [self._row(r) or {} for r in conn.execute(sql + " ORDER BY created_at, usage_id", args)]

    def get_mission_analytics(self, mission_id: str) -> Dict[str, Any]:
        """Calculates measured analytics from canonical database tables."""
        with self._connection() as conn:
            self._require_owner(conn, mission_id)
            m_row = conn.execute(
                "SELECT created_at, updated_at, actual_state FROM mission_control WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if not m_row:
                return {}

            created_at, updated_at, actual_state = m_row["created_at"], m_row["updated_at"], m_row["actual_state"]

            # Count mapped tasks and their statuses
            has_tasks = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone() is not None

            if has_tasks:
                task_rows = conn.execute(
                    "SELECT tm.task_id, t.status, t.consecutive_failures "
                    "FROM mission_tasks tm "
                    "LEFT JOIN tasks t ON tm.task_id = t.id "
                    "WHERE tm.mission_id = ?",
                    (mission_id,),
                ).fetchall()
                tasks_total = len(task_rows)
                tasks_completed = sum(1 for r in task_rows if (r["status"] or "").lower() in ("done", "completed"))
                tasks_failed = sum(1 for r in task_rows if (r["status"] or "").lower() == "failed")
                total_retries = sum(int(r["consecutive_failures"] or 0) for r in task_rows)
            else:
                task_rows = conn.execute(
                    "SELECT task_id FROM mission_tasks WHERE mission_id = ?",
                    (mission_id,),
                ).fetchall()
                tasks_total = len(task_rows)
                tasks_completed = 0
                tasks_failed = 0
                total_retries = 0

            # Sum tokens from execution_usage if available
            usage_row = conn.execute(
                "SELECT SUM(total_tokens) as tokens FROM execution_usage WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            total_tokens = usage_row["tokens"] if (usage_row and usage_row["tokens"] is not None) else None

            duration_seconds = None
            if created_at and updated_at and actual_state in ("completed", "failed", "running"):
                duration_seconds = max(0, round(float(updated_at) - float(created_at)))

            return {
                "mission_id": mission_id,
                "actual_state": actual_state,
                "tasks_total": tasks_total,
                "tasks_completed": tasks_completed,
                "tasks_failed": tasks_failed,
                "total_failures": tasks_failed,
                "total_retries": total_retries,
                "total_tokens": total_tokens,
                "duration_seconds": duration_seconds,
            }


__all__ = ["MissionStore"]
