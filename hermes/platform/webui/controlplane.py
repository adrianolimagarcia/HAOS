"""Control Plane Backend & control-plane Engine (Phase 5 — Control Plane).

Implements:
1. ControlPlaneService: aggregation of live execution metrics and interventions.
2. ControlPlaneService: Aggregates metrics across TeamRuntime, SpecialistPool, AdaptiveIntelligence, and Federation.
3. ControlIntervention: Steer, pause, resume, and abort controls over active workers.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.evolution.adaptive_intelligence import AdaptiveIntelligenceCoordinator
from hermes.platform.execution.team_runtime import MultiAgentTeamRuntime
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.protocols.gateway import UniversalProtocolGateway


# -- Live board resolution ------------------------------------------------- #
# The Control Plane must reflect the ACTIVE HAOS kanban board (the DB the CLI
# dispatcher and gateway lanes write), not only the stores injected at server
# construction (which can be a private/stale copy). We resolve the canonical
# DB file by environment/HOME convention and read it read-only.
_TERMINAL_STATUSES = ("done", "completed", "failed", "error", "blocked")
_ACTIVE_STATUSES = ("in_progress", "running", "claimed", "dispatched")


def canonical_kanban_db_paths() -> List[str]:
    """Candidate canonical HAOS kanban DB files, env/HOME-derived.

    HOME fallbacks (~/.haos, ~/.hermes) only apply when no profile env vars are
    set, so unit runs (which set HERMES_HOME to a temp dir) never read the real
    user board.
    """
    candidates: List[str] = []
    for key in ("HERMES_KANBAN_DB", "HAOS_KANBAN_DB", "KANBAN_DB"):
        val = os.environ.get(key)
        if val:
            candidates.append(str(Path(val).expanduser()))
    profile_set = bool(os.environ.get("HAOS_HOME") or os.environ.get("HERMES_HOME"))
    for key in ("HAOS_HOME", "HERMES_HOME"):
        base = os.environ.get(key)
        if base:
            candidates.append(str(Path(base).expanduser() / "kanban.db"))
    if not profile_set:
        candidates.append(str(Path.home() / ".haos" / "kanban.db"))
        candidates.append(str(Path.home() / ".hermes" / "kanban.db"))  # haos-legacy-path: candidato legado
    # De-duplicate preserving order.
    seen: set[str] = set()
    out: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def canonical_state_db_paths() -> List[str]:
    """Candidate canonical HAOS state.db files, env/HOME-derived.

    Mirrors ``canonical_kanban_db_paths()``: HOME fallbacks only apply when no
    profile env vars are set, so unit runs (HERMES_HOME = temp dir) never read
    a real store from another installation.
    """
    candidates: List[str] = []
    for key in ("HAOS_STATE_DB", "STATE_DB"):
        val = os.environ.get(key)
        if val:
            candidates.append(str(Path(val).expanduser()))
    profile_set = bool(os.environ.get("HAOS_HOME") or os.environ.get("HERMES_HOME"))
    for key in ("HAOS_HOME", "HERMES_HOME"):
        base = os.environ.get(key)
        if base:
            candidates.append(str(Path(base).expanduser() / "state.db"))
    if not profile_set:
        candidates.append(str(Path.home() / ".haos" / "state.db"))
        candidates.append(str(Path.home() / ".hermes" / "state.db"))  # haos-legacy-path: candidato legado
    # De-duplicate preserving order.
    seen: set[str] = set()
    out: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def read_live_board(db_path: str) -> Dict[str, Any]:
    """Read-only aggregation of a kanban DB file.

    Returns {"db", "total", "active", "terminal", "tasks"} where ``tasks`` are
    lightweight dicts ({id, title, body, status, assignee}) for the control-plane.
    """
    result: Dict[str, Any] = {"db": db_path, "total": 0, "active": 0,
                              "terminal": 0, "tasks": []}
    if not db_path or not Path(db_path).is_file():
        return result
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON;")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(tasks)")]
        if "id" not in cols or "status" not in cols:
            conn.close()
            return result
        rows = cur.execute(
            "SELECT id, title, body, status, assignee FROM tasks"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return result

    terminal = 0
    active = 0
    tasks: List[Dict[str, Any]] = []
    for r in rows:
        st = str(r["status"] or "").lower().strip()
        tasks.append({
            "id": r["id"],
            "title": r["title"] or "",
            "body": r["body"] or "",
            "status": st,
            "assignee": r["assignee"] or "",
        })
        if st in _ACTIVE_STATUSES:
            active += 1
        elif st in _TERMINAL_STATUSES:
            terminal += 1
    result["tasks"] = tasks
    result["total"] = len(tasks)
    result["active"] = active
    result["terminal"] = terminal
    return result


@dataclass
class ControlPlaneOverview:
    total_missions: int
    active_workers: int
    idle_specialists: int
    total_tokens: int
    total_cost_usd: float
    reputation_healthy_pct: float
    active_routes_count: int
    pool_size: int = 4
    board_db: str = ""


class ControlPlaneService:
    """Central orchestration service for Phase 5 Control Plane."""

    def __init__(
        self,
        event_store: EventStore,
        team_runtime: Optional[MultiAgentTeamRuntime] = None,
        adaptive_coordinator: Optional[AdaptiveIntelligenceCoordinator] = None,
        protocol_gateway: Optional[UniversalProtocolGateway] = None,
        kanban: Optional[Any] = None,
        concurrency_guard: Optional[Any] = None,
        data_dir: Optional[Any] = None,
    ):
        self.data_dir = data_dir
        self.event_store = event_store
        self.runtime = team_runtime
        self.adaptive = adaptive_coordinator
        self.gateway = protocol_gateway
        self.kanban = kanban
        self.guard = concurrency_guard
        self._interventions: Dict[str, str] = {}  # worker_id -> intervention command
        self._hydrate_interventions()
    def _resolve_live_board(self) -> Dict[str, Any]:
        """Best-effort read of the ACTIVE kanban board.

        Prefers rows from the injected adapter (the store this server actually
        dispatches through). When the adapter is empty/stale, falls back to the
        canonical HAOS kanban DB file (env/HOME) so overview and control-plane
        keep following the board the CLI/gateway dispatcher really writes.
        """
        empty: Dict[str, Any] = {"db": "", "total": 0, "active": 0,
                                 "terminal": 0, "tasks": []}
        adapter_tasks: List[Dict[str, Any]] = []
        adapter_db = ""
        if self.kanban is not None:
            try:
                adapter_tasks = list(self.kanban.list_tasks() or [])
            except Exception:
                adapter_tasks = []
            db_path = getattr(self.kanban, "db_path", "") or getattr(self.kanban, "path", "")
            adapter_db = str(db_path) if db_path else ""

        def _counts(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
            active = sum(1 for t in tasks
                         if str(t.get("status", "")).lower().strip() in _ACTIVE_STATUSES)
            terminal = sum(1 for t in tasks
                           if str(t.get("status", "")).lower().strip() in _TERMINAL_STATUSES)
            return {"db": adapter_db, "total": len(tasks), "active": active,
                    "terminal": terminal, "tasks": tasks}

        if adapter_tasks:
            return _counts(adapter_tasks)

        for cand in canonical_kanban_db_paths():
            if cand == adapter_db:
                continue
            board = read_live_board(cand)
            if board["total"] > 0:
                return board

        if adapter_db:
            return read_live_board(adapter_db)
        return empty

    def _live_tasks(self) -> List[Dict[str, Any]]:
        """Unified task view: canonical live board when it has rows, else adapter."""
        board = self._resolve_live_board()
        if board["tasks"]:
            return board["tasks"]
        if self.kanban is not None:
            try:
                return list(self.kanban.list_tasks() or [])
            except Exception:
                return []
        return []

    def _pool_capacity(self) -> int:
        """Real concurrency ceiling (ConcurrencyGuard > runtime pool > config > 4)."""
        if self.guard is not None:
            try:
                return int(getattr(self.guard, "max_global", 0) or 0) or 4
            except Exception:
                pass
        if self.runtime is not None:
            try:
                return int(getattr(self.runtime.pool, "max_pool_size", 0) or 0) or 4
            except Exception:
                pass
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            val = int(cfg.get("delegation", {}).get("max_concurrent_children", 0) or 0)
            return val or 4
        except Exception:
            return 4

    def _hydrate_interventions(self) -> None:
        """Reidrata intervenções a partir do event store na inicialização."""
        if not hasattr(self.event_store, "read_events"):
            return
        try:
            events = self.event_store.read_events()
            for ev in events:
                if ev.name == "controlplane.intervention":
                    payload = ev.payload or {}
                    target_id = payload.get("target_id")
                    action = payload.get("action")
                    if target_id and action:
                        self._interventions[target_id] = action
        except Exception:
            pass

    def get_overview(self) -> ControlPlaneOverview:
        events = self.event_store.read_events()
        board = self._resolve_live_board()
        live_tasks = board.get("tasks") or []

        # Mission identifiers from the event history + terminal tasks on the
        # live board (union keeps the count monotonic as either source grows).
        ev_mission_ids = {
            (e.payload or {}).get("mission_id") or (e.payload or {}).get("task_id")
            for e in events
            if e.name in ("team.formed", "haos.task.spawned", "task.run.completed")
            and ((e.payload or {}).get("mission_id") or (e.payload or {}).get("task_id"))
        }
        board_terminal_ids = {t["id"] for t in live_tasks if t["status"] in _TERMINAL_STATUSES}
        mission_ids = ev_mission_ids | board_terminal_ids

        # Fallback for adapters whose rows never produced events (pure board view).
        if not mission_ids and live_tasks:
            mission_ids = {t["id"] for t in live_tasks}

        mission_count = len(mission_ids)

        total_tokens = sum(int(e.payload.get("tokens") or 0) for e in events if "tokens" in (e.payload or {}))

        # Se total_tokens for 0 no EventStore (ex: execuções via CLI/subagentes
        # diretos), consulta a contagem real auditada no state.db do PERFIL ATIVO
        # (session_model_usage) — nunca caminhos hardcoded de outro host/store.
        if total_tokens == 0:
            try:
                for sdb in canonical_state_db_paths():
                    if Path(sdb).is_file():
                        conn = sqlite3.connect(str(sdb), timeout=1.0)
                        cur = conn.cursor()
                        row = cur.execute("SELECT sum(input_tokens + output_tokens + reasoning_tokens) FROM session_model_usage;").fetchone()
                        conn.close()
                        if row and row[0]:
                            total_tokens = int(row[0])
                            break
            except Exception:
                pass

        # Active workers: live board first (in_progress/running), then runtime pool.
        active_workers = board.get("active", 0)
        if active_workers == 0 and live_tasks:
            active_workers = sum(1 for t in live_tasks if t["status"] in _ACTIVE_STATUSES)
        if active_workers == 0 and self.runtime:
            try:
                active_workers = self.runtime.pool.active_count()
            except Exception:
                pass
        if active_workers == 0 and self.kanban and not live_tasks:
            try:
                tasks = self.kanban.list_tasks()
                active_workers = sum(1 for t in tasks if str(t.get("status", "")).lower() in _ACTIVE_STATUSES)
            except Exception:
                pass

        pool_size = self._pool_capacity()
        idle_workers = max(0, pool_size - active_workers)

        cost = (total_tokens / 1000.0) * 0.001 if total_tokens > 0 else 0.0  # Estimativa

        has_activity = (active_workers > 0 or mission_count > 0 or total_tokens > 0)
        return ControlPlaneOverview(
            total_missions=mission_count,
            active_workers=active_workers,
            idle_specialists=max(0, idle_workers),
            total_tokens=total_tokens,
            total_cost_usd=round(cost, 4),
            reputation_healthy_pct=100.0 if has_activity else 0.0,
            active_routes_count=2 if has_activity else 0,
            pool_size=pool_size,
            board_db=board.get("db", ""),
        )


    def record_intervention(self, target_id: str, action: str, reason: str) -> None:
        """Records an operator control-plane intervention (steer, pause, interrupt, resume)."""
        self._interventions[target_id] = action
        self.event_store.append(
            Event(
                name="controlplane.intervention",
                payload={"target_id": target_id, "action": action, "reason": reason},
            )
        )

    def get_pending_intervention(self, target_id: str) -> Optional[str]:
        return self._interventions.get(target_id)
