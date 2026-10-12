"""Turn State Machine (FSM) and Pre-turn Decider.

Implements the strict turn lifecycle states:
- BOOTING: initialization and session restore
- IDLE: awaiting input (human, webhook, cron)
- COLLECTING: assembling context and pending inputs
- PLANNING: assembling plan / seed for complex tasks
- EXECUTING: active tool calls and generation
- AWAITING_APPROVAL: waiting on human decision with timeout
- VERIFYING: checking acceptance criteria
- COMPLETED: turn completed successfully
- FAILED: terminal failure
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger("hermes.platform.decider")


class TurnState(str, Enum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


# Strict transition table defining allowed moves
VALID_TRANSITIONS: Dict[TurnState, Set[TurnState]] = {
    TurnState.BOOTING: {TurnState.IDLE, TurnState.FAILED},
    TurnState.IDLE: {TurnState.COLLECTING, TurnState.FAILED},
    TurnState.COLLECTING: {TurnState.PLANNING, TurnState.EXECUTING, TurnState.FAILED},
    TurnState.PLANNING: {TurnState.EXECUTING, TurnState.FAILED},
    TurnState.EXECUTING: {
        TurnState.AWAITING_APPROVAL,
        TurnState.VERIFYING,
        TurnState.COMPLETED,
        TurnState.FAILED,
    },
    TurnState.AWAITING_APPROVAL: {TurnState.EXECUTING, TurnState.FAILED},
    TurnState.VERIFYING: {TurnState.COMPLETED, TurnState.EXECUTING, TurnState.FAILED},
    TurnState.COMPLETED: {TurnState.IDLE},
    TurnState.FAILED: {TurnState.IDLE},
}


class TurnFSM:
    """Finite State Machine managing agent turn lifecycles."""

    def __init__(self, initial_state: TurnState = TurnState.BOOTING):
        self.state = initial_state
        self.history: List[Dict[str, str]] = [{"state": self.state.value, "reason": "init"}]

    def transition_to(self, target_state: TurnState, reason: str = "") -> None:
        """Transition to target_state or raise InvalidStateTransitionError."""
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if target_state not in allowed:
            err_msg = f"Invalid state transition from {self.state.value} to {target_state.value} (reason: {reason})"
            logger.error(err_msg)
            raise InvalidStateTransitionError(err_msg)

        logger.debug("TurnFSM transition: %s -> %s (%s)", self.state.value, target_state.value, reason)
        self.state = target_state
        self.history.append({"state": target_state.value, "reason": reason})

    def handle_approval_timeout(self, timeout_policy: str = "abort") -> TurnState:
        """Handle expiration of AWAITING_APPROVAL state according to Phase 1 timeout policy."""
        if self.state != TurnState.AWAITING_APPROVAL:
            raise InvalidStateTransitionError(
                f"Cannot handle approval timeout in non-approval state: {self.state.value}"
            )

        if timeout_policy == "proceed_read_only":
            self.transition_to(TurnState.EXECUTING, reason="approval_timeout_proceed_read_only")
        else:
            self.transition_to(TurnState.FAILED, reason="approval_timeout_abort")

        return self.state


class PreTurnDecision(str, Enum):
    DIRECT_EXECUTION = "DIRECT_EXECUTION"
    REQUIRES_PLANNING = "REQUIRES_PLANNING"
    NOOP = "NOOP"


_PLANNING_TRIGGER_PATTERNS = [
    r"\b(miss[aã]o|mission)\b",
    r"\b(passos|steps)\b",
    r"\b(fases|phases)\b",
    r"\b(etapas)\b",
    r"\b(plano\s+de\s+ado[çc][ãa]o)\b",
    r"\b(roadmap)\b",
    r"\b(milestones?)\b",
    r"\b(fase\s+\d+)\b",
    r"\b(passo\s+\d+)\b",
]
_COMPILED_PLANNING_RE = re.compile("|".join(_PLANNING_TRIGGER_PATTERNS), re.IGNORECASE)


def classify_pre_turn(message: str) -> PreTurnDecision:
    """Classify message intent using fast, zero-latency heuristics.

    Returns:
    - NOOP for empty/whitespace inputs
    - REQUIRES_PLANNING for structured, multi-step, or mission prompts
    - DIRECT_EXECUTION for straightforward questions, commands, and tasks
    """
    if not message or not message.strip():
        return PreTurnDecision.NOOP

    cleaned = message.strip()

    # Check for explicit multi-step keywords
    if _COMPILED_PLANNING_RE.search(cleaned):
        return PreTurnDecision.REQUIRES_PLANNING

    # Check for long structured inputs with multiple numbered/bulleted items
    lines = cleaned.splitlines()
    if len(cleaned) > 500 and len(lines) >= 5:
        # Check if list items exist
        list_items = [ln for ln in lines if re.match(r"^\s*(\d+[\.\)]|[\-\*])\s+", ln)]
        if len(list_items) >= 3:
            return PreTurnDecision.REQUIRES_PLANNING

    return PreTurnDecision.DIRECT_EXECUTION


def decide_and_advance_turn(fsm: TurnFSM, message: str) -> PreTurnDecision:
    """Advance FSM from IDLE through COLLECTING to PLANNING or EXECUTING based on decision."""
    decision = classify_pre_turn(message)

    if decision == PreTurnDecision.NOOP:
        return decision

    # Must be IDLE to start collecting
    if fsm.state == TurnState.BOOTING:
        fsm.transition_to(TurnState.IDLE, reason="boot_complete")

    if fsm.state == TurnState.IDLE:
        fsm.transition_to(TurnState.COLLECTING, reason="input_received")

    if decision == PreTurnDecision.REQUIRES_PLANNING:
        fsm.transition_to(TurnState.PLANNING, reason="complex_mission_requires_planning")
    else:
        fsm.transition_to(TurnState.EXECUTING, reason="direct_execution")

    return decision
