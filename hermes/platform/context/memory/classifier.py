"""Deterministic linguistic classification of memory assertions (PT-BR/EN)."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


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
        if not isinstance(data, dict):
            raise TypeError(f"AssertionClassification.from_dict requires dict, got {type(data).__name__}")

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

        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (ValueError, TypeError):
            confidence = 0.0

        raw_codes = data.get("rationale_codes", [])
        if isinstance(raw_codes, (list, tuple)):
            rationale_codes = [str(c) for c in raw_codes if c is not None]
        elif isinstance(raw_codes, str):
            rationale_codes = [raw_codes] if raw_codes else []
        else:
            rationale_codes = []

        raw_refs = data.get("evidence_refs", [])
        if isinstance(raw_refs, (list, tuple)):
            evidence_refs = [str(r) for r in raw_refs if r is not None]
        elif isinstance(raw_refs, str):
            evidence_refs = [raw_refs] if raw_refs else []
        else:
            evidence_refs = []

        raw_prov = data.get("source_provenance")
        source_provenance = str(raw_prov) if raw_prov is not None else ""

        assertion_id = str(data.get("assertion_id") or uuid.uuid4())
        refuted_assertion_id = data.get("refuted_assertion_id")
        if refuted_assertion_id is not None:
            refuted_assertion_id = str(refuted_assertion_id)

        try:
            observed_at = float(data.get("observed_at", time.time()))
        except (ValueError, TypeError):
            observed_at = time.time()

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
            assertion_type=assertion_type,
            confidence=confidence,
            rationale_codes=rationale_codes,
            evidence_refs=evidence_refs,
            source_provenance=source_provenance,
            assertion_id=assertion_id,
            refuted_assertion_id=refuted_assertion_id,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            raw_text=str(data.get("raw_text", "") or ""),
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
_ATTRIBUTION_RE = re.compile(
    r'(?i)\b(?:Segundo|De acordo com|Conforme (?:afirmado|relatado|informado)|According to|As reported by|Citando)\b'
)

_ATTRIBUTION = (
    "disse que", "afirmou que", "alega que", "alegou que", "segundo ",
    "disse:", "afirmou:", "de acordo com", "the report mentioned",
    "yesterday's report stated", "reported that", "said that", "claims that",
    "according to ", "the user said:", "the user stated that",
    "conforme afirmado", "conforme relatado", "conforme informado",
    "as reported by", "citando",
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


def _filter_valid_evidence(
    evidence_refs: Optional[List[Union[str, Any]]],
    text: str = "",
    verifier: Optional[Any] = None,
) -> List[str]:
    if not evidence_refs:
        return []
    from hermes.platform.context.memory.evidence import (
        EvidenceRecord,
        EvidenceStatus,
        get_default_evidence_registry,
        verify_claim_support,
    )

    reg = verifier or get_default_evidence_registry()
    valid: List[str] = []
    for r in evidence_refs:
        rec = None
        if isinstance(r, EvidenceRecord):
            rec = r
        elif isinstance(r, str):
            s = r.strip()
            if not s:
                continue
            if hasattr(reg, "resolve"):
                try:
                    rec = reg.resolve(s, statement=text)
                except TypeError:
                    rec = reg.resolve(s)
            elif hasattr(reg, "_records") and isinstance(reg._records, dict):
                rec = reg._records.get(s)

        if rec is not None:
            # Check verified status via registry or record
            is_valid_status = False
            if hasattr(reg, "verify"):
                try:
                    is_valid_status = reg.verify(rec, statement=text)
                except TypeError:
                    is_valid_status = reg.verify(rec)
            elif hasattr(rec, "is_verified"):
                is_valid_status = rec.is_verified()

            if is_valid_status:
                if text:
                    if verify_claim_support(rec, text):
                        uri = getattr(rec, "uri", str(r))
                        valid.append(uri)
                else:
                    uri = getattr(rec, "uri", str(r))
                    valid.append(uri)
            continue

        if isinstance(r, str):
            s = r.strip()
            if not s:
                continue
            if hasattr(reg, "verify"):
                try:
                    if reg.verify(s, statement=text):
                        valid.append(s)
                except TypeError:
                    if reg.verify(s):
                        valid.append(s)
            elif hasattr(reg, "is_verified"):
                if reg.is_verified(s):
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
    text: str,
    evidence_refs: Optional[List[Union[str, Any]]] = None,
    source_provenance: str = "",
    verifier: Optional[Any] = None,
) -> AssertionClassification:
    """Classify text conservatively; cited uncertainty is scoped to the quote; fail-closed on unverified claims."""
    raw = text or ""
    lower = raw.lower()
    valid_evidence = _filter_valid_evidence(evidence_refs, text=raw, verifier=verifier)
    had_unverified_evidence = bool(evidence_refs and not valid_evidence)
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

    cited = bool(_ATTRIBUTION_RE.search(raw)) or _has_marker(lower, _ATTRIBUTION)
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
        codes = ["EXPLICIT_HYPOTHESIS"]
        if cited:
            codes.insert(0, "CITED_ASSERTION")
        return AssertionClassification(
            assertion_type=AssertionType.HYPOTHESIS,
            confidence=0.9,
            rationale_codes=codes,
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    if _has_marker(scope_text, _IMPLICIT):
        codes = ["IMPLICIT_MODAL"]
        if cited:
            codes.insert(0, "CITED_ASSERTION")
        return AssertionClassification(
            assertion_type=AssertionType.HYPOTHESIS,
            confidence=0.85,
            rationale_codes=codes,
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    if cited and not valid_evidence:
        codes = ["CITED_ASSERTION", "CITATION_ATTRIBUTION"]
        if had_unverified_evidence:
            codes.append("UNTRUSTED_EVIDENCE_REJECTED")
        return AssertionClassification(
            assertion_type=AssertionType.FACT_UNVERIFIED,
            confidence=0.7,
            rationale_codes=codes,
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=True,
        )

    if valid_evidence:
        high_conf = raw.strip().lower().startswith("observed:") or "exit code 0" in lower or "verificado nos logs" in lower
        codes = ["VERIFIED_OBSERVATION"]
        if cited:
            codes.insert(0, "CITED_ASSERTION")
        return AssertionClassification(
            assertion_type=AssertionType.FACT_VERIFIED,
            confidence=0.95 if high_conf else 0.8,
            rationale_codes=codes,
            evidence_refs=valid_evidence,
            source_provenance=source_provenance,
            raw_text=raw,
            is_cited=cited,
        )

    fallback_codes = ["UNVERIFIED_FALLBACK"]
    if cited:
        fallback_codes.insert(0, "CITED_ASSERTION")
    if had_unverified_evidence:
        fallback_codes.insert(0, "UNTRUSTED_EVIDENCE_REJECTED")

    return AssertionClassification(
        assertion_type=AssertionType.FACT_UNVERIFIED,
        confidence=0.6,
        rationale_codes=fallback_codes,
        evidence_refs=valid_evidence,
        source_provenance=source_provenance,
        raw_text=raw,
        is_cited=cited,
    )
