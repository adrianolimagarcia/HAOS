"""Control Plane Backend & Team Graph Engine (Phase 5 — Control Plane).

Implements:
1. TeamGraphNode & TeamGraphSnapshot: Typed hierarchical representation of the active multi-agent team.
2. ControlPlaneService: Aggregates metrics across TeamRuntime, SpecialistPool, AdaptiveIntelligence, and Federation.
3. ControlIntervention: Steer, pause, resume, and abort controls over active workers.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple

from hermes.platform.evolution.adaptive_intelligence import AdaptiveIntelligenceCoordinator
from hermes.platform.execution.team_runtime import MultiAgentTeamRuntime, PooledSpecialist
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.protocols.gateway import UniversalProtocolGateway
from hermes.platform.workers.harness_registry import HarnessRegistry
from hermes.platform.webui.harness_bindings import HarnessBindingStore


class NodeStatus(str, enum.Enum):
    PENDING = "pending"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


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
    lightweight dicts ({id, title, body, status, assignee}) for the Team Graph.
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


def _resolve_role_model_binding(role: str, fallback_model: str = "", fallback_provider: str = "") -> Tuple[str, str]:
    """Dynamically reads delegation.role_models and model.provider from active config.yaml."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        del_roles = cfg.get("delegation", {}).get("role_models", {})
        default_provider = cfg.get("model", {}).get("provider", fallback_provider)
        default_model = cfg.get("model", {}).get("default", fallback_model)

        target_model = None
        if role in ("mayor", "town_mayor"):
            target_model = del_roles.get("mayor") or default_model
        elif role in ("sub_orchestrator", "orchestrator", "architect"):
            target_model = del_roles.get("orchestrator") or default_model
        elif role in ("worker", "leaf", "coder", "polecat"):
            target_model = del_roles.get("leaf") or del_roles.get("polecat") or default_model
        elif role in ("reviewer", "witness", "qa"):
            target_model = del_roles.get("witness") or del_roles.get("reviewer") or del_roles.get("leaf") or default_model

        if not target_model:
            target_model = fallback_model or default_model

        provider = default_provider or fallback_provider
        if not target_model or not provider:
            return "", ""
        return target_model, provider
    except Exception:
        return fallback_model, fallback_provider


