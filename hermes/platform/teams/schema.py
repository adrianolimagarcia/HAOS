"""Declarative Team Templates Schema with Strict Validation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class CoordinationMode(str, Enum):
    sequential = "sequential"
    parallel_fanout = "parallel_fanout"
    hierarchical_lead = "hierarchical_lead"


class TeamRole(BaseModel):
    name: str
    system_prompt: str
    tools_allowed: Optional[List[str]] = None
    tools_denied: List[str] = Field(default_factory=list)
    model_override: Optional[str] = None
    max_iterations: int = 50
    approval_policy: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    skills_enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Role name cannot be empty")
        return v.strip()

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed for this role."""
        if tool_name in self.tools_denied:
            return False
        if self.tools_allowed is not None:
            return tool_name in self.tools_allowed
        return True


class TeamCoordination(BaseModel):
    mode: CoordinationMode = CoordinationMode.sequential
    shared_context: List[str] = Field(default_factory=list)
    termination_condition: Optional[str] = None


class TeamTemplate(BaseModel):
    name: str
    description: str
    roles: List[TeamRole]
    coordination: TeamCoordination = Field(default_factory=TeamCoordination)
    routines: List[Dict[str, Any]] = Field(default_factory=list)
    connection_slots: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_team_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Team name cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_roles(self) -> TeamTemplate:
        if not self.roles:
            raise ValueError("Team must have at least one role defined")
        role_names = set()
        for r in self.roles:
            if r.name in role_names:
                raise ValueError(f"Duplicate role name found: {r.name}")
            role_names.add(r.name)
        return self
