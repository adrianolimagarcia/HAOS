"""Bot Identity Evolution Engine (HAOS Civilization V3).

Manages ExperienceEvents, EvolutionProposals, risk classification,
and promotion/rollback workflows without violating prompt caching.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_IDENTITY_CRITICAL = "identity-critical"

STATUS_DRAFT = "draft"
STATUS_REVIEW = "review"
STATUS_APPROVED = "approved"
STATUS_CANARY = "canary"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
STATUS_ROLLED_BACK = "rolled_back"

EVENT_EXPERIENCE_RECORDED = "civ.evolution.experience_recorded"
EVENT_PROPOSAL_CREATED = "civ.evolution.proposal_created"
EVENT_PROPOSAL_UPDATED = "civ.evolution.proposal_updated"
EVENT_PROPOSAL_PROMOTED = "civ.evolution.proposal_promoted"
EVENT_PROPOSAL_ROLLED_BACK = "civ.evolution.proposal_rolled_back"


class EvolutionError(Exception):
    """Base error for evolution operations."""


class StaleBaseVersionError(EvolutionError):
    """Raised when trying to approve or promote a proposal based on an outdated version."""


class PolicyViolationError(EvolutionError):
    """Raised when an identity critical change violates governance policy."""


@dataclass
class ExperienceEvent:
    id: str
    bot_id: str
    event_type: str
    domain: str
    summary: str
    success: bool
    payload: Dict[str, Any] = field(default_factory=dict)
    occurred_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExperienceEvent:
        return cls(
            id=data["id"],
            bot_id=data["bot_id"],
            event_type=data["event_type"],
            domain=data["domain"],
            summary=data["summary"],
            success=bool(data["success"]),
            payload=data.get("payload", {}),
            occurred_at=float(data.get("occurred_at", time.time())),
        )


@dataclass
class EvolutionProposal:
    id: str
    bot_id: str
    base_version_hash: str
    risk_class: str
    status: str
    rationale: str
    proposed_soul_patch: Optional[str] = None
    proposed_identity_patch: Optional[str] = None
    proposed_values_patch: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvolutionProposal:
        return cls(
            id=data["id"],
            bot_id=data["bot_id"],
            base_version_hash=data["base_version_hash"],
            risk_class=data.get("risk_class", RISK_LOW),
            status=data.get("status", STATUS_DRAFT),
            rationale=data.get("rationale", ""),
            proposed_soul_patch=data.get("proposed_soul_patch"),
            proposed_identity_patch=data.get("proposed_identity_patch"),
            proposed_values_patch=data.get("proposed_values_patch"),
            evidence_refs=data.get("evidence_refs", []),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class BotEvolutionManager:
    """Manages bot evolution lifecycle: experience ingestion, proposals, and promotions."""

    def __init__(self, event_store: EventStore) -> None:
        self.store = event_store

    def _events(self) -> List[Event]:
        return [
            e
            for e in self.store.get_all()
            if e.name.startswith("civ.evolution.")
        ]

    def record_experience(
        self,
        bot_id: str,
        event_type: str,
        domain: str,
        summary: str,
        success: bool,
        payload: Optional[Dict[str, Any]] = None,
    ) -> ExperienceEvent:
        exp_id = f"exp-{uuid.uuid4().hex[:12]}"
        exp = ExperienceEvent(
            id=exp_id,
            bot_id=bot_id,
            event_type=event_type,
            domain=domain,
            summary=summary,
            success=success,
            payload=payload or {},
            occurred_at=time.time(),
        )

        self.store.append(
            Event(
                name=EVENT_EXPERIENCE_RECORDED,
                payload={"experience": exp.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return exp

    def create_proposal(
        self,
        bot_id: str,
        base_version_hash: str,
        risk_class: str,
        rationale: str,
        proposed_soul_patch: Optional[str] = None,
        proposed_identity_patch: Optional[str] = None,
        proposed_values_patch: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> EvolutionProposal:
        prop_id = f"prop-{uuid.uuid4().hex[:12]}"
        now = time.time()
        prop = EvolutionProposal(
            id=prop_id,
            bot_id=bot_id,
            base_version_hash=base_version_hash,
            risk_class=risk_class,
            status=STATUS_DRAFT,
            rationale=rationale,
            proposed_soul_patch=proposed_soul_patch,
            proposed_identity_patch=proposed_identity_patch,
            proposed_values_patch=proposed_values_patch,
            evidence_refs=evidence_refs or [],
            created_at=now,
            updated_at=now,
        )

        self.store.append(
            Event(
                name=EVENT_PROPOSAL_CREATED,
                payload={"proposal": prop.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return prop

    def get_proposals(self, bot_id: Optional[str] = None) -> List[EvolutionProposal]:
        proposals: Dict[str, EvolutionProposal] = {}

        for e in self._events():
            payload = e.payload or {}
            if e.name in (EVENT_PROPOSAL_CREATED, EVENT_PROPOSAL_UPDATED):
                if "proposal" in payload:
                    p = EvolutionProposal.from_dict(payload["proposal"])
                    proposals[p.id] = p
            elif e.name == EVENT_PROPOSAL_PROMOTED:
                pid = payload.get("proposal_id")
                if pid and pid in proposals:
                    proposals[pid].status = STATUS_PROMOTED
                    proposals[pid].updated_at = e.timestamp
            elif e.name == EVENT_PROPOSAL_ROLLED_BACK:
                pid = payload.get("proposal_id")
                if pid and pid in proposals:
                    proposals[pid].status = STATUS_ROLLED_BACK
                    proposals[pid].updated_at = e.timestamp

        res = list(proposals.values())
        if bot_id:
            res = [p for p in res if p.bot_id == bot_id]
        res.sort(key=lambda x: x.created_at, reverse=True)
        return res

    def update_status(
        self,
        proposal_id: str,
        new_status: str,
        current_bot_version_hash: str,
    ) -> EvolutionProposal:
        proposals = {p.id: p for p in self.get_proposals()}
        if proposal_id not in proposals:
            raise EvolutionError(f"Proposal '{proposal_id}' not found.")

        target = proposals[proposal_id]

        # Invariant: Stale base version cannot be approved or promoted
        if (
            new_status in (STATUS_APPROVED, STATUS_CANARY, STATUS_PROMOTED)
            and target.base_version_hash != current_bot_version_hash
        ):
            raise StaleBaseVersionError(
                f"Cannot transition to '{new_status}': proposal base '{target.base_version_hash}' "
                f"differs from current active version '{current_bot_version_hash}'."
            )

        target.status = new_status
        target.updated_at = time.time()

        ev_name = EVENT_PROPOSAL_UPDATED
        if new_status == STATUS_PROMOTED:
            ev_name = EVENT_PROPOSAL_PROMOTED
        elif new_status == STATUS_ROLLED_BACK:
            ev_name = EVENT_PROPOSAL_ROLLED_BACK

        self.store.append(
            Event(
                name=ev_name,
                payload={"proposal_id": target.id, "proposal": target.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return target
