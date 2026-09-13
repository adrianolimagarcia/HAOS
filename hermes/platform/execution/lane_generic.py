"""Canonical GenericWorkerLane Contract (Step 4.4 / ADR-010).

Defines the universal lifecycle for all execution substrates:
prepare -> spawn -> heartbeat -> collect_result -> cancel -> cleanup.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.tasks.spec import TaskSpec


class LaneRunStatus(str, enum.Enum):
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class WorkerResult:
    run_id: str
    task_id: str
    status: LaneRunStatus
    exit_code: int = 0
    output: str = ""
    error: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    tokens_consumed: int = 0
    cost_usd: float = 0.0
    duration_sec: float = 0.0


@dataclass
class WorkerFailure:
    run_id: str
    task_id: str
    error_type: str
    message: str
    retryable: bool = True
    timestamp: float = field(default_factory=time.time)


class GenericWorkerLane(abc.ABC):
    """Universal execution lane contract governing worker lifecycles across local and isolated substrates."""

    def __init__(self, lane_id: str):
        self.lane_id = lane_id
        self._active_runs: Dict[str, Dict[str, Any]] = {}

    @abc.abstractmethod
    def prepare(self, task: TaskSpec, workspace_path: Optional[Path] = None) -> Path:
        """Prepares workspace, worktrees, credentials, and dependencies. Returns workspace path."""
        pass

    @abc.abstractmethod
    def spawn(self, assignment: AssignmentSpec, workspace_path: Path) -> str:
        """Launches the worker process or thread. Returns run_id."""
        pass

    @abc.abstractmethod
    def heartbeat(self, run_id: str) -> bool:
        """Records a keep-alive heartbeat. Returns True if alive, False if expired or missing."""
        pass

    @abc.abstractmethod
    def collect_result(self, run_id: str, timeout: Optional[float] = None) -> WorkerResult:
        """Collects worker result, artifacts, and token consumption upon completion."""
        pass

    @abc.abstractmethod
    def cancel(self, run_id: str, reason: str = "Operator requested") -> bool:
        """Cancels or terminates the active worker execution."""
        pass

    @abc.abstractmethod
    def cleanup(self, workspace_path: Path) -> None:
        """Purges ephemeral workspaces, worktrees, and temporary handles."""
        pass


class LocalMockWorkerLane(GenericWorkerLane):
    """Deterministic in-memory implementation of GenericWorkerLane for testing and verification."""

    def prepare(self, task: TaskSpec, workspace_path: Optional[Path] = None) -> Path:
        ws = workspace_path or Path(f"/tmp/haos_mock_lane_{task.id}")
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def spawn(self, assignment: AssignmentSpec, workspace_path: Path) -> str:
        run_id = f"run-{assignment.task_id}-{int(time.time() * 1000)}"
        self._active_runs[run_id] = {
            "assignment": assignment,
            "workspace": workspace_path,
            "status": LaneRunStatus.RUNNING,
            "last_heartbeat": time.time(),
            "start_time": time.time(),
        }
        return run_id

    def heartbeat(self, run_id: str) -> bool:
        if run_id not in self._active_runs:
            return False
        run = self._active_runs[run_id]
        if run["status"] != LaneRunStatus.RUNNING:
            return False
        run["last_heartbeat"] = time.time()
        return True

    def collect_result(self, run_id: str, timeout: Optional[float] = None) -> WorkerResult:
        if run_id not in self._active_runs:
            raise KeyError(f"Run {run_id} not found")
        run = self._active_runs[run_id]
        run["status"] = LaneRunStatus.COMPLETED
        duration = time.time() - run["start_time"]
        return WorkerResult(
            run_id=run_id,
            task_id=run["assignment"].task_id,
            status=LaneRunStatus.COMPLETED,
            exit_code=0,
            output="Execution successful",
            duration_sec=duration,
            tokens_consumed=450,
            cost_usd=0.00045,
        )

    def cancel(self, run_id: str, reason: str = "Operator requested") -> bool:
        if run_id not in self._active_runs:
            return False
        self._active_runs[run_id]["status"] = LaneRunStatus.CANCELLED
        return True

    def cleanup(self, workspace_path: Path) -> None:
        if workspace_path.exists():
            import shutil
            shutil.rmtree(workspace_path, ignore_errors=True)
