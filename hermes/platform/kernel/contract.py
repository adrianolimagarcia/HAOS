"""Agent Runtime Contract (P0 Architectural Standard).

Every autonomous agent in the HAOS Civilization must operate under an explicit,
validated, immutable runtime contract that governs its identity, capabilities,
memory boundaries, budgets, output schema, and validation requirements.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class AgentRole(str, Enum):
    PLANNER = "planner"
    BUILDER = "builder"
    CRITIC = "critic"
    VALIDATOR = "validator"
    PROMOTER = "promoter"
    SECURITY = "security"
    ANALYST = "analyst"
    OPERATOR = "operator"
    GENERAL = "general"


class ToolRiskTier(str, Enum):
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        order = {
            ToolRiskTier.READ: 1,
            ToolRiskTier.LOW: 2,
            ToolRiskTier.MEDIUM: 3,
            ToolRiskTier.HIGH: 4,
            ToolRiskTier.CRITICAL: 5,
        }
        return order[self]

    def allows(self, other: ToolRiskTier) -> bool:
        """Returns True if self risk tier is equal or higher than other (can authorize it)."""
        return self.level >= other.level


class MemoryScope(str, Enum):
    WORKING = "working"      # In-flight scratchpad, local to turn/task
    SESSION = "session"      # Live session memory
    PROJECT = "project"      # Repo/project-level memory
    DOMAIN = "domain"        # Cross-project domain specialization
    GLOBAL = "global"        # Civilization-wide skills & constitution
    GLOBAL_SKILL = "global"  # Civilization-wide skill pattern


class ContractViolationError(Exception):
    """Raised when an agent attempts an action that violates its runtime contract."""
    pass


@dataclass(frozen=True)
class AgentIdentityContract:
    """Immutable identity specification for an agent instance."""
    agent_id: str
    role: AgentRole
    domain: str = "general"
    spec_version: str = "3.0.0"
    model: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id or not self.agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        if not self.domain or not self.domain.strip():
            raise ValueError("domain must be a non-empty string")


@dataclass(frozen=True)
class AgentCapabilityContract:
    """Explicit tool authorization and security bounds."""
    allowed_tools: Set[str] = field(default_factory=set)
    max_risk_tier: ToolRiskTier = ToolRiskTier.MEDIUM
    allowed_paths: List[str] = field(default_factory=lambda: ["."])
    network_allowed: bool = False
    bash_sandboxed: bool = True

    def is_tool_allowed(self, tool_name: str, tool_risk: ToolRiskTier) -> bool:
        """Verify if tool is explicitly permitted and within the authorized risk tier."""
        if tool_name not in self.allowed_tools:
            return False
        return self.max_risk_tier.allows(tool_risk)


@dataclass(frozen=True)
class AgentMemoryContract:
    """Memory read and write isolation boundaries."""
    read_scopes: Set[MemoryScope] = field(
        default_factory=lambda: {MemoryScope.WORKING, MemoryScope.SESSION, MemoryScope.PROJECT}
    )
    write_scopes: Set[MemoryScope] = field(
        default_factory=lambda: {MemoryScope.WORKING, MemoryScope.SESSION}
    )
    provenance_required: bool = True

    def can_read(self, scope: MemoryScope) -> bool:
        return scope in self.read_scopes

    def can_write(self, scope: MemoryScope) -> bool:
        return scope in self.write_scopes


@dataclass(frozen=True)
class AgentBudgetContract:
    """Hard execution budget limits to prevent runaway loops."""
    max_tokens: int = 100_000
    timeout_seconds: float = 300.0
    max_iterations: int = 25
    max_cost_usd: float = 2.00
    max_usd_cost: Optional[float] = None
    max_time_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_usd_cost is not None:
            object.__setattr__(self, "max_cost_usd", self.max_usd_cost)
        if self.max_time_seconds is not None:
            object.__setattr__(self, "timeout_seconds", self.max_time_seconds)
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")


@dataclass(frozen=True)
class AgentValidationContract:
    """Requirements for validation and multi-agent separation of powers."""
    required_checks: List[str] = field(default_factory=lambda: ["syntax", "test"])
    require_critic_approval: bool = True
    require_validator_approval: bool = True
    min_confidence: float = 0.80
    requires_critic: Optional[bool] = None
    requires_tests: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.requires_critic is not None:
            object.__setattr__(self, "require_critic_approval", self.requires_critic)


@dataclass(frozen=True)
class AgentRuntimeContract:
    """The canonical P0 Agent Runtime Contract."""
    contract_id: str = ""
    identity: AgentIdentityContract = field(default_factory=lambda: AgentIdentityContract(agent_id="default-agent", role=AgentRole.BUILDER))
    capability: AgentCapabilityContract = field(default_factory=AgentCapabilityContract)
    memory: AgentMemoryContract = field(default_factory=AgentMemoryContract)
    budget: AgentBudgetContract = field(default_factory=AgentBudgetContract)
    validation: AgentValidationContract = field(default_factory=AgentValidationContract)
    output_schema: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.contract_id:
            object.__setattr__(self, "contract_id", f"contract-{uuid.uuid4().hex[:8]}")

    @classmethod
    def create(
        cls,
        agent_id: str,
        role: AgentRole,
        domain: str = "general",
        model: str = "default",
        allowed_tools: Optional[Set[str]] = None,
        max_risk_tier: ToolRiskTier = ToolRiskTier.MEDIUM,
        allowed_paths: Optional[List[str]] = None,
        network_allowed: bool = False,
        read_scopes: Optional[Set[MemoryScope]] = None,
        write_scopes: Optional[Set[MemoryScope]] = None,
        max_tokens: int = 100_000,
        timeout_seconds: float = 300.0,
        max_iterations: int = 25,
        max_cost_usd: float = 2.00,
        require_critic: bool = True,
        require_validator: bool = True,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> AgentRuntimeContract:
        """Factory helper with standard safe defaults based on role."""
        if allowed_tools is None:
            # Default capability sets by role
            if role == AgentRole.BUILDER:
                allowed_tools = {"read", "write", "edit", "glob", "grep", "bash", "todo_write"}
            elif role == AgentRole.CRITIC:
                allowed_tools = {"read", "glob", "grep"}
                max_risk_tier = ToolRiskTier.READ
            elif role == AgentRole.VALIDATOR:
                allowed_tools = {"read", "glob", "grep", "bash"}
            elif role == AgentRole.PLANNER:
                allowed_tools = {"read", "glob", "grep", "todo_write"}
                max_risk_tier = ToolRiskTier.LOW
            elif role == AgentRole.SECURITY:
                allowed_tools = {"read", "glob", "grep"}
                max_risk_tier = ToolRiskTier.READ
            elif role == AgentRole.PROMOTER:
                allowed_tools = {"read", "todo_write"}
                max_risk_tier = ToolRiskTier.MEDIUM
            else:
                allowed_tools = {"read", "write", "edit", "glob", "grep"}

        identity = AgentIdentityContract(agent_id=agent_id, role=role, domain=domain, model=model)
        capability = AgentCapabilityContract(
            allowed_tools=allowed_tools,
            max_risk_tier=max_risk_tier,
            allowed_paths=allowed_paths or ["."],
            network_allowed=network_allowed,
        )
        if write_scopes is None:
            if role == AgentRole.PROMOTER:
                write_scopes = {
                    MemoryScope.WORKING,
                    MemoryScope.SESSION,
                    MemoryScope.PROJECT,
                    MemoryScope.DOMAIN,
                    MemoryScope.GLOBAL,
                }
            else:
                write_scopes = {MemoryScope.WORKING, MemoryScope.SESSION}

        memory = AgentMemoryContract(
            read_scopes=read_scopes or {MemoryScope.WORKING, MemoryScope.SESSION, MemoryScope.PROJECT, MemoryScope.DOMAIN, MemoryScope.GLOBAL},
            write_scopes=write_scopes,
            provenance_required=True,
        )
        budget = AgentBudgetContract(
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            max_iterations=max_iterations,
            max_cost_usd=max_cost_usd,
        )
        validation = AgentValidationContract(
            require_critic_approval=require_critic,
            require_validator_approval=require_validator,
        )

        contract = cls(
            contract_id=f"contract-{uuid.uuid4().hex[:12]}",
            identity=identity,
            capability=capability,
            memory=memory,
            budget=budget,
            validation=validation,
            output_schema=output_schema,
        )
        contract.validate_invariants()
        return contract

    def validate_invariants(self) -> None:
        """Verify safety invariants of the contract."""
        # Critic cannot modify code or execute arbitrary destructive commands
        if self.identity.role == AgentRole.CRITIC:
            forbidden = {"write", "edit", "delete_file"}
            intersect = self.capability.allowed_tools.intersection(forbidden)
            if intersect:
                raise ContractViolationError(
                    f"Critic role cannot possess mutating tools: {intersect}"
                )
            if self.capability.max_risk_tier.level > ToolRiskTier.LOW.level:
                raise ContractViolationError(
                    f"Critic role cannot exceed LOW risk tier (got {self.capability.max_risk_tier})"
                )

        # Validator cannot write to permanent project/domain/global memory directly
        if self.identity.role == AgentRole.VALIDATOR:
            if MemoryScope.GLOBAL in self.memory.write_scopes:
                raise ContractViolationError(
                    "Validator role cannot write directly to GLOBAL memory scope"
                )

        # Direct global promotion requires PROMOTER role
        if MemoryScope.GLOBAL in self.memory.write_scopes and self.identity.role != AgentRole.PROMOTER:
            raise ContractViolationError(
                f"Only PROMOTER role may write directly to GLOBAL memory (role: {self.identity.role})"
            )

    def validate_tool_access(self, tool_name: str, tier: ToolRiskTier) -> None:
        if tool_name not in self.capability.allowed_tools:
            raise ContractViolationError(
                f"Tool '{tool_name}' is not in allowed tools for agent '{self.identity.agent_id}'"
            )
        if tier.level > self.capability.max_risk_tier.level:
            raise ContractViolationError(
                f"Tool '{tool_name}' risk tier {tier.name} exceeds agent max allowed tier {self.capability.max_risk_tier.name}"
            )

    def validate_memory_read(self, scope: MemoryScope) -> None:
        if not self.memory.can_read(scope):
            raise ContractViolationError(
                f"Agent '{self.identity.agent_id}' is not authorized to read from memory scope '{scope.value}'"
            )

    def validate_memory_write(self, scope: MemoryScope) -> None:
        if not self.memory.can_write(scope):
            raise ContractViolationError(
                f"Agent '{self.identity.agent_id}' is not authorized to write to memory scope '{scope.value}'"
            )
