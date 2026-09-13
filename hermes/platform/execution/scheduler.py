"""Scheduler com suporte a Critical Path Method (CPM), Priority Inheritance e Anti-Starvation.

Mantém compatibilidade total com HAOSScheduler v1.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from hermes.platform.tasks.spec import TaskSpec, DependencyEdge
from hermes.platform.execution.spawn_resolver import SpawnResolver
from hermes.platform.execution.assignment import AssignmentSpec


def pick_with_fairness(
    scored: List[Tuple[str, float]], last_scheduled_id: Optional[str]
) -> str:
    """Puro: entre os candidatos de MAIOR score, roda (anti-starvation).

    - Maior score vence sempre (nunca sacrifica prioridade por justiça).
    - Empate no topo: prefere quem NÃO foi o último a rodar; ida e volta
      estável para 2+ candidatos empatados.
    """
    if not scored:
        raise ValueError("scored cannot be empty")
    top_score = max(score for _, score in scored)
    top = [task_id for task_id, score in scored if abs(score - top_score) < 1e-9]
    if len(top) == 1:
        return top[0]
    if last_scheduled_id in top and len(top) > 1:
        idx = top.index(last_scheduled_id)
        return top[(idx + 1) % len(top)]
    return top[0]


def extract_requires_dependencies(tasks: Iterable[TaskSpec]) -> Dict[str, Set[str]]:
    """Extrai o mapeamento task_id -> set(predecessor_ids) onde task_id depende de predecessor_id.

    Respeita task.requires_tasks e typed_dependencies com kind == 'requires'.
    """
    deps: Dict[str, Set[str]] = defaultdict(set)
    for task in tasks:
        # 1. requires_tasks direto
        for req in (task.requires_tasks or []):
            deps[task.id].add(req)

        # 2. typed_dependencies: DependencyEdge(source_task, target_task, kind="requires")
        for edge in getattr(task, "typed_dependencies", []) or []:
            if getattr(edge, "kind", None) == "requires":
                deps[edge.source_task].add(edge.target_task)

    return deps


def compute_deterministic_critical_path(tasks: Iterable[TaskSpec]) -> List[str]:
    """Calcula deterministamente os IDs das tasks no Critical Path via CPM.

    Desempates utilizam ordenação lexicográfica estável de task.id.
    Retorna lista vazia em caso de grafo com ciclos.
    """
    task_map = {t.id: t for t in tasks}
    if not task_map:
        return []

    req_deps = extract_requires_dependencies(task_map.values())

    # Grafo de execução: u precisa rodar antes de v (u -> v)
    # se v requer u: u é predecessor, v é sucessor
    adj: Dict[str, Set[str]] = defaultdict(set)
    in_degree: Dict[str, int] = {tid: 0 for tid in task_map}

    for tid, preds in req_deps.items():
        if tid not in task_map:
            continue
        for p in preds:
            if p in task_map:
                adj[p].add(tid)
                in_degree[tid] += 1

    # Ordenação topológica determinística (Kahn com lista ordenada)
    zero_in = sorted([tid for tid, deg in in_degree.items() if deg == 0])
    topo_order: List[str] = []

    curr_in_degree = dict(in_degree)
    available = list(zero_in)

    while available:
        available.sort()
        curr = available.pop(0)
        topo_order.append(curr)
        for nxt in sorted(adj[curr]):
            curr_in_degree[nxt] -= 1
            if curr_in_degree[nxt] == 0:
                available.append(nxt)

    # Detecção de ciclo: se não percorreu todos os nós, há ciclo
    if len(topo_order) != len(task_map):
        return []

    durations = {
        tid: max(1.0, float(task_map[tid].max_runtime_minutes or 1.0))
        for tid in task_map
    }

    # Forward Pass: Earliest Start (ES) e Earliest Finish (EF)
    es: Dict[str, float] = {tid: 0.0 for tid in task_map}
    ef: Dict[str, float] = {tid: 0.0 for tid in task_map}

    for u in topo_order:
        ef[u] = es[u] + durations[u]
        for v in adj[u]:
            if ef[u] > es[v]:
                es[v] = ef[u]

    max_project_duration = max(ef.values()) if ef else 0.0

    # Backward Pass: Latest Finish (LF) e Latest Start (LS)
    lf: Dict[str, float] = {tid: max_project_duration for tid in task_map}
    ls: Dict[str, float] = {}

    for u in reversed(topo_order):
        if adj[u]:
            lf[u] = min(ls[v] for v in adj[u])
        ls[u] = lf[u] - durations[u]

    # Identificar Critical Path (Slack == 0)
    critical_path: List[str] = [
        tid for tid in topo_order
        if abs(lf[tid] - ef[tid]) < 1e-6
    ]
    return critical_path


def compute_inherited_priorities(tasks: Iterable[TaskSpec]) -> Dict[str, float]:
    """Propaga prioridade de tarefas dependentes para seus requisitos (Priority Inheritance Protocol).

    Se A (100) requer B (20), a prioridade herdada de B torna-se 100.
    """
    task_map = {t.id: t for t in tasks}
    if not task_map:
        return {}

    req_deps = extract_requires_dependencies(task_map.values())
    dependents: Dict[str, Set[str]] = defaultdict(set)
    for tid, preds in req_deps.items():
        if tid in task_map:
            for p in preds:
                if p in task_map:
                    dependents[p].add(tid)

    memo: Dict[str, float] = {}
    visiting: Set[str] = set()

    def get_effective(tid: str) -> float:
        if tid in memo:
            return memo[tid]
        if tid in visiting:
            # Proteção contra ciclos de dependência
            return float(task_map[tid].priority)

        visiting.add(tid)
        base = float(task_map[tid].priority)
        inherited = base

        for dep_tid in dependents.get(tid, ()):
            dep_effective = get_effective(dep_tid)
            if dep_effective > inherited:
                inherited = dep_effective

        visiting.remove(tid)
        memo[tid] = inherited
        return inherited

    for tid in task_map:
        get_effective(tid)

    return memo


class HAOSScheduler:
    """Scheduler com concorrência limitada, Critical Path, PIP e Anti-Starvation."""

    def __init__(
        self,
        spawn_resolver: Optional[SpawnResolver] = None,
        max_concurrency: int = 8,
        age_bonus_rate: float = 1.0,
        max_age_bonus: float = 30.0,
    ):
        self.spawn_resolver = spawn_resolver or SpawnResolver()
        self.max_concurrency = max_concurrency
        self.age_bonus_rate = age_bonus_rate
        self.max_age_bonus = max_age_bonus
        self.last_reject_reason: str = ""
        self._last_scheduled_id: Optional[str] = None
        self._wait_ticks: Dict[str, int] = defaultdict(int)

    def calculate_priority_score(
        self,
        task: TaskSpec,
        critical_path_ids: Optional[List[str]] = None,
        inherited_priority: Optional[float] = None,
        wait_ticks: Optional[int] = None,
    ) -> float:
        """Calcula o score de prioridade de uma task mantendo retrocompatibilidade de assinatura."""
        cp_ids = critical_path_ids or []

        # 1. Base / Priority Inheritance
        base_priority = (
            inherited_priority
            if inherited_priority is not None
            else float(task.priority)
        )
        score = base_priority

        # 2. Critical Path Bonus
        if task.id in cp_ids:
            score += 50.0

        # 3. Risk Level Bonus
        if task.risk_level == "high":
            score += 10.0
        elif task.risk_level == "critical":
            score += 20.0

        # 4. Age Bonus (Starvation mitigation)
        ticks = wait_ticks if wait_ticks is not None else self._wait_ticks.get(task.id, 0)
        age_bonus = min(self.max_age_bonus, ticks * self.age_bonus_rate)
        score += age_bonus

        return score

    def schedule_next(
        self,
        ready_tasks: List[TaskSpec],
        critical_path_ids: Optional[List[str]] = None,
        running_count: int = 0,
        all_tasks: Optional[List[TaskSpec]] = None,
    ) -> Optional[AssignmentSpec]:
        """Próxima atribuição com backpressure, CPM, PIP e fairness."""
        self.last_reject_reason = ""
        if running_count >= self.max_concurrency:
            self.last_reject_reason = "backpressure"
            return None
        if not ready_tasks:
            self.last_reject_reason = "no_ready"
            return None

        graph_tasks = all_tasks or ready_tasks

        # 1. Critical Path determinístico
        if critical_path_ids is not None:
            cp_ids = critical_path_ids
        else:
            cp_ids = compute_deterministic_critical_path(graph_tasks)

        # 2. Inherited Priorities (PIP)
        inherited_map = compute_inherited_priorities(graph_tasks)

        # 3. Cálculo de scores
        scored = [
            (
                t.id,
                self.calculate_priority_score(
                    task=t,
                    critical_path_ids=cp_ids,
                    inherited_priority=inherited_map.get(t.id, float(t.priority)),
                    wait_ticks=self._wait_ticks[t.id],
                ),
            )
            for t in ready_tasks
        ]

        # 4. Seleção com fairness
        chosen_id = pick_with_fairness(scored, self._last_scheduled_id)
        self._last_scheduled_id = chosen_id

        # 5. Atualiza contadores de espera
        for t in ready_tasks:
            if t.id == chosen_id:
                self._wait_ticks[t.id] = 0
            else:
                self._wait_ticks[t.id] += 1

        active_ids = {t.id for t in ready_tasks}
        for tid in list(self._wait_ticks.keys()):
            if tid not in active_ids:
                del self._wait_ticks[tid]

        top_task = next(t for t in ready_tasks if t.id == chosen_id)
        return self.spawn_resolver.resolve(top_task)


# Alias para retrocompatibilidade
TaskScheduler = HAOSScheduler
