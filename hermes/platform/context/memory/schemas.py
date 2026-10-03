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
KnowledgeKind = Literal["adr", "convention", "fact", "constraint", "architecture", "heuristic"]


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

    def digest(self) -> str:
        """Hash SHA-256 canônico para auditoria criptográfica e deduplicação."""
        payload = f"{self.id}:{self.kind}:{self.scope}:{self.title}:{self.content}:{self.supersedes}"
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
        )


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
