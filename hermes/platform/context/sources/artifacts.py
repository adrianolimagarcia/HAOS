"""ArtifactSource — ContextSource para gerenciar artefatos de execução com elisão automática."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from hermes.platform.context.budget.budgeter import ArtifactStore
from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource


class ArtifactSource(ContextSource):
    """Fonte para artefatos gerados por tarefas, pipelines ou ferramentas."""

    def __init__(self, store: Optional[ArtifactStore] = None):
        self.store = store or ArtifactStore()
        self._artifacts: List[ContextItem] = []

    @property
    def source_name(self) -> str:
        return "artifacts"

    def add_artifact(
        self,
        item_id: str,
        item_type: str,
        content: str,
        title: str = "",
        summary: Optional[str] = None,
        abstract: Optional[str] = None,
        trust: TrustLevel = TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
    ) -> ContextItem:
        """Adiciona um artefato, efetuando elisão automática se exceder o limite."""
        item = self.store.store_or_pass(
            item_id=item_id,
            item_type=item_type,
            content=content,
            title=title,
            summary=summary,
            abstract=abstract,
            trust=trust,
        )
        self._artifacts.append(item)
        return item

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        if not query:
            return list(self._artifacts)

        # Filtro simples por substring de query
        q_lower = query.lower()
        matched = []
        for it in self._artifacts:
            if q_lower in it.title.lower() or q_lower in it.content.lower() or q_lower in it.item_type.lower():
                matched.append(it)
        return matched
