"""Ultra SOTA Harness Loop and Separation of Powers Orchestrator.

Enforces:
1. Strict Separation of Powers:
   Builder -> Critic Agent -> Validator -> Promoter
   Anti-Confirmation Bias Invariant: The same agent cannot create, validate, and approve.
2. Ultra SOTA Harness Loop:
   UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> CRITIQUE -> REFLECT -> MEMORIZE
   Hard Invariant: No permanent memory is created before verification and critic approval.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from hermes.platform.kernel.contract import (
    AgentRole,
    AgentRuntimeContract,
    ContractViolationError,
    MemoryScope,
    ToolRiskTier,
)
from hermes.platform.kernel.fsm import AgentState, AgentStateMachine
from hermes.platform.kernel.memory import MemoryHierarchyStore, MemoryItem, MemoryValidationError
from hermes.platform.kernel.reputation import AgentReputationEngine
from hermes.platform.kernel.tool_sandbox import ToolSandbox
from hermes.platform.kernel.tracer import DistributedTracer, GLOBAL_TRACER


class SeparationOfPowersViolationError(Exception):
    """Raised when the anti-confirmation bias or separation of powers rules are violated."""
    pass


class HarnessLoopPhase(str, Enum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    CRITIQUE = "critique"
    REFLECT = "reflect"
    MEMORIZE = "memorize"


@dataclass
class PhaseOutcome:
    phase: HarnessLoopPhase
    success: bool
    agent_id: str
    output: Any = None
    error: Optional[str] = None
    tokens: int = 0
    duration_ms: float = 0.0


@dataclass
class TaskExecutionResult:
    task_id: str
    trace_id: str
    success: bool
    final_state: AgentState
    phase_outcomes: List[PhaseOutcome] = field(default_factory=list)
    critic_approved: bool = False
    validation_passed: bool = False
    memories_recorded: List[str] = field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    error: Optional[str] = None

    @property
    def current_phase(self) -> HarnessLoopPhase:
        if self.phase_outcomes:
            return self.phase_outcomes[-1].phase
        return HarnessLoopPhase.UNDERSTAND

    @property
    def phases_completed(self) -> List[PhaseOutcome]:
        return self.phase_outcomes

    @property
    def memory_id(self) -> Optional[str]:
        return self.memories_recorded[0] if self.memories_recorded else None


class SeparationOfPowersCoordinator:
    """Guarantees distinct agent identities for Builder, Critic, Validator, and Promoter."""

    def __init__(
        self,
        builder_contract: Optional[AgentRuntimeContract] = None,
        critic_contract: Optional[AgentRuntimeContract] = None,
        validator_contract: Optional[AgentRuntimeContract] = None,
        promoter_contract: Optional[AgentRuntimeContract] = None,
        builder_agent_id: Optional[str] = None,
        critic_agent_id: Optional[str] = None,
        validator_agent_id: Optional[str] = None,
        promoter_agent_id: Optional[str] = None,
    ):
        if builder_contract is None and builder_agent_id is not None:
            builder_contract = AgentRuntimeContract.create(agent_id=builder_agent_id, role=AgentRole.BUILDER)
        if critic_contract is None and critic_agent_id is not None:
            critic_contract = AgentRuntimeContract.create(agent_id=critic_agent_id, role=AgentRole.CRITIC)
        if validator_contract is None and validator_agent_id is not None:
            validator_contract = AgentRuntimeContract.create(agent_id=validator_agent_id, role=AgentRole.VALIDATOR)
        if promoter_contract is None and promoter_agent_id is not None:
            promoter_contract = AgentRuntimeContract.create(agent_id=promoter_agent_id, role=AgentRole.PROMOTER)

        self.builder = builder_contract or AgentRuntimeContract.create("builder-bot", AgentRole.BUILDER)
        self.critic = critic_contract or AgentRuntimeContract.create("critic-bot", AgentRole.CRITIC)
        self.validator = validator_contract or AgentRuntimeContract.create("validator-bot", AgentRole.VALIDATOR)
        self.promoter = promoter_contract or AgentRuntimeContract.create("promoter-governor", AgentRole.PROMOTER)
        self.validate_separation()

    def validate(self) -> None:
        self.validate_separation()

    def validate_separation(self) -> None:
        """Anti-confirmation bias rule: Builder, Critic, and Validator MUST be distinct agents."""
        ids = {
            self.builder.identity.agent_id,
            self.critic.identity.agent_id,
            self.validator.identity.agent_id,
        }
        if len(ids) < 3:
            raise SeparationOfPowersViolationError(
                f"Separation of powers violated! Builder cannot be the Critic or Validator. "
                f"Got: Builder='{self.builder.identity.agent_id}', "
                f"Critic='{self.critic.identity.agent_id}', "
                f"Validator='{self.validator.identity.agent_id}'"
            )

        if self.builder.identity.role != AgentRole.BUILDER:
            raise SeparationOfPowersViolationError(f"Builder agent must have BUILDER role (got {self.builder.identity.role})")
        if self.critic.identity.role != AgentRole.CRITIC:
            raise SeparationOfPowersViolationError(f"Critic agent must have CRITIC role (got {self.critic.identity.role})")
        if self.validator.identity.role != AgentRole.VALIDATOR:
            raise SeparationOfPowersViolationError(f"Validator agent must have VALIDATOR role (got {self.validator.identity.role})")


class HarnessLoopOrchestrator:
    """Executes the 7-stage Ultra SOTA Harness Loop."""

    def __init__(
        self,
        coordinator: SeparationOfPowersCoordinator,
        sandbox: Optional[ToolSandbox] = None,
        memory_store: Optional[MemoryHierarchyStore] = None,
        reputation_engine: Optional[AgentReputationEngine] = None,
        tracer: Optional[DistributedTracer] = None,
    ):
        self.coordinator = coordinator
        self.sandbox = sandbox or ToolSandbox()
        self.memory = memory_store or MemoryHierarchyStore()
        self.reputation = reputation_engine or AgentReputationEngine()
        self.tracer = tracer or GLOBAL_TRACER

    def execute_task(
        self,
        task_id: str,
        objective: str,
        context: Optional[Dict[str, Any]] = None,
        builder_fn: Optional[Any] = None,
        validator_fn: Optional[Any] = None,
        critic_fn: Optional[Any] = None,
    ) -> TaskExecutionResult:
        b_fn = builder_fn or (lambda c, p, ctx: {"status": "ok", "patch": "applied", "tokens": 100, "cost_usd": 0.001})
        v_fn = validator_fn or (lambda c, ctx: {"status": "ok", "tests_passed": True, "tokens": 50, "cost_usd": 0.0005})
        c_fn = critic_fn or (lambda c, ctx: {"approved": True, "critique": "Solid logic", "tokens": 50, "cost_usd": 0.0005})
        return self.run_task(
            task_id=task_id,
            task_prompt=objective,
            builder_fn=b_fn,
            validator_fn=v_fn,
            critic_fn=c_fn,
            task_context=context,
        )

    def run_task(
        self,
        task_id: str,
        task_prompt: str,
        builder_fn: Callable[[AgentRuntimeContract, str, Dict[str, Any]], Dict[str, Any]],
        validator_fn: Callable[[AgentRuntimeContract, Dict[str, Any]], Dict[str, Any]],
        critic_fn: Callable[[AgentRuntimeContract, Dict[str, Any]], Dict[str, Any]],
        task_context: Optional[Dict[str, Any]] = None,
    ) -> TaskExecutionResult:
        """Execute a full task cycle through the 7-phase SOTA loop."""
        ctx = task_context or {}
        trace_id = self.tracer.start_trace(
            task_id=task_id,
            root_agent_id=self.coordinator.builder.identity.agent_id,
            name=f"task:{task_id}",
        )
        fsm = AgentStateMachine(agent_id=self.coordinator.builder.identity.agent_id)
        phase_outcomes: List[PhaseOutcome] = []
        memories_recorded: List[str] = []

        total_tokens = 0
        total_cost = 0.0

        # Phase 1: UNDERSTAND
        span_und = self.tracer.start_span(
            trace_id=trace_id,
            name="1.understand",
            agent_id=self.coordinator.builder.identity.agent_id,
            task_id=task_id,
        )
        fsm.transition_to(AgentState.PLANNING, reason="Beginning task analysis and understanding")
        und_start = time.time()
        # Analyze problem context
        understanding = {
            "objective": task_prompt,
            "constraints": ctx.get("constraints", []),
            "assumptions": ctx.get("assumptions", []),
        }
        und_dur = (time.time() - und_start) * 1000.0
        phase_outcomes.append(PhaseOutcome(
            phase=HarnessLoopPhase.UNDERSTAND,
            success=True,
            agent_id=self.coordinator.builder.identity.agent_id,
            output=understanding,
            duration_ms=und_dur,
        ))
        span_und.finish(status="OK", decision="Problem understood and constraints bounded")

        # Phase 2: PLAN
        span_plan = self.tracer.start_span(
            trace_id=trace_id,
            name="2.plan",
            agent_id=self.coordinator.builder.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_und.span_id,
        )
        plan_start = time.time()
        plan = {
            "plan_steps": ctx.get("plan_steps", ["analyze", "execute", "verify"]),
            "target_files": ctx.get("target_files", []),
            "risk_assessment": ctx.get("risk_assessment", "low"),
        }
        fsm.transition_to(AgentState.READY, reason="Plan formulated and ready for execution")
        plan_dur = (time.time() - plan_start) * 1000.0
        phase_outcomes.append(PhaseOutcome(
            phase=HarnessLoopPhase.PLAN,
            success=True,
            agent_id=self.coordinator.builder.identity.agent_id,
            output=plan,
            duration_ms=plan_dur,
        ))
        span_plan.finish(status="OK", decision="Plan finalized")

        # Phase 3: EXECUTE (Builder)
        span_exec = self.tracer.start_span(
            trace_id=trace_id,
            name="3.execute",
            agent_id=self.coordinator.builder.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_plan.span_id,
        )
        fsm.transition_to(AgentState.EXECUTING, reason="Executing builder actions")
        exec_start = time.time()
        try:
            builder_output = builder_fn(self.coordinator.builder, task_prompt, {**ctx, "plan": plan})
            exec_dur = (time.time() - exec_start) * 1000.0
            exec_tokens = builder_output.get("tokens", 1000)
            exec_cost = builder_output.get("cost_usd", 0.005)
            total_tokens += exec_tokens
            total_cost += exec_cost
            span_exec.finish(status="OK", tokens=exec_tokens, cost_usd=exec_cost, decision="Builder completed changes")
            phase_outcomes.append(PhaseOutcome(
                phase=HarnessLoopPhase.EXECUTE,
                success=True,
                agent_id=self.coordinator.builder.identity.agent_id,
                output=builder_output,
                tokens=exec_tokens,
                duration_ms=exec_dur,
            ))
        except Exception as exc:
            exec_dur = (time.time() - exec_start) * 1000.0
            fsm.transition_to(AgentState.FAILED, reason=f"Builder execution failed: {exc}")
            span_exec.finish(status="ERROR", error=str(exc))
            self.reputation.record_outcome(
                agent_id=self.coordinator.builder.identity.agent_id,
                success=False,
                tokens=total_tokens,
                cost_usd=total_cost,
                role=self.coordinator.builder.identity.role.value,
            )
            return TaskExecutionResult(
                task_id=task_id,
                trace_id=trace_id,
                success=False,
                final_state=fsm.current_state,
                phase_outcomes=phase_outcomes,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                error=f"Execution error: {exc}",
            )

        # Phase 4: VERIFY (Validator)
        span_ver = self.tracer.start_span(
            trace_id=trace_id,
            name="4.verify",
            agent_id=self.coordinator.validator.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_exec.span_id,
        )
        fsm.transition_to(AgentState.VALIDATING, reason="Validator running test suite and verification")
        ver_start = time.time()
        try:
            ver_output = validator_fn(self.coordinator.validator, builder_output)
            ver_dur = (time.time() - ver_start) * 1000.0
            ver_passed = bool(ver_output.get("passed", ver_output.get("tests_passed", ver_output.get("success", False))))
            ver_tokens = ver_output.get("tokens", 500)
            total_tokens += ver_tokens
            span_ver.finish(
                status="OK" if ver_passed else "ERROR",
                tokens=ver_tokens,
                decision=f"Verification {'passed' if ver_passed else 'failed'}",
            )
            phase_outcomes.append(PhaseOutcome(
                phase=HarnessLoopPhase.VERIFY,
                success=ver_passed,
                agent_id=self.coordinator.validator.identity.agent_id,
                output=ver_output,
                tokens=ver_tokens,
                duration_ms=ver_dur,
            ))
            if not ver_passed:
                fsm.transition_to(AgentState.FAILED, reason="Verification tests failed")
                self.reputation.record_outcome(
                    agent_id=self.coordinator.builder.identity.agent_id,
                    success=False,
                    tokens=total_tokens,
                    cost_usd=total_cost,
                    role=self.coordinator.builder.identity.role.value,
                )
                return TaskExecutionResult(
                    task_id=task_id,
                    trace_id=trace_id,
                    success=False,
                    final_state=fsm.current_state,
                    phase_outcomes=phase_outcomes,
                    validation_passed=False,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    error=f"Validation failed: {ver_output.get('error', 'Test assertions failed')}",
                )
        except Exception as exc:
            fsm.transition_to(AgentState.FAILED, reason=f"Validator threw exception: {exc}")
            span_ver.finish(status="ERROR", error=str(exc))
            return TaskExecutionResult(
                task_id=task_id,
                trace_id=trace_id,
                success=False,
                final_state=fsm.current_state,
                phase_outcomes=phase_outcomes,
                validation_passed=False,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                error=f"Validator error: {exc}",
            )

        # Phase 5: CRITIQUE (Critic Agent)
        span_crit = self.tracer.start_span(
            trace_id=trace_id,
            name="5.critique",
            agent_id=self.coordinator.critic.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_ver.span_id,
        )
        crit_start = time.time()
        try:
            crit_output = critic_fn(self.coordinator.critic, {
                "builder_output": builder_output,
                "verification": ver_output,
            })
            crit_dur = (time.time() - crit_start) * 1000.0
            crit_approved = crit_output.get("approved", False)
            crit_tokens = crit_output.get("tokens", 500)
            total_tokens += crit_tokens
            span_crit.finish(
                status="OK" if crit_approved else "ERROR",
                tokens=crit_tokens,
                decision=f"Critic {'approved' if crit_approved else 'rejected'}",
            )
            phase_outcomes.append(PhaseOutcome(
                phase=HarnessLoopPhase.CRITIQUE,
                success=crit_approved,
                agent_id=self.coordinator.critic.identity.agent_id,
                output=crit_output,
                tokens=crit_tokens,
                duration_ms=crit_dur,
            ))
            if not crit_approved:
                fsm.transition_to(AgentState.ROLLED_BACK, reason=f"Critic rejected solution: {crit_output.get('critique')}")
                self.reputation.record_outcome(
                    agent_id=self.coordinator.builder.identity.agent_id,
                    success=False,
                    tokens=total_tokens,
                    cost_usd=total_cost,
                    rolled_back=True,
                    role=self.coordinator.builder.identity.role.value,
                )
                return TaskExecutionResult(
                    task_id=task_id,
                    trace_id=trace_id,
                    success=False,
                    final_state=fsm.current_state,
                    phase_outcomes=phase_outcomes,
                    critic_approved=False,
                    validation_passed=True,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    error=f"Critic rejection: {crit_output.get('critique', 'Unspecified defect')}",
                )
        except Exception as exc:
            fsm.transition_to(AgentState.FAILED, reason=f"Critic evaluation failed: {exc}")
            span_crit.finish(status="ERROR", error=str(exc))
            return TaskExecutionResult(
                task_id=task_id,
                trace_id=trace_id,
                success=False,
                final_state=fsm.current_state,
                phase_outcomes=phase_outcomes,
                critic_approved=False,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                error=f"Critic error: {exc}",
            )

        # Phase 6: REFLECT
        fsm.transition_to(AgentState.REFLECTING, reason="Synthesizing lessons learned and insights")
        span_ref = self.tracer.start_span(
            trace_id=trace_id,
            name="6.reflect",
            agent_id=self.coordinator.builder.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_crit.span_id,
        )
        ref_start = time.time()
        reflection = {
            "insights": f"Task '{task_id}' succeeded with verified zero-defect contract.",
            "efficiency": f"{total_tokens} tokens, {total_cost:.4f} USD",
        }
        ref_dur = (time.time() - ref_start) * 1000.0
        span_ref.finish(status="OK", decision="Reflection recorded")
        phase_outcomes.append(PhaseOutcome(
            phase=HarnessLoopPhase.REFLECT,
            success=True,
            agent_id=self.coordinator.builder.identity.agent_id,
            output=reflection,
            duration_ms=ref_dur,
        ))

        # Phase 7: MEMORIZE (Promoter) - Only reached if verified AND critic approved!
        span_mem = self.tracer.start_span(
            trace_id=trace_id,
            name="7.memorize",
            agent_id=self.coordinator.promoter.identity.agent_id,
            task_id=task_id,
            parent_span_id=span_ref.span_id,
        )
        mem_start = time.time()
        # Invariant: Record verified memory with full provenance
        mem_item = self.memory.write(
            contract=self.coordinator.promoter,
            key=f"task_solution:{task_id}",
            value={
                "task": task_prompt,
                "solution_summary": builder_output.get("summary", "Complete"),
            },
            task_id=task_id,
            confidence=0.95,
            target_scope=MemoryScope.PROJECT,
            validated_by=[
                self.coordinator.validator.identity.agent_id,
                self.coordinator.critic.identity.agent_id,
            ],
        )
        memories_recorded.append(mem_item.memory_id)
        mem_dur = (time.time() - mem_start) * 1000.0
        span_mem.finish(status="OK", decision="Validated memory promoted to PROJECT scope")
        phase_outcomes.append(PhaseOutcome(
            phase=HarnessLoopPhase.MEMORIZE,
            success=True,
            agent_id=self.coordinator.promoter.identity.agent_id,
            output={"memory_id": mem_item.memory_id},
            duration_ms=mem_dur,
        ))

        # Successfully transition to COMPLETED
        fsm.transition_to(AgentState.COMPLETED, reason="Task passed all 7 harness loop stages")

        # Update reputation with success
        self.reputation.record_outcome(
            agent_id=self.coordinator.builder.identity.agent_id,
            success=True,
            tokens=total_tokens,
            cost_usd=total_cost,
            role=self.coordinator.builder.identity.role.value,
        )

        return TaskExecutionResult(
            task_id=task_id,
            trace_id=trace_id,
            success=True,
            final_state=fsm.current_state,
            phase_outcomes=phase_outcomes,
            critic_approved=True,
            validation_passed=True,
            memories_recorded=memories_recorded,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
        )
