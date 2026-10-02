"""HAOS operator-driven operational agent framework.

Provides typed contracts, atomic state, operational markdown memory and
auditable event routing. Importing the package does not activate autonomy.
"""

from __future__ import annotations

from hermes.platform.agent_framework.approvals import ApprovalStore, StepApproval
from hermes.platform.agent_framework.event_bus import HAOSEventBus
from hermes.platform.agent_framework.hierarchy import HierarchicalDiagnostician, HierarchyResult
from hermes.platform.agent_framework.memory_store import OperationalMemoryStore
from hermes.platform.agent_framework.models import (
    Diagnosis,
    EventSeverity,
    EvidenceItem,
    ExecutionPlan,
    ExecutionResult,
    HAOSEvent,
    PlanStatus,
    PlanStep,
    RiskTier,
    StepStatus,
    VerificationResult,
)
from hermes.platform.agent_framework.policy import AgentPolicyEngine, PolicyDecision
from hermes.platform.agent_framework.pipeline import (
    DiagnosticianAgent,
    ExecutorAgent,
    HAOSOrchestrator,
    ObserverAgent,
    PlannerAgent,
    VerifierAgent,
)
from hermes.platform.agent_framework.state_manager import (
    HAOSStateManager,
    LockTimeoutError,
)

__all__ = [
    # Models & Enums
    "EventSeverity",
    "RiskTier",
    "StepStatus",
    "PlanStatus",
    "HAOSEvent",
    "EvidenceItem",
    "Diagnosis",
    "PlanStep",
    "ExecutionPlan",
    "ExecutionResult",
    "VerificationResult",
    # State Management
    "HAOSStateManager",
    "LockTimeoutError",
    # Memory Management
    "OperationalMemoryStore",
    # Event Bus
    "HAOSEventBus",
    # Policy gates
    "StepApproval",
    "ApprovalStore",
    "AgentPolicyEngine",
    "PolicyDecision",
    # Operator-driven operational cycle
    "HierarchicalDiagnostician",
    "HierarchyResult",
    "ObserverAgent",
    "DiagnosticianAgent",
    "PlannerAgent",
    "ExecutorAgent",
    "VerifierAgent",
    "HAOSOrchestrator",
]
