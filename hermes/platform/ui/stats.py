"""C — Fase 3 UI/Control Plane: ``haos.ui.stats`` — agregados determinísticos
das views AionUI/Studio sobre o EventStore + KanbanAdapter reais.

Sem HTML fixo e sem ler o código-fonte: o dashboard é um *estado observável*
derivado dos dados canônicos (contadores), com as mesmas invariantes de um
time: nada é fabricado; modo demo (sem store) vira `available() False` e os
pontos de dado ficam vazios/zero — nunca lixo de outra base.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event


@dataclass
class TaskBoardStats:
    """Contadores do taskboard (AionUI) por status do card canônico."""

    total: int = 0
    by_status: Dict[str, int] = field(default_factory=dict)
    by_phase: Dict[str, int] = field(default_factory=dict)
    recent: List[Dict[str, Any]] = field(default_factory=list)  # 8 mais novos

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "by_status": dict(self.by_status),
            "by_phase": dict(self.by_phase),
            "recent": list(self.recent),
        }


@dataclass
class StudioStats:
    """Grafo de memória (Hermes Studio): nós por tipo + arestas causais."""

    event_count: int = 0
    by_name: Dict[str, int] = field(default_factory=dict)
    traces: List[str] = field(default_factory=list)       # trace_ids distintos
    correlated: List[str] = field(default_factory=list)   # correlation_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_count": self.event_count,
            "by_name": dict(self.by_name),
            "traces": list(self.traces),
            "correlated": list(self.correlated),
        }


@dataclass
class ConcurrencyStats:
    """Status operacional do ConcurrencyGuard em tempo real."""

    active_global: int = 0
    max_global: int = 0
    available_global: int = 0
    by_provider: Dict[str, int] = field(default_factory=dict)
    provider_limits: Dict[str, int] = field(default_factory=dict)
    by_model: Dict[str, int] = field(default_factory=dict)
    model_limits: Dict[str, int] = field(default_factory=dict)
    active_tasks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_global": self.active_global,
            "max_global": self.max_global,
            "available_global": self.available_global,
            "by_provider": dict(self.by_provider),
            "provider_limits": dict(self.provider_limits),
            "by_model": dict(self.by_model),
            "model_limits": dict(self.model_limits),
            "active_tasks": list(self.active_tasks),
        }


@dataclass
class CriticalPathStats:
    """Cálculo CPM e PIP determinístico sobre as tarefas do Kanban."""

    critical_path_ids: List[str] = field(default_factory=list)
    inherited_priorities: Dict[str, float] = field(default_factory=dict)
    total_tasks_evaluated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "critical_path_ids": list(self.critical_path_ids),
            "inherited_priorities": dict(self.inherited_priorities),
            "total_tasks_evaluated": self.total_tasks_evaluated,
        }


class DashboardStats:
    """Fonte única de métricas do control plane (Kanban + EventStore reais)."""

    def __init__(
        self,
        kanban: Optional[KanbanAdapter] = None,
        event_store: Optional[EventStore] = None,
        concurrency_guard: Optional[Any] = None,
    ):
        self.kanban = kanban
        self.event_store = event_store
        self.concurrency_guard = concurrency_guard

    def available(self) -> bool:
        return self.kanban is not None

    def task_board(self, *, recent_limit: int = 60) -> TaskBoardStats:
        if self.kanban is None:
            return TaskBoardStats()
        tasks = self.kanban.list_tasks()
        stats = TaskBoardStats()
        stats.total = len(tasks)
        for item in tasks:
            status = str(item.get("status") or "unknown")
            stats.by_status[status] = stats.by_status.get(status, 0) + 1
            phase = str(item.get("phase") or "triage")
            stats.by_phase[phase] = stats.by_phase.get(phase, 0) + 1
        # Detalhe (result/created_at) só da janela recente — N+1 limitado ao
        # que o dashboard renderiza; colunas derivam da lista leve.
        def _safe_get_task(tid: str) -> Dict[str, Any]:
            try:
                return self.kanban.get_task(tid) or {}
            except Exception:
                return {}

        def _sort_key(item: Dict[str, Any]) -> float:
            detail = _safe_get_task(item["id"])
            return (detail or {}).get("created_at", 0.0)

        ordered = sorted(tasks, key=_sort_key, reverse=True)
        recent = []
        for item in ordered[:recent_limit]:
            detail = _safe_get_task(item["id"])
            if detail:
                recent.append(detail)
        stats.recent = recent
        return stats

    def studio(self, *, recent_limit: int = 200) -> StudioStats:
        if self.event_store is None:
            return StudioStats()
        events: List[Event] = self.event_store.get_all(limit=recent_limit)
        stats = StudioStats()
        stats.event_count = len(events)
        seen_traces: List[str] = []
        seen_corr: List[str] = []
        for event in events:
            stats.by_name[event.name] = stats.by_name.get(event.name, 0) + 1
            if event.trace_id and event.trace_id not in seen_traces:
                seen_traces.append(event.trace_id)
            if event.correlation_id and event.correlation_id not in seen_corr:
                seen_corr.append(event.correlation_id)

        # Enriquecimento com entidades do GraphRAG e notas do Obsidian Vault quando presentes
        try:
            from hermes_constants import get_hermes_home
            from pathlib import Path
            import os
            h = Path(get_hermes_home())
            vault = h / "obsidian_vault"
            if vault.is_dir():
                from hermes.platform.memory.obsidian import ObsidianAdapter
                obs = ObsidianAdapter(str(vault))
                if obs.available():
                    notes = obs.list_notes()
                    stats.by_name["obsidian.notes"] = len(notes)
            
            gr_dir = h / "graphrag"
            if (gr_dir / "entities.csv").is_file():
                from hermes.platform.memory.graphrag import GraphRAGClient
                gr = GraphRAGClient(index_dir=str(gr_dir))
                if gr.available():
                    entities = gr.query_global("")
                    stats.by_name["graphrag.entities"] = len(entities)
        except Exception:
            pass

        stats.traces = seen_traces
        stats.correlated = seen_corr
        return stats

    def evolution_pending(self) -> List[Dict[str, Any]]:
        """Propostas do Ouroboros ainda sem decisão (delta 44).

        Derivada do EventStore via ``EvolutionLedger.pending``: propostas
        ``evolution.proposal.submitted`` sem ``evolution.proposal.decided``.
        Fail-safe: sem event_store (ou sem propostas) -> [] — nunca lixo.
        """
        if self.event_store is None:
            return []
        from hermes.platform.evolution.ledger import EvolutionLedger  # noqa: PLC0415

        return EvolutionLedger(self.event_store).pending()

    def concurrency(self) -> ConcurrencyStats:
        """Coleta métricas atômicas do ConcurrencyGuard se configurado."""
        if self.concurrency_guard is None:
            return ConcurrencyStats()
        stats_dict = self.concurrency_guard.stats()
        return ConcurrencyStats(
            active_global=stats_dict["global"]["active"],
            max_global=stats_dict["global"]["max"],
            available_global=stats_dict["global"]["available"],
            by_provider={p: v["active"] for p, v in stats_dict["providers"].items()},
            provider_limits={p: v["limit"] for p, v in stats_dict["providers"].items()},
            by_model={m: v["active"] for m, v in stats_dict["models"].items()},
            model_limits={m: v["limit"] for m, v in stats_dict["models"].items() if v["limit"] is not None},
            active_tasks=stats_dict["active_task_ids"],
        )

    def critical_path(self) -> CriticalPathStats:
        """Calcula CPM e prioridades herdadas sobre todas as tarefas do Kanban."""
        if self.kanban is None:
            return CriticalPathStats()
        from hermes.platform.tasks.spec import TaskSpec
        from hermes.platform.execution.scheduler import (
            compute_deterministic_critical_path,
            compute_inherited_priorities,
        )

        all_tasks = self.kanban.list_tasks()
        specs: List[TaskSpec] = []
        for t in all_tasks:
            detail = self.kanban.get_task(t["id"]) or {}
            spec_data = detail.get("spec") or {}
            if spec_data:
                try:
                    specs.append(TaskSpec.from_dict(spec_data))
                except Exception:
                    pass

        if not specs:
            return CriticalPathStats()

        cp_ids = compute_deterministic_critical_path(specs)
        inherited = compute_inherited_priorities(specs)
        return CriticalPathStats(
            critical_path_ids=cp_ids,
            inherited_priorities=inherited,
            total_tasks_evaluated=len(specs),
        )

    def sanitized_review_context(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Gera contexto de revisão higienizado (Anti-Anchoring) sem CoT/transcripts."""
        if self.kanban is None:
            return None
        detail = self.kanban.get_task(task_id)
        if not detail:
            return None
        spec_data = detail.get("spec") or {}
        from hermes.platform.tasks.spec import TaskSpec
        from hermes.platform.tasks.review_pipeline import AntiAnchoringContextBuilder

        spec = TaskSpec.from_dict(spec_data) if spec_data else TaskSpec(id=task_id, title=detail.get("title", ""), goal="")
        result = detail.get("result") or {}
        result_dict = result.to_dict() if hasattr(result, "to_dict") else (result if isinstance(result, dict) else {})
        
        return AntiAnchoringContextBuilder.build_reviewer_context(
            spec=spec,
            artifacts=result_dict.get("artifacts") or [],
            evidence=result_dict.get("evidence") or {},
            residual_risk=result_dict.get("residual_risk") or [],
            summary=result_dict.get("summary") or "",
        )
