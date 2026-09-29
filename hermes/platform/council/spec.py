"""Council specification, session, and decision domain models."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.bots.identity import CouncilSpec, DecisionRecord


@dataclass
class CouncilSession:
    """A live or completed deliberation session run by a Council."""
    session_id: str
    council_id: str
    objective: str
    members: List[str] = field(default_factory=list)
    # FSM phases:
    # created, selecting_members, independent_analysis, debate_round,
    # synthesis, policy_check, decision_recorded, action_pending,
    # completed, failed, aborted, paused
    phase: str = "created"
    revision: int = 1
    round: int = 0
    budget: Dict[str, Any] = field(default_factory=dict)
    command_id: Optional[str] = None
    positions: Dict[str, Any] = field(default_factory=dict)  # bot_id -> position text/data
    dissent: Dict[str, str] = field(default_factory=dict)  # bot_id -> reason
    leaf_runs: List[str] = field(default_factory=list)  # leaf_ids
    leaf_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # bot_id -> snapshot dict
    debate_turns: List[Dict[str, Any]] = field(default_factory=list)  # history of debate messages/critiques
    evidence_refs: List[str] = field(default_factory=list)
    action_plan: List[Dict[str, Any]] = field(default_factory=list)
    decision_id: Optional[str] = None
    cost_usd: float = 0.0
    tokens_used: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouncilSession":
        valid_keys = {
            "session_id",
            "council_id",
            "objective",
            "members",
            "phase",
            "revision",
            "round",
            "budget",
            "command_id",
            "positions",
            "dissent",
            "leaf_runs",
            "leaf_snapshots",
            "debate_turns",
            "evidence_refs",
            "action_plan",
            "decision_id",
            "cost_usd",
            "tokens_used",
            "error",
            "created_at",
            "updated_at",
            "correlation_id",
            "metadata",
        }
        fields = {k: data[k] for k in valid_keys if k in data}
        return cls(**fields)


__all__ = ["CouncilSpec", "DecisionRecord", "CouncilSession"]
