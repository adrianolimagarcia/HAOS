"""Bot Identity Evolution Engine (HAOS Civilization V3).

Manages ExperienceEvents, EvolutionProposals, risk classification,
and promotion/rollback workflows without violating prompt caching.

Security invariants (identity-poisoning hardening):
- Experience summaries are UNTRUSTED task output: sanitized at ingestion
  (truncation + instruction-pattern stripping) and stamped provenance='untrusted'.
- High / identity-critical proposals cannot be approved/canaried/promoted
  without an explicit human ``approver`` (PolicyViolationError otherwise).
- Proposals carrying a soul patch cannot be approved without
  ``explicit_soul_change=True`` plus a human approver.
- Status transitions follow an explicit FSM; rejected/rolled_back are terminal.
"""

from __future__ import annotations

import re
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

#: Risk classes that require an explicit human approver on gate transitions.
HUMAN_APPROVAL_REQUIRED_RISKS = (RISK_HIGH, RISK_IDENTITY_CRITICAL)

STATUS_DRAFT = "draft"
STATUS_REVIEW = "review"
STATUS_APPROVED = "approved"
STATUS_CANARY = "canary"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
STATUS_ROLLED_BACK = "rolled_back"

#: Explicit FSM: draft -> review -> approved -> canary -> promoted.
#: rejected / rolled_back are TERMINAL (no transition out).
#: promoted -> rolled_back is allowed (rollback must always remain possible).
STATUS_TRANSITIONS: Dict[str, tuple] = {
    STATUS_DRAFT: (STATUS_REVIEW, STATUS_REJECTED),
    STATUS_REVIEW: (STATUS_APPROVED, STATUS_REJECTED),
    STATUS_APPROVED: (STATUS_CANARY, STATUS_REJECTED),
    STATUS_CANARY: (STATUS_PROMOTED, STATUS_ROLLED_BACK),
    STATUS_PROMOTED: (STATUS_ROLLED_BACK,),
    STATUS_REJECTED: (),
    STATUS_ROLLED_BACK: (),
}

#: Transitions that gate a change into the live identity and require policy checks.
GATE_STATUSES = (STATUS_APPROVED, STATUS_CANARY, STATUS_PROMOTED)

EVENT_EXPERIENCE_RECORDED = "civ.evolution.experience_recorded"
EVENT_PROPOSAL_CREATED = "civ.evolution.proposal_created"
EVENT_PROPOSAL_UPDATED = "civ.evolution.proposal_updated"
EVENT_PROPOSAL_PROMOTED = "civ.evolution.proposal_promoted"
EVENT_PROPOSAL_ROLLED_BACK = "civ.evolution.proposal_rolled_back"

# ---------------------------------------------------------------------------
# A3: summary sanitization (untrusted task output feeding identity)
# ---------------------------------------------------------------------------

SUMMARY_MAX_CHARS = 500

# Instruction-like line heuristics: prompt-injection prefixes, role/system
# markers, markdown headers and role XML tags. Deliberately conservative:
# drop whole lines rather than rewriting content.
_INJECTION_PATTERNS = re.compile(
    r"""(
          ignore\s+(all\s+)?(previous|prior|above)      # 'ignore previous instructions'
        | disregard\s+(all\s+)?(previous|prior|above)
        | ^\s*(system|assistant|user|developer)\s*:      # role prefixes 'system: ...'
        | ^\s*</?(system|assistant|user|developer|role|instruction|prompt)\b   # role XML tags
        | ^\s*[\#]{1,6}\s                                  # markdown headers
        | ^\s*(você\s+deve|voce\s+deve|you\s+must)        # imperative role steering
        | \bnew\s+instructions?\b
        | \bjailbreak\b
        )""",
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)


