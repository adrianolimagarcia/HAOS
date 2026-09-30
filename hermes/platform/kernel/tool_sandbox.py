"""Tool Governance, Capability Security, Risk Tiers, and Idempotency.

Governs all tool executions against AgentRuntimeContract, enforcing:
1. Tool allowlist and Risk Tier checks (READ, LOW, MEDIUM, HIGH, CRITICAL).
2. Capability boundaries (e.g. read-only roles cannot mutate).
3. Path containment within allowed workspace scopes.
4. Action Idempotency via OperationLedger (deduplication of duplicate operation_ids).
"""

from __future__ import annotations

import os
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from hermes.platform.kernel.contract import (
    AgentRole,
    AgentRuntimeContract,
    ContractViolationError,
    ToolRiskTier,
)


# Default classification of standard tools
DEFAULT_TOOL_RISK_MAP: Dict[str, ToolRiskTier] = {
    "read": ToolRiskTier.READ,
    "read_file": ToolRiskTier.READ,
    "grep": ToolRiskTier.READ,
    "glob": ToolRiskTier.READ,
    "list_agents": ToolRiskTier.READ,
    "get_goal": ToolRiskTier.READ,
    "haos_direct_status": ToolRiskTier.READ,
    "chatgpt_read": ToolRiskTier.READ,
    "medium_read": ToolRiskTier.READ,
    "reddit_read": ToolRiskTier.READ,
    "web_search": ToolRiskTier.LOW,
    "web_fetch": ToolRiskTier.LOW,
    "ask_user_question": ToolRiskTier.LOW,
    "chatgpt_ask": ToolRiskTier.LOW,
    "perplexity_ask": ToolRiskTier.LOW,
    "write": ToolRiskTier.MEDIUM,
    "edit": ToolRiskTier.MEDIUM,
    "todo_write": ToolRiskTier.MEDIUM,
    "update_goal": ToolRiskTier.MEDIUM,
    "send_message": ToolRiskTier.MEDIUM,
    "git_commit": ToolRiskTier.MEDIUM,
    "subagent": ToolRiskTier.MEDIUM,
    "subagent_fork": ToolRiskTier.MEDIUM,
    "bash": ToolRiskTier.HIGH,
    "shell": ToolRiskTier.HIGH,
    "job_kill": ToolRiskTier.HIGH,
    "workflow": ToolRiskTier.HIGH,
    "ralph": ToolRiskTier.HIGH,
    "delete_file": ToolRiskTier.CRITICAL,
    "rm_rf": ToolRiskTier.CRITICAL,
    "system_reboot": ToolRiskTier.CRITICAL,
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    risk_tier: ToolRiskTier
    violates_contract: bool = False


@dataclass
class OperationRecord:
    operation_id: str
    agent_id: str
    tool_name: str
    status: str  # "executed", "deduplicated", "failed", "denied"
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class OperationLedger:
    """In-memory and event-sourced ledger for action idempotency."""

    def __init__(self) -> None:
        self._records: Dict[str, OperationRecord] = {}

    def get(self, operation_id: str) -> Optional[OperationRecord]:
        return self._records.get(operation_id)

    def record(
        self,
        operation_id: str,
        agent_id: str,
        tool_name: str,
        status: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> OperationRecord:
        record = OperationRecord(
            operation_id=operation_id,
            agent_id=agent_id,
            tool_name=tool_name,
            status=status,
            result=result,
            error=error,
        )
        self._records[operation_id] = record
        return record


class ToolGovernancePolicy:
    """Enforces policy constraints on tool requests."""

    def __init__(
        self,
        risk_map: Optional[Dict[str, ToolRiskTier]] = None,
        custom_risk_map: Optional[Dict[str, ToolRiskTier]] = None,
    ):
        self.risk_map = dict(DEFAULT_TOOL_RISK_MAP)
        if risk_map:
            self.risk_map.update(risk_map)
        if custom_risk_map:
            self.risk_map.update(custom_risk_map)

    def classify_tool(self, tool_name: str) -> ToolRiskTier:
        return self.risk_map.get(tool_name, ToolRiskTier.HIGH)

    def evaluate(
        self,
        contract: AgentRuntimeContract,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> PolicyDecision:
        risk = self.classify_tool(tool_name)

        # 1. Role capability restrictions
        if contract.identity.role == AgentRole.CRITIC and risk.level > ToolRiskTier.LOW.level:
            return PolicyDecision(
                allowed=False,
                reason=f"CRITIC role cannot execute {risk.value} risk tools ({tool_name})",
                risk_tier=risk,
                violates_contract=True,
            )

        if contract.identity.role in {AgentRole.ANALYST, AgentRole.SECURITY} and risk.level > ToolRiskTier.LOW.level:
            return PolicyDecision(
                allowed=False,
                reason=f"{contract.identity.role.value} role is read-only / low-risk (attempted {tool_name})",
                risk_tier=risk,
                violates_contract=True,
            )

        # 2. Allowed tools list
        if tool_name not in contract.capability.allowed_tools:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is not in allowed_tools for agent '{contract.identity.agent_id}'",
                risk_tier=risk,
                violates_contract=True,
            )

        # 3. Max Risk Tier check
        if not contract.capability.max_risk_tier.allows(risk):
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Tool '{tool_name}' has risk tier {risk.value}, exceeding agent maximum "
                    f"allowed tier {contract.capability.max_risk_tier.value}"
                ),
                risk_tier=risk,
                violates_contract=True,
            )

        # 4. Path boundary containment check (for file tools)
        if tool_name in {"write", "edit", "read", "read_file"} and "file_path" in tool_args:
            target_path = tool_args["file_path"]
            norm_target = os.path.normpath(target_path)
            # Prevent absolute paths escaping allowed paths or directory traversal outside root
            if norm_target.startswith("..") or (os.path.isabs(norm_target) and not any(norm_target.startswith(os.path.abspath(p)) for p in contract.capability.allowed_paths if p != ".")):
                # If allowed_paths is default ["."], target must not escape cwd
                return PolicyDecision(
                    allowed=False,
                    reason=f"File path '{target_path}' escapes allowed path boundaries",
                    risk_tier=risk,
                    violates_contract=True,
                )

        # 5. Network access check
        if tool_name in {"web_search", "web_fetch"} and not contract.capability.network_allowed:
            return PolicyDecision(
                allowed=False,
                reason=f"Network access disabled in contract for tool '{tool_name}'",
                risk_tier=risk,
                violates_contract=True,
            )

        return PolicyDecision(allowed=True, reason="Authorized by contract", risk_tier=risk)


