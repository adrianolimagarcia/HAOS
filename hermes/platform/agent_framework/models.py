"""Typed data contracts and models for the HAOS Autonomous Agent Framework.

Provides strict, dataclass-based models with JSON/YAML-ready serialization
for events, diagnoses, execution plans, verification, and audit contracts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EventSeverity(str, Enum):
    """Severity classification for framework and platform events."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        """Numeric rank for severity comparisons."""
        _rank = {
            EventSeverity.DEBUG: 10,
            EventSeverity.INFO: 20,
            EventSeverity.WARNING: 30,
            EventSeverity.ERROR: 40,
            EventSeverity.CRITICAL: 50,
        }
        return _rank[self]

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, EventSeverity):
            return self.level < other.level
        return NotImplemented

    def __le__(self, other: Any) -> bool:
        if isinstance(other, EventSeverity):
            return self.level <= other.level
        return NotImplemented

    def __gt__(self, other: Any) -> bool:
        if isinstance(other, EventSeverity):
            return self.level > other.level
        return NotImplemented

    def __ge__(self, other: Any) -> bool:
        if isinstance(other, EventSeverity):
            return self.level >= other.level
        return NotImplemented


class RiskTier(str, Enum):
    """Risk tier governing agent autonomy boundaries."""

    SAFE = "safe"  # Can run autonomously
    MODERATE = "moderate"  # Requires notification/audit or dry-run approval
    CRITICAL = "critical"  # Never runs automatically without explicit operator confirmation


class StepStatus(str, Enum):
    """Lifecycle status of an individual execution plan step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


class PlanStatus(str, Enum):
    """Lifecycle status of a multi-step execution plan."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class HAOSEvent:
    """Core structured event for bus dispatch, logging, and state tracking."""

    event_type: str
    source: str
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp: float = field(default_factory=time.time)
    severity: EventSeverity = EventSeverity.INFO
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.severity, str) and not isinstance(self.severity, EventSeverity):
            self.severity = EventSeverity(self.severity.lower())

    def to_dict(self) -> dict[str, Any]:
        """Convert event to primitive dictionary suitable for JSON/YAML serialization."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "source": self.source,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HAOSEvent:
        """Create an HAOSEvent from a dictionary payload."""
        data_copy = dict(data)
        severity = data_copy.get("severity", EventSeverity.INFO)
        if isinstance(severity, str):
            severity = EventSeverity(severity.lower())
        return cls(
            id=str(data_copy.get("id") or f"evt_{uuid.uuid4().hex[:12]}"),
            event_type=str(data_copy.get("event_type", "unknown")),
            source=str(data_copy.get("source", "system")),
            timestamp=float(data_copy.get("timestamp", time.time())),
            severity=severity,
            payload=dict(data_copy.get("payload") or {}),
            metadata=dict(data_copy.get("metadata") or {}),
        )


