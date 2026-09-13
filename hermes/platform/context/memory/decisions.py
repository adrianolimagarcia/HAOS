"""DecisionStore — Gerenciamento de decisões e detecção de conflitos/supersessão temporal.

Mantém decisões arquiteturais com campos:
- valid_from / valid_until
- supersedes / superseded_by

Garante que se uma nova ADR superseder uma regra antiga, o contexto não traga
ambas em contradição: a mais recente de alta autoridade vence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource


@dataclass
class ArchitectureDecision:
    """Decisão arquitetural tipada com controle temporal."""
    id: str  # ex: ADR-018
    title: str
    status: str  # proposed | accepted | superseded | rejected
    content: str
    authority: AuthorityLevel = AuthorityLevel.ARCHITECTURE
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d", time.gmtime()))
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None


class DecisionStore(ContextSource):
    """Repositório estruturado de decisões de projeto com resolução de conflito temporal."""

    def __init__(self):
        self._decisions: Dict[str, ArchitectureDecision] = {}

    @property
    def source_name(self) -> str:
        return "decision_store"

    def record_decision(
        self,
        decision_id: str,
        title: str,
        content: str,
        supersedes: Optional[List[str]] = None,
        authority: AuthorityLevel = AuthorityLevel.ARCHITECTURE,
    ) -> ArchitectureDecision:
        """Registra uma decisão, marcando decisões anteriores como superseded."""
        superseded_list = supersedes or []
        decision = ArchitectureDecision(
            id=decision_id,
            title=title,
            status="accepted",
            content=content,
            authority=authority,
            supersedes=superseded_list,
        )

        # Marca decisões antigas como superseded por esta nova
        for old_id in superseded_list:
            if old_id in self._decisions:
                self._decisions[old_id].status = "superseded"
                self._decisions[old_id].superseded_by = decision_id

        self._decisions[decision_id] = decision
        return decision

    def get_decision(self, decision_id: str) -> Optional[ArchitectureDecision]:
        """Recupera uma decisão específica por ID."""
        return self._decisions.get(decision_id)

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """Retorna decisões ativas (não superseded), resolvendo conflitos temporais."""
        results: List[ContextItem] = []
        q_lower = query.lower()

        for dec_id, dec in self._decisions.items():
            # Filtro temporal: ignora decisões superseded a menos que explicitamente solicitado
            if dec.status == "superseded":
                continue

            if query and (q_lower not in dec_id.lower() and q_lower not in dec.title.lower() and q_lower not in dec.content.lower()):
                continue

            item = ContextItem(
                id=f"decision-{dec.id}",
                item_type="architecture_decision",
                source_uri=f"decision://{dec.id}",
                content=f"# {dec.id}: {dec.title}\nStatus: {dec.status}\n\n{dec.content}",
                title=f"{dec.id} - {dec.title}",
                summary=f"{dec.id} ({dec.title}): {dec.content[:200]}...",
                abstract=f"{dec.id}: {dec.title}",
                trust=TrustLevel.ARCHITECTURE_DECISIONS,
                authority=dec.authority,
                relevance=1.0,
                supersedes=dec.supersedes,
                superseded_by=dec.superseded_by,
                metadata={"status": dec.status},
            )
            results.append(item)

        return results
