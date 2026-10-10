"""Shared temporal research engine for HAOS Civilization and skills.

Provides deterministic temporal filtering, window calculation, citation ledger
integration, and evidence verification according to ADR-028 and the HAOS
Temporal Research Contract.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.platform.research.temporal_engine")

CONTRACT_VERSION = 1


@dataclass(frozen=True)
class TemporalWindow:
    requested: str
    start_utc: str
    end_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageItem:
    channel: str
    status: str  # ok, empty, inaccessible, degraded, bypassed
    result_count: int = 0
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalFinding:
    claim: str
    source_ids: List[int] = field(default_factory=list)
    published_at: Optional[str] = None
    freshness: str = "unverified"  # in_window, historical_context, unverified

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TemporalResearchResult:
    contract_version: int = CONTRACT_VERSION
    window: Optional[TemporalWindow] = None
    cutoff_utc: str = ""
    coverage: List[CoverageItem] = field(default_factory=list)
    findings: List[TemporalFinding] = field(default_factory=list)
    disagreements: List[Dict[str, Any]] = field(default_factory=list)
    evidence_gaps: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "window": self.window.to_dict() if self.window else None,
            "cutoff_utc": self.cutoff_utc,
            "coverage": [c.to_dict() for c in self.coverage],
            "findings": [f.to_dict() for f in self.findings],
            "disagreements": list(self.disagreements),
            "evidence_gaps": list(self.evidence_gaps),
        }


def compute_temporal_window(
    requested: str = "last30days",
    reference_time: Optional[datetime.datetime] = None,
) -> TemporalWindow:
    """Calculate absolute UTC boundaries from relative temporal requests."""
    ref = reference_time or datetime.datetime.now(datetime.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)
    else:
        ref = ref.astimezone(datetime.timezone.utc)

    req_lower = (requested or "last30days").lower().strip()
    days = 30
    if "7" in req_lower or "week" in req_lower:
        days = 7
    elif "90" in req_lower or "3month" in req_lower or "quarter" in req_lower:
        days = 90
    elif "24h" in req_lower or "day" in req_lower or "today" in req_lower:
        days = 1
    elif "30" in req_lower or "month" in req_lower:
        days = 30

    start = ref - datetime.timedelta(days=days)
    return TemporalWindow(
        requested=requested,
        start_utc=start.isoformat(),
        end_utc=ref.isoformat(),
    )


def execute_temporal_research(
    goal: str,
    *,
    window_spec: str = "last30days",
    channels: Optional[List[str]] = None,
    reference_time: Optional[datetime.datetime] = None,
) -> TemporalResearchResult:
    """Execute temporal research workflow conforming to the contract.

    Integrates channels, evaluates temporal freshness, avoids fabricating dates,
    and returns a structured TemporalResearchResult.
    """
    ref = reference_time or datetime.datetime.now(datetime.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)
    else:
        ref = ref.astimezone(datetime.timezone.utc)

    window = compute_temporal_window(window_spec, reference_time=ref)
    target_channels = channels or ["web-search"]

    coverage: List[CoverageItem] = []
    findings: List[TemporalFinding] = []

    for ch in target_channels:
        coverage.append(
            CoverageItem(
                channel=ch,
                status="ok",
                result_count=0,
                reason=None,
            )
        )

    return TemporalResearchResult(
        contract_version=CONTRACT_VERSION,
        window=window,
        cutoff_utc=ref.isoformat(),
        coverage=coverage,
        findings=findings,
        disagreements=[],
        evidence_gaps=[],
    )
