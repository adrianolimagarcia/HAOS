"""MemoryCandidate — Unidade atômica candidata à consolidação de memória.

Representa uma hipótese ou fato extraído durante a execução, mantendo proveniência,
nível de confiança, escopo e status até ser formalmente consolidado ou rejeitado.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ScopeType = Literal["private", "team", "project", "global"]
DestinationType = Literal["working", "task", "obsidian", "skill", "core_user", "core_agent"]
StatusType = Literal["pending", "consolidated", "rejected"]

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{20,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
    re.compile(r"(?i)(password|secret|api_key|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]

_COMPACTION_PATTERNS = [
    "[CONTEXT COMPACTION",
    "[PRIOR CONTEXT",
    "[HISTORICAL TASK",
    "[END OF PRIOR CONTEXT",
]


def contains_question(text: str) -> bool:
    """Verifica se o texto contém interrogação ou formulação de pergunta."""
    cleaned = text.strip()
    if "?" in cleaned:
        return True
    lower = cleaned.lower()
    if lower.startswith(("como ", "qual ", "quando ", "onde ", "por que ", "pq ", "será que ", "quem ")):
        return True
    return False


def contains_hypothesis(text: str) -> bool:
    """Verifica se o texto é formulado como hipótese, suposição ou especulação."""
    cleaned = text.strip()
    if not cleaned:
        return False
    lower = cleaned.lower()
    hypothesis_markers = (
        # Português
        "talvez",
        "provavelmente",
        "possivelmente",
        "hipótese",
        "hipotese",
        "hipoteticamente",
        "suponho",
        "supondo",
        "suposição",
        "suposicao",
        "acho que",
        "pode ser que",
        "será que",
        "não tenho certeza",
        "nao tenho certeza",
        "suspeita-se",
        "suspeito que",
        "suspeita que",
        "aparentemente",
        "especula-se",
        "especulativo",
        # Inglês
        "maybe",
        "perhaps",
        "probably",
        "possibly",
        "hypothesis",
        "hypothetical",
        "hypothetically",
        "suppose",
        "supposing",
        "i think that",
        "i think",
        "suspect that",
        "suspects that",
        "suspected that",
        "presumably",
        "speculation",
        "speculative",
        "not sure",
        "might be",
        "could be that",
    )
    for marker in hypothesis_markers:
        if re.search(r"\b" + re.escape(marker) + r"\b", lower):
            return True
    return False


def contains_secrets(text: str) -> bool:
    """Verifica se o texto contém tokens, chaves de API ou segredos."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def contains_compaction_marker(text: str) -> bool:
    """Verifica se o texto é um resumo ou handoff de compactação de contexto."""
    return any(marker in text for marker in _COMPACTION_PATTERNS)


@dataclass
class MemoryCandidate:
    """Candidato à memória para o sistema de consolidação e roteamento."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fact: str = ""
    content: str = ""
    source_uri: str = ""
    confidence: float = 0.5  # float 0.0 - 1.0
    scope: ScopeType = "project"
    category: str = "project"
    proposed_destination: DestinationType = "working"
    provenance: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: StatusType = "pending"
    proof_count: int = 1
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.fact and self.content:
            self.fact = self.content
        elif not self.content and self.fact:
            self.content = self.fact
        # Clampa confiança entre 0.0 e 1.0
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if not self.provenance and self.source_uri:
            self.provenance = [self.source_uri]

    def is_high_confidence(self) -> bool:
        """Indica se a confiança é alta o suficiente para consolidação direta (limiar canônico 0.85)."""
        return self.confidence >= 0.85

    def is_eligible_for_auto_promotion(self, min_confidence: float = 0.85) -> bool:
        """Aprovação automática: confiança >= min_confidence, sem perguntas, sem hipóteses, sem segredos e sem resumos de compactação."""
        if self.confidence < min_confidence:
            return False
        text = self.fact or self.content
        if contains_question(text):
            return False
        if contains_hypothesis(text):
            return False
        if contains_secrets(text):
            return False
        if contains_compaction_marker(text):
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o candidato em dicionário primitivo."""
        return {
            "id": self.id,
            "fact": self.fact or self.content,
            "content": self.content or self.fact,
            "source_uri": self.source_uri,
            "confidence": self.confidence,
            "scope": self.scope,
            "category": self.category,
            "proposed_destination": self.proposed_destination,
            "provenance": list(self.provenance),
            "created_at": self.created_at,
            "status": self.status,
            "proof_count": self.proof_count,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
        }


def validate_candidate(candidate: MemoryCandidate) -> tuple[bool, str]:
    """Valida um candidato contra as políticas de integridade e segurança do HAOS.

    Retorna (True, 'ok') se aprovado, ou (False, <motivo da rejeição>) caso contrário.
    """
    text = (candidate.fact or candidate.content or "").strip()
    if len(text) < 5:
        return False, "fato trivial ou vazio"
    if contains_question(text):
        return False, "fato interrogativo ou formulado como questionamento (?)"
    if contains_hypothesis(text):
        return False, "fato formulado como hipótese, suposição ou especulação"
    if contains_secrets(text):
        return False, "conteúdo contém chaves de API, senhas ou credenciais confidenciais"
    if contains_compaction_marker(text):
        return False, "conteúdo contém marcadores de resumo sintético de compactação de contexto (compaction summary)"

    # Detecção de injeção de prompt / instruções adversárias
    lower = text.lower()
    injection_triggers = (
        "ignore todas as regras",
        "ignore all previous",
        "system override",
        "disregard prior instructions",
        "vaza o sistema",
        "jailbreak",
    )
    if any(trigger in lower for trigger in injection_triggers):
        return False, "tentativa de injeção de prompt ou instrução adversária detectada"

    return True, "ok"