def sanitize_summary(summary: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Sanitize an untrusted task summary before it can influence identity.

    - strips whole lines that look like instructions (role prefixes, markdown
      headers, role XML tags, 'ignore previous ...', imperative steering);
    - collapses whitespace;
    - hard-truncates to ``max_chars``.
    """
    if not summary:
        return ""
    kept: List[str] = []
    for line in str(summary).splitlines():
        if _INJECTION_PATTERNS.search(line):
            continue
        cleaned = line.strip()
        if cleaned:
            kept.append(cleaned)
    text = " | ".join(kept)
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


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
    #: Provenance of the summary content. Task output is untrusted by default.
    provenance: str = "untrusted"
    #: Identity bundle hash active when the experience was recorded (canary stamp).
    identity_version: Optional[str] = None

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
            provenance=data.get("provenance", "untrusted"),
            identity_version=data.get("identity_version"),
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
        identity_version: Optional[str] = None,
    ) -> ExperienceEvent:
        """Ingest a task experience.

        The summary is UNTRUSTED task output (A3): it is sanitized at
        ingestion (instruction-like lines stripped, truncated) and stamped
        with provenance='untrusted' so downstream identity synthesis can
        never treat it as authoritative content.
        """
        exp_id = f"exp-{uuid.uuid4().hex[:12]}"
        safe_payload = dict(payload or {})
        safe_payload["provenance"] = "untrusted"
        exp = ExperienceEvent(
            id=exp_id,
            bot_id=bot_id,
            event_type=event_type,
            domain=domain,
            summary=sanitize_summary(summary),
            success=success,
            payload=safe_payload,
            occurred_at=time.time(),
            provenance="untrusted",
            identity_version=identity_version,
        )

        self.store.append(
            Event(
                name=EVENT_EXPERIENCE_RECORDED,
                payload={"experience": exp.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                # Content originates from task output: never internal-trusted.
                trust_level="untrusted_external",
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
        approver: Optional[str] = None,
        explicit_soul_change: bool = False,
    ) -> EvolutionProposal:
        """Advance a proposal through the governance FSM.

        Guards (fail-closed):
        - M2: only STATUS_TRANSITIONS edges are legal; rejected/rolled_back are
          terminal (ValueError otherwise).
        - Stale base version cannot be approved/canaried/promoted.
        - A2: RISK_HIGH / RISK_IDENTITY_CRITICAL gate transitions require an
          explicit human ``approver`` (PolicyViolationError otherwise).
        - A3: approving a proposal that carries a soul patch requires BOTH a
          human ``approver`` and ``explicit_soul_change=True`` — soul changes
          are never auto-approved.
        """
        proposals = {p.id: p for p in self.get_proposals()}
        if proposal_id not in proposals:
            raise EvolutionError(f"Proposal '{proposal_id}' not found.")

        target = proposals[proposal_id]

        # --- M2: explicit FSM validation -----------------------------------
        if new_status not in STATUS_TRANSITIONS:
            raise ValueError(
                f"Unknown status '{new_status}'. Valid: {sorted(STATUS_TRANSITIONS)}"
            )
        allowed = STATUS_TRANSITIONS.get(target.status, ())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal transition '{target.status}' -> '{new_status}' for "
                f"proposal '{proposal_id}'. Allowed: {list(allowed) or '(terminal state)'}"
            )

        # Invariant: Stale base version cannot be approved or promoted
        if (
            new_status in GATE_STATUSES
            and target.base_version_hash != current_bot_version_hash
        ):
            raise StaleBaseVersionError(
                f"Cannot transition to '{new_status}': proposal base '{target.base_version_hash}' "
                f"differs from current active version '{current_bot_version_hash}'."
            )

        # --- A2: risk_class enforcement on gate transitions -----------------
        if new_status in GATE_STATUSES and target.risk_class in HUMAN_APPROVAL_REQUIRED_RISKS:
            if not (isinstance(approver, str) and approver.strip()):
                raise PolicyViolationError(
                    f"Proposal '{proposal_id}' has risk_class '{target.risk_class}': "
                    f"transition to '{new_status}' requires an explicit human approver."
                )

        # --- A3: soul patches are never auto-approved ------------------------
        if new_status == STATUS_APPROVED and target.proposed_soul_patch:
            if not explicit_soul_change or not (
                isinstance(approver, str) and approver.strip()
            ):
                raise PolicyViolationError(
                    f"Proposal '{proposal_id}' carries a soul patch: approval requires "
                    f"explicit_soul_change=True and a human approver (never auto-approved)."
                )

        target.status = new_status
        target.updated_at = time.time()

        ev_name = EVENT_PROPOSAL_UPDATED
        if new_status == STATUS_PROMOTED:
            ev_name = EVENT_PROPOSAL_PROMOTED
        elif new_status == STATUS_ROLLED_BACK:
            ev_name = EVENT_PROPOSAL_ROLLED_BACK

        update_payload: Dict[str, Any] = {
            "proposal_id": target.id,
            "proposal": target.to_dict(),
        }
        if approver:
            update_payload["approver"] = approver
        if explicit_soul_change:
            update_payload["explicit_soul_change"] = True

        self.store.append(
            Event(
                name=ev_name,
                payload=update_payload,
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return target
