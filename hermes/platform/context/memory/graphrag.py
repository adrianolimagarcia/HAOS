"""GraphRAGAdapter — Projeção relacional derivada de conhecimento.

O GraphRAG é estritamente uma projeção derivada (derived projection) do corpus canônico
(Obsidian/docs). Ele responde a consultas conceituais, holísticas e de dependência:
- Local Query: combina grafo e chunks para entidades específicas.
- Global Query: sintetiza relatórios comunitários para perguntas holísticas.
- DRIFT Query: expande busca local com contexto estruturado da comunidade.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource


class GraphQueryMode(str, enum.Enum):
    LOCAL = "local"      # Focado em entidades e conexões imediatas
    GLOBAL = "global"    # Holístico / Community reports
    DRIFT = "drift"      # Busca local ampliada com comunidades


@dataclass
class GraphEntity:
    name: str
    entity_type: str
    description: str
    community_id: Optional[str] = None


@dataclass
class GraphRelation:
    source: str
    target: str
    relation_type: str
    description: str


class GraphRAGAdapter(ContextSource):
    """Adaptador para projeções de conhecimento e consultas em grafo."""

    def __init__(self):
        self._entities: Dict[str, GraphEntity] = {}
        self._relations: List[GraphRelation] = []
        self._communities: Dict[str, str] = {}  # community_id -> summary

    @property
    def source_name(self) -> str:
        return "graphrag"

    def register_entity(self, name: str, entity_type: str, description: str, community: Optional[str] = None) -> None:
        self._entities[name] = GraphEntity(name=name, entity_type=entity_type, description=description, community_id=community)

    def register_relation(self, source: str, target: str, relation_type: str, description: str) -> None:
        self._relations.append(GraphRelation(source=source, target=target, relation_type=relation_type, description=description))

    def register_community_report(self, community_id: str, summary: str) -> None:
        self._communities[community_id] = summary

    def query_local(self, entity_name: str) -> Optional[ContextItem]:
        """Consulta Local: Entidade + conexões imediatas."""
        ent = self._entities.get(entity_name)
        if not ent:
            return None

        connected_rels = [
            r for r in self._relations
            if r.source.lower() == entity_name.lower() or r.target.lower() == entity_name.lower()
        ]
        rel_lines = [f"- {r.source} -> [{r.relation_type}] -> {r.target}: {r.description}" for r in connected_rels]

        content = (
            f"GRAPH ENTITY: {ent.name} ({ent.entity_type})\n"
            f"Description: {ent.description}\n"
            f"Relations ({len(connected_rels)}):\n" + "\n".join(rel_lines)
        )

        return ContextItem(
            id=f"graphrag-local-{ent.name}",
            item_type="dependency_graph",
            source_uri=f"graphrag://local/{ent.name}",
            content=content,
            title=f"Graph Entity: {ent.name}",
            summary=f"{ent.name} connects to {len(connected_rels)} components",
            abstract=f"{ent.name} ({ent.entity_type})",
            trust=TrustLevel.DOCUMENT_CONTENT,
            authority=AuthorityLevel.ADVISORY,
            relevance=0.85,
        )

    def query_global(self, query: str = "") -> List[ContextItem]:
        """Consulta Global: Relatórios de comunidades sobre grandes temas do sistema."""
        items = []
        for comm_id, summary in self._communities.items():
            if query and query.lower() not in summary.lower():
                continue
            items.append(
                ContextItem(
                    id=f"graphrag-global-{comm_id}",
                    item_type="holistic_community_report",
                    source_uri=f"graphrag://global/{comm_id}",
                    content=f"COMMUNITY THEME [{comm_id}]:\n{summary}",
                    title=f"Community Report: {comm_id}",
                    summary=summary[:200] + "...",
                    abstract=f"Community {comm_id}",
                    trust=TrustLevel.DOCUMENT_CONTENT,
                    authority=AuthorityLevel.ADVISORY,
                    relevance=0.75,
                )
            )
        return items

    def query_drift(self, entity_or_concept: str) -> List[ContextItem]:
        """Consulta DRIFT: Combina busca local da entidade com relatórios comunitários associados."""
        results: List[ContextItem] = []
        local = self.query_local(entity_or_concept)
        if local:
            results.append(local)

        # Encontra a comunidade da entidade para enriquecer
        ent = self._entities.get(entity_or_concept)
        if ent and ent.community_id and ent.community_id in self._communities:
            comm_summary = self._communities[ent.community_id]
            results.append(
                ContextItem(
                    id=f"graphrag-drift-comm-{ent.community_id}",
                    item_type="holistic_community_report",
                    source_uri=f"graphrag://drift/{ent.community_id}",
                    content=f"ASSOCIATED COMMUNITY CONTEXT [{ent.community_id}]:\n{comm_summary}",
                    title=f"Drift Community: {ent.community_id}",
                    summary=comm_summary[:200] + "...",
                    abstract=f"Community {ent.community_id}",
                    trust=TrustLevel.DOCUMENT_CONTENT,
                    authority=AuthorityLevel.ADVISORY,
                    relevance=0.80,
                )
            )
        return results

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """Executa retrieval baseado na intenção relacional ou entidade."""
        results: List[ContextItem] = []

        # Tenta busca local em entidades conhecidas
        for ent_name in self._entities:
            if ent_name.lower() in query.lower():
                item = self.query_local(ent_name)
                if item:
                    results.append(item)

        # Se não encontrou entidade local ou for pergunta ampla, recorre aos relatórios globais
        if not results:
            results.extend(self.query_global(query))

        return results
