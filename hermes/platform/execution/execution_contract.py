"""Common, backend-neutral task execution contract.

This is deliberately a leaf module: adapters may translate their native
protocols to these dataclasses without importing a dispatcher or changing the
existing Kanban/TaskRun APIs.  The wire shape is JSON-compatible and versioned
by ``schema_version``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


CONTRACT_SCHEMA_VERSION = 1


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            self.COMPLETED,
            self.FAILED,
            self.TIMED_OUT,
            self.CANCELLED,
        }


class ExecutionErrorCode(str, Enum):
    BACKEND_ERROR = "backend_error"
    PROTOCOL_ERROR = "protocol_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskExecutionRequest:
    """The smallest common request accepted by an execution backend."""

    task: str
    workspace: str
    model: Optional[str] = None
    timeout_seconds: Optional[float] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("task must be a non-empty string")
        if not isinstance(self.workspace, str) or not self.workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model.strip()
        ):
            raise ValueError("model must be a non-empty string when provided")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive when provided")
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError("unsupported execution contract schema_version")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskExecutionRequest":
        return cls(
            task=data["task"],
            workspace=data["workspace"],
            model=data.get("model"),
            timeout_seconds=data.get("timeout_seconds"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class ExecutionArtifact:
    """A reference to an output; payloads must not embed arbitrary file data."""

    ref: str
    kind: str = "file"
    label: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or not self.ref.strip():
            raise ValueError("artifact ref must be a non-empty string")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("artifact kind must be a non-empty string")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionError:
    code: ExecutionErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, ExecutionErrorCode):
            object.__setattr__(self, "code", ExecutionErrorCode(self.code))
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("error message must be a non-empty string")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["code"] = self.code.value
        result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class ExecutionEvent:
    """A monotonic observation emitted by a backend for one execution."""

    execution_id: str
    status: ExecutionStatus
    timestamp: float
    message: Optional[str] = None
    error: Optional[ExecutionError] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.execution_id.strip():
            raise ValueError("execution_id must be non-empty")
        if not isinstance(self.status, ExecutionStatus):
            object.__setattr__(self, "status", ExecutionStatus(self.status))
        if self.timestamp < 0:
            raise ValueError("timestamp must be non-negative")
        if self.status == ExecutionStatus.FAILED and self.error is None:
            raise ValueError("failed event requires an error")
        if self.status == ExecutionStatus.TIMED_OUT and (
            self.error is None or self.error.code != ExecutionErrorCode.TIMEOUT
        ):
            raise ValueError("timed_out event requires a timeout error")
        if self.status == ExecutionStatus.CANCELLED and (
            self.error is None or self.error.code != ExecutionErrorCode.CANCELLED
        ):
            raise ValueError("cancelled event requires a cancelled error")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["error"] = self.error.to_dict() if self.error else None
        result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True)
class TaskExecutionResult:
    """Terminal (or partial) result shared by all execution backends."""

    execution_id: str
    status: ExecutionStatus
    summary: str = ""
    artifacts: List[ExecutionArtifact] = field(default_factory=list)
    error: Optional[ExecutionError] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.execution_id.strip():
            raise ValueError("execution_id must be non-empty")
        if not isinstance(self.status, ExecutionStatus):
            object.__setattr__(self, "status", ExecutionStatus(self.status))
        if not self.status.terminal:
            raise ValueError("result status must be terminal")
        if self.status == ExecutionStatus.COMPLETED and self.error is not None:
            raise ValueError("completed result cannot contain an error")
        if self.status != ExecutionStatus.COMPLETED and self.error is None:
            raise ValueError("non-completed result requires an error")

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        result["error"] = self.error.to_dict() if self.error else None
        return result


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ExecutionArtifact",
    "ExecutionError",
    "ExecutionErrorCode",
    "ExecutionEvent",
    "ExecutionStatus",
    "TaskExecutionRequest",
    "TaskExecutionResult",
]
