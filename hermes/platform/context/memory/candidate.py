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

from hermes.platform.context.memory.classifier import AssertionClassification, AssertionType

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
    """Compatibilidade: filtro contextual de linguagem hipotética."""
    from hermes.platform.context.memory.classifier import AssertionType, classify_assertion

    return classify_assertion(text).assertion_type == AssertionType.HYPOTHESIS


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
    classification: Optional[AssertionClassification] = None

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

        # Verificação estrita de sanitização e segurança
        text = self.fact or self.content
        if contains_secrets(text):
            return False
        if contains_compaction_marker(text):
            return False

        # Se classificação tipada estiver presente:
        if self.classification is not None:
            if self.classification.assertion_type in (
                AssertionType.HYPOTHESIS,
                AssertionType.REFUTED,
                AssertionType.FACT_UNVERIFIED,
            ):
                return False
            if self.classification.assertion_type != AssertionType.FACT_VERIFIED:
                return False
            # Aceitar apenas FACT_VERIFIED com evidências (evidence_refs válido e não vazio)
            evidence = getattr(self.classification, "evidence_refs", None) or []
            valid_evidence = [e for e in evidence if str(e).strip()]
            if not valid_evidence:
                return False
            # Não pode conter perguntas
            if contains_question(text):
                return False
            return True

        # Caminho legado sem classification:
        if contains_question(text):
            return False
        if contains_hypothesis(text):
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serializa o candidato em dicionário primitivo."""
        data: Dict[str, Any] = {
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
        if self.classification is not None:
            data["classification"] = self.classification.to_dict()
        else:
            data["classification"] = None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryCandidate":
        """Deserializa dicionário primitivo para MemoryCandidate com validação fail-closed."""
        if not isinstance(data, dict):
            raise TypeError(f"MemoryCandidate.from_dict requires dict, got {type(data).__name__}")

        # Validar integridade e sanitizar campos essenciais
        raw_classification = data.get("classification")
        classification_obj = None
        if raw_classification is not None:
            if isinstance(raw_classification, dict):
                classification_obj = AssertionClassification.from_dict(raw_classification)
            elif isinstance(raw_classification, AssertionClassification):
                classification_obj = raw_classification

        raw_prov = data.get("provenance", [])
        if isinstance(raw_prov, (list, tuple)):
            provenance = [str(p) for p in raw_prov if p is not None]
        elif isinstance(raw_prov, str):
            provenance = [raw_prov] if raw_prov else []
        else:
            provenance = []

        try:
            confidence = float(data.get("confidence", 0.5))
        except (ValueError, TypeError):
            confidence = 0.5

        try:
            created_at = float(data.get("created_at", time.time()))
        except (ValueError, TypeError):
            created_at = time.time()

        try:
            proof_count = int(data.get("proof_count", 1))
        except (ValueError, TypeError):
            proof_count = 1

        valid_from = data.get("valid_from")
        if valid_from is not None:
            try:
                valid_from = float(valid_from)
            except (ValueError, TypeError):
                valid_from = None

        valid_until = data.get("valid_until")
        if valid_until is not None:
            try:
                valid_until = float(valid_until)
            except (ValueError, TypeError):
                valid_until = None

        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            fact=str(data.get("fact", "") or ""),
            content=str(data.get("content", "") or ""),
            source_uri=str(data.get("source_uri", "") or ""),
            confidence=confidence,
            scope=data.get("scope", "project"),
            category=str(data.get("category", "project") or "project"),
            proposed_destination=data.get("proposed_destination", "working"),
            provenance=provenance,
            created_at=created_at,
            status=data.get("status", "pending"),
            proof_count=proof_count,
            valid_from=valid_from,
            valid_until=valid_until,
            supersedes=data.get("supersedes"),
            superseded_by=data.get("superseded_by"),
            classification=classification_obj,
        )


def validate_candidate(candidate: MemoryCandidate) -> tuple[bool, str]:
    """Valida um candidato contra as políticas de integridade e segurança do HAOS.

    Retorna (True, 'ok') se aprovado, ou (False, <motivo da rejeição>) caso contrário.
    """
    text = (candidate.fact or candidate.content or "").strip()
    if len(text) < 5:
        return False, "fato trivial ou vazio"
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

    # Verificação baseada em classification tipada (se fornecida)
    if candidate.classification is not None:
        if candidate.classification.assertion_type == AssertionType.REFUTED:
            return False, "fato refutado por evidência conclusiva (REFUTED)"
        if candidate.classification.assertion_type == AssertionType.HYPOTHESIS:
            return False, "fato formulado como hipótese, suposição ou especulação (HYPOTHESIS)"
        if candidate.classification.assertion_type == AssertionType.FACT_UNVERIFIED:
            return False, "fato não verificado independentemente (FACT_UNVERIFIED)"
        if candidate.classification.assertion_type != AssertionType.FACT_VERIFIED:
            return False, f"tipo de asserção não aceito para promoção: {candidate.classification.assertion_type}"
        evidence = getattr(candidate.classification, "evidence_refs", None) or []
        valid_evidence = [e for e in evidence if str(e).strip()]
        if not valid_evidence:
            return False, "fato sem referências de evidência válidas (evidence_refs)"

    if contains_question(text):
        return False, "fato interrogativo ou formulado como questionamento (?)"
    if contains_hypothesis(text):
        return False, "fato formulado como hipótese, suposição ou especulação"

    return True, "ok"
