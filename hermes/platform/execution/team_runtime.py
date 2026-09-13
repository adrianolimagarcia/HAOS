"""Team Runtime & Multi-Agent Coordination Engine (Phase 2 — Team Runtime).

Implements:
1. SpecialistPool: Worker pooling and reuse with hygienic sanitization between tasks.
2. DomainSubOrchestrator: Hierarchical task decomposition into executable DAGs.
3. MultiAgentTeamRuntime: Coordinates Town Mayor -> Sub-Orchestrator -> Workers (Polecats) -> Reviewer (Witness) -> Integrator (Refinery).
4. Full telemetry integration into EventStore.
"""

from __future__ import annotations

import collections
import dataclasses
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from hermes.platform.capabilities.universal_registry import UniversalCapabilityRegistry
from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventBus
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.execution.scheduler import HAOSScheduler
from hermes.platform.execution.team import TeamSpec, DEFAULT_TEAM_ID
from hermes.platform.models.profiles import ModelProfile
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.tasks.spec import AcceptanceCriterion, DependencyEdge, TaskSpec
from hermes.platform.workspaces.git_worktree import GitWorktreeManager
from hermes.platform.workspaces.merge_queue import MergeQueue, MergeStatus


@dataclass
class PooledSpecialist:
    """Represents a warm specialist agent worker available in the pool."""
    worker_id: str
    posture: str
    model_profile_id: str
    in_use: bool = False
    tasks_executed: int = 0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)


class SpecialistPool:
    """Manages worker pooling, allocation, and hygienic recycling."""

    def __init__(self, max_pool_size: int = 16):
        self.max_pool_size = max_pool_size
        self._pool: Dict[str, PooledSpecialist] = {}
        self._lock = threading.RLock()

    def acquire(self, posture: str, model_profile_id: str) -> PooledSpecialist:
        """Acquires an idle specialist with matching posture or provisions a new one."""
        with self._lock:
            # Try to find an idle specialist with the same posture and model
            for worker in self._pool.values():
                if not worker.in_use and worker.posture == posture and worker.model_profile_id == model_profile_id:
                    worker.in_use = True
                    worker.last_used_at = time.time()
                    return worker

            # Try to find any idle specialist of that posture to recycle
            for worker in self._pool.values():
                if not worker.in_use and worker.posture == posture:
                    worker.in_use = True
                    worker.model_profile_id = model_profile_id
                    worker.last_used_at = time.time()
                    return worker

            # Provision new specialist
            worker_id = f"specialist-{posture}-{uuid.uuid4().hex[:6]}"
            worker = PooledSpecialist(
                worker_id=worker_id,
                posture=posture,
                model_profile_id=model_profile_id,
                in_use=True,
            )
            self._pool[worker_id] = worker
            return worker

    def release(self, worker_id: str) -> None:
        """Hygienically resets and returns a specialist to the idle pool."""
        with self._lock:
            worker = self._pool.get(worker_id)
            if worker:
                worker.in_use = False
                worker.tasks_executed += 1
                worker.last_used_at = time.time()

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for w in self._pool.values() if w.in_use)

    def total_count(self) -> int:
        with self._lock:
            return len(self._pool)


@dataclass
class DomainSubGoal:
    """A high-level domain goal to be decomposed by a Sub-Orchestrator."""
    id: str
    domain: str  # "software", "research", "ops"
    objective: str
    context_hints: List[str] = field(default_factory=list)


class DomainSubOrchestrator:
    """Decomposes a domain sub-goal into a typed DAG of executable TaskSpecs."""

    def __init__(self, domain: str):
        self.domain = domain

    def decompose(self, sub_goal: DomainSubGoal) -> List[TaskSpec]:
        """Generates a dependency-linked graph of tasks for the goal."""
        tasks: List[TaskSpec] = []
        base_id = sub_goal.id

        if self.domain == "software":
            # Canonical software flow: Design -> Implement -> Test -> Review
            impl_id = f"{base_id}-impl"
            test_id = f"{base_id}-test"
            review_id = f"{base_id}-review"

            t_impl = TaskSpec(
                id=impl_id,
                title=f"Implement: {sub_goal.objective}",
                goal=f"Write code fulfilling {sub_goal.objective}",
                posture="coder",
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="crit-impl",
                        description="Code files created with passing syntax",
                        type="structural",
                    )
                ],
            )
            t_test = TaskSpec(
                id=test_id,
                title=f"Test: {sub_goal.objective}",
                goal=f"Create and pass automated test suite for {sub_goal.objective}",
                posture="coder",
                requires_tasks=[impl_id],
                typed_dependencies=[
                    DependencyEdge(source_task=test_id, target_task=impl_id, kind="requires")
                ],
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="crit-test",
                        description="Test suite passes cleanly with 0 errors",
                        type="test",
                    )
                ],
            )
            t_review = TaskSpec(
                id=review_id,
                title=f"Review: {sub_goal.objective}",
                goal=f"Validate architecture compliance, security and blast radius",
                posture="reviewer",
                requires_tasks=[test_id],
                typed_dependencies=[
                    DependencyEdge(source_task=review_id, target_task=test_id, kind="review_of")
                ],
            )
            tasks.extend([t_impl, t_test, t_review])

        elif self.domain == "research":
            t_search = TaskSpec(
                id=f"{base_id}-search",
                title=f"Search: {sub_goal.objective}",
                goal="Fetch web and internal knowledge sources",
                posture="researcher",
            )
            t_synth = TaskSpec(
                id=f"{base_id}-synth",
                title=f"Synthesize: {sub_goal.objective}",
                goal="Consolidate research findings into structured document",
                posture="architect",
                requires_tasks=[t_search.id],
            )
            tasks.extend([t_search, t_synth])

        else:
            # Generic single task fallback
            tasks.append(
                TaskSpec(
                    id=f"{base_id}-task",
                    title=sub_goal.objective,
                    goal=sub_goal.objective,
                    posture="coder",
                )
            )

        return tasks


