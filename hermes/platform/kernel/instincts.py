"""Instinct System with Multi-Dimensional Confidence Vector.

Replaces naive success counts with a rigorous confidence vector:
- correctness: test pass rate
- test_coverage: verification coverage
- production_history: total execution volume
- rollback_rate: proportion of executions rolled back
- age_decay: time decay based on half-life
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ConfidenceVector:
    """Multi-dimensional metric vector evaluating the empirical reliability of an instinct or skill."""
    correctness: float = 1.0          # Empirical pass rate (0.0 - 1.0)
    test_coverage: float = 0.8        # Test coverage fraction (0.0 - 1.0)
    executions: int = 0               # Total production executions
    failures: int = 0                 # Execution failures
    rollbacks: int = 0                # Rollbacks triggered
    last_executed_at: float = field(default_factory=time.time)
    half_life_days: float = 30.0      # Half-life for age decay
    production_history: float = 1.0
    age_days: float = 0.0

    def __post_init__(self) -> None:
        if self.age_days > 0.0:
            self.last_executed_at = time.time() - (self.age_days * 86400.0)

    @property
    def rollback_rate(self) -> float:
        if self.executions == 0:
            return 0.0
        return min(1.0, self.rollbacks / self.executions)

    @property
    def age_decay(self) -> float:
        """Exponential decay based on elapsed days since last successful execution."""
        elapsed_seconds = max(0.0, time.time() - self.last_executed_at)
        elapsed_days = elapsed_seconds / 86400.0
        if self.half_life_days <= 0:
            return 1.0
        # Decay: 2^(-elapsed / half_life)
        return math.pow(0.5, elapsed_days / self.half_life_days)

    def composite_score(self) -> float:
        """Calculate weighted composite confidence score."""
        if self.executions == 0:
            return 0.0

        # Logarithmic scaling for execution volume (asymptotic to 1.0 at ~100 executions)
        volume_factor = min(1.0, math.log10(self.executions + 1) / 2.0)

        # Base quality
        base_quality = (
            0.40 * self.correctness +
            0.30 * self.test_coverage +
            0.30 * volume_factor
        )

        # Penalize for rollbacks and scale by age decay
        penalized = base_quality * (1.0 - 0.90 * self.rollback_rate)
        final_score = penalized * self.age_decay
        return round(max(0.0, min(1.0, final_score)), 4)

    def record_outcome(
        self,
        success: bool,
        test_passed: bool = True,
        rolled_back: bool = False,
    ) -> None:
        self.executions += 1
        self.last_executed_at = time.time()

        if not success:
            self.failures += 1
        if rolled_back:
            self.rollbacks += 1

        # Incremental moving average for correctness and test coverage
        alpha = 1.0 / min(self.executions, 50)
        curr_correctness = 1.0 if (success and not rolled_back) else 0.0
        self.correctness = (1.0 - alpha) * self.correctness + alpha * curr_correctness

        curr_cov = 1.0 if test_passed else 0.0
        self.test_coverage = (1.0 - alpha) * self.test_coverage + alpha * curr_cov

    def record_execution(
        self,
        success: bool,
        test_passed: bool = True,
        rolled_back: bool = False,
    ) -> None:
        self.record_outcome(success=success, test_passed=test_passed, rolled_back=rolled_back)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite_score": self.composite_score(),
            "correctness": round(self.correctness, 4),
            "test_coverage": round(self.test_coverage, 4),
            "executions": self.executions,
            "failures": self.failures,
            "rollbacks": self.rollbacks,
            "rollback_rate": round(self.rollback_rate, 4),
            "age_decay": round(self.age_decay, 4),
            "last_executed_at": self.last_executed_at,
        }


@dataclass
class InstinctProfile:
    """An instinct or skill pattern with its associated confidence vector."""
    instinct_id: str = ""
    skill_name: str = ""
    domain: str = "general"
    confidence: ConfidenceVector = field(default_factory=ConfidenceVector)
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instinct_id:
            self.instinct_id = f"inst-{self.skill_name or 'unnamed'}"

    @property
    def executions(self) -> int:
        return self.confidence.executions

    @property
    def failures(self) -> int:
        return self.confidence.failures

    def record_execution(self, success: bool, has_tests: bool = True, rolled_back: bool = False) -> None:
        self.confidence.record_execution(success=success, test_passed=has_tests, rolled_back=rolled_back)

    def is_reliable(self, threshold: float = 0.70) -> bool:
        return self.confidence.composite_score() >= threshold

    def is_eligible_for_promotion(self, min_confidence: float = 0.85, min_executions: int = 5) -> bool:
        """Determines if the instinct has demonstrated empirical reliability for promotion."""
        return (
            self.confidence.executions >= min_executions
            and self.confidence.composite_score() >= min_confidence
            and self.confidence.rollback_rate <= 0.05
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instinct_id": self.instinct_id,
            "skill_name": self.skill_name,
            "domain": self.domain,
            "description": self.description,
            "confidence": self.confidence.to_dict(),
            "eligible_for_promotion": self.is_eligible_for_promotion(),
        }
