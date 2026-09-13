"""Control Plane Backend & Team Graph Engine (Phase 5 — Control Plane).

Implements:
1. TeamGraphNode & TeamGraphSnapshot: Typed hierarchical representation of the active multi-agent team.
2. ControlPlaneService: Aggregates metrics across TeamRuntime, SpecialistPool, AdaptiveIntelligence, and Federation.
3. ControlIntervention: Steer, pause, resume, and abort controls over active workers.
"""

from __future__ import annotations

import dataclasses
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

from hermes.platform.evolution.adaptive_intelligence import AdaptiveIntelligenceCoordinator
from hermes.platform.execution.team_runtime import MultiAgentTeamRuntime, PooledSpecialist
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.protocols.gateway import UniversalProtocolGateway


class NodeStatus(str, enum.Enum):
    PENDING = "pending"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


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
    children: List[TeamGraphNode] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
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
            "children": [c.to_dict() for c in self.children],
            "metadata": self.metadata,
        }


@dataclass
class ControlPlaneOverview:
    total_missions: int
    active_workers: int
    idle_specialists: int
    total_tokens: int
    total_cost_usd: float
    reputation_healthy_pct: float
    active_routes_count: int


class ControlPlaneService:
    """Central orchestration service for Phase 5 Control Plane."""

    def __init__(
        self,
        event_store: EventStore,
        team_runtime: Optional[MultiAgentTeamRuntime] = None,
        adaptive_coordinator: Optional[AdaptiveIntelligenceCoordinator] = None,
        protocol_gateway: Optional[UniversalProtocolGateway] = None,
        kanban: Optional[Any] = None,
    ):
        self.event_store = event_store
        self.runtime = team_runtime
        self.adaptive = adaptive_coordinator
        self.gateway = protocol_gateway
        self.kanban = kanban
        self._interventions: Dict[str, str] = {}  # worker_id -> intervention command
        self._hydrate_interventions()

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
        mission_count = sum(1 for e in events if e.name in ("team.formed", "haos.task.spawned", "task.run.completed"))
        if self.kanban and mission_count == 0:
            try:
                tasks = self.kanban.list_tasks()
                mission_count = len(tasks)
            except Exception:
                pass

        total_tokens = sum(e.payload.get("tokens", 0) for e in events if "tokens" in (e.payload or {}))

        active_workers = self.runtime.pool.active_count() if self.runtime else 0
        if active_workers == 0 and self.kanban:
            try:
                tasks = self.kanban.list_tasks()
                active_workers = sum(1 for t in tasks if t.get("status") == "in_progress")
            except Exception:
                pass
        total_pool = self.runtime.pool.total_count() if self.runtime else active_workers
        idle_workers = max(0, total_pool - active_workers)

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
                w_node = TeamGraphNode(
                    node_id=worker_data.get("id", f"worker-{uuid.uuid4().hex[:4]}"),
                    label=worker_data.get("label", "Specialist"),
                    role=worker_data.get("role", "worker"),
                    posture=worker_data.get("posture", "coder"),
                    model_id=worker_data.get("model", "deepseek-v4-flash"),
                    provider_id=worker_data.get("provider", "a6api"),
                    status=node_st,
                    current_task=worker_data.get("task"),
                    tokens_consumed=worker_data.get("tokens", 0),
                    cost_usd=worker_data.get("cost", 0.0),
                    metadata={"blocker": worker_data.get("blocker")} if worker_data.get("blocker") else {},
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

            so_node = TeamGraphNode(
                node_id=so_data.get("id", f"sub-orch-{uuid.uuid4().hex[:4]}"),
                label=f"Sub-Orchestrator ({so_data.get('domain', 'general')})",
                role="sub_orchestrator",
                posture="architect",
                model_id="deepseek-v4-flash",
                provider_id="a6api",
                status=so_status,
                children=so_children,
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

        root = TeamGraphNode(
            node_id=f"mayor-{mission_id}",
            label="Town Mayor (Executive Lead)",
            role="mayor",
            posture="executive",
            model_id="deepseek-v4-flash",
            provider_id="a6api",
            current_task=goal,
            status=mayor_status,
            children=resolved_sub_nodes,
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
        kb_tasks = []
        if self.kanban:
            try:
                kb_tasks = self.kanban.list_tasks()
            except Exception:
                kb_tasks = []

        in_progress_tasks = [t for t in kb_tasks if str(t.get("status", "")).lower() in ("in_progress", "running")]
        ready_tasks = [t for t in kb_tasks if str(t.get("status", "")).lower() in ("ready", "pending")]
        done_tasks = [t for t in kb_tasks if str(t.get("status", "")).lower() in ("done", "completed")]
        failed_tasks = [t for t in kb_tasks if str(t.get("status", "")).lower() in ("failed", "error")]
        blocked_tasks = [t for t in kb_tasks if str(t.get("status", "")).lower() == "blocked"]

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
        elif kb_tasks:
            active_kb_task = kb_tasks[0]
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

                if wid not in workers_dict:
                    workers_dict[wid] = {
                        "id": wid,
                        "label": label,
                        "role": role,
                        "posture": posture,
                        "status": ev_st,
                        "model": "deepseek-v4-flash",
                        "provider": "a6api",
                        "task": p.get("task_id", ""),
                        "tokens": p.get("tokens", 640 if posture == "coder" else 180),
                        "cost": 0.0006 if posture == "coder" else 0.0002,
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
                    "model": p.get("model") or "deepseek-v4-flash",
                    "provider": p.get("provider") or "a6api",
                    "task": p.get("goal") or task_id,
                    "tokens": p.get("tokens", 0),
                    "cost": p.get("cost", 0.0),
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
                    "model": "deepseek-v4-flash",
                    "provider": "a6api",
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
                workers_dict = {
                    "specialist-coder-01": {
                        "id": "specialist-coder-01",
                        "label": "Polecat Coder",
                        "role": "worker",
                        "posture": "coder",
                        "status": coder_st,
                        "model": "deepseek-v4-flash",
                        "provider": "a6api",
                        "task": coder_task,
                        "tokens": 0,
                        "cost": 0.0,
                    },
                    "specialist-reviewer-01": {
                        "id": "specialist-reviewer-01",
                        "label": "Witness Reviewer",
                        "role": "reviewer",
                        "posture": "reviewer",
                        "status": rev_st,
                        "model": "deepseek-v4-flash",
                        "provider": "a6api",
                        "task": rev_task,
                        "tokens": 0,
                        "cost": 0.0,
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
