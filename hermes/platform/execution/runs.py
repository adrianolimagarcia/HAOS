"""HAOS run/result entities (v1.1 Emenda D3; spec "Task != Run != Result").

- TaskSpec     = *what* should be achieved (intent, stable).
- ExecutionPlan = *how* the agent intends to achieve it (mutable).
- TaskRun      = *what happened in one concrete attempt* — a frozen, replayable
                 snapshot of the resolved execution (posture/model/provider/
                 lane/context/workspace) plus lifecycle timestamps.
- TaskResult   = *what was actually delivered* — summary, artifacts, evidence,
                 residual risk, acceptance/review verdicts.

These are HAOS presentation/extensions of the canonical upstream Kanban run
(``hermes_cli.kanban_db.task_runs``), persisted as JSON in the adapter's
additive meta table — they never own lifecycle state of their own.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class TaskRun:
    task_id: str
    run_id: Optional[int] = None          # canonical upstream task_runs.run_id
    attempt: int = 1                      # número da tentativa (run retry)
    worker_id: Optional[str] = None
    posture_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    resolved_model: Optional[str] = None  # "family:variant@revision"
    provider_chain: List[Dict[str, Any]] = field(default_factory=list)  # ordered, full chain
    lane: Optional[str] = None
    context_package_id: Optional[str] = None
    context_package_hash: Optional[str] = None
    workspace_uri: Optional[str] = None
    started_at: float = 0.0
    heartbeat_at: Optional[float] = None
    status: str = "running"               # running | ended
    exit_reason: Optional[str] = None     # review_rejected | worker_crash | accepted | ...
    snapshot: Dict[str, Any] = field(default_factory=dict)  # frozen assignment extras

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRun":
        return cls(
            task_id=data["task_id"],
            run_id=data.get("run_id"),
            attempt=int(data.get("attempt", 1)),
            worker_id=data.get("worker_id"),
            posture_id=data.get("posture_id"),
            model_profile_id=data.get("model_profile_id"),
            resolved_model=data.get("resolved_model"),
            provider_chain=data.get("provider_chain") or [],
            lane=data.get("lane"),
            context_package_id=data.get("context_package_id"),
            context_package_hash=data.get("context_package_hash"),
            workspace_uri=data.get("workspace_uri"),
            started_at=data.get("started_at") or 0.0,
            heartbeat_at=data.get("heartbeat_at"),
            status=data.get("status") or "running",
            exit_reason=data.get("exit_reason"),
            snapshot=data.get("snapshot") or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def end(self, exit_reason: str) -> None:
        self.status = "ended"
        self.exit_reason = exit_reason


@dataclass
class TaskResult:
    task_id: str
    run_id: Optional[int] = None
    summary: str = ""
    artifacts: List[str] = field(default_factory=list)      # artifact refs (git://, artifact://)
    evidence: Dict[str, Any] = field(default_factory=dict)  # tests/lsp/schema metrics
    residual_risk: List[str] = field(default_factory=list)  # what remains unverified
    acceptance: List[Dict[str, Any]] = field(default_factory=list)  # AC verdicts
    reviewer_verdict: Optional[str] = None                  # approved | changes_requested | ...
    completed_at: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskResult":
        return cls(
            task_id=data.get("task_id") or "",
            run_id=data.get("run_id"),
            summary=data.get("summary") or "",
            artifacts=data.get("artifacts") or [],
            evidence=data.get("evidence") or {},
            residual_risk=data.get("residual_risk") or [],
            acceptance=data.get("acceptance") or [],
            reviewer_verdict=data.get("reviewer_verdict"),
            completed_at=data.get("completed_at") or 0.0,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
