"""Canonical Architecture Contracts v0.1 (Frozen Schemas).

Defines schema-versioned frozen data structures for all core HAOS primitives:
TaskSpec, TaskRun, TaskResult, AssignmentSpec, TeamSpec, ModelProfile,
CapabilitySpec, ContextPackage, MemoryItem, ArtifactSpec.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class ContractBase:
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass(frozen=True)
class TaskSpecContract(ContractBase):
    id: str = ""
    title: str = ""
    goal: str = ""
    description: str = ""
    posture: str = "general"
    priority: int = 0
    parent_id: Optional[str] = None
    requires_tasks: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TaskRunContract(ContractBase):
    run_id: str = ""
    task_id: str = ""
    assignment_id: str = ""
    status: str = "preparing"
    worker_id: str = ""
    lane: str = "hermes"
    started_at: float = 0.0
    heartbeat_at: Optional[float] = None
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ArtifactSpecContract(ContractBase):
    id: str = ""
    task_id: str = ""
    run_id: str = ""
    path: str = ""
    mime_type: str = "text/plain"
    size_bytes: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class ContextPackageContract(ContractBase):
    package_id: str = ""
    task_id: str = ""
    system_prompt_prefix: str = ""
    items: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    byte_stable_hash: str = ""


@dataclass(frozen=True)
class MemoryItemContract(ContractBase):
    id: str = ""
    scope: str = "project"  # private | team | project | global
    content: str = ""
    source_task_id: Optional[str] = None
    source_run_id: Optional[str] = None
    created_at: float = 0.0
    superseded_by: Optional[str] = None


@dataclass(frozen=True)
class ArchitectureChangeRequestContract(ContractBase):
    acr_id: str = ""
    title: str = ""
    author: str = ""
    status: str = "PROPOSED"  # PROPOSED | UNDER_REVIEW | ACCEPTED | REJECTED | SUPERSEDED
    impacted_schemas: List[str] = field(default_factory=list)
    rationale: str = ""
    backward_compatible: bool = True
