"""ContextPackage e ContextManifest — Pacote estruturado e auditoria completa de contexto.

ContextPackage é o artefato compilado entregue para a execução da tarefa.
ContextManifest documenta exatamente por que cada item foi incluído ou excluído (explicabilidade / debugging).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import ContextItem, TrustLevel


@dataclass
class ManifestEntry:
    """Registro de decisão de inclusão ou exclusão de um ContextItem."""
    item_id: str
    item_type: str
    source_uri: str
    included: bool
    reason: str
    score: float
    token_cost: int
    representation_level: str = "full"
    trust: str = "trusted"


@dataclass
class ContextManifest:
    """Trilha de auditoria completa de como o contexto foi montado."""
    package_id: str
    task_id: str
    entries: List[ManifestEntry] = field(default_factory=list)
    total_candidates: int = 0
    total_included: int = 0
    total_tokens_used: int = 0
    token_budget_limit: int = 64000

    def record_inclusion(
        self,
        item: ContextItem,
        reason: str,
        score: float,
        level: str = "full",
        tokens: Optional[int] = None,
    ) -> None:
        self.entries.append(
            ManifestEntry(
                item_id=item.id,
                item_type=item.item_type,
                source_uri=item.source_uri,
                included=True,
                reason=reason,
                score=round(score, 4),
                token_cost=tokens if tokens is not None else item.token_cost,
                representation_level=level,
                trust=item.trust.value,
            )
        )
        self.total_included += 1

    def record_exclusion(
        self,
        item: ContextItem,
        reason: str,
        score: float,
    ) -> None:
        self.entries.append(
            ManifestEntry(
                item_id=item.id,
                item_type=item.item_type,
                source_uri=item.source_uri,
                included=False,
                reason=reason,
                score=round(score, 4),
                token_cost=item.token_cost,
                trust=item.trust.value,
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "task_id": self.task_id,
            "total_candidates": self.total_candidates,
            "total_included": self.total_included,
            "total_tokens_used": self.total_tokens_used,
            "token_budget_limit": self.token_budget_limit,
            "entries": [
                {
                    "item_id": e.item_id,
                    "item_type": e.item_type,
                    "source_uri": e.source_uri,
                    "included": e.included,
                    "reason": e.reason,
                    "score": e.score,
                    "token_cost": e.token_cost,
                    "representation_level": e.representation_level,
                    "trust": e.trust,
                }
                for e in self.entries
            ],
        }


@dataclass
class ContextPackage:
    """Pacote de contexto isolado e estruturado entregue ao modelo."""
    id: str
    task_id: str
    task_revision: int
    posture_id: str
    budget_limit: int = 64000
    sections: Dict[str, List[ContextItem]] = field(default_factory=dict)
    representations: Dict[str, str] = field(default_factory=dict)  # item_id -> level ('title'|'summary'|'full')
    token_count: int = 0
    digest_hash: str = ""
    manifest: Optional[ContextManifest] = None

    def __post_init__(self):
        if not self.digest_hash and self.sections:
            self.recalculate_digest_and_tokens()

    def recalculate_digest_and_tokens(self) -> None:
        """Recalcula determinística e puramente o hash digest e a contagem de tokens."""
        total_tokens = 0
        digest_pieces = []

        # Ordenação estável das seções para preservar cache
        for sec_name in sorted(self.sections.keys()):
            items = self.sections[sec_name]
            # Ordenação determinística de itens dentro da seção por ID
            for item in sorted(items, key=lambda x: x.id):
                rep_lvl = self.representations.get(item.id, "full")
                rendered = item.render_enveloped(rep_lvl)
                item_tokens = max(1, len(rendered) // 4)
                total_tokens += item_tokens
                h = hashlib.sha256(f"{sec_name}:{item.id}:{rep_lvl}:{rendered}".encode()).hexdigest()
                digest_pieces.append(h)

        combined_digest = hashlib.sha256(":".join(digest_pieces).encode()).hexdigest()
        self.token_count = total_tokens
        self.digest_hash = combined_digest

    def render_section(self, section_name: str) -> str:
        """Renderiza uma seção inteira em texto estruturado."""
        if section_name not in self.sections:
            return ""
        items = sorted(self.sections[section_name], key=lambda x: x.id)
        rendered_items = []
        for it in items:
            lvl = self.representations.get(it.id, "full")
            rendered_items.append(it.render_enveloped(lvl))
        return "\n\n".join(rendered_items)

    def render_stable_prefix(self) -> str:
        """Renderiza o prefixo estável (Identity, Task, Core Decisions).
        
        Isso garante o máximo de Prompt Cache Hit para LLMs upstream.
        """
        stable_sections = ["identity", "task", "decisions"]
        blocks = []
        for sec in stable_sections:
            content = self.render_section(sec)
            if content:
                blocks.append(f"### SECTION: {sec.upper()}\n{content}")
        return "\n\n".join(blocks)

    def render_semi_stable_body(self) -> str:
        """Renderiza o corpo semi-estável (Code context, Memory, Artifacts, Retrieved)."""
        semi_stable = ["code", "memory", "artifacts", "retrieved", "task_history"]
        blocks = []
        for sec in semi_stable:
            content = self.render_section(sec)
            if content:
                blocks.append(f"### SECTION: {sec.upper()}\n{content}")
        return "\n\n".join(blocks)