@dataclass
class TeamGraphNode:
    """Represents an agent or sub-orchestrator in the Team Graph."""
    node_id: str
    label: str
    role: str  # "mayor", "sub_orchestrator", "worker", "reviewer", "external"
    posture: str
    model_id: str
    provider_id: str
    status: NodeStatus = NodeStatus.IDLE
    current_task: Optional[str] = None
    worktree_path: Optional[str] = None
    tokens_consumed: int = 0
    cost_usd: float = 0.0
    latency_sec: float = 0.0
    harness: str = "native"
    harness_status: str = "available"  # "available" | "missing" | "allocated" | "fallback-native" | "unsupported"
    children: List[TeamGraphNode] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    hierarchy_node_id: Optional[str] = field(default=None, kw_only=True)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "node_id": self.node_id,
            "label": self.label,
            "role": self.role,
            "posture": self.posture,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "current_task": self.current_task,
            "worktree_path": self.worktree_path,
            "tokens_consumed": self.tokens_consumed,
            "cost_usd": self.cost_usd,
            "latency_sec": self.latency_sec,
            "harness": self.harness,
            "harness_status": self.harness_status,
            "children": [c.to_dict() for c in self.children],
            "metadata": self.metadata,
        }
        if self.hierarchy_node_id is not None:
            out["hierarchy_node_id"] = self.hierarchy_node_id
        return out


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
        # Bindings papel -> harness (seleção do operador no Team Graph).
        # data_dir (opcional) é o diretório canônico do painel; o arquivo de
        # bindings vive dentro dele. Sem data_dir, resolve env/home (~/.haos).
        if data_dir is not None:
            self.bindings = HarnessBindingStore(Path(data_dir) / "harness_bindings.json")
        else:
            self.bindings = HarnessBindingStore()

    # -- harness bindings (seletor do Team Graph) ------------------------ #
    def harness_for(self, role_key: str) -> str:
        """Harness efetivo do papel (binding persistido; default 'native')."""
        return self.bindings.get(role_key)

    def harness_overview(self) -> Dict[str, Any]:
        """Catálogo + bindings atuais para popular o seletor do painel."""
        return self.bindings.overview()

    def set_harness_binding(self, role_key: str, harness_name: str) -> Dict[str, Any]:
        """Persiste papel -> harness; ValueError para papel/harness inválido."""
        return self.bindings.set(role_key, harness_name)

    # -- live kanban resolution ------------------------------------------- #
    def _resolve_live_board(self) -> Dict[str, Any]:
        """Best-effort read of the ACTIVE kanban board.

        Prefers rows from the injected adapter (the store this server actually
        dispatches through). When the adapter is empty/stale, falls back to the
        canonical HAOS kanban DB file (env/HOME) so overview and Team Graph
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

    def build_team_graph(
        self,
        mission_id: str,
        goal: str,
        sub_orchestrators: List[Dict[str, Any]],
        root_status: Optional[Union[NodeStatus, str]] = None,
    ) -> TeamGraphNode:
        """Constructs a hierarchical TeamGraphNode tree representing the mission hierarchy."""
        resolved_sub_nodes: List[TeamGraphNode] = []

        for so_data in sub_orchestrators:
            so_children: List[TeamGraphNode] = []
            for worker_data in so_data.get("workers", []):
                raw_st = str(worker_data.get("status", "idle")).lower()
                node_st = NodeStatus(raw_st) if raw_st in [s.value for s in NodeStatus] else NodeStatus.IDLE
                w_role_key = (
                    "reviewer"
                    if str(worker_data.get("role", "")).lower() == "reviewer"
                    or str(worker_data.get("posture", "")).lower() == "reviewer"
                    else "coder"
                )
                w_harness = str(worker_data.get("harness") or self.harness_for(w_role_key)).strip().lower()
                w_h_info = HarnessRegistry.get_instance().detect(w_harness)
                if node_st == NodeStatus.RUNNING:
                    w_h_st = worker_data.get("harness_status") or "allocated"
                elif w_h_info.available:
                    w_h_st = worker_data.get("harness_status") or w_h_info.status
                else:
                    w_h_st = worker_data.get("harness_status") or "fallback-native"
                w_meta = {"blocker": worker_data.get("blocker")} if worker_data.get("blocker") else {}
                w_meta["harness_info"] = w_h_info.to_dict()

                w_node = TeamGraphNode(
                    node_id=worker_data.get("id", f"worker-{uuid.uuid4().hex[:4]}"),
                    label=worker_data.get("label", "Specialist"),
                    role=worker_data.get("role", "worker"),
                    posture=worker_data.get("posture", "coder"),
                    model_id=worker_data.get("model") or _resolve_role_model_binding(worker_data.get("role", "worker"))[0],
                    provider_id=worker_data.get("provider") or _resolve_role_model_binding(worker_data.get("role", "worker"))[1],
                    status=node_st,
                    current_task=worker_data.get("task"),
                    tokens_consumed=worker_data.get("tokens", 0),
                    cost_usd=worker_data.get("cost", 0.0),
                    harness=w_harness,
                    harness_status=w_h_st,
                    metadata=w_meta,
                )
                so_children.append(w_node)

            # Determine Sub-Orchestrator status from configuration or children
            so_raw_st = so_data.get("status")
            if so_raw_st and str(so_raw_st).lower() in [s.value for s in NodeStatus]:
                so_status = NodeStatus(str(so_raw_st).lower())
            elif any(w.status == NodeStatus.RUNNING for w in so_children):
                so_status = NodeStatus.RUNNING
            elif any(w.status == NodeStatus.PAUSED for w in so_children) and not any(w.status == NodeStatus.RUNNING for w in so_children):
                so_status = NodeStatus.PAUSED
            elif any(w.status == NodeStatus.FAILED for w in so_children) and not any(w.status == NodeStatus.RUNNING for w in so_children):
                so_status = NodeStatus.FAILED
            elif any(w.status == NodeStatus.BLOCKED for w in so_children) and not any(w.status == NodeStatus.RUNNING for w in so_children):
                so_status = NodeStatus.BLOCKED
            elif so_children and all(w.status == NodeStatus.COMPLETED for w in so_children):
                so_status = NodeStatus.COMPLETED
            else:
                so_status = NodeStatus.IDLE

            so_model, so_prov = _resolve_role_model_binding("sub_orchestrator")
            so_harness = str(so_data.get("harness") or self.harness_for("sub_orchestrator")).strip().lower()
            so_h_info = HarnessRegistry.get_instance().detect(so_harness)
            if so_status == NodeStatus.RUNNING:
                so_h_st = so_data.get("harness_status") or "allocated"
            elif so_h_info.available:
                so_h_st = so_data.get("harness_status") or so_h_info.status
            else:
                so_h_st = so_data.get("harness_status") or "fallback-native"
            so_node = TeamGraphNode(
                node_id=so_data.get("id", f"sub-orch-{uuid.uuid4().hex[:4]}"),
                label=f"Sub-Orchestrator ({so_data.get('domain', 'general')})",
                role="sub_orchestrator",
                posture="architect",
                model_id=so_model,
                provider_id=so_prov,
                status=so_status,
                harness=so_harness,
                harness_status=so_h_st,
                children=so_children,
                metadata={"harness_info": so_h_info.to_dict()},
            )
            resolved_sub_nodes.append(so_node)

        # Determine Mayor (root) status
        if root_status is not None:
            if isinstance(root_status, NodeStatus):
                mayor_status = root_status
            elif str(root_status).lower() in [s.value for s in NodeStatus]:
                mayor_status = NodeStatus(str(root_status).lower())
            else:
                mayor_status = NodeStatus.IDLE
        elif any(s.status == NodeStatus.RUNNING for s in resolved_sub_nodes):
            mayor_status = NodeStatus.RUNNING
        elif any(s.status == NodeStatus.PAUSED for s in resolved_sub_nodes):
            mayor_status = NodeStatus.PAUSED
        elif any(s.status == NodeStatus.FAILED for s in resolved_sub_nodes):
            mayor_status = NodeStatus.FAILED
        elif any(s.status == NodeStatus.BLOCKED for s in resolved_sub_nodes):
            mayor_status = NodeStatus.BLOCKED
        elif resolved_sub_nodes and all(s.status == NodeStatus.COMPLETED for s in resolved_sub_nodes):
            mayor_status = NodeStatus.COMPLETED
        else:
            mayor_status = NodeStatus.IDLE

        mayor_model, mayor_prov = _resolve_role_model_binding("mayor")
        mayor_harness = self.harness_for("mayor")
        mayor_h_info = HarnessRegistry.get_instance().detect(mayor_harness)
        mayor_h_st = "allocated" if mayor_status == NodeStatus.RUNNING else mayor_h_info.status
        root = TeamGraphNode(
            node_id=f"mayor-{mission_id}",
            label="Town Mayor (Executive Lead)",
            role="mayor",
            posture="executive",
            model_id=mayor_model,
            provider_id=mayor_prov,
            current_task=goal,
            status=mayor_status,
            harness=mayor_harness,
            harness_status=mayor_h_st,
            children=resolved_sub_nodes,
            metadata={"harness_info": mayor_h_info.to_dict()},
        )

        return root

    def build_graph_from_team_spec(
        self,
        team_spec: Any,
        mission_id: str = "mission-01",
        goal: str = "Execute Mission",
        active_tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> TeamGraphNode:
        """Constructs a hierarchical TeamGraphNode tree from a declarative TeamSpec.

        Dynamically probes harness availability for each role in the roster,
        mapping Town Mayor, Sub-Orchestrator, and Workers into a correlated graph.
        """
        registry = HarnessRegistry.get_instance()
        active_tasks = active_tasks or []
        task_by_role: Dict[str, Dict[str, Any]] = {}
        for t in active_tasks:
            role_key = t.get("role") or t.get("assignee")
            if role_key:
                task_by_role[str(role_key).lower()] = t

        mayor_role = next((r for r in team_spec.roles if r.role_id == "mayor"), None)
        so_roles = [r for r in team_spec.roles if r.role_id in ("sub_orchestrator", "architect")]
        worker_roles = [r for r in team_spec.roles if r.role_id not in ("mayor", "sub_orchestrator", "architect")]

        # Build workers
        worker_nodes: List[TeamGraphNode] = []
        for w_role in worker_roles:
            harness = getattr(w_role, "harness", "native") or "native"
            h_info = registry.detect(harness)
            active_t = task_by_role.get(w_role.role_id.lower())
            st = NodeStatus.RUNNING if active_t else NodeStatus.IDLE
            if st == NodeStatus.RUNNING:
                h_status = "allocated"
            elif h_info.available:
                h_status = h_info.status
            else:
                h_status = "fallback-native"  # not found -> warn + continue locally
            model_id = w_role.model_profile or "coding-primary"

            node = TeamGraphNode(
                node_id=f"worker-{w_role.role_id}-{uuid.uuid4().hex[:4]}",
                label=f"{w_role.role_id.title()} ({harness})",
                role=w_role.role_id,
                posture=w_role.posture_id or "coder",
                model_id=model_id,
                provider_id="auto",
                status=st,
                current_task=active_t.get("title") if active_t else None,
                harness=harness,
                harness_status=h_status,
                metadata={"harness_info": h_info.to_dict(), "lane": w_role.lane or "kilo"},
            )
            worker_nodes.append(node)

        # Build Sub-Orchestrator
        so_nodes: List[TeamGraphNode] = []
        if so_roles:
            for s_role in so_roles:
                harness = getattr(s_role, "harness", "native") or "native"
                h_info = registry.detect(harness)
                active_t = task_by_role.get(s_role.role_id.lower())
                st = NodeStatus.RUNNING if any(w.status == NodeStatus.RUNNING for w in worker_nodes) else NodeStatus.IDLE
                if st == NodeStatus.RUNNING:
                    h_status = "allocated"
                elif h_info.available:
                    h_status = h_info.status
                else:
                    h_status = "fallback-native"

                so_node = TeamGraphNode(
                    node_id=f"so-{s_role.role_id}-{uuid.uuid4().hex[:4]}",
                    label=f"Sub-Orchestrator ({harness})",
                    role=s_role.role_id,
                    posture=s_role.posture_id or "architect",
                    model_id=s_role.model_profile or "architecture-primary",
                    provider_id="auto",
                    status=st,
                    harness=harness,
                    harness_status=h_status,
                    children=list(worker_nodes),
                    metadata={"harness_info": h_info.to_dict()},
                )
                so_nodes.append(so_node)
        else:
            so_node = TeamGraphNode(
                node_id=f"so-default-{uuid.uuid4().hex[:4]}",
                label="Sub-Orchestrator (native)",
                role="sub_orchestrator",
                posture="architect",
                model_id="architecture-primary",
                provider_id="auto",
                status=NodeStatus.RUNNING if any(w.status == NodeStatus.RUNNING for w in worker_nodes) else NodeStatus.IDLE,
                harness="native",
                harness_status="available",
                children=list(worker_nodes),
            )
            so_nodes.append(so_node)

        # Build Mayor
        mayor_harness = getattr(mayor_role, "harness", "native") if mayor_role else "native"
        mayor_h_info = registry.detect(mayor_harness)
        mayor_st = NodeStatus.RUNNING if any(s.status == NodeStatus.RUNNING for s in so_nodes) else NodeStatus.IDLE
        if mayor_st == NodeStatus.RUNNING:
            mayor_h_status = "allocated"
        elif mayor_h_info.available:
            mayor_h_status = mayor_h_info.status
        else:
            mayor_h_status = "fallback-native"

        root = TeamGraphNode(
            node_id=f"mayor-{mission_id}",
            label=f"Town Mayor ({mayor_harness})",
            role="mayor",
            posture="executive",
            model_id=getattr(mayor_role, "model_profile", "orchestrator-primary") if mayor_role else "orchestrator-primary",
            provider_id="auto",
            current_task=goal,
            status=mayor_st,
            harness=mayor_harness,
            harness_status=mayor_h_status,
            children=so_nodes,
            metadata={"harness_info": mayor_h_info.to_dict()},
        )
        return root

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

    def get_team_graph_snapshot(self, mission_id: Optional[str] = None) -> Dict[str, Any]:
        """Dynamically reconstructs or retrieves the hierarchical team graph snapshot from EventStore and Kanban."""
        events = self.event_store.read_events()
        kb_tasks = self._live_tasks()

        # ISOLAMENTO ULTRA SOTA: O Team Graph (GasTown) NUNCA deve absorver ou exibir os nós da
        # Hierarquia Permanente de Bots (ex: the_eye, orquestrador, sentinel_sre, forge_coder, etc.).
        # O Team Graph é estritamente a topologia de execução da missão (Mayor -> Sub-Orch -> Leafs/Witnesses).
        # Os bots da hierarquia vivem exclusiva e isoladamente na aba "Bots".
        hierarchy_bot_ids = set()
        resolved_data_dir = self.data_dir
        if not resolved_data_dir:
            try:
                from hermes_constants import get_hermes_home
                resolved_data_dir = get_hermes_home()
            except Exception:
                pass
        if resolved_data_dir:
            try:
                ah_file = Path(resolved_data_dir) / "agent_hierarchy.json"
                if ah_file.exists():
                    import json
                    ah_data = json.loads(ah_file.read_text(encoding="utf-8"))
                    for n in ah_data.get("nodes", []):
                        if n.get("id"):
                            hierarchy_bot_ids.add(str(n["id"]).lower())
                        if n.get("profile"):
                            hierarchy_bot_ids.add(str(n["profile"]).lower())
            except Exception:
                pass

        # Filtra tarefas atribuídas à hierarquia fixa de bots para que o Team Graph
        # reflita exclusivamente missões de swarm / GasTown
        mission_kb_tasks = [
            t for t in kb_tasks
            if str(t.get("assignee", "")).lower() not in hierarchy_bot_ids
        ]

        in_progress_tasks = [t for t in mission_kb_tasks if str(t.get("status", "")).lower() in ("in_progress", "running")]
        ready_tasks = [t for t in mission_kb_tasks if str(t.get("status", "")).lower() in ("ready", "pending")]
        done_tasks = [t for t in mission_kb_tasks if str(t.get("status", "")).lower() in ("done", "completed")]
        failed_tasks = [t for t in mission_kb_tasks if str(t.get("status", "")).lower() in ("failed", "error")]
        blocked_tasks = [t for t in mission_kb_tasks if str(t.get("status", "")).lower() == "blocked"]

        target_mission = mission_id
        mission_goal = "Software Engineering & System Verification"
        mission_status = NodeStatus.IDLE

        active_kb_task = None
        if in_progress_tasks:
            active_kb_task = in_progress_tasks[0]
            mission_status = NodeStatus.RUNNING
            mission_goal = active_kb_task.get("title") or active_kb_task.get("body") or mission_goal
            target_mission = target_mission or active_kb_task.get("id")
        elif ready_tasks:
            active_kb_task = ready_tasks[0]
            mission_status = NodeStatus.IDLE
            mission_goal = f"Pronta para despacho: {active_kb_task.get('title') or active_kb_task.get('body') or active_kb_task.get('id')}"
            target_mission = target_mission or active_kb_task.get("id")
        elif failed_tasks and not done_tasks:
            active_kb_task = failed_tasks[0]
            mission_status = NodeStatus.FAILED
            mission_goal = f"Falha na tarefa: {active_kb_task.get('title') or active_kb_task.get('id')}"
            target_mission = target_mission or active_kb_task.get("id")
        elif blocked_tasks and not done_tasks:
            active_kb_task = blocked_tasks[0]
            mission_status = NodeStatus.BLOCKED
            mission_goal = f"Tarefa bloqueada: {active_kb_task.get('title') or active_kb_task.get('id')}"
            target_mission = target_mission or active_kb_task.get("id")
        elif done_tasks and not in_progress_tasks and not ready_tasks:
            active_kb_task = done_tasks[0]
            mission_status = NodeStatus.COMPLETED
            mission_goal = "Todas as tarefas foram concluídas com sucesso"
            target_mission = target_mission or active_kb_task.get("id")
        elif mission_kb_tasks:
            active_kb_task = mission_kb_tasks[0]
            raw_st = str(active_kb_task.get("status", "")).lower()
            if raw_st in ("done", "completed"):
                mission_status = NodeStatus.COMPLETED
            elif raw_st in ("in_progress", "running"):
                mission_status = NodeStatus.RUNNING
            else:
                mission_status = NodeStatus.IDLE
            mission_goal = active_kb_task.get("title") or active_kb_task.get("body") or mission_goal
            target_mission = target_mission or active_kb_task.get("id")

        if not target_mission:
            for ev in reversed(events):
                m_id = ev.payload.get("mission_id") or ev.payload.get("task_id")
                if m_id:
                    target_mission = m_id
                    break

        target_mission = target_mission or "mission-live-01"

        # Check mission-level events
        for ev in reversed(events):
            p = ev.payload
            m = p.get("mission_id")
            if not mission_id or m == target_mission:
                if ev.name == "mission.completed":
                    if not in_progress_tasks:
                        mission_status = NodeStatus.COMPLETED
                    break
                elif ev.name == "mission.failed":
                    if not in_progress_tasks:
                        mission_status = NodeStatus.FAILED
                    break

        workers_dict: Dict[str, Dict[str, Any]] = {}

        # 1. Process EventStore events
        mission_events = []
        if target_mission:
            for ev in events:
                p = ev.payload or {}
                if p.get("mission_id") == target_mission or p.get("team_id") == target_mission:
                    mission_events.append(ev)
        else:
            for ev in reversed(events):
                mission_events.append(ev)
                if ev.name == "team.formed" and ev.payload.get("mission_id") == target_mission:
                    break
            mission_events.reverse()

        for ev in mission_events:
            p = ev.payload
            wid = p.get("worker_id")
            if wid:
                if str(wid).lower() in hierarchy_bot_ids:
                    continue
                posture = p.get("posture") or ("reviewer" if "reviewer" in wid else "coder")
                role = "reviewer" if posture == "reviewer" else "worker"
                label = "Witness Reviewer" if posture == "reviewer" else "Polecat Coder"

                ev_st = "idle"
                if ev.name in ("worker.completed", "task.completed"):
                    ev_st = "completed"
                elif ev.name in ("worker.failed", "task.failed"):
                    ev_st = "failed"
                elif in_progress_tasks:
                    ev_st = "running" if mission_status == NodeStatus.RUNNING else "idle"
                elif mission_status == NodeStatus.COMPLETED:
                    ev_st = "completed"

                w_model, w_prov = _resolve_role_model_binding(posture)
                if wid not in workers_dict:
                    workers_dict[wid] = {
                        "id": wid,
                        "label": label,
                        "role": role,
                        "posture": posture,
                        "status": ev_st,
                        "model": w_model,
                        "provider": w_prov,
                        "task": p.get("task_id", ""),
                        "tokens": p.get("tokens", 640 if posture == "coder" else 180),
                        "cost": 0.0006 if posture == "coder" else 0.0002,
                        "hierarchy_node_id": p.get("hierarchy_node_id"),
                    }
                else:
                    if p.get("task_id"):
                        workers_dict[wid]["task"] = p.get("task_id")
                    if p.get("tokens"):
                        workers_dict[wid]["tokens"] += p.get("tokens")
                    if ev_st != "idle":
                        workers_dict[wid]["status"] = ev_st

            # Handle HAOS delegation bridge events (haos.task.spawned / haos.task.completed)
            if ev.name == "haos.task.spawned":
                task_id = p.get("task_id")
                assignee = p.get("assignee") or task_id or "worker"
                # Se for atribuição direta a um bot da hierarquia persistente, não polui o Team Graph
                if str(assignee).lower() in hierarchy_bot_ids:
                    continue
                is_rev = "reviewer" in assignee or "witness" in assignee
                posture = "reviewer" if is_rev else "coder"
                role = "reviewer" if is_rev else "worker"
                label = f"Witness ({assignee})" if is_rev else f"Polecat ({assignee})"
                workers_dict[assignee] = {
                    "id": assignee,
                    "label": label,
                    "role": role,
                    "posture": posture,
                    "status": "running" if p.get("status") in ("in_progress", "running") else "idle",
                    "model": p.get("model") or _resolve_role_model_binding(posture)[0],
                    "provider": p.get("provider") or _resolve_role_model_binding(posture)[1],
                    "task": p.get("goal") or task_id,
                    "tokens": p.get("tokens", 0),
                    "cost": p.get("cost", 0.0),
                    "hierarchy_node_id": p.get("hierarchy_node_id"),
                }
            elif ev.name == "haos.task.completed":
                task_id = p.get("task_id")
                for w in workers_dict.values():
                    if w.get("task") == task_id or w.get("id") == task_id or task_id in str(w.get("id")):
                        w["status"] = "idle"
                        w["task"] = f"Livre (concluído: {task_id})"

        # 2. Reconcile with Kanban tasks as primary source of truth for active worker states
        kb_by_assignee: Dict[str, List[Dict[str, Any]]] = {}
        for t in kb_tasks:
            assignee = t.get("assignee")
            if assignee and assignee not in ("engine", "hermes", "root", "user"):
                if str(assignee).lower() in hierarchy_bot_ids:
                    # Isolamento estrito: Tarefas atribuídas diretamente a um Bot da Hierarquia
                    # pertencem ao ciclo de vida do Bot, e NÃO poluem o Team Graph!
                    continue
                kb_by_assignee.setdefault(assignee, []).append(t)

        for assignee, tasks_list in kb_by_assignee.items():
            is_rev = "reviewer" in assignee.lower() or "witness" in assignee.lower()
            posture = "reviewer" if is_rev else "coder"
            role = "reviewer" if is_rev else "worker"
            label = f"Witness ({assignee})" if is_rev else f"Polecat ({assignee})"

            # Determine canonical status for this assignee from their tasks
            has_running = any(str(t.get("status", "")).lower() in ("in_progress", "running") for t in tasks_list)
            has_failed = any(str(t.get("status", "")).lower() in ("failed", "error") for t in tasks_list)
            has_blocked = any(str(t.get("status", "")).lower() == "blocked" for t in tasks_list)
            has_ready = any(str(t.get("status", "")).lower() in ("ready", "pending") for t in tasks_list)
            all_done = bool(tasks_list) and all(str(t.get("status", "")).lower() in ("done", "completed") for t in tasks_list)

            if has_running:
                st = "running"
                running_t = next(t for t in tasks_list if str(t.get("status", "")).lower() in ("in_progress", "running"))
                cur_task = running_t.get("title") or running_t.get("body") or running_t.get("id")
            elif has_blocked:
                st = "blocked"
                blocked_t = next(t for t in tasks_list if str(t.get("status", "")).lower() == "blocked")
                cur_task = blocked_t.get("title") or blocked_t.get("body") or blocked_t.get("id")
                blocker_info = blocked_t.get("blocker")
            elif has_failed:
                st = "failed"
                failed_t = next(t for t in tasks_list if str(t.get("status", "")).lower() in ("failed", "error"))
                cur_task = failed_t.get("title") or failed_t.get("body") or failed_t.get("id")
            elif all_done:
                st = "idle"
                last_t = tasks_list[-1]
                cur_task = f"Livre (última: {last_t.get('title') or last_t.get('body') or last_t.get('id')})"
            elif has_ready:
                st = "idle"
                ready_t = next(t for t in tasks_list if str(t.get("status", "")).lower() in ("ready", "pending"))
                cur_task = f"Pronta: {ready_t.get('title') or ready_t.get('body') or ready_t.get('id')}"
            else:
                st = "idle"
                cur_task = ""

            if assignee not in workers_dict:
                workers_dict[assignee] = {
                    "id": assignee,
                    "label": label,
                    "role": role,
                    "posture": posture,
                    "status": st,
                    "model": _resolve_role_model_binding(posture)[0],
                    "provider": _resolve_role_model_binding(posture)[1],
                    "task": cur_task,
                    "tokens": 500,
                    "cost": 0.0005,
                    "blocker": blocker_info if st == "blocked" else None,
                }
            else:
                workers_dict[assignee]["status"] = st
                workers_dict[assignee]["task"] = cur_task
                if st == "blocked":
                    workers_dict[assignee]["blocker"] = blocker_info

        # 3. Contextual fallback if no workers discovered
        if not workers_dict:
            if in_progress_tasks:
                active_t = in_progress_tasks[0]
                is_rev_task = any(k in (active_t.get("title") or "").lower() or k in (active_t.get("phase") or "").lower() for k in ("review", "audit", "witness"))
                coder_st = "idle" if is_rev_task else "running"
                rev_st = "running" if is_rev_task else "idle"
                coder_task = "" if is_rev_task else (active_t.get("title") or active_t.get("id"))
                rev_task = (active_t.get("title") or active_t.get("id")) if is_rev_task else "Aguardando conclusão do código"
            elif mission_status == NodeStatus.COMPLETED:
                coder_st = "idle"
                rev_st = "idle"
                coder_task = "Livre (tarefas concluídas)"
                rev_task = "Livre (verificação concluída)"
            elif mission_status == NodeStatus.FAILED:
                coder_st = "failed"
                rev_st = "idle"
                coder_task = failed_tasks[0].get("title") if failed_tasks else "Falha na execução"
                rev_task = ""
            elif mission_status == NodeStatus.BLOCKED:
                coder_st = "blocked"
                rev_st = "idle"
                coder_task = blocked_tasks[0].get("title") if blocked_tasks else "Bloqueado"
                rev_task = ""
            else:
                coder_st = "idle"
                rev_st = "idle"
                coder_task = (ready_tasks[0].get("title") or ready_tasks[0].get("id")) if ready_tasks else ""
                rev_task = ""

            # Check if there are actual kanban tasks before inventing dummy specialists
            if kb_tasks:
                c_model, c_prov = _resolve_role_model_binding("coder")
                r_model, r_prov = _resolve_role_model_binding("reviewer")
                workers_dict = {
                    "specialist-coder-01": {
                        "id": "specialist-coder-01",
                        "label": "Polecat Coder",
                        "role": "worker",
                        "posture": "coder",
                        "status": coder_st,
                        "model": c_model,
                        "provider": c_prov,
                        "task": coder_task,
                        "tokens": 0,
                        "cost": 0.0,
                        "hierarchy_node_id": t.get("hierarchy_node_id") or (t.get("spec") or {}).get("hierarchy_node_id"),
                    },
                    "specialist-reviewer-01": {
                        "id": "specialist-reviewer-01",
                        "label": "Witness Reviewer",
                        "role": "reviewer",
                        "posture": "reviewer",
                        "status": rev_st,
                        "model": r_model,
                        "provider": r_prov,
                        "task": rev_task,
                        "tokens": 0,
                        "cost": 0.0,
                        "hierarchy_node_id": t.get("hierarchy_node_id") or (t.get("spec") or {}).get("hierarchy_node_id"),
                    },
                }
            else:
                workers_dict = {}

        # 4. Apply operator interventions
        for wid, w_info in workers_dict.items():
            intervention = self.get_pending_intervention(wid)
            if intervention == "pause":
                w_info["status"] = "paused"
            elif intervention in ("interrupt", "abort"):
                w_info["status"] = "failed"
            elif intervention == "resume" and w_info["status"] == "paused":
                w_info["status"] = "running" if in_progress_tasks else "idle"

        mayor_intervention = self.get_pending_intervention(f"mayor-{target_mission}") or self.get_pending_intervention("mayor")
        if mayor_intervention == "pause":
            mission_status = NodeStatus.PAUSED
        elif mayor_intervention in ("interrupt", "abort"):
            mission_status = NodeStatus.FAILED

        sub_orchestrators = [
            {
                "id": f"sub-orch-{target_mission}",
                "domain": "software",
                "workers": list(workers_dict.values()),
            }
        ]

        root = self.build_team_graph(
            mission_id=target_mission,
            goal=mission_goal,
            sub_orchestrators=sub_orchestrators,
            root_status=mission_status,
        )

        if mission_status == NodeStatus.COMPLETED:
            root.status = NodeStatus.COMPLETED
            for child in root.children:
                child.status = NodeStatus.COMPLETED
                for w in child.children:
                    w.status = NodeStatus.COMPLETED
        elif mission_status == NodeStatus.PAUSED:
            root.status = NodeStatus.PAUSED

        return root.to_dict()