@dataclass
class EvidenceItem:
    """Individual observed metric or diagnostic finding supporting a diagnosis."""

    metric: str
    observed_value: Any
    threshold: Any = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence item to primitive dict."""
        return {
            "metric": self.metric,
            "observed_value": self.observed_value,
            "threshold": self.threshold,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        """Create EvidenceItem from dict."""
        return cls(
            metric=str(data.get("metric", "")),
            observed_value=data.get("observed_value"),
            threshold=data.get("threshold"),
            description=str(data.get("description", "")),
        )


@dataclass
class Diagnosis:
    """Diagnostic outcome identified by autonomous or human assessment."""

    issue_id: str
    title: str
    cause: str
    confidence: float  # 0.0 to 1.0
    evidence: list[EvidenceItem] = field(default_factory=list)
    impacted_subsystems: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        """Convert diagnosis to dict representation."""
        return {
            "issue_id": self.issue_id,
            "title": self.title,
            "cause": self.cause,
            "confidence": self.confidence,
            "evidence": [e.to_dict() if isinstance(e, EvidenceItem) else dict(e) for e in self.evidence],
            "impacted_subsystems": list(self.impacted_subsystems),
            "recommended_actions": list(self.recommended_actions),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Diagnosis:
        """Create Diagnosis from dict representation."""
        raw_evidence = data.get("evidence") or []
        evidence_items: list[EvidenceItem] = []
        for item in raw_evidence:
            if isinstance(item, EvidenceItem):
                evidence_items.append(item)
            elif isinstance(item, dict):
                evidence_items.append(EvidenceItem.from_dict(item))

        return cls(
            issue_id=str(data.get("issue_id", "")),
            title=str(data.get("title", "")),
            cause=str(data.get("cause", "")),
            confidence=float(data.get("confidence", 0.0)),
            evidence=evidence_items,
            impacted_subsystems=list(data.get("impacted_subsystems") or []),
            recommended_actions=list(data.get("recommended_actions") or []),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class PlanStep:
    """Discrete executable action in an agent execution plan."""

    step_id: str
    order: int
    action_name: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.SAFE
    is_reversible: bool = False
    rollback_action: str | None = None
    rollback_params: dict[str, Any] | None = None
    status: StepStatus = StepStatus.PENDING

    def __post_init__(self) -> None:
        if isinstance(self.risk_tier, str) and not isinstance(self.risk_tier, RiskTier):
            self.risk_tier = RiskTier(self.risk_tier.lower())
        if isinstance(self.status, str) and not isinstance(self.status, StepStatus):
            self.status = StepStatus(self.status.lower())

    def to_dict(self) -> dict[str, Any]:
        """Convert step to dict."""
        return {
            "step_id": self.step_id,
            "order": self.order,
            "action_name": self.action_name,
            "target": self.target,
            "params": dict(self.params),
            "risk_tier": self.risk_tier.value,
            "is_reversible": self.is_reversible,
            "rollback_action": self.rollback_action,
            "rollback_params": dict(self.rollback_params) if self.rollback_params is not None else None,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        """Create PlanStep from dict."""
        risk = data.get("risk_tier", RiskTier.SAFE)
        if isinstance(risk, str):
            risk = RiskTier(risk.lower())
        status = data.get("status", StepStatus.PENDING)
        if isinstance(status, str):
            status = StepStatus(status.lower())

        rollback_params = data.get("rollback_params")
        if isinstance(rollback_params, dict):
            rollback_params = dict(rollback_params)

        return cls(
            step_id=str(data.get("step_id", "")),
            order=int(data.get("order", 0)),
            action_name=str(data.get("action_name", "")),
            target=str(data.get("target", "")),
            params=dict(data.get("params") or {}),
            risk_tier=risk,
            is_reversible=bool(data.get("is_reversible", False)),
            rollback_action=data.get("rollback_action"),
            rollback_params=rollback_params,
            status=status,
        )


@dataclass
class ExecutionPlan:
    """Ordered collection of steps designed to remediate or optimize a system condition."""

    plan_id: str
    diagnosis_id: str | None = None
    title: str = ""
    description: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    dry_run: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str) and not isinstance(self.status, PlanStatus):
            self.status = PlanStatus(self.status.lower())

    def to_dict(self) -> dict[str, Any]:
        """Convert execution plan to dict."""
        return {
            "plan_id": self.plan_id,
            "diagnosis_id": self.diagnosis_id,
            "title": self.title,
            "description": self.description,
            "steps": [s.to_dict() if isinstance(s, PlanStep) else dict(s) for s in self.steps],
            "status": self.status.value,
            "created_at": self.created_at,
            "dry_run": self.dry_run,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        """Create ExecutionPlan from dict."""
        raw_steps = data.get("steps") or []
        steps: list[PlanStep] = []
        for s in raw_steps:
            if isinstance(s, PlanStep):
                steps.append(s)
            elif isinstance(s, dict):
                steps.append(PlanStep.from_dict(s))

        status = data.get("status", PlanStatus.DRAFT)
        if isinstance(status, str):
            status = PlanStatus(status.lower())

        return cls(
            plan_id=str(data.get("plan_id", "")),
            diagnosis_id=data.get("diagnosis_id"),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            steps=steps,
            status=status,
            created_at=float(data.get("created_at", time.time())),
            dry_run=bool(data.get("dry_run", False)),
            notes=str(data.get("notes", "")),
        )

    def to_markdown(self) -> str:
        """Render the execution plan as clean human-readable Markdown."""
        lines = [
            f"# Execution Plan: {self.title or self.plan_id}",
            f"- **Plan ID**: `{self.plan_id}`",
        ]
        if self.diagnosis_id:
            lines.append(f"- **Diagnosis ID**: `{self.diagnosis_id}`")
        lines.extend([
            f"- **Status**: `{self.status.value}`",
            f"- **Dry Run**: `{'Yes' if self.dry_run else 'No'}`",
        ])
        if self.description:
            lines.append(f"- **Description**: {self.description}")
        if self.notes:
            lines.append(f"- **Notes**: {self.notes}")

        lines.extend(["", "## Execution Steps"])
        if not self.steps:
            lines.append("*No steps defined.*")
        else:
            for step in self.steps:
                lines.append(
                    f"{step.order}. **{step.action_name}** on `{step.target}` "
                    f"[{step.risk_tier.value.upper()}] (Status: `{step.status.value}`)"
                )
                if step.params:
                    lines.append(f"   - **Params**: `{step.params}`")
                lines.append(f"   - **Reversible**: `{step.is_reversible}`")
                if step.is_reversible and step.rollback_action:
                    lines.append(
                        f"   - **Rollback**: `{step.rollback_action}` with `{step.rollback_params or {}}`"
                    )
        return "\n".join(lines).strip()


@dataclass
class ExecutionResult:
    """Outcome report for an executed plan step."""

    step_id: str
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dict."""
        return {
            "step_id": self.step_id,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionResult:
        """Create ExecutionResult from dict."""
        return cls(
            step_id=str(data.get("step_id", "")),
            success=bool(data.get("success", False)),
            output=str(data.get("output", "")),
            error=data.get("error"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class VerificationResult:
    """Verification outcome proving efficacy and stability after plan execution."""

    plan_id: str
    verified: bool
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    details: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert verification result to dict."""
        return {
            "plan_id": self.plan_id,
            "verified": self.verified,
            "metrics_before": dict(self.metrics_before),
            "metrics_after": dict(self.metrics_after),
            "details": self.details,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        """Create VerificationResult from dict."""
        return cls(
            plan_id=str(data.get("plan_id", "")),
            verified=bool(data.get("verified", False)),
            metrics_before=dict(data.get("metrics_before") or {}),
            metrics_after=dict(data.get("metrics_after") or {}),
            details=str(data.get("details", "")),
            timestamp=float(data.get("timestamp", time.time())),
        )
