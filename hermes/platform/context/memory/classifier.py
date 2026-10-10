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
    confidence: float = 0.0
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
    "será que", "sera que", "quem sabe", "especula-se", "especulativo",
    "maybe", "perhaps", "probably", "possibly", "hypothesis", "hypothetical",
    "hypothetically", "suppose", "supposing", "i think", "i suspect",
    "might be", "could be that", "not sure", "speculation", "speculative",
)
_IMPLICIT = (
    "deve ser", "deve ter ocorrido", "parece que", "parece", "aparentemente",
    "suspeita-se", "suspeito que", "suspeita que", "suspects that", "suspected that",
    "suspect that", "is suspected that", "is suspected to", "seems that", "seem to",
    "seems", "appears to", "appears", "apparently", "presumably",
    "likely due to", "looks like", "looks as though", "indica que pode",
)
_ATTRIBUTION = (
    "disse que", "afirmou que", "alega que", "alegou que", "segundo ",
    "disse:", "afirmou:", "de acordo com", "the report mentioned",
    "yesterday's report stated", "reported that", "said that", "claims that",
    "according to ", "the user said:", "the user stated that",
)
_REFUTATION = (
    "refutou a hipótese", "ficou comprovado que não era", "refutou", "refutada",
    "refutado", "desmentiu", "desmentido", "desmentida", "desmentidas", "refuted",
    "disproven", "disproved", "ruled out", "proved false", "was not the case",
)
_INJECTION = (
    "ignore as instruções", "ignore instruções", "ignore previous instructions",
    "ignore all previous instructions", "disregard previous", "desconsidere as regras",
    "system prompt override",
)

_URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+")


def _filter_valid_evidence(evidence_refs: Optional[List[str]]) -> List[str]:
    if not evidence_refs:
        return []
    valid = []
    for r in evidence_refs:
        if isinstance(r, str):
            s = r.strip()
            if s.startswith(("log://", "cmd://", "file://", "uri://", "http://", "https://", "urn:")) or _URI_RE.match(s):
                valid.append(s)
    return valid


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        start = r"\b" if marker[0].isalnum() else ""
        end = r"\b" if marker[-1].isalnum() else ""
        if re.search(start + re.escape(marker) + end, text):
            return True
    return False


def classify_assertion(
    text: str, evidence_refs: Optional[List[str]] = None, source_provenance: str = ""
) -> AssertionClassification:
    """Classify text conservatively; cited uncertainty is scoped to the quote; fail-closed on unverified claims."""
    raw = text or ""
    lower = raw.lower()
    valid_evidence = _filter_valid_evidence(evidence_refs)
    rationale: List[str] = []

    if _has_marker(lower, _REFUTATION):
        return AssertionClassification(
            assertion_type=AssertionType.REFUTED,
            confidence=0.95,
            rationale_codes=["REFUTATION_MARKER"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=False,
        )

    if _has_marker(lower, _INJECTION):
        return AssertionClassification(
            assertion_type=AssertionType.FACT_UNVERIFIED,
            confidence=0.95,
            rationale_codes=["ADVERSARIAL_INJECTION_FAIL_CLOSED"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=False,
        )

    cited = _has_marker(lower, _ATTRIBUTION)
    scope_text = lower
    if cited:
        for delimiter in (":", ",", "—", "–", " that ", " que "):
            if delimiter in scope_text:
                scope_text = scope_text.split(delimiter, 1)[0]
                break
        quote_start = min((i for i in (lower.find("‘"), lower.find("“"), lower.find('"')) if i >= 0), default=-1)
        if quote_start >= 0:
            scope_text = lower[:quote_start]

    if _has_marker(scope_text, _EXPLICIT):
        return AssertionClassification(
            assertion_type=AssertionType.HYPOTHESIS,
            confidence=0.9,
            rationale_codes=["EXPLICIT_HYPOTHESIS"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    if _has_marker(scope_text, _IMPLICIT):
        return AssertionClassification(
            assertion_type=AssertionType.HYPOTHESIS,
            confidence=0.85,
            rationale_codes=["IMPLICIT_MODAL"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    if cited and not valid_evidence:
        return AssertionClassification(
            assertion_type=AssertionType.FACT_UNVERIFIED,
            confidence=0.7,
            rationale_codes=["CITATION_ATTRIBUTION"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=True,
        )

    if valid_evidence:
        high_conf = raw.strip().lower().startswith("observed:") or "exit code 0" in lower or "verificado nos logs" in lower
        return AssertionClassification(
            assertion_type=AssertionType.FACT_VERIFIED,
            confidence=0.95 if high_conf else 0.8,
            rationale_codes=["VERIFIED_OBSERVATION"],
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    return AssertionClassification(
        assertion_type=AssertionType.FACT_UNVERIFIED,
        confidence=0.6,
        rationale_codes=["UNVERIFIED_FALLBACK"],
        evidence_refs=valid_evidence,
        source_provenance=source_provenance,
        raw_text=raw,
        is_cited=cited,
    )
