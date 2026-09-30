"""Agent Reputation Engine.

Tracks performance metrics and calculates dynamic trust scores:
- agent_id, role, domain
- successes, failures, rollbacks
- avg_cost_usd, avg_tokens
- trust_score (0.0 to 1.0)

Enforces trust-gated autonomy: high-risk operations require proportional trust.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from hermes.platform.kernel.contract import ToolRiskTier


@dataclass
class AgentReputationRecord:
    agent_id: str
    role: str
    domain: str
    successes: int = 0
    failures: int = 0
    rollbacks: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    trust_score: float = 0.70  # Default initial baseline trust
    last_updated_at: float = field(default_factory=time.time)

    @property
    def total_tasks(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 1.0
        return self.successes / self.total_tasks

    @property
    def rollback_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.rollbacks / self.total_tasks

    @property
    def avg_tokens(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.total_tokens / self.total_tasks

    @property
    def avg_cost_usd(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.total_cost_usd / self.total_tasks

    def update_performance(
        self,
        success: bool,
        tokens: int = 0,
        cost_usd: float = 0.0,
        rolled_back: bool = False,
    ) -> None:
        self.total_tokens += tokens
        self.total_cost_usd += cost_usd
        self.last_updated_at = time.time()

        if success and not rolled_back:
            self.successes += 1
            # Positive trust increment with saturation
            self.trust_score = min(1.0, self.trust_score + 0.03 * (1.0 - self.trust_score))
        else:
            self.failures += 1
            # Asymmetric penalty for failure
            penalty = 0.10
            if rolled_back:
                self.rollbacks += 1
                penalty = 0.25  # Severe penalty for causing rollbacks
            self.trust_score = max(0.05, self.trust_score - penalty)

    def can_execute_risk_tier(self, tier: ToolRiskTier) -> bool:
        """Determines if the agent's current trust score satisfies the tier threshold."""
        tier_thresholds = {
            ToolRiskTier.READ: 0.10,
            ToolRiskTier.LOW: 0.30,
            ToolRiskTier.MEDIUM: 0.50,
            ToolRiskTier.HIGH: 0.75,
            ToolRiskTier.CRITICAL: 0.95,
        }
        return self.trust_score >= tier_thresholds.get(tier, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "domain": self.domain,
            "trust_score": round(self.trust_score, 4),
            "successes": self.successes,
            "failures": self.failures,
            "rollbacks": self.rollbacks,
            "total_tasks": self.total_tasks,
            "success_rate": round(self.success_rate, 4),
            "rollback_rate": round(self.rollback_rate, 4),
            "avg_tokens": round(self.avg_tokens, 1),
            "avg_cost_usd": round(self.avg_cost_usd, 4),
            "last_updated_at": self.last_updated_at,
        }


class AgentReputationEngine:
    """Manages the civilization-wide reputation ledger for all agents."""

    def __init__(self, event_store: Optional[Any] = None) -> None:
        self.event_store = event_store
        self._records: Dict[str, AgentReputationRecord] = {}

    def list_all(self) -> List[AgentReputationRecord]:
        return list(self._records.values())

    def get_or_create(
        self,
        agent_id: str,
        role: str = "general",
        domain: str = "general",
    ) -> AgentReputationRecord:
        if agent_id not in self._records:
            self._records[agent_id] = AgentReputationRecord(
                agent_id=agent_id,
                role=role,
                domain=domain,
            )
        return self._records[agent_id]

    def record_outcome(
        self,
        agent_id: str,
        success: bool,
        tokens: int = 0,
        cost_usd: float = 0.0,
        rolled_back: bool = False,
        role: str = "general",
        domain: str = "general",
    ) -> AgentReputationRecord:
        record = self.get_or_create(agent_id, role=role, domain=domain)
        record.update_performance(
            success=success,
            tokens=tokens,
            cost_usd=cost_usd,
            rolled_back=rolled_back,
        )
        return record

    def get_reputation(self, agent_id: str) -> Optional[AgentReputationRecord]:
        return self._records.get(agent_id)

    def list_all(self) -> Dict[str, Dict[str, Any]]:
        return {aid: rec.to_dict() for aid, rec in self._records.items()}
