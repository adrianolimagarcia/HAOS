"""Deterministic linguistic classification of memory assertions (PT-BR/EN)."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AssertionType(str, Enum):
    FACT_VERIFIED = "FACT_VERIFIED"
    FACT_UNVERIFIED = "FACT_UNVERIFIED"
    HYPOTHESIS = "HYPOTHESIS"
    REFUTED = "REFUTED"


@dataclass
class AssertionClassification:
    """Classification plus provenance and temporal metadata for an assertion."""

    assertion_type: AssertionType
    confidence: float
    rationale_codes: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    source_provenance: str = ""
    assertion_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    refuted_assertion_id: Optional[str] = None
    observed_at: float = field(default_factory=time.time)
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    raw_text: str = ""
    is_cited: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assertion_type": self.assertion_type.value if isinstance(self.assertion_type, AssertionType) else str(self.assertion_type),
            "confidence": float(self.confidence),
            "rationale_codes": list(self.rationale_codes),
            "evidence_refs": list(self.evidence_refs),
            "source_provenance": self.source_provenance,
            "assertion_id": self.assertion_id,
            "refuted_assertion_id": self.refuted_assertion_id,
            "observed_at": self.observed_at,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "is_cited": bool(self.is_cited),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AssertionClassification:
        raw_type = data.get("assertion_type", AssertionType.FACT_UNVERIFIED)
        if isinstance(raw_type, str):
            try:
                assertion_type = AssertionType(raw_type)
            except ValueError:
                assertion_type = AssertionType.FACT_UNVERIFIED
        elif isinstance(raw_type, AssertionType):
            assertion_type = raw_type
        else:
            assertion_type = AssertionType.FACT_UNVERIFIED

        return cls(
            assertion_type=assertion_type,
            confidence=float(data.get("confidence", 0.0)),
            rationale_codes=list(data.get("rationale_codes", [])),
            evidence_refs=list(data.get("evidence_refs", [])),
            source_provenance=data.get("source_provenance"),
            assertion_id=data.get("assertion_id"),
            refuted_assertion_id=data.get("refuted_assertion_id"),
            observed_at=data.get("observed_at"),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            is_cited=bool(data.get("is_cited", False)),
        )



_EXPLICIT = (
    "talvez", "provavelmente", "possivelmente", "hipótese", "hipotese",
    "hipoteticamente", "suponho", "supondo", "suposição", "suposicao",
    "acho que", "pode ser que", "não tenho certeza", "nao tenho certeza",
    "maybe", "perhaps", "probably", "possibly", "hypothesis", "hypothetical",
    "hypothetically", "suppose", "supposing", "i think", "i suspect",
    "might be", "could be that", "not sure",
)
_IMPLICIT = (
    "deve ser", "parece que", "aparentemente", "suspeita-se", "suspeito que",
    "suspeita que", "seems that", "seem to", "appears to", "apparently",
    "presumably", "is suspected to",
)
_ATTRIBUTION = ("disse que", "afirmou que", "alega que", "alegou que", "segundo ", "reported that", "said that", "claims that", "according to ")
_REFUTATION = ("refutou a hipótese", "ficou comprovado que não era", "refutou", "refutada", "refutado", "desmentiu", "desmentido", "refuted", "disproven", "disproved")


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(re.search(r"\b" + re.escape(marker) + r"\b", text) for marker in markers)


def classify_assertion(
    text: str, evidence_refs: Optional[List[str]] = None, source_provenance: str = ""
) -> AssertionClassification:
    """Classify text conservatively; cited uncertainty is scoped to the quote."""
    raw = text or ""
    lower = raw.lower()
    evidence = list(evidence_refs or [])
    cited = _has_marker(lower, _ATTRIBUTION)
    # Quoted uncertainty belongs to the source, not necessarily to the attribution.
    scope_text = lower
    if cited:
        for delimiter in (":", ",", "—", "–"):
            if delimiter in scope_text:
                scope_text = scope_text.split(delimiter, 1)[0]
                break
        # Attribution clauses state a verifiable report even when its quoted content is uncertain.
        quote_start = min((i for i in (lower.find("‘"), lower.find("“"), lower.find('"')) if i >= 0), default=-1)
        if quote_start >= 0:
            scope_text = lower[:quote_start]
    rationale: List[str] = []
    if _has_marker(lower, _REFUTATION):
        kind, confidence = AssertionType.REFUTED, 0.95
        rationale.append("REFUTATION_MARKER")
    elif _has_marker(scope_text, _EXPLICIT):
        kind, confidence = AssertionType.HYPOTHESIS, 0.9
        rationale.append("EXPLICIT_HYPOTHESIS")
    elif _has_marker(scope_text, _IMPLICIT):
        kind, confidence = AssertionType.HYPOTHESIS, 0.85
        rationale.append("IMPLICIT_MODAL")
    elif cited:
        kind, confidence = AssertionType.FACT_VERIFIED, 0.8
        rationale.append("CITATION_ATTRIBUTION")
    elif evidence and (raw.strip().lower().startswith("observed:") or "exit code 0" in lower or "verificado nos logs" in lower):
        kind, confidence = AssertionType.FACT_VERIFIED, 0.95
        rationale.append("VERIFIED_OBSERVATION")
    elif evidence:
        kind, confidence = AssertionType.FACT_VERIFIED, 0.8
        rationale.append("VERIFIED_OBSERVATION")
    else:
        kind, confidence = AssertionType.FACT_UNVERIFIED, 0.6
        rationale.append("UNVERIFIED_FALLBACK")
    return AssertionClassification(
        assertion_type=kind, confidence=confidence, rationale_codes=rationale,
        evidence_refs=evidence, source_provenance=source_provenance, raw_text=raw, is_cited=cited,
    )
