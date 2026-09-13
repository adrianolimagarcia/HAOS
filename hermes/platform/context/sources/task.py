"""TaskSource — ContextSource para o estado formal e histórico de tarefas (TaskSpec)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource
from hermes.platform.tasks.spec import TaskSpec


class TaskSource(ContextSource):
    """Fonte para especificação de tarefas, critérios de aceite e histórico imediato."""

    def __init__(self, task_spec: Optional[TaskSpec] = None):
        self.task_spec = task_spec

    @property
    def source_name(self) -> str:
        return "task"

    def set_task_spec(self, task_spec: TaskSpec) -> None:
        self.task_spec = task_spec

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        if not self.task_spec:
            return []

        items: List[ContextItem] = []
        spec = self.task_spec

        # 1. Task Spec Primária
        spec_content = (
            f"TASK ID: {spec.id} (Revision {spec.version})\n"
            f"TITLE: {spec.title}\n"
            f"DESCRIPTION:\n{spec.description}\n"
            f"PRIORITY: {spec.priority}\n"
            f"REQUIRES TASKS: {', '.join(spec.requires_tasks) if spec.requires_tasks else 'None'}"
        )
        items.append(
            ContextItem(
                id=f"task-spec-{spec.id}",
                item_type="task_spec",
                source_uri=f"task://specs/{spec.id}",
                content=spec_content,
                title=f"TaskSpec: {spec.title}",
                summary=f"Task {spec.id} ({spec.title}): {spec.description[:200]}...",
                abstract=f"TaskSpec {spec.id} - {spec.title}",
                trust=TrustLevel.TASK_SPEC,
                authority=AuthorityLevel.TASK,
                relevance=1.0,
                immutable=True,
            )
        )

        # 2. Critérios de Aceite (se presentes)
        if spec.acceptance_criteria:
            crit_lines = []
            crit_summaries = []
            for c in spec.acceptance_criteria:
                desc = getattr(c, "description", str(c))
                crit_lines.append(f"- [ ] {desc}")
                crit_summaries.append(desc)
            crit_content = "ACCEPTANCE CRITERIA:\n" + "\n".join(crit_lines)
            items.append(
                ContextItem(
                    id=f"task-acceptance-{spec.id}",
                    item_type="acceptance_criteria",
                    source_uri=f"task://specs/{spec.id}/acceptance",
                    content=crit_content,
                    title=f"Acceptance Criteria for {spec.id}",
                    summary="; ".join(crit_summaries[:3]),
                    abstract=f"{len(spec.acceptance_criteria)} acceptance criteria",
                    trust=TrustLevel.TASK_SPEC,
                    authority=AuthorityLevel.TASK,
                    relevance=1.0,
                    immutable=True,
                )
            )

        return items
