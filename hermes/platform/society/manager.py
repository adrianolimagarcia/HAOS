"""Event-sourced SocietyManager managing bot relationships, reputation vectors, and council roles."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..observability.event_store import EventStore
from ..observability.events import Event
from .models import (
    CollaborationRecord,
    DomainScore,
    RelationshipEdge,
    ReputationEvent,
    ReputationVector,
    RoleAssignment,
)

EVENT_RELATION_ESTABLISHED = "civ.society.relationship-established"
EVENT_REPUTATION_RECORDED = "civ.society.reputation-event-recorded"
EVENT_ROLE_ASSIGNED = "civ.society.role-assigned"
EVENT_COLLABORATION_RECORDED = "civ.society.collaboration-recorded"


class SelfEndorsementError(ValueError):
    """Raised when a bot attempts to award positive reputation to itself."""


class SocietyManager:
    def __init__(self, event_store: EventStore) -> None:
        self.store = event_store

    def _events(self) -> List[Event]:
        return [
            e
            for e in self.store.get_all()
            if e.name.startswith("civ.society.")
        ]

    def establish_relationship(
        self,
        from_bot: str,
        to_bot: str,
        relation_type: str,
        weight: float = 1.0,
        evidence_refs: Optional[List[str]] = None,
    ) -> RelationshipEdge:
        if not from_bot or not to_bot:
            raise ValueError("from_bot and to_bot are required")

        edge_id = f"rel-{uuid.uuid4().hex[:12]}"
        edge = RelationshipEdge(
            id=edge_id,
            from_bot=from_bot,
            to_bot=to_bot,
            relation_type=relation_type,
            weight=weight,
            valid_from=time.time(),
            evidence_refs=evidence_refs or [],
        )

        self.store.append(
            Event(
                name=EVENT_RELATION_ESTABLISHED,
                payload={"edge": edge.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return edge

    def record_reputation(
        self,
        subject_bot: str,
        domain: str,
        evidence_ref: str,
        outcome: str,
        delta_hint: float,
        actor_bot: Optional[str] = None,
    ) -> ReputationEvent:
        if not subject_bot or not domain:
            raise ValueError("subject_bot and domain are required")

        # Anti-self-endorsement invariant
        if actor_bot == subject_bot and outcome == "success" and delta_hint > 0.0:
            raise SelfEndorsementError(
                f"Bot '{subject_bot}' cannot self-endorse with positive reputation."
            )

        rep_id = f"rep-{uuid.uuid4().hex[:12]}"
        rep = ReputationEvent(
            id=rep_id,
            subject_bot=subject_bot,
            domain=domain,
            evidence_ref=evidence_ref,
            outcome=outcome,
            delta_hint=delta_hint,
            actor_bot=actor_bot,
            occurred_at=time.time(),
        )

        self.store.append(
            Event(
                name=EVENT_REPUTATION_RECORDED,
                payload={"reputation": rep.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return rep

    def get_relationships(self, bot_id: str) -> List[RelationshipEdge]:
        edges = []
        for e in self._events():
            if e.name == EVENT_RELATION_ESTABLISHED:
                payload = e.payload or {}
                if "edge" in payload:
                    edge = RelationshipEdge.from_dict(payload["edge"])
                    if edge.from_bot == bot_id or edge.to_bot == bot_id:
                        edges.append(edge)
        return edges

    def get_reputation_vector(self, bot_id: str) -> ReputationVector:
        domain_events: Dict[str, List[ReputationEvent]] = {}

        for e in self._events():
            if e.name == EVENT_REPUTATION_RECORDED:
                payload = e.payload or {}
                if "reputation" in payload:
                    rep = ReputationEvent.from_dict(payload["reputation"])
                    if rep.subject_bot == bot_id:
                        domain_events.setdefault(rep.domain, []).append(rep)

        domain_scores: Dict[str, DomainScore] = {}
        total_score = 0.0
        total_evidence = 0
        now = time.time()

        for domain, d_events in domain_events.items():
            score = 0.5  # neutral prior
            count = len(d_events)
            last_updated = 0.0

            for ev in d_events:
                delta = max(0.1, abs(ev.delta_hint))
                if ev.outcome == "success":
                    score = min(1.0, score + delta)
                elif ev.outcome == "failure":
                    score = max(0.0, score - delta)
                if ev.occurred_at > last_updated:
                    last_updated = ev.occurred_at

            confidence = min(1.0, count / 5.0)
            total_score += score
            total_evidence += count

            domain_scores[domain] = DomainScore(
                domain=domain,
                score=score,
                confidence=confidence,
                evidence_count=count,
                last_updated=last_updated,
            )

        overall = (total_score / len(domain_scores)) if domain_scores else 0.5

        return ReputationVector(
            bot_id=bot_id,
            domains=domain_scores,
            overall_score=overall,
            evidence_count=total_evidence,
            as_of=now,
        )

    def get_all_reputations(self) -> Dict[str, ReputationVector]:
        """Aggregate reputation vectors for all bots that have reputation events."""
        bots = set()
        for e in self._events():
            if e.name == EVENT_REPUTATION_RECORDED:
                payload = e.payload or {}
                if "reputation" in payload:
                    rep = ReputationEvent.from_dict(payload["reputation"])
                    bots.add(rep.subject_bot)
        return {b: self.get_reputation_vector(b) for b in bots}

    def select_specialists(
        self,
        candidate_bots: List[str],
        domain: str,
        count: int = 1,
    ) -> List[Tuple[str, float]]:
        ranked = []
        for bot_id in candidate_bots:
            vec = self.get_reputation_vector(bot_id)
            ds = vec.domains.get(domain)
            if ds:
                effective_score = ds.score * ds.confidence + 0.5 * (1.0 - ds.confidence)
            else:
                effective_score = 0.5  # cold-start exploration baseline
            ranked.append((bot_id, effective_score))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:count]

    def assign_role(
        self,
        session_id: str,
        bot_id: str,
        role: str,
        rationale: str,
    ) -> RoleAssignment:
        assignment_id = f"role-{uuid.uuid4().hex[:12]}"
        assignment = RoleAssignment(
            id=assignment_id,
            council_session_id=session_id,
            bot_id=bot_id,
            role=role,
            rationale=rationale,
            assigned_at=time.time(),
        )

        self.store.append(
            Event(
                name=EVENT_ROLE_ASSIGNED,
                payload={"role": assignment.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return assignment

    def record_collaboration(
        self,
        participants: List[str],
        task_ref: str,
        outcome_ref: str,
        reviewer_refs: List[str],
        score: Optional[float] = None,
    ) -> CollaborationRecord:
        record_id = f"collab-{uuid.uuid4().hex[:12]}"
        record = CollaborationRecord(
            id=record_id,
            participants=participants,
            task_ref=task_ref,
            outcome_ref=outcome_ref,
            reviewer_refs=reviewer_refs,
            recorded_at=time.time(),
            score=score,
        )

        self.store.append(
            Event(
                name=EVENT_COLLABORATION_RECORDED,
                payload={"collaboration": record.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return record