@dataclass
class ToolExecutionResult:
    operation_id: str
    status: str
    output: Any = None
    result: Any = None
    error: Optional[str] = None
    tool_name: str = ""
    cached: bool = False

    def __post_init__(self) -> None:
        if self.output is None and self.result is not None:
            self.output = self.result
        if self.result is None and self.output is not None:
            self.result = self.output


class ToolSandbox:
    """Execution sandbox wrapping tools with governance and idempotency."""

    def __init__(
        self,
        contract: Optional[AgentRuntimeContract] = None,
        policy: Optional[ToolGovernancePolicy] = None,
        ledger: Optional[OperationLedger] = None,
    ):
        self.contract = contract
        self.policy = policy or ToolGovernancePolicy()
        self.ledger = ledger or OperationLedger()

    def execute(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        operation_id: Optional[str] = None,
        executor: Optional[Callable[[Dict[str, Any]], Any]] = None,
        contract: Optional[AgentRuntimeContract] = None,
    ) -> ToolExecutionResult:
        """Convenience execution method returning a ToolExecutionResult object."""
        eff_contract = contract or self.contract
        if eff_contract is None:
            raise ValueError("ToolSandbox execution requires an active AgentRuntimeContract")
        args = params or {}
        call_fn = executor or (lambda p: None)
        raw = self.execute_tool(
            contract=eff_contract,
            tool_name=tool_name,
            tool_args=args,
            tool_callable=call_fn,
            operation_id=operation_id,
        )
        st = "cached" if raw.get("status") == "deduplicated" or raw.get("cached") else raw.get("status", "executed")
        return ToolExecutionResult(
            operation_id=raw["operation_id"],
            status=st,
            output=raw.get("result"),
            result=raw.get("result"),
            error=raw.get("error"),
            tool_name=tool_name,
            cached=bool(raw.get("cached", False)),
        )

    def execute_tool(
        self,
        contract: AgentRuntimeContract,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_callable: Callable[[Dict[str, Any]], Any],
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a tool with governance checks, idempotency, and error containment."""
        op_id = operation_id or f"op-{uuid.uuid4().hex}"

        # Check idempotency
        cached = self.ledger.get(op_id)
        if cached is not None:
            return {
                "operation_id": op_id,
                "status": "deduplicated",
                "result": cached.result,
                "error": cached.error,
                "tool_name": tool_name,
                "cached": True,
            }

        # Policy preflight check
        decision = self.policy.evaluate(contract, tool_name, tool_args)
        if not decision.allowed:
            self.ledger.record(
                operation_id=op_id,
                agent_id=contract.identity.agent_id,
                tool_name=tool_name,
                status="denied",
                error=decision.reason,
            )
            if decision.violates_contract:
                raise ContractViolationError(decision.reason)
            return {
                "operation_id": op_id,
                "status": "denied",
                "error": decision.reason,
                "tool_name": tool_name,
                "cached": False,
            }

        # Execute
        try:
            result = tool_callable(tool_args)
            record = self.ledger.record(
                operation_id=op_id,
                agent_id=contract.identity.agent_id,
                tool_name=tool_name,
                status="executed",
                result=result,
            )
            return {
                "operation_id": op_id,
                "status": "executed",
                "result": result,
                "error": None,
                "tool_name": tool_name,
                "cached": False,
            }
        except Exception as exc:
            record = self.ledger.record(
                operation_id=op_id,
                agent_id=contract.identity.agent_id,
                tool_name=tool_name,
                status="failed",
                error=str(exc),
            )
            return {
                "operation_id": op_id,
                "status": "failed",
                "result": None,
                "error": str(exc),
                "tool_name": tool_name,
                "cached": False,
            }
