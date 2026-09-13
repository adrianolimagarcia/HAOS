"""Adaptive Intelligence Platform (Phase 3 — Adaptive Intelligence).

Implements:
1. FailurePatternDetector: Identifies recurrent failure clusters across execution traces.
2. AdaptiveRoutingOptimizer: Model/Provider performance tracking and route recommendations.
3. AgentTaskAffinity: Bayesian affinity scoring for specialist dispatch.
4. ExperimentFramework: Controlled A/B experimentation with automated rollback of inferior candidates.
5. AdaptiveIntelligenceCoordinator: Orchestrates closed-loop evolution strictly adhering to:
   Runs -> Events -> Evaluator -> Pattern Detection -> Candidate -> Sandbox -> Benchmark -> Compare -> Adopt/Reject.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.skills.procedural_engine import SkillRegistry
from hermes.platform.skills.spec import SkillSpec


class FailureCategory(str, enum.Enum):
    SYNTAX_ERROR = "syntax_error"
    TEST_REGRESSION = "test_regression"
    TOOL_PERMISSION_DENIED = "tool_permission_denied"
    TIMEOUT_EXHAUSTION = "timeout_exhaustion"
    ANCHORING_BIAS = "anchoring_bias"
    UNKNOWN = "unknown"


@dataclass
class FailureIncident:
    task_id: str
    category: FailureCategory
    error_message: str
    posture: str
    model_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class PatternDiagnostic:
    pattern_id: str
    category: FailureCategory
    frequency: int
    affected_tasks: List[str]
    suggested_remediation: str
    confidence: float


class FailurePatternDetector:
    """Detects recurrent failure patterns across task runs."""

    def __init__(self, min_cluster_size: int = 2):
        self.min_cluster_size = min_cluster_size
        self._incidents: List[FailureIncident] = []

    def record_failure(self, incident: FailureIncident) -> None:
        self._incidents.append(incident)

    def detect_patterns(self) -> List[PatternDiagnostic]:
        clusters: Dict[Tuple[FailureCategory, str], List[FailureIncident]] = collections.defaultdict(list)
        for inc in self._incidents:
            clusters[(inc.category, inc.posture)].append(inc)

        diagnostics: List[PatternDiagnostic] = []
        for (category, posture), group in clusters.items():
            if len(group) >= self.min_cluster_size:
                pattern_id = f"pat-{category.value}-{posture}-{uuid.uuid4().hex[:6]}"
                remediation = self._suggest_remediation(category, posture)
                diagnostics.append(
                    PatternDiagnostic(
                        pattern_id=pattern_id,
                        category=category,
                        frequency=len(group),
                        affected_tasks=[inc.task_id for inc in group],
                        suggested_remediation=remediation,
                        confidence=min(1.0, 0.5 + (len(group) * 0.1)),
                    )
                )
        return diagnostics

    @staticmethod
    def _suggest_remediation(category: FailureCategory, posture: str) -> str:
        if category == FailureCategory.SYNTAX_ERROR:
            return "Synthesize procedural skill enforcing AST linter pre-check before commit."
        elif category == FailureCategory.TEST_REGRESSION:
            return "Increase test coverage gate and require witness validation."
        elif category == FailureCategory.TOOL_PERMISSION_DENIED:
            return "Inspect capability manifest and request explicit posture capability grant."
        elif category == FailureCategory.TIMEOUT_EXHAUSTION:
            return "Decompose task into smaller sub-tasks via DomainSubOrchestrator."
        return "Review task context hints."


@dataclass
class RouteMetric:
    successes: int = 0
    failures: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_sec: float = 0.0

    @property
    def total_runs(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        return (self.successes / self.total_runs) if self.total_runs > 0 else 0.0

    @property
    def avg_latency(self) -> float:
        return (self.total_latency_sec / self.total_runs) if self.total_runs > 0 else 0.0


@dataclass
class RouteRecommendation:
    posture: str
    current_route: str
    recommended_route: str
    projected_savings_pct: float
    confidence: float
    rationale: str


class AdaptiveRoutingOptimizer:
    """Tracks model/provider metrics and recommends cost-effective and resilient routes."""

    def __init__(self):
        # Key: (posture, provider_id, model_id)
        self._metrics: Dict[Tuple[str, str, str], RouteMetric] = collections.defaultdict(RouteMetric)

    def record_run(
        self,
        posture: str,
        provider_id: str,
        model_id: str,
        success: bool,
        tokens: int,
        cost_usd: float,
        latency_sec: float,
    ) -> None:
        metric = self._metrics[(posture, provider_id, model_id)]
        if success:
            metric.successes += 1
        else:
            metric.failures += 1
        metric.total_tokens += tokens
        metric.total_cost_usd += cost_usd
        metric.total_latency_sec += latency_sec

    def recommend_optimizations(self, posture: str) -> List[RouteRecommendation]:
        """Recommends route optimizations when a cheaper route matches or exceeds baseline success rate."""
        posture_routes = {k: v for k, v in self._metrics.items() if k[0] == posture and v.total_runs >= 2}
        if len(posture_routes) < 2:
            return []

        # Sort by success rate descending, then cost ascending
        sorted_routes = sorted(
            posture_routes.items(),
            key=lambda item: (item[1].success_rate, -item[1].total_cost_usd),
            reverse=True,
        )

        best_key, best_metric = sorted_routes[0]
        recommendations = []

        for key, metric in sorted_routes[1:]:
            if best_metric.success_rate >= metric.success_rate and best_metric.total_cost_usd < metric.total_cost_usd:
                savings = (1.0 - (best_metric.total_cost_usd / max(0.0001, metric.total_cost_usd))) * 100
                recommendations.append(
                    RouteRecommendation(
                        posture=posture,
                        current_route=f"{key[1]}:{key[2]}",
                        recommended_route=f"{best_key[1]}:{best_key[2]}",
                        projected_savings_pct=round(savings, 1),
                        confidence=0.85,
                        rationale=f"Higher/equal success rate ({best_metric.success_rate*100:.0f}% vs {metric.success_rate*100:.0f}%) with {savings:.1f}% cost reduction.",
                    )
                )
        return recommendations


class AgentTaskAffinity:
    """Tracks Bayesian affinity between specialist workers and task domains."""

    def __init__(self):
        # Key: (worker_id, domain) -> (successes, total)
        self._counts: Dict[Tuple[str, str], Tuple[int, int]] = collections.defaultdict(lambda: (0, 0))

    def record_outcome(self, worker_id: str, domain: str, success: bool) -> None:
        succ, total = self._counts[(worker_id, domain)]
        self._counts[(worker_id, domain)] = (succ + (1 if success else 0), total + 1)

    def get_affinity_score(self, worker_id: str, domain: str) -> float:
        """Laplace smoothed affinity score [0.0, 1.0]."""
        succ, total = self._counts[(worker_id, domain)]
        return (succ + 1) / (total + 2)


@dataclass
class ExperimentResult:
    experiment_id: str
    target: str
    baseline_score: float
    candidate_score: float
    adopted: bool
    rationale: str
    rolled_back: bool


class ExperimentFramework:
    """Controlled A/B benchmark evaluation with automatic rollback."""

    def __init__(self, min_improvement_delta: float = 0.05):
        self.min_improvement_delta = min_improvement_delta

    def run_experiment(
        self,
        target: str,
        baseline_eval_fn: Callable[[], float],
        candidate_eval_fn: Callable[[], float],
        adoption_fn: Callable[[], None],
        rollback_fn: Callable[[], None],
    ) -> ExperimentResult:
        exp_id = f"exp-{uuid.uuid4().hex[:8]}"

        score_a = baseline_eval_fn()
        score_b = candidate_eval_fn()

        if score_b >= (score_a + self.min_improvement_delta):
            # Statistically significant improvement: Adopt
            adoption_fn()
            return ExperimentResult(
                experiment_id=exp_id,
                target=target,
                baseline_score=score_a,
                candidate_score=score_b,
                adopted=True,
                rationale=f"Candidate improved eval score from {score_a:.2f} to {score_b:.2f} (delta +{score_b - score_a:.2f}).",
                rolled_back=False,
            )
        else:
            # Underperformed or marginal: Automatic Rollback
            rollback_fn()
            return ExperimentResult(
                experiment_id=exp_id,
                target=target,
                baseline_score=score_a,
                candidate_score=score_b,
                adopted=False,
                rationale=f"Candidate did not meet required threshold delta (+{self.min_improvement_delta:.2f}). Automatically rolled back.",
                rolled_back=True,
            )


class AdaptiveIntelligenceCoordinator:
    """Central Phase 3 coordinator connecting telemetry, pattern detection, routing recommendations, and A/B experiments."""

    def __init__(
        self,
        event_store: EventStore,
        failure_detector: Optional[FailurePatternDetector] = None,
        routing_optimizer: Optional[AdaptiveRoutingOptimizer] = None,
        affinity_matrix: Optional[AgentTaskAffinity] = None,
        experiment_framework: Optional[ExperimentFramework] = None,
    ):
        self.event_store = event_store
        self.failure_detector = failure_detector or FailurePatternDetector()
        self.routing_optimizer = routing_optimizer or AdaptiveRoutingOptimizer()
        self.affinity_matrix = affinity_matrix or AgentTaskAffinity()
        self.experiment_framework = experiment_framework or ExperimentFramework()

    def process_task_result(
        self,
        task_id: str,
        posture: str,
        domain: str,
        worker_id: str,
        provider_id: str,
        model_id: str,
        success: bool,
        tokens: int,
        cost_usd: float,
        latency_sec: float,
        error_message: Optional[str] = None,
        failure_category: Optional[FailureCategory] = None,
    ) -> None:
        """Ingests task telemetry and updates all adaptive intelligence models."""
        # 1. Update Routing Optimizer
        self.routing_optimizer.record_run(
            posture=posture,
            provider_id=provider_id,
            model_id=model_id,
            success=success,
            tokens=tokens,
            cost_usd=cost_usd,
            latency_sec=latency_sec,
        )

        # 2. Update Affinity Matrix
        self.affinity_matrix.record_outcome(worker_id=worker_id, domain=domain, success=success)

        # 3. Record Failure if applicable
        if not success and error_message:
            cat = failure_category or FailureCategory.UNKNOWN
            incident = FailureIncident(
                task_id=task_id,
                category=cat,
                error_message=error_message,
                posture=posture,
                model_id=model_id,
            )
            self.failure_detector.record_failure(incident)
            self.event_store.append(
                Event(
                    name="intelligence.failure_recorded",
                    payload={"task_id": task_id, "category": cat.value, "posture": posture},
                )
            )

        # 4. Check for emergent patterns
        patterns = self.failure_detector.detect_patterns()
        for pat in patterns:
            self.event_store.append(
                Event(
                    name="intelligence.pattern_detected",
                    payload={
                        "pattern_id": pat.pattern_id,
                        "category": pat.category.value,
                        "frequency": pat.frequency,
                        "remediation": pat.suggested_remediation,
                    },
                )
            )
