"""Shared temporal research engine for HAOS Civilization and skills.

Provides deterministic temporal filtering, window calculation, citation ledger
integration, and evidence verification according to ADR-028 and the HAOS
Temporal Research Contract.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.research.temporal_connectors import (
    search_hackernews,
    search_reddit,
    search_web,
)
from hermes.platform.research.temporal_utils import (
    normalize_url,
    parse_timestamp_to_utc,
)

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
    url: Optional[str] = None
    source_channel: Optional[str] = None

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
    elif req_lower in ("24h", "last24h", "lastday", "day", "today", "yesterday"):
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
    limit_per_channel: int = 5,
) -> TemporalResearchResult:
    """Execute temporal research workflow conforming to the contract.

    Integrates channels, evaluates temporal freshness, avoids fabricating dates,
    deduplicates findings across sources, and returns a structured TemporalResearchResult.
    """
    ref = reference_time or datetime.datetime.now(datetime.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=datetime.timezone.utc)
    else:
        ref = ref.astimezone(datetime.timezone.utc)

    window = compute_temporal_window(window_spec, reference_time=ref)
    start_utc_dt = parse_timestamp_to_utc(window.start_utc) or ref
    end_utc_dt = parse_timestamp_to_utc(window.end_utc) or ref

    target_channels = channels or ["web-search"]

    coverage: List[CoverageItem] = []
    raw_findings: List[Dict[str, Any]] = []

    for ch in target_channels:
        ch_name = ch.strip().lower()
        items: List[Dict[str, Any]] = []
        status: str = "ok"
        reason: Optional[str] = None

        if ch_name in ("web-search", "web", "google"):
            items, status, reason = search_web(
                goal,
                limit=limit_per_channel,
                start_utc_dt=start_utc_dt,
                end_utc_dt=end_utc_dt,
            )
        elif ch_name == "reddit":
            items, status, reason = search_reddit(
                goal,
                limit=limit_per_channel,
                start_utc_dt=start_utc_dt,
                end_utc_dt=end_utc_dt,
            )
        elif ch_name in ("hackernews", "hacker-news", "hn"):
            items, status, reason = search_hackernews(
                goal,
                limit=limit_per_channel,
                start_utc_dt=start_utc_dt,
                end_utc_dt=end_utc_dt,
            )
        elif ch_name in ("x-twitter", "twitter", "youtube", "polymarket"):
            status = "bypassed"
            reason = "skipped_no_credentials"
            items = []
        else:
            status = "bypassed"
            reason = f"unsupported_channel_{ch}"
            items = []

        coverage.append(
            CoverageItem(
                channel=ch,
                status=status,
                result_count=len(items),
                reason=reason,
            )
        )
        raw_findings.extend(items)

    # Deduplicate findings by normalized URL, or claim text if URL missing
    seen_keys = set()
    deduped_findings: List[TemporalFinding] = []
    source_counter = 1

    for rf in raw_findings:
        url_norm = normalize_url(rf.get("url") or "")
        key = ("url", url_norm) if url_norm else ("claim", rf.get("claim", "").strip().lower())

        if key in seen_keys:
            continue
        seen_keys.add(key)

        finding = TemporalFinding(
            claim=rf.get("claim", ""),
            source_ids=[source_counter],
            published_at=rf.get("published_at"),
            freshness=rf.get("freshness", "unverified"),
            url=url_norm if url_norm else None,
            source_channel=rf.get("source_channel"),
        )
        deduped_findings.append(finding)
        source_counter += 1

    return TemporalResearchResult(
        contract_version=CONTRACT_VERSION,
        window=window,
        cutoff_utc=ref.isoformat(),
        coverage=coverage,
        findings=deduped_findings,
        disagreements=[],
        evidence_gaps=[],
    )
