"""KnowledgeItem & ADR Formal Schemas — Camada canônica do Memory Fabric.

Define:
- KnowledgeItem: Unidade fundamental de conhecimento com proveniência, temporalidade,
  versão semântica, escopo (private | team | project | global) e confiança.
- ADRDocument: Registro formal de decisão arquitetural (ADR) com status, decisão,
  consequências, supersedes e superseded_by.
- Experiência vs Skill / Memória:
  - Fato declarativo/relacional/arquitetural -> Memory Fabric (Obsidian / GraphRAG).
  - Procedimento executável/fluxo determinístico com steps -> Skill System (Procedural Intelligence).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ScopeType = Literal["private", "team", "project", "global"]
# Includes canonical kinds plus Hindsight 4-Network Logical Taxonomy (World Facts, Experiences, Observations, Mental Models)
KnowledgeKind = Literal[
    "adr",
    "convention",
    "fact",
    "constraint",
    "architecture",
    "heuristic",
    "world_fact",
    "experience",
    "observation",
    "mental_model",
]


@dataclass
class KnowledgeItem:
    """Unidade estruturada de conhecimento com proveniência estrita e temporalidade."""

    id: str
    title: str
    kind: KnowledgeKind
    content: str
    scope: ScopeType = "project"
    source_uri: str = ""
    author: str = "haos-agent"
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 1
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    proof_count: int = 1
    supporting_quotes: List[Dict[str, Any]] = field(default_factory=list)
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None

    def digest(self) -> str:
        """Hash SHA-256 canônico para auditoria criptográfica e deduplicação."""
        payload = f"{self.id}:{self.kind}:{self.scope}:{self.title}:{self.content}:{self.supersedes}:{self.proof_count}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_active(self) -> bool:
        """Indica se este conhecimento ainda é canônico ou se foi superado no tempo."""
        return self.superseded_by is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "content": self.content,
            "scope": self.scope,
            "source_uri": self.source_uri,
            "author": self.author,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "proof_count": self.proof_count,
            "supporting_quotes": list(self.supporting_quotes),
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "digest": self.digest(),
            "is_active": self.is_active(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgeItem:
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            kind=data.get("kind", "fact"),
            content=data.get("content", ""),
            scope=data.get("scope", "project"),
            source_uri=data.get("source_uri", ""),
            author=data.get("author", "haos-agent"),
            confidence=float(data.get("confidence", 1.0)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            version=int(data.get("version", 1)),
            supersedes=data.get("supersedes"),
            superseded_by=data.get("superseded_by"),
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
            proof_count=int(data.get("proof_count", 1)),
            supporting_quotes=list(data.get("supporting_quotes", [])),
            valid_from=float(data["valid_from"]) if data.get("valid_from") is not None else None,
            valid_until=float(data["valid_until"]) if data.get("valid_until") is not None else None,
        )


@dataclass
class MentalModelBlockSchema:
    """Bloco atômico de conteúdo dentro de uma seção de modelo mental."""
    block_id: str
    content: str
    proof_count: int = 1
    source_node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "content": self.content,
            "proof_count": self.proof_count,
            "source_node_ids": list(self.source_node_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MentalModelBlockSchema:
        return cls(
            block_id=data["block_id"],
            content=data["content"],
            proof_count=int(data.get("proof_count", 1)),
            source_node_ids=list(data.get("source_node_ids", [])),
        )


@dataclass
class MentalModelSectionSchema:
    """Seção agrupando blocos de um modelo mental."""
    section_id: str
    title: str
    order: int = 0
    blocks: List[MentalModelBlockSchema] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "order": self.order,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MentalModelSectionSchema:
        return cls(
            section_id=data["section_id"],
            title=data["title"],
            order=int(data.get("order", 0)),
            blocks=[MentalModelBlockSchema.from_dict(b) for b in data.get("blocks", [])],
        )


@dataclass
class MentalModelASTSchema:
    """Documento estruturado compilável (AST) de um Modelo Mental (ADR-022)."""
    model_id: str
    title: str
    version: int = 1
    sections: List[MentalModelSectionSchema] = field(default_factory=list)

    def compile_to_markdown(self) -> str:
        out = [f"# {self.title}\n"]
        for sec in sorted(self.sections, key=lambda s: s.order):
            out.append(f"## {sec.title}")
            for blk in sec.blocks:
                out.append(f"- {blk.content} (provas: {blk.proof_count})")
            out.append("")
        return "\n".join(out).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "title": self.title,
            "version": self.version,
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MentalModelASTSchema:
        return cls(
            model_id=data["model_id"],
            title=data["title"],
            version=int(data.get("version", 1)),
            sections=[MentalModelSectionSchema.from_dict(s) for s in data.get("sections", [])],
        )


@dataclass
class DeltaOpSchema:
    """Operação Delta atômica para mutação de AST de Modelo Mental."""
    op_type: Literal["add_section", "append_block", "replace_block", "remove_block"]
    section_id: str
    title: Optional[str] = None
    order: Optional[int] = None
    block: Optional[MentalModelBlockSchema] = None
    block_id: Optional[str] = None
    new_content: Optional[str] = None
    proof_increment: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.op_type,
            "section_id": self.section_id,
        }
        if self.title is not None:
            d["title"] = self.title
        if self.order is not None:
            d["order"] = self.order
        if self.block is not None:
            d["block"] = self.block.to_dict()
        if self.block_id is not None:
            d["block_id"] = self.block_id
        if self.new_content is not None:
            d["new_content"] = self.new_content
        if self.proof_increment:
            d["proof_increment"] = self.proof_increment
        return d


@dataclass
class ADRDocument:
    """Architectural Decision Record (ADR) padronizado."""

    id: str  # ex.: "ADR-042"
    title: str
    status: Literal["proposed", "accepted", "superseded", "deprecated", "rejected"] = "accepted"
    context: str = ""
    decision: str = ""
    consequences: str = ""
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Gera representação canônica em Markdown compatível com Obsidian Vault."""
        lines = [
            "---",
            f"id: {self.id}",
            f"title: {json.dumps(self.title)}",
            f"status: {self.status}",
            f"type: adr",
            f"created_at: {self.created_at}",
        ]
        if self.supersedes:
            lines.append(f"supersedes: {self.supersedes}")
        if self.superseded_by:
            lines.append(f"superseded_by: {self.superseded_by}")
        lines.append("---")
        lines.append(f"\n# {self.id}: {self.title}\n")
        lines.append("## Status")
        lines.append(f"{self.status.upper()}\n")
        lines.append("## Context")
        lines.append(f"{self.context}\n")
        lines.append("## Decision")
        lines.append(f"{self.decision}\n")
        lines.append("## Consequences")
        lines.append(f"{self.consequences}\n")
        return "\n".join(lines)


def classify_experience(
    experience_text: str,
    action_sequence: Optional[List[str]] = None,
) -> Literal["memory", "skill"]:
    """Distingue determinística e formalmente quando uma experiência vira Memória ou vira Skill.

    Regra Fundamental:
    - Se é um procedimento reproduzível com múltiplos passos operacionais (steps, scripts, comandos CLI)
      -> Vira SKILL (Procedural Intelligence).
    - Se é um fato, decisão arquitetural, restrição de segurança, convenção ou relacionamento conceitual
      -> Vira MEMÓRIA (Memory Fabric).
    """
    text = (experience_text or "").lower()

    # Se há uma sequência explícita de ações sequenciais executáveis -> Skill
    if action_sequence and len(action_sequence) >= 2:
        return "skill"

    procedural_signals = [
        "passo a passo", "step 1", "step 2", "procedimento para", "how to deploy",
        "script para", "workflow de", "instruções de execução", "cli command",
        "receita para", "pipeline de", "procedimento operacional",
    ]
    if any(sig in text for sig in procedural_signals):
        return "skill"

    return "memory"
