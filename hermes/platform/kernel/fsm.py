"""Agent State Machine (FSM).

Enforces deterministic lifecycle transitions for agents:
CREATED -> PLANNING -> READY -> EXECUTING -> VALIDATING -> REFLECTING -> COMPLETED
with guarded error and terminal paths: FAILED, BLOCKED, CANCELLED, ROLLED_BACK.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class AgentState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    READY = "ready"
    EXECUTING = "executing"
    VALIDATING = "validating"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"

    @property
    def is_terminal(self) -> bool:
        return self in {
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
            AgentState.ROLLED_BACK,
        }

    @property
    def is_error(self) -> bool:
        return self in {
            AgentState.FAILED,
            AgentState.BLOCKED,
            AgentState.CANCELLED,
            AgentState.ROLLED_BACK,
        }


class InvalidStateTransitionError(Exception):
    """Raised when an illegal transition is attempted in the agent FSM."""
    def __init__(self, from_state: AgentState, to_state: AgentState, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(
            f"Invalid agent state transition from {from_state.value} to {to_state.value}. "
            f"Reason: {reason or 'Not allowed by FSM policy'}"
        )


# Valid transition graph
LEGAL_TRANSITIONS: Dict[AgentState, Set[AgentState]] = {
    AgentState.CREATED: {
        AgentState.PLANNING,
        AgentState.READY,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.PLANNING: {
        AgentState.READY,
        AgentState.BLOCKED,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.READY: {
        AgentState.EXECUTING,
        AgentState.BLOCKED,
        AgentState.FAILED,
        AgentState.CANCELLED,
    },
    AgentState.EXECUTING: {
        AgentState.VALIDATING,
        AgentState.BLOCKED,
        AgentState.FAILED,
        AgentState.CANCELLED,
        AgentState.ROLLED_BACK,
    },
    AgentState.VALIDATING: {
        AgentState.REFLECTING,
        AgentState.EXECUTING,  # Iteration/retry if validation finds fixable defects
        AgentState.FAILED,
        AgentState.ROLLED_BACK,
        AgentState.BLOCKED,
        AgentState.CANCELLED,
    },
    AgentState.REFLECTING: {
        AgentState.COMPLETED,
        AgentState.PLANNING,  # Next milestone in long-running task
        AgentState.FAILED,
        AgentState.ROLLED_BACK,
    },
    # Unblock paths
    AgentState.BLOCKED: {
        AgentState.PLANNING,
        AgentState.READY,
        AgentState.EXECUTING,
        AgentState.CANCELLED,
        AgentState.FAILED,
    },
    # Terminal states have no valid outward transitions
    AgentState.COMPLETED: set(),
    AgentState.FAILED: set(),
    AgentState.CANCELLED: set(),
    AgentState.ROLLED_BACK: set(),
}


@dataclass(frozen=True)
class StateTransitionRecord:
    from_state: AgentState
    to_state: AgentState
    timestamp: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentStateMachine:
    """Deterministic Finite State Machine managing an individual agent's lifecycle."""

    def __init__(self, agent_id: str, initial_state: AgentState = AgentState.CREATED):
        self.agent_id = agent_id
        self._current_state = initial_state
        self._history: List[StateTransitionRecord] = []
        # Record initial creation
        self._history.append(
            StateTransitionRecord(
                from_state=AgentState.CREATED,
                to_state=initial_state,
                timestamp=time.time(),
                reason="Initial state instantiation",
            )
        )

    @property
    def current_state(self) -> AgentState:
        return self._current_state

    @property
    def state(self) -> AgentState:
        return self._current_state

    @property
    def history(self) -> List[StateTransitionRecord]:
        return list(self._history)

    def is_terminal(self) -> bool:
        return self._current_state.is_terminal

    def can_transition_to(self, target_state: AgentState) -> bool:
        allowed = LEGAL_TRANSITIONS.get(self._current_state, set())
        return target_state in allowed

    def transition_to(
        self,
        target_state: AgentState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StateTransitionRecord:
        """Attempt to transition to target_state.

        Raises InvalidStateTransitionError if transition is illegal.
        """
        if not self.can_transition_to(target_state):
            raise InvalidStateTransitionError(
                from_state=self._current_state,
                to_state=target_state,
                reason=reason,
            )

        from_state = self._current_state
        self._current_state = target_state
        record = StateTransitionRecord(
            from_state=from_state,
            to_state=target_state,
            timestamp=time.time(),
            reason=reason,
            metadata=metadata or {},
        )
        self._history.append(record)
        return record
