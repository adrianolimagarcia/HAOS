"""Hard budget enforcement and tracking for Council deliberations."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


class BudgetExhaustedError(RuntimeError):
    """Raised when a hard council budget constraint is exceeded."""
    def __init__(self, metric: str, limit: float, current: float):
        super().__init__(f"Council budget exceeded on {metric}: current {current} >= limit {limit}")
        self.metric = metric
        self.limit = limit
        self.current = current


@dataclass
class CouncilBudget:
    """Configured limits and live counters for a deliberation session."""
    max_rounds: int = 3
    max_turns: int = 12
    max_tokens: int = 50_000
    max_cost_usd: float = 2.0
    timeout_seconds: float = 300.0
    max_members: int = 6
    per_member_tokens: int = 15_000

    # Live usage counters
    rounds_used: int = 0
    turns_used: int = 0
    tokens_used: int = 0
    cost_usd_used: float = 0.0
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouncilBudget":
        valid_keys = {
            "max_rounds", "max_turns", "max_tokens", "max_cost_usd",
            "timeout_seconds", "max_members", "per_member_tokens",
            "rounds_used", "turns_used", "tokens_used", "cost_usd_used", "started_at"
        }
        filtered = {k: data[k] for k in valid_keys if k in data}
        return cls(**filtered)

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)

    @property
    def remaining_cost_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self.cost_usd_used)

    @property
    def is_timed_out(self) -> bool:
        return self.elapsed_seconds >= self.timeout_seconds

    def check_limits(self) -> None:
        """Verify that current usage has not exceeded any hard limits."""
        if self.rounds_used > self.max_rounds:
            raise BudgetExhaustedError("max_rounds", self.max_rounds, self.rounds_used)
        if self.turns_used > self.max_turns:
            raise BudgetExhaustedError("max_turns", self.max_turns, self.turns_used)
        if self.tokens_used > self.max_tokens:
            raise BudgetExhaustedError("max_tokens", self.max_tokens, self.tokens_used)
        if self.cost_usd_used > self.max_cost_usd:
            raise BudgetExhaustedError("max_cost_usd", self.max_cost_usd, self.cost_usd_used)
        if self.is_timed_out:
            raise BudgetExhaustedError("timeout_seconds", self.timeout_seconds, self.elapsed_seconds)

    def reserve(self, estimated_tokens: int = 0, estimated_cost: float = 0.0) -> None:
        """Pre-flight check before initiating an LLM or member execution call."""
        if self.is_timed_out:
            raise BudgetExhaustedError("timeout_seconds", self.timeout_seconds, self.elapsed_seconds)
        if (self.tokens_used + estimated_tokens) > self.max_tokens:
            raise BudgetExhaustedError("max_tokens", self.max_tokens, self.tokens_used + estimated_tokens)
        if (self.cost_usd_used + estimated_cost) > self.max_cost_usd:
            raise BudgetExhaustedError("max_cost_usd", self.max_cost_usd, self.cost_usd_used + estimated_cost)

    def charge(self, tokens: int = 0, cost_usd: float = 0.0, turns: int = 1) -> None:
        """Record actual usage and immediately check boundaries."""
        self.tokens_used += max(0, tokens)
        self.cost_usd_used += max(0.0, cost_usd)
        self.turns_used += max(0, turns)
        self.check_limits()

    def advance_round(self) -> None:
        """Increment round counter and enforce round cap."""
        self.rounds_used += 1
        if self.rounds_used > self.max_rounds:
            raise BudgetExhaustedError("max_rounds", self.max_rounds, self.rounds_used)


__all__ = ["CouncilBudget", "BudgetExhaustedError"]
