"""MemoryCandidate — Unidade atômica candidata à consolidação de memória.

Representa uma hipótese ou fato extraído durante a execução, mantendo proveniência,
nível de confiança, escopo e status até ser formalmente consolidado ou rejeitado.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal

ScopeType = Literal["private", "team", "project", "global"]
DestinationType = Literal["working", "task", "obsidian", "skill", "core_user", "core_agent"]
StatusType = Literal["pending", "consolidated", "rejected"]


@dataclass
class MemoryCandidate:
    """Candidato à memória para o sistema de consolidação e roteamento."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fact: str = ""
    source_uri: str = ""
    confidence: float = 0.5  # float 0.0 - 1.0
    scope: ScopeType = "project"
    proposed_destination: DestinationType = "working"
    provenance: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: StatusType = "pending"

    def __post_init__(self) -> None:
        # Clampa confiança entre 0.0 e 1.0
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if not self.provenance and self.source_uri:
            self.provenance = [self.source_uri]

    def is_high_confidence(self) -> bool:
        """Indica se a confiança é alta o suficiente para consolidação direta."""
        return self.confidence >= 0.85

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o candidato em dicionário primitivo."""
        return {
            "id": self.id,
            "fact": self.fact,
            "source_uri": self.source_uri,
            "confidence": self.confidence,
            "scope": self.scope,
            "proposed_destination": self.proposed_destination,
            "provenance": list(self.provenance),
            "created_at": self.created_at,
            "status": self.status,
        }