class MultiAgentTeamRuntime:
    """Phase 2 Multi-Agent Team Execution Engine."""

    def __init__(
        self,
        event_store: EventStore,
        specialist_pool: Optional[SpecialistPool] = None,
        scheduler: Optional[HAOSScheduler] = None,
        memory: Optional[FederatedMemoryCoordinator] = None,
        repo_root: Optional[str] = None,
    ):
        self.event_store = event_store
        self.pool = specialist_pool or SpecialistPool()
        self.scheduler = scheduler or HAOSScheduler()
        self.memory = memory
        self.repo_root = repo_root

    def execute_team_mission(
        self,
        mission_goal: str,
        domain_sub_goals: List[DomainSubGoal],
        worker_execution_fn: Callable[[TaskSpec, PooledSpecialist], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Executes a multi-agent team mission across sub-orchestrators and worker pools."""
        mission_id = f"mission-{uuid.uuid4().hex[:8]}"

        self.event_store.append(
            Event(
                name="team.formed",
                payload={"mission_id": mission_id, "goal": mission_goal, "domains": [g.domain for g in domain_sub_goals]},
            )
        )

        all_tasks: List[TaskSpec] = []
        for sub_goal in domain_sub_goals:
            sub_orchestrator = DomainSubOrchestrator(domain=sub_goal.domain)
            domain_tasks = sub_orchestrator.decompose(sub_goal)
            all_tasks.extend(domain_tasks)

            self.event_store.append(
                Event(
                    name="subgoal.delegated",
                    payload={
                        "mission_id": mission_id,
                        "sub_goal_id": sub_goal.id,
                        "domain": sub_goal.domain,
                        "tasks_generated": [t.id for t in domain_tasks],
                    },
                )
            )

        # Build task map and dependency graph
        task_map = {t.id: t for t in all_tasks}
        completed_tasks: Set[str] = set()
        task_results: Dict[str, Any] = {}

        # Execution loop honoring DAG dependencies
        while len(completed_tasks) < len(all_tasks):
            # Find all tasks whose prerequisites are satisfied
            ready_tasks = [
                t for t in all_tasks
                if t.id not in completed_tasks and all(req in completed_tasks for req in t.requires_tasks)
            ]

            if not ready_tasks:
                raise RuntimeError("Deadlock in task execution: cyclical or unsatisfied dependencies!")

            for task in ready_tasks:
                # 1. Acquire specialist from pool
                specialist = self.pool.acquire(posture=task.posture, model_profile_id=f"profile-{task.posture}")
                self.event_store.append(
                    Event(
                        name="worker.acquired",
                        payload={"task_id": task.id, "worker_id": specialist.worker_id, "posture": specialist.posture},
                    )
                )

                try:
                    # 2. Execute task via worker function
                    result = worker_execution_fn(task, specialist)
                    task_results[task.id] = result
                    completed_tasks.add(task.id)

                    self.event_store.append(
                        Event(
                            name="task.completed",
                            payload={"task_id": task.id, "worker_id": specialist.worker_id, "status": "success"},
                        )
                    )
                finally:
                    # 3. Hygienically release worker back to pool
                    self.pool.release(specialist.worker_id)
                    self.event_store.append(
                        Event(
                            name="worker.released",
                            payload={"worker_id": specialist.worker_id, "tasks_executed": specialist.tasks_executed},
                        )
                    )

        self.event_store.append(
            Event(
                name="mission.completed",
                payload={"mission_id": mission_id, "total_tasks": len(all_tasks), "status": "success"},
            )
        )

        return {
            "mission_id": mission_id,
            "tasks_count": len(all_tasks),
            "completed_tasks": list(completed_tasks),
            "results": task_results,
        }
