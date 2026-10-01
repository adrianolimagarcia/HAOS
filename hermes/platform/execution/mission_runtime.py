"""Profile-scoped cooperative mission supervisor.

This module is deliberately small: Kanban remains the task/run authority.  The
supervisor only compiles a mission into canonical cards, gates admission, and
runs an injected executor.  Production adapters may inject a dispatcher-backed
executor; tests can inject a deterministic function.  It never suspends a
process with SIGSTOP.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.execution.mission_store import MissionStore


@dataclass(frozen=True)
class MissionTask:
    node_id: str
    task_id: str
    spec: TaskSpec


class MissionSupervisor:
    """One mission control loop bound to one immutable profile/database."""

    def __init__(
        self,
        *,
        store: MissionStore,
        adapter: KanbanAdapter,
        profile_key: str,
        home_path: str | Path,
        executor: Optional[Callable[[TaskSpec, threading.Event], Any]] = None,
        poll_seconds: float = 0.25,
    ) -> None:
        if store.profile_key != str(profile_key):
            raise ValueError("mission store/profile mismatch")
        self.store = store
        self.adapter = adapter
        self.profile_key = str(profile_key)
        self.home_path = str(Path(home_path).resolve())
        self.executor = executor
        self.poll_seconds = max(0.01, float(poll_seconds))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: Dict[str, threading.Thread] = {}
        self._cancel: Dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def compile(self, mission: Dict[str, Any]) -> list[MissionTask]:
        """Persist workflow nodes as idempotent canonical cards and mappings.

        Nodes are topologically sorted so all parent tasks exist in Kanban DB
        before dependent tasks are saved.
        """
        mission_id = str(mission["id"])
        self.store.create_or_import(
            mission_id,
            title=mission.get("title"),
            objective=mission.get("goal"),
            metadata=mission,
        )
        workflow = mission.get("workflow") or {}
        nodes = workflow.get("nodes") or []
        edges = workflow.get("edges") or []

        # Map node dependencies
        node_map: Dict[str, Dict[str, Any]] = {str(n["id"]): n for n in nodes}
        deps: Dict[str, set[str]] = {nid: set() for nid in node_map}
        for edge in edges:
            from_node = str(edge.get("from_node"))
            to_node = str(edge.get("to_node"))
            if to_node in deps and from_node in node_map:
                deps[to_node].add(from_node)

        # Topological sort (Kahn's algorithm)
        in_degree: Dict[str, int] = {nid: len(parents) for nid, parents in deps.items()}
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        ordered_ids: list[str] = []

        while queue:
            curr = queue.pop(0)
            ordered_ids.append(curr)
            for nid, parents in deps.items():
                if curr in parents:
                    in_degree[nid] -= 1
                    if in_degree[nid] == 0:
                        queue.append(nid)

        if len(ordered_ids) < len(node_map):
            # Fallback to original order if cycle or disconnected node missing
            remaining = [nid for nid in node_map if nid not in ordered_ids]
            ordered_ids.extend(remaining)

        by_node: Dict[str, MissionTask] = {}
        for pos, node_id in enumerate(ordered_ids):
            node = node_map[node_id]
            parent_node_ids = sorted(deps.get(node_id, set()))
            requires_approval = bool(node.get("requires_approval"))
            spec = TaskSpec(
                id=f"civ-{mission_id}-{node_id}",
                title=str(node.get("action") or node_id),
                goal=str(node.get("action") or mission.get("goal") or mission.get("title") or node_id),
                description=str(mission.get("goal") or ""),
                created_by="civilization-ui",
                posture="implementer",
                agent_profile=str(node.get("agent_id") or "") or None,
                requires_tasks=[f"civ-{mission_id}-{dep}" for dep in parent_node_ids],
                tags=["civilization", f"mission:{mission_id}", f"node:{node_id}"],
                workspace_type="scratch",
            )
            # If task has unresolved parents, upstream create_task demotes to 'todo' or blocked
            task_id = self.adapter.save_task(spec, status="READY", phase="civilization")
            self.store.map_task(
                mission_id,
                task_id,
                role=str(node.get("role") or "agent"),
                position=pos,
                metadata={
                    "node_id": node_id,
                    "agent_id": str(node.get("agent_id") or ""),
                    "requires_approval": requires_approval,
                    "revision": int(mission.get("revision", 1)),
                },
            )
            by_node[node_id] = MissionTask(node_id=node_id, task_id=task_id, spec=spec)

        return [by_node[nid] for nid in ordered_ids]

    def start(self, mission: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = str(mission["id"])
        self.store.create_or_import(mission_id, title=mission.get("title"), objective=mission.get("goal"), metadata=mission)
        tasks = self.compile(mission)
        self.store.start(mission_id)
        with self._lock:
            thread = self._threads.get(mission_id)
            if thread is None or not thread.is_alive():
                self._stop.clear()
                thread = threading.Thread(
                    target=self._loop,
                    args=(mission_id,),
                    daemon=True,
                    name=f"civ-mission-{mission_id}",
                )
                self._threads[mission_id] = thread
                thread.start()
        self._wake.set()
        return {
            "mission_id": mission_id,
            "profile_key": self.profile_key,
            "status": "RUNNING",
            "task_ids": [t.task_id for t in tasks],
            "runtime_control": "queued",
        }

    def recover_active_missions(self) -> list[str]:
        """Scans for active running missions in this profile and ensures their loops are running."""
        recovered = []
        for m in self.store.list_active_missions():
            mid = str(m["mission_id"])
            with self._lock:
                thread = self._threads.get(mid)
                if thread is None or not thread.is_alive():
                    self._stop.clear()
                    thread = threading.Thread(
                        target=self._loop,
                        args=(mid,),
                        daemon=True,
                        name=f"civ-mission-{mid}",
                    )
                    self._threads[mid] = thread
                    thread.start()
                    recovered.append(mid)
        self._wake.set()
        return recovered

    def pause(self, mission_id: str) -> Dict[str, Any]:
        result = self.store.set_desired_state(mission_id, "paused")
        self._wake.set()
        mappings = self.store.list_task_mappings(mission_id)
        for m in mappings:
            tid = str(m.get("task_id", ""))
            if tid in self._cancel:
                self._cancel[tid].set()
        return {**result, "runtime_control": "cooperative_admission_gate"}

    def resume(self, mission_id: str) -> Dict[str, Any]:
        result = self.store.set_desired_state(mission_id, "running")
        with self._lock:
            thread = self._threads.get(mission_id)
            if thread is None or not thread.is_alive():
                self._stop.clear()
                thread = threading.Thread(
                    target=self._loop,
                    args=(mission_id,),
                    daemon=True,
                    name=f"civ-mission-{mission_id}",
                )
                self._threads[mission_id] = thread
                thread.start()
        self._wake.set()
        return {**result, "runtime_control": "queued"}

    def wake(self) -> None:
        """Nudge supervisor loops to re-evaluate immediately (e.g. after an
        approval gate is decided) instead of waiting out the poll interval."""
        self._wake.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        for cancel in list(self._cancel.values()):
            cancel.set()
        threads = list(self._threads.values())
        deadline = time.time() + max(0.1, timeout)
        for thread in threads:
            rem = max(0.0, deadline - time.time())
            thread.join(timeout=rem)
        alive = [t.name for t in threads if t.is_alive()]
        if alive:
            raise RuntimeError(f"mission supervisor threads did not stop: {alive}")

    def _loop(self, mission_id: str) -> None:
        try:
            self._loop_inner(mission_id)
        except Exception as exc:
            import traceback
            traceback.print_exc()

    def _loop_inner(self, mission_id: str) -> None:
        while not self._stop.is_set():
            state = self.store.get(mission_id) or {}
            desired = state.get("desired_state")
            if desired == "paused":
                self.store._set_state(mission_id, actual_state="paused")
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
                continue
            if desired in ("completed", "failed", "cancelled"):
                self.store._set_state(mission_id, actual_state=desired)
                break

            if state.get("actual_state") != "running":
                self.store._set_state(mission_id, actual_state="running")

            mappings = self.store.list_task_mappings(mission_id)
            if not mappings:
                break

            all_done = True
            progressed = False

            for mapping in mappings:
                if self._stop.is_set() or (self.store.get(mission_id) or {}).get("desired_state") == "paused":
                    break
                task_id = str(mapping["task_id"])
                task = self.adapter.get_task(task_id)
                if not task:
                    continue

                status = str(task.get("status", "")).lower()
                if status in ("done", "completed"):
                    continue

                all_done = False
                if status not in ("ready", "todo", "blocked", "triage"):
                    continue

                spec_data = task.get("spec") or {}
                requires = spec_data.get("requires_tasks") or []
                if requires:
                    parents_done = True
                    for parent_id in requires:
                        ptask = self.adapter.get_task(parent_id)
                        if not ptask or str(ptask.get("status", "")).lower() not in ("done", "completed"):
                            parents_done = False
                            break
                    if not parents_done:
                        continue

                # Check approval gate
                meta = mapping.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                requires_approval = bool(meta.get("requires_approval"))
                node_id = meta.get("node_id") or task_id
                agent_id = meta.get("agent_id") or task.get("assignee") or "agent"

                if requires_approval:
                    gate_id = f"gate-{mission_id}-{node_id}"
                    gate = self.store.get_gate(gate_id)
                    if not gate:
                        self.store.request_gate(
                            mission_id,
                            gate_id=gate_id,
                            task_id=task_id,
                            node_id=node_id,
                            kind="approval",
                            action="deploy",
                            requested_by=agent_id,
                            payload={"description": f"Operator approval required for {node_id}"},
                        )
                        continue
                    if gate.get("status") == "pending":
                        continue
                    if gate.get("status") != "approved":
                        self.adapter.record_task_failure(task_id, error="approval gate rejected", outcome="failed")
                        progressed = True
                        continue

                if self.executor is None:
                    continue

                try:
                    spec = TaskSpec.from_dict(spec_data) if hasattr(TaskSpec, "from_dict") else None
                except Exception:
                    spec = None
                if spec is None:
                    continue

                cancel = threading.Event()
                self._cancel[task_id] = cancel
                try:
                    claimed = self.adapter.claim_task(task_id, worker_id=f"civ:{self.profile_key}")
                    if not claimed:
                        continue

                    result = self.executor(spec, cancel)
                    if cancel.is_set():
                        self.adapter.record_task_failure(
                            task_id,
                            error="task cancelled cooperatively",
                            outcome="cancelled",
                        )
                    else:
                        tokens = 100
                        if isinstance(result, dict):
                            summary = str(result.get("summary") or result.get("result") or "")
                            evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
                            tokens = int(result.get("tokens") or result.get("total_tokens") or 100)
                        else:
                            summary, evidence = str(result or ""), {}
                        evidence["tokens"] = tokens
                        self.adapter.complete_task(task_id, summary=summary, evidence=evidence)
                        self.store.record_usage(
                            mission_id,
                            task_id=task_id,
                            run_id=f"run-{task_id}",
                            provider="deterministic",
                            model="lane-worker",
                            total_tokens=tokens,
                        )
                    progressed = True
                except Exception as exc:
                    outcome = "cancelled" if cancel.is_set() else "failed"
                    self.adapter.record_task_failure(task_id, error=str(exc), outcome=outcome)
                    progressed = True
                finally:
                    self._cancel.pop(task_id, None)

            if all_done:
                self.store._set_state(mission_id, actual_state="completed")
                break

            if not progressed:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()

    def cancel_task(self, task_id: str) -> bool:
        event = self._cancel.get(str(task_id))
        if event is None:
            return False
        event.set()
        return True


__all__ = ["MissionSupervisor", "MissionTask"]
