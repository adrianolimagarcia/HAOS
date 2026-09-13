"""RetrievalRouter — Roteador hierárquico e determinístico de fontes de contexto.

Hierarquia:
1. Task-local state / TaskSpec (TaskSource)
2. Live Code Intelligence (LSPSource)
3. Architecture Decisions / ADRs / Canonical Docs (ObsidianSource / DecisionStore)
4. Relational / Dependency Concepts (GraphRAGSource)
5. Task Artifacts & Execution History (ArtifactSource)
6. External Evidence / Web (WebSource)

Evita desperdício de tokens e chamadas caras:
- "onde ProtocolAdapter é definido?" -> vai para LSP, NUNCA GraphRAG.
- "qual ADR decidiu separar MCP de A2A?" -> vai para Obsidian / DecisionStore.
- "quais componentes dependem de X?" -> vai para GraphRAG.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from hermes.platform.context.primitives.item import ContextItem
from hermes.platform.context.sources.base import ContextSource


class QueryIntent(str, enum.Enum):
    """Classificação determinística da intenção da consulta."""
    CODE_SYMBOL = "code_symbol"          # LSP, Git
    ARCHITECTURE = "architecture"        # Obsidian, ADRs, DecisionStore
    RELATIONAL = "relational"            # GraphRAG (Local/DRIFT/Global)
    TASK_STATE = "task_state"            # TaskSpec, Run history
    ARTIFACT = "artifact"                # ArtifactStore
    EXTERNAL_RESEARCH = "external"       # Web, external sources
    HYBRID = "hybrid"                    # Decomposição necessária


@dataclass
class RoutedSubQuery:
    """Sub-consulta direcionada para fontes específicas."""
    intent: QueryIntent
    query: str
    target_sources: List[str]
    max_items: int = 5


class RetrievalRouter:
    """Roteador hierárquico com classificação determinística de intenção."""

    # Padrões regex para classificação determinística rápida e barata (zero custo LLM)
    RE_CODE_SYMBOL = re.compile(
        r"\b(def|class|function|method|symbol|module|imported|variable|interface|type|signature|callers?|references?|defined|file|line)\b",
        re.IGNORECASE,
    )
    RE_ARCHITECTURE = re.compile(
        r"\b(adr[-\s]?\d*|architecture|decision|convention|policy|specification|protocol|standard|rationale|governance)\b",
        re.IGNORECASE,
    )
    RE_RELATIONAL = re.compile(
        r"\b(depend[s|ency|encies]*|relat[ed|ion|ionships]*|affect[s|ed]*|impact[s|ed]*|holistic|ecosystem|components?\s+depend)\b",
        re.IGNORECASE,
    )
    RE_TASK = re.compile(
        r"\b(task|taskspec|acceptance\s*criteria|priority|deadline|plan|subtask|dependency\s*edge)\b",
        re.IGNORECASE,
    )
    RE_ARTIFACT = re.compile(
        r"\b(artifact|diff|patch|test\s*report|terminal\s*log|output\s*pointer|result\.json)\b",
        re.IGNORECASE,
    )

    def __init__(self, sources: Optional[Dict[str, ContextSource]] = None):
        self._sources: Dict[str, ContextSource] = sources or {}

    def register_source(self, source: ContextSource) -> None:
        """Registra uma ContextSource no roteador."""
        self._sources[source.source_name] = source

    def classify_intent(self, query: str) -> QueryIntent:
        """Classifica determinísticamente a intenção sem gastar chamada de LLM."""
        q = query.strip().lower()

        # Checagens prioritárias
        is_code = bool(self.RE_CODE_SYMBOL.search(q))
        is_arch = bool(self.RE_ARCHITECTURE.search(q))
        is_rel = bool(self.RE_RELATIONAL.search(q))
        is_task = bool(self.RE_TASK.search(q))
        is_art = bool(self.RE_ARTIFACT.search(q))

        matches = sum([is_code, is_arch, is_rel, is_task, is_art])
        if matches > 1:
            return QueryIntent.HYBRID

        if is_code:
            return QueryIntent.CODE_SYMBOL
        if is_arch:
            return QueryIntent.ARCHITECTURE
        if is_rel:
            return QueryIntent.RELATIONAL
        if is_task:
            return QueryIntent.TASK_STATE
        if is_art:
            return QueryIntent.ARTIFACT

        # Padrão seguro: se mencionar código direto ou nomes PascalCase/camelCase, tende a código
        if re.search(r"\b[A-Z][a-zA-Z0-9]+[A-Z][a-zA-Z0-9]*\b", query):
            return QueryIntent.CODE_SYMBOL

        return QueryIntent.ARCHITECTURE

    def decompose_query(self, query: str) -> List[RoutedSubQuery]:
        """Decompõe consultas híbridas ou direciona consultas simples."""
        intent = self.classify_intent(query)

        if intent == QueryIntent.CODE_SYMBOL:
            return [RoutedSubQuery(
                intent=intent,
                query=query,
                target_sources=["lsp", "git"],
                max_items=8,
            )]

        if intent == QueryIntent.ARCHITECTURE:
            return [RoutedSubQuery(
                intent=intent,
                query=query,
                target_sources=["obsidian", "decision_store", "hermes_memory"],
                max_items=5,
            )]

        if intent == QueryIntent.RELATIONAL:
            return [RoutedSubQuery(
                intent=intent,
                query=query,
                target_sources=["graphrag"],
                max_items=5,
            )]

        if intent == QueryIntent.TASK_STATE:
            return [RoutedSubQuery(
                intent=intent,
                query=query,
                target_sources=["task"],
                max_items=5,
            )]

        if intent == QueryIntent.ARTIFACT:
            return [RoutedSubQuery(
                intent=intent,
                query=query,
                target_sources=["artifacts"],
                max_items=5,
            )]

        # Híbrido: quebra em sub-consultas direcionadas para fontes complementares
        return [
            RoutedSubQuery(
                intent=QueryIntent.CODE_SYMBOL,
                query=query,
                target_sources=["lsp"],
                max_items=4,
            ),
            RoutedSubQuery(
                intent=QueryIntent.ARCHITECTURE,
                query=query,
                target_sources=["obsidian", "decision_store"],
                max_items=4,
            ),
            RoutedSubQuery(
                intent=QueryIntent.RELATIONAL,
                query=query,
                target_sources=["graphrag"],
                max_items=3,
            ),
        ]

    def route_and_retrieve(
        self,
        query: str,
        task_id: str,
        budget_tokens: int = 4000,
    ) -> List[ContextItem]:
        """Roteia determinística e estritamente para as fontes corretas."""
        subqueries = self.decompose_query(query)
        collected_items: List[ContextItem] = []
        seen_ids: Set[str] = set()

        for sub in subqueries:
            for src_name in sub.target_sources:
                source = self._sources.get(src_name)
                if not source:
                    continue
                try:
                    items = source.retrieve(
                        query=sub.query,
                        task_id=task_id,
                        budget_hint=budget_tokens // len(subqueries),
                    )
                    for it in items[:sub.max_items]:
                        if it.id not in seen_ids:
                            seen_ids.add(it.id)
                            collected_items.append(it)
                except Exception:
                    # Falha em uma fonte não bloqueia as demais
                    continue

        return collected_items
