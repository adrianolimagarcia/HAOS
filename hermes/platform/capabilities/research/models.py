"""Pesquisa profunda (Fase 1, port puro do padrão DeerFlow 1.x) — modelos.

DeerFlow 1.x decompõe a pergunta em um plano tipado (``Plan``/``Step``), cada
step guarda sua descoberta (``execution_res``) + citações próprias, e o
``reporter`` sintetiza UMA resposta com a lista de citações agregadas. O HAOS
tipa o elo que o DeerFlow deixa implícito: ``Finding`` = descoberta de UMA
sub-pergunta com SUAS citações.

``Citation.id`` é derivado (sha256(url)[:12], determinístico — DeerFlow
``models.py:63-66``): não armazenado, nunca exigido na entrada.

Convenção de proveniência segue PerceptionArtifact (artifact_id/summary/
source/produced_by/model_identity/uncertainty), stdlib-only.
"""

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_STEP_TYPES = ("research", "analysis", "processing")


@dataclass
class ResearchQuestion:
    """Uma sub-pergunta do plano de pesquisa (≈ DeerFlow 1.x ``Step``)."""

    question: str
    rationale: str = ""          # ≈ Step.description
    need_search: bool = True     # ≈ Step.need_search
    step_type: str = "research"  # research | analysis | processing (≈ StepType)
    depth: int = 0               # 0 = pergunta raiz; sub-questões têm depth>0
    sub_questions: List["ResearchQuestion"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "rationale": self.rationale,
            "need_search": self.need_search,
            "step_type": self.step_type,
            "depth": self.depth,
            "sub_questions": [q.to_dict() for q in self.sub_questions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchQuestion":
        return cls(
            question=data["question"],
            rationale=data.get("rationale", ""),
            need_search=data.get("need_search", True),
            step_type=data.get("step_type", "research"),
            depth=data.get("depth", 0),
            sub_questions=[cls.from_dict(q) for q in data.get("sub_questions", [])],
        )


@dataclass
class Citation:
    """Evidência única (≈ DeerFlow 1.x Citation/CitationMetadata)."""

    url: str
    title: str = "Untitled"
    snippet: str = ""            # ≈ content_snippet[:500]
    domain: str = ""             # derivado de urlparse quando vazio
    accessed_at: str = ""        # ISO 8601
    relevance_score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)  # {query, result_type}

    def __post_init__(self):
        if not self.domain and self.url:
            parsed = urlparse(self.url)
            self.domain = parsed.netloc

    @property
    def id(self) -> str:
        """Determinístico (sha256(url)[:12]) — DeerFlow não armazena o id."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Citation":
        return cls(
            url=data["url"],
            title=data.get("title", "Untitled"),
            snippet=data.get("snippet", ""),
            domain=data.get("domain", ""),
            accessed_at=data.get("accessed_at", ""),
            relevance_score=data.get("relevance_score", 0.0),
            extra=data.get("extra") or {},
        )


@dataclass
class Finding:
    """Descoberta de UMA sub-pergunta + suas citações (elo que o DeerFlow
    deixa implícito em Step.execution_res)."""

    question: str                  # ResearchQuestion de origem
    claim: str                     # texto da descoberta
    citations: List[Citation] = field(default_factory=list)
    confidence: float = 0.0
    produced_by: str = ""          # estilo PerceptionArtifact.produced_by
    model_identity: Optional[Dict[str, str]] = None
    step_index: int = -1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "claim": self.claim,
            "citations": [c.to_dict() for c in self.citations],
            "confidence": self.confidence,
            "produced_by": self.produced_by,
            "model_identity": self.model_identity,
            "step_index": self.step_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        return cls(
            question=data["question"],
            claim=data.get("claim", ""),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
            confidence=data.get("confidence", 0.0),
            produced_by=data.get("produced_by", ""),
            model_identity=data.get("model_identity"),
            step_index=data.get("step_index", -1),
        )


@dataclass
class ResearchArtifact:
    """Artefato de evidência tipado do worker de pesquisa (Fase 1).

    style PerceptionArtifact + campos research-only: pergunta raiz, findings
    por sub-pergunta, citações agregadas (dedupe por url), resposta única
    sintetizada (≈ DeerFlow final_report) e confiança. ``evidence`` é o slot
    JSON que encaixa direto em proposal["evidence"] do Ouroboros.
    """

    artifact_id: str
    question: str                  # pergunta raiz (research_topic)
    answer: Optional[str] = None   # síntese única (final_report)
    findings: List[Finding] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)  # agregadas/dedupe
    confidence: float = 0.0
    model: str = ""                # modelo sintetizador
    status: str = "draft"          # draft | running | complete | failed
    uncertainty: str = ""          # low | medium | high
    produced_by: str = ""          # provider id
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "question": self.question,
            "answer": self.answer,
            "findings": [f.to_dict() for f in self.findings],
            "citations": [c.to_dict() for c in self.citations],
            "confidence": self.confidence,
            "model": self.model,
            "status": self.status,
            "uncertainty": self.uncertainty,
            "produced_by": self.produced_by,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchArtifact":
        return cls(
            artifact_id=data["artifact_id"],
            question=data["question"],
            answer=data.get("answer"),
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
            confidence=data.get("confidence", 0.0),
            model=data.get("model", ""),
            status=data.get("status", "draft"),
            uncertainty=data.get("uncertainty", ""),
            produced_by=data.get("produced_by", ""),
            evidence=data.get("evidence") or {},
        )
