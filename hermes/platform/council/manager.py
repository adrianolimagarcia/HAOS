"""Event-sourced CouncilManager managing CouncilSpecs, CouncilSessions, and DecisionRecords."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from .spec import CouncilSession, CouncilSpec, DecisionRecord

logger = logging.getLogger("hermes.platform.council.manager")

_COUNCIL_CREATED = "civ.council.created"
_SESSION_STARTED = "civ.council.session-started"
_POSITION_SUBMITTED = "civ.council.position-submitted"
_PHASE_CHANGED = "civ.council.phase-changed"
_DEBATE_ROUND_STARTED = "civ.council.debate-round-started"
_DEBATE_TURN_RECORDED = "civ.council.debate-turn-recorded"
_DECISION_RECORDED = "civ.council.decision-recorded"
_SESSION_PAUSED = "civ.council.session-paused"
_SESSION_ABORTED = "civ.council.session-aborted"
_SESSION_FAILED = "civ.council.session-failed"

_LEGAL_TRANSITIONS: Dict[str, set[str]] = {
    "created": {"selecting_members", "independent_analysis", "aborted", "failed"},
    "selecting_members": {"independent_analysis", "aborted", "failed"},
    "independent_analysis": {"debate_round", "synthesis", "paused", "aborted", "failed"},
    "debate_round": {"debate_round", "synthesis", "paused", "aborted", "failed"},
    "synthesis": {"policy_check", "decision_recorded", "paused", "aborted", "failed"},
    "policy_check": {"decision_recorded", "action_pending", "paused", "aborted", "failed"},
    "decision_recorded": {"action_pending", "completed", "paused", "aborted", "failed"},
    "action_pending": {"completed", "failed", "paused", "aborted"},
    "paused": {"independent_analysis", "debate_round", "synthesis", "policy_check", "action_pending", "aborted", "failed"},
    "completed": set(),
    "aborted": set(),
    "failed": set(),
}


class CouncilManager:
    """Manages the full lifecycle and event-sourced persistence of Councils."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def _events(self) -> List[Event]:
        return [
            e
            for e in self.event_store.get_all()
            if e.name
            in (
                _COUNCIL_CREATED,
                _SESSION_STARTED,
                _POSITION_SUBMITTED,
                _PHASE_CHANGED,
                _DEBATE_ROUND_STARTED,
                _DEBATE_TURN_RECORDED,
                _DECISION_RECORDED,
                _SESSION_PAUSED,
                _SESSION_ABORTED,
                _SESSION_FAILED,
            )
        ]

    def _state(
        self,
    ) -> tuple[
        Dict[str, CouncilSpec],
        Dict[str, CouncilSession],
        Dict[str, DecisionRecord],
    ]:
        """Replay events and materialize current state."""
        councils: Dict[str, CouncilSpec] = {}
        sessions: Dict[str, CouncilSession] = {}
        decisions: Dict[str, DecisionRecord] = {}

        for e in self._events():
            name = e.name
            payload = e.payload

            if name == _COUNCIL_CREATED:
                spec_dict = payload.get("spec")
                if spec_dict:
                    spec = CouncilSpec.from_dict(spec_dict)
                    councils[spec.id] = spec

            elif name == _SESSION_STARTED:
                sess_dict = payload.get("session")
                if sess_dict:
                    sess = CouncilSession.from_dict(sess_dict)
                    sessions[sess.session_id] = sess

            elif name == _POSITION_SUBMITTED:
                sess_id = payload.get("session_id")
                bot_id = payload.get("bot_id")
                pos = payload.get("position")
                dis = payload.get("dissent")
                leaf_id = payload.get("leaf_id")
                snapshot = payload.get("leaf_snapshot")
                cost = float(payload.get("cost_usd", 0.0))
                tokens = int(payload.get("tokens", 0))

                if sess_id in sessions and bot_id:
                    sess = sessions[sess_id]
                    sess.positions[bot_id] = pos
                    if dis:
                        sess.dissent[bot_id] = dis
                    if leaf_id and leaf_id not in sess.leaf_runs:
                        sess.leaf_runs.append(leaf_id)
                    if snapshot:
                        sess.leaf_snapshots[bot_id] = snapshot
                    sess.cost_usd += cost
                    sess.tokens_used += tokens
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _DEBATE_ROUND_STARTED:
                sess_id = payload.get("session_id")
                round_num = payload.get("round", 1)
                if sess_id in sessions:
                    sess = sessions[sess_id]
                    sess.phase = "debate_round"
                    sess.round = round_num
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _DEBATE_TURN_RECORDED:
                sess_id = payload.get("session_id")
                turn_data = payload.get("turn")
                cost = float(payload.get("cost_usd", 0.0))
                tokens = int(payload.get("tokens", 0))
                if sess_id in sessions and turn_data:
                    sess = sessions[sess_id]
                    sess.debate_turns.append(turn_data)
                    bot_id = turn_data.get("bot_id")
                    if bot_id and "position" in turn_data:
                        sess.positions[bot_id] = turn_data["position"]
                    if bot_id and turn_data.get("dissent"):
                        sess.dissent[bot_id] = turn_data["dissent"]
                    sess.cost_usd += cost
                    sess.tokens_used += tokens
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _PHASE_CHANGED:
                sess_id = payload.get("session_id")
                new_phase = payload.get("phase")
                if sess_id in sessions and new_phase:
                    sess = sessions[sess_id]
                    sess.phase = new_phase
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _SESSION_PAUSED:
                sess_id = payload.get("session_id")
                reason = payload.get("reason")
                if sess_id in sessions:
                    sess = sessions[sess_id]
                    sess.phase = "paused"
                    sess.metadata["paused_reason"] = reason
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _SESSION_ABORTED:
                sess_id = payload.get("session_id")
                reason = payload.get("reason")
                if sess_id in sessions:
                    sess = sessions[sess_id]
                    sess.phase = "aborted"
                    sess.metadata["aborted_reason"] = reason
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _SESSION_FAILED:
                sess_id = payload.get("session_id")
                err = payload.get("error")
                if sess_id in sessions:
                    sess = sessions[sess_id]
                    sess.phase = "failed"
                    sess.error = err
                    sess.revision += 1
                    sess.updated_at = payload.get("timestamp", time.time())

            elif name == _DECISION_RECORDED:
                dec_dict = payload.get("decision")
                if dec_dict:
                    dec = DecisionRecord.from_dict(dec_dict)
                    decisions[dec.id] = dec
                    if dec.council_session_id in sessions:
                        sess = sessions[dec.council_session_id]
                        sess.decision_id = dec.id
                        sess.phase = "completed"
                        sess.revision += 1
                        sess.updated_at = payload.get("timestamp", time.time())

        return councils, sessions, decisions

    def register(self, spec: CouncilSpec) -> CouncilSpec:
        """Register a new CouncilSpec. Rejects duplicates."""
        councils, _, _ = self._state()
        if spec.id in councils:
            raise ValueError(f"CouncilSpec already exists: {spec.id}")
        self.event_store.append(
            Event(name=_COUNCIL_CREATED, payload={"spec": spec.to_dict()})
        )
        return spec

    def get(self, council_id: str) -> Optional[CouncilSpec]:
        councils, _, _ = self._state()
        return councils.get(council_id)

    def list(self) -> List[CouncilSpec]:
        councils, _, _ = self._state()
        return list(councils.values())

    def start_session(
        self,
        council_id: str,
        objective: str,
        members: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
        command_id: Optional[str] = None,
        budget: Optional[Dict[str, Any]] = None,
    ) -> CouncilSession:
        """Initiate a deliberation session under council_id with idempotency check."""
        _, sessions, _ = self._state()

        if command_id:
            for s in sessions.values():
                if s.command_id == command_id:
                    return s

        council = self.get(council_id)
        if council is None:
            raise KeyError(f"Council {council_id} not registered")

        session_members = members or list(council.members)
        if len(session_members) < 2:
            raise ValueError("A CouncilSession requires at least 2 member bots for collective deliberation")

        session_id = f"csess-{council_id}-{uuid.uuid4().hex[:8]}"
        session = CouncilSession(
            session_id=session_id,
            council_id=council_id,
            objective=objective,
            members=session_members,
            phase="independent_analysis",
            command_id=command_id,
            budget=budget or council.budget or {},
            correlation_id=correlation_id,
        )

        self.event_store.append(
            Event(
                name=_SESSION_STARTED,
                payload={"session": session.to_dict()},
                correlation_id=correlation_id,
            )
        )
        return session

    def advance_phase(
        self,
        session_id: str,
        new_phase: str,
        expected_revision: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> CouncilSession:
        """Atomically advance the phase of a session checking legal transitions and revision."""
        _, sessions, _ = self._state()
        sess = sessions.get(session_id)
        if sess is None:
            raise KeyError(f"CouncilSession {session_id} not found")
        if expected_revision is not None and sess.revision != expected_revision:
            raise ValueError(f"Optimistic concurrency conflict: session revision {sess.revision} != expected {expected_revision}")

        legal = _LEGAL_TRANSITIONS.get(sess.phase, set())
        if new_phase not in legal:
            raise ValueError(f"Illegal transition from '{sess.phase}' to '{new_phase}'")

        self.event_store.append(
            Event(
                name=_PHASE_CHANGED,
                payload={"session_id": session_id, "phase": new_phase, "timestamp": time.time()},
                correlation_id=correlation_id or sess.correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def submit_position(
        self,
        session_id: str,
        bot_id: str,
        position: Any,
        dissent: Optional[str] = None,
        leaf_id: Optional[str] = None,
        leaf_snapshot: Optional[Dict[str, Any]] = None,
        cost_usd: float = 0.0,
        tokens: int = 0,
        correlation_id: Optional[str] = None,
    ) -> CouncilSession:
        """Record an independent position (and optional dissent) from a member bot."""
        _, sessions, _ = self._state()
        session = sessions.get(session_id)
        if session is None:
            raise KeyError(f"CouncilSession {session_id} not found")
        if bot_id not in session.members:
            raise PermissionError(f"Bot {bot_id} is not an enrolled member of session {session_id}")

        self.event_store.append(
            Event(
                name=_POSITION_SUBMITTED,
                payload={
                    "session_id": session_id,
                    "bot_id": bot_id,
                    "position": position,
                    "dissent": dissent,
                    "leaf_id": leaf_id,
                    "leaf_snapshot": leaf_snapshot,
                    "cost_usd": cost_usd,
                    "tokens": tokens,
                    "timestamp": time.time(),
                },
                correlation_id=correlation_id or session.correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def record_debate_turn(
        self,
        session_id: str,
        turn_data: Dict[str, Any],
        cost_usd: float = 0.0,
        tokens: int = 0,
        correlation_id: Optional[str] = None,
    ) -> CouncilSession:
        """Record a turn message or critique in a multi-round debate."""
        _, sessions, _ = self._state()
        sess = sessions.get(session_id)
        if sess is None:
            raise KeyError(f"CouncilSession {session_id} not found")

        self.event_store.append(
            Event(
                name=_DEBATE_TURN_RECORDED,
                payload={
                    "session_id": session_id,
                    "turn": turn_data,
                    "cost_usd": cost_usd,
                    "tokens": tokens,
                    "timestamp": time.time(),
                },
                correlation_id=correlation_id or sess.correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def pause_session(self, session_id: str, reason: str, correlation_id: Optional[str] = None) -> CouncilSession:
        self.event_store.append(
            Event(
                name=_SESSION_PAUSED,
                payload={"session_id": session_id, "reason": reason, "timestamp": time.time()},
                correlation_id=correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def abort_session(self, session_id: str, reason: str, correlation_id: Optional[str] = None) -> CouncilSession:
        self.event_store.append(
            Event(
                name=_SESSION_ABORTED,
                payload={"session_id": session_id, "reason": reason, "timestamp": time.time()},
                correlation_id=correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def fail_session(self, session_id: str, error: str, correlation_id: Optional[str] = None) -> CouncilSession:
        self.event_store.append(
            Event(
                name=_SESSION_FAILED,
                payload={"session_id": session_id, "error": error, "timestamp": time.time()},
                correlation_id=correlation_id,
            )
        )
        _, sessions, _ = self._state()
        return sessions[session_id]

    def record_decision(
        self,
        session_id: str,
        synthesis: str,
        decision: str,
        confidence: float = 1.0,
        action_refs: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> DecisionRecord:
        """Synthesize independent positions and record the binding DecisionRecord."""
        _, sessions, _ = self._state()
        session = sessions.get(session_id)
        if session is None:
            raise KeyError(f"CouncilSession {session_id} not found")
        if not session.positions:
            raise ValueError(f"Cannot record decision: no positions have been submitted for session {session_id}")

        decision_id = f"dec-{session.council_id}-{uuid.uuid4().hex[:8]}"
        record = DecisionRecord(
            id=decision_id,
            council_id=session.council_id,
            council_session_id=session_id,
            objective=session.objective,
            participants=list(session.positions.keys()),
            positions=dict(session.positions),
            synthesis=synthesis,
            dissent=dict(session.dissent),
            decision=decision,
            confidence=confidence,
            action_refs=action_refs or [],
            evidence_refs=evidence_refs or list(session.evidence_refs),
            leaf_refs=list(session.leaf_runs),
            correlation_id=correlation_id or session.correlation_id,
        )

        self.event_store.append(
            Event(
                name=_DECISION_RECORDED,
                payload={"decision": record.to_dict(), "timestamp": time.time()},
                correlation_id=correlation_id or session.correlation_id,
            )
        )
        return record

    def get_session(self, session_id: str) -> Optional[CouncilSession]:
        _, sessions, _ = self._state()
        return sessions.get(session_id)

    def list_sessions(self, council_id: Optional[str] = None) -> List[CouncilSession]:
        _, sessions, _ = self._state()
        results = list(sessions.values())
        if council_id is not None:
            results = [s for s in results if s.council_id == council_id]
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def get_decision(self, decision_id: str) -> Optional[DecisionRecord]:
        _, _, decisions = self._state()
        return decisions.get(decision_id)

    def list_decisions(self, council_id: Optional[str] = None) -> List[DecisionRecord]:
        _, _, decisions = self._state()
        results = list(decisions.values())
        if council_id is not None:
            results = [d for d in results if d.council_id == council_id]
        results.sort(key=lambda d: d.created_at, reverse=True)
        return results


__all__ = ["CouncilManager"]
