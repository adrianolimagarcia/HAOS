"""Domain models for HAOS Civilization V2 Society.

Includes RelationshipEdge, ReputationEvent, ReputationVector,
RoleAssignment, CollaborationRecord, and DebatePhase.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DebatePhase(str, Enum):
    INDEPENDENT_ANALYSIS = "independent_analysis"
    CROSS_EXAMINATION = "cross_examination"
    REBUTTAL = "rebuttal"
    SYNTHESIS = "synthesis"
    DISSENT_RECORDED = "dissent_recorded"
    COMPLETED = "completed"


@dataclass(frozen=True)
class RelationshipEdge:
    id: str
    from_bot: str
    to_bot: str
    relation_type: str  # "trusts", "collaborates_with", "advises", "conflicts_with"
    weight: float = 1.0
    valid_from: float = field(default_factory=time.time)
    valid_to: Optional[float] = None
    evidence_refs: List[str] = field(default_factory=list)
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RelationshipEdge:
        return cls(
            id=data["id"],
            from_bot=data["from_bot"],
            to_bot=data["to_bot"],
            relation_type=data["relation_type"],
            weight=float(data.get("weight", 1.0)),
            valid_from=float(data.get("valid_from", time.time())),
            valid_to=data.get("valid_to"),
            evidence_refs=list(data.get("evidence_refs", [])),
            schema_version=int(data.get("schema_version", 1)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ReputationEvent:
    id: str
    subject_bot: str
    domain: str  # "architecture", "security", "code_quality", "consensus"
    evidence_ref: str
    outcome: str  # "success", "failure", "neutral"
    delta_hint: float
    actor_bot: Optional[str] = None
    occurred_at: float = field(default_factory=time.time)
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReputationEvent:
        return cls(
            id=data["id"],
            subject_bot=data["subject_bot"],
            domain=data["domain"],
            evidence_ref=data["evidence_ref"],
            outcome=data["outcome"],
            delta_hint=float(data.get("delta_hint", 0.0)),
            actor_bot=data.get("actor_bot"),
            occurred_at=float(data.get("occurred_at", time.time())),
            schema_version=int(data.get("schema_version", 1)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class DomainScore:
    domain: str
    score: float
    confidence: float
    evidence_count: int
    last_updated: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReputationVector:
    bot_id: str
    domains: Dict[str, DomainScore]
    overall_score: float
    evidence_count: int
    as_of: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "overall_score": self.overall_score,
            "evidence_count": self.evidence_count,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class RoleAssignment:
    id: str
    council_session_id: str
    bot_id: str
    role: str  # "moderator", "advocate", "skeptic", "synthesizer"
    rationale: str
    assigned_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollaborationRecord:
    id: str
    participants: List[str]
    task_ref: str
    outcome_ref: str
    reviewer_refs: List[str]
    recorded_at: float = field(default_factory=time.time)
    score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
