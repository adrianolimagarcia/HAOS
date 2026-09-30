"""Comprehensive Test Suite for HAOS Civilization 3.0 Agent Kernel.

Tests all forensic architecture pillars:
1. Agent Runtime Contract validation & boundary enforcement
2. Deterministic FSM lifecycle state transitions & invalid jump rejection
3. Tool Sandbox governance, risk tiers, and operation idempotency
4. Distributed kernel tracing & hierarchical span tree rendering
5. Tiered memory hierarchy with provenance signatures and validation gates
6. Multi-dimensional confidence vector & instinct profiles
7. Agent reputation engine & trust-gated execution
8. Separation of Powers coordinator & Ultra SOTA 7-phase harness loop
9. Digital Twin repository analysis & blast radius prediction
10. Simulation Mode zero-mutation dry runs
11. Safe Evolution Gate with Ouroboros runaway protection
12. Agent evaluation benchmark test harness
"""

import os
import pytest
from unittest.mock import MagicMock

from hermes.platform.kernel.contract import (
    AgentBudgetContract,
    AgentCapabilityContract,
    AgentIdentityContract,
    AgentMemoryContract,
    AgentRole,
    AgentRuntimeContract,
    AgentValidationContract,
    ContractViolationError,
    MemoryScope,
    ToolRiskTier,
)
from hermes.platform.kernel.fsm import (
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)
from hermes.platform.kernel.tool_sandbox import (
    OperationLedger,
    ToolGovernancePolicy,
    ToolSandbox,
)
from hermes.platform.kernel.tracer import (
    DistributedTracer,
    Span,
)
from hermes.platform.kernel.memory import (
    MemoryHierarchyStore,
    MemoryItem,
    MemoryValidationError,
)
from hermes.platform.kernel.instincts import (
    ConfidenceVector,
    InstinctProfile,
)
from hermes.platform.kernel.reputation import (
    AgentReputationEngine,
)
from hermes.platform.kernel.orchestrator import (
    HarnessLoopOrchestrator,
    HarnessLoopPhase,
    SeparationOfPowersCoordinator,
    SeparationOfPowersViolationError,
)
from hermes.platform.kernel.digital_twin import (
    ProjectDigitalTwin,
    BlastRadius,
)
from hermes.platform.kernel.simulation import (
    SimulationEngine,
    SimulationReport,
)
from hermes.platform.kernel.evolution_gate import (
    EvolutionGate,
    EvolutionStage,
    MutationProposal,
)
from hermes.platform.kernel.agent_eval import (
    AgentEvaluationHarness,
    BenchmarkTask,
)


# ==========================================
# 1. Agent Runtime Contract Tests
# ==========================================

def test_contract_validation_success():
    contract = AgentRuntimeContract(
        identity=AgentIdentityContract(agent_id="coder-01", role=AgentRole.BUILDER, domain="backend"),
        capability=AgentCapabilityContract(
            allowed_tools={"read_file", "edit_code"},
            max_risk_tier=ToolRiskTier.MEDIUM,
        ),
        memory=AgentMemoryContract(
            read_scopes={MemoryScope.WORKING, MemoryScope.SESSION, MemoryScope.PROJECT},
            write_scopes={MemoryScope.WORKING},
        ),
        budget=AgentBudgetContract(max_tokens=20000, max_usd_cost=0.5, max_iterations=10, max_time_seconds=60.0),
        validation=AgentValidationContract(requires_critic=True, requires_tests=True),
    )
    # Validations should pass
    contract.validate_tool_access("read_file", ToolRiskTier.READ)
    contract.validate_tool_access("edit_code", ToolRiskTier.MEDIUM)
    contract.validate_memory_read(MemoryScope.PROJECT)
    contract.validate_memory_write(MemoryScope.WORKING)


def test_contract_unauthorized_tool_rejected():
    contract = AgentRuntimeContract(
        identity=AgentIdentityContract(agent_id="reviewer-01", role=AgentRole.CRITIC, domain="security"),
        capability=AgentCapabilityContract(
            allowed_tools={"read_file"},
            max_risk_tier=ToolRiskTier.READ,
        ),
        memory=AgentMemoryContract(),
        budget=AgentBudgetContract(),
        validation=AgentValidationContract(),
    )
    with pytest.raises(ContractViolationError, match="not in allowed tools"):
        contract.validate_tool_access("delete_file", ToolRiskTier.CRITICAL)

    with pytest.raises(ContractViolationError, match="exceeds agent max allowed tier"):
        contract.validate_tool_access("read_file", ToolRiskTier.HIGH)


def test_contract_unauthorized_memory_write_rejected():
    contract = AgentRuntimeContract(
        identity=AgentIdentityContract(agent_id="junior-01", role=AgentRole.BUILDER),
        capability=AgentCapabilityContract(),
        memory=AgentMemoryContract(
            read_scopes={MemoryScope.WORKING},
            write_scopes={MemoryScope.WORKING},
        ),
        budget=AgentBudgetContract(),
        validation=AgentValidationContract(),
    )
    with pytest.raises(ContractViolationError, match="not authorized to write to memory scope"):
        contract.validate_memory_write(MemoryScope.GLOBAL_SKILL)


# ==========================================
# 2. Agent State Machine (FSM) Tests
# ==========================================

def test_fsm_happy_path_lifecycle():
    fsm = AgentStateMachine(agent_id="test-agent")
    assert fsm.state == AgentState.CREATED

    fsm.transition_to(AgentState.PLANNING, reason="Decomposing task")
    assert fsm.state == AgentState.PLANNING

    fsm.transition_to(AgentState.READY, reason="Plan ratified")
    assert fsm.state == AgentState.READY

    fsm.transition_to(AgentState.EXECUTING, reason="Building patch")
    assert fsm.state == AgentState.EXECUTING

    fsm.transition_to(AgentState.VALIDATING, reason="Running verification suite")
    assert fsm.state == AgentState.VALIDATING

    fsm.transition_to(AgentState.REFLECTING, reason="Reviewing critic feedback")
    assert fsm.state == AgentState.REFLECTING

    fsm.transition_to(AgentState.COMPLETED, reason="Task successfully validated")
    assert fsm.state == AgentState.COMPLETED
    assert fsm.is_terminal()
    assert len(fsm.history) == 7


def test_fsm_invalid_transition_rejected():
    fsm = AgentStateMachine(agent_id="rogue-agent")
    assert fsm.state == AgentState.CREATED

    # Cannot jump directly from CREATED to EXECUTING without PLANNING & READY
    with pytest.raises(InvalidStateTransitionError):
        fsm.transition_to(AgentState.EXECUTING, reason="Bypassing planning")


def test_fsm_failure_and_rollback():
    fsm = AgentStateMachine(agent_id="buggy-agent")
    fsm.transition_to(AgentState.PLANNING)
    fsm.transition_to(AgentState.READY)
    fsm.transition_to(AgentState.EXECUTING)
    fsm.transition_to(AgentState.VALIDATING)

    # Failed validation moves to ROLLED_BACK
    fsm.transition_to(AgentState.ROLLED_BACK, reason="Critical regression detected")
    assert fsm.state == AgentState.ROLLED_BACK
    assert fsm.is_terminal()


# ==========================================
# 3. Tool Sandbox & Idempotency Tests
# ==========================================

def test_tool_sandbox_execution_and_idempotency():
    contract = AgentRuntimeContract(
        identity=AgentIdentityContract(agent_id="worker-01", role=AgentRole.BUILDER),
        capability=AgentCapabilityContract(
            allowed_tools={"echo", "git_commit"},
            max_risk_tier=ToolRiskTier.HIGH,
        ),
        memory=AgentMemoryContract(),
        budget=AgentBudgetContract(),
        validation=AgentValidationContract(),
    )
    sandbox = ToolSandbox(contract=contract)

    # First execution with operation_id
    res1 = sandbox.execute(
        tool_name="echo",
        params={"msg": "hello world"},
        operation_id="op-1001",
        executor=lambda p: f"Echoed: {p['msg']}",
    )
    assert res1.status == "executed"
    assert res1.output == "Echoed: hello world"
    assert res1.cached is False

    # Second execution with identical operation_id returns cached result (idempotency)
    res2 = sandbox.execute(
        tool_name="echo",
        params={"msg": "hello world"},
        operation_id="op-1001",
        executor=lambda p: "Should not be called",
    )
    assert res2.status == "cached"
    assert res2.output == "Echoed: hello world"
    assert res2.cached is True


def test_tool_sandbox_risk_tier_enforcement():
    contract = AgentRuntimeContract(
        identity=AgentIdentityContract(agent_id="restricted-worker", role=AgentRole.BUILDER),
        capability=AgentCapabilityContract(
            allowed_tools={"read_file", "delete_database"},
            max_risk_tier=ToolRiskTier.LOW,
        ),
        memory=AgentMemoryContract(),
        budget=AgentBudgetContract(),
        validation=AgentValidationContract(),
    )
    policy = ToolGovernancePolicy(custom_risk_map={"delete_database": ToolRiskTier.CRITICAL})
    sandbox = ToolSandbox(contract=contract, policy=policy)

    with pytest.raises(ContractViolationError, match="exceed.* allowed tier"):
        sandbox.execute(
            tool_name="delete_database",
            params={"target": "prod"},
            operation_id="op-malicious",
            executor=lambda p: "destroyed",
        )


# ==========================================
# 4. Distributed Tracing Tests
# ==========================================

def test_distributed_tracing_hierarchy():
    tracer = DistributedTracer()
    trace_id = tracer.start_trace(task_id="task-99", root_agent_id="planner-01", name="TaskDecomposition")

    span_root = tracer.get_trace(trace_id)[0]
    assert span_root.name == "TaskDecomposition"

    # Child span for coder
    child_id = tracer.start_span(
        trace_id=trace_id,
        parent_span_id=span_root.span_id,
        agent_id="coder-01",
        name="GenerateCode",
        kind="agent_step",
    )
    tracer.end_span(child_id, status="success", tokens=450, cost_usd=0.002, tools_called=["read_file", "edit_code"])
    tracer.end_span(span_root.span_id, status="success", tokens=200, cost_usd=0.001)

    spans = tracer.get_trace(trace_id)
    assert len(spans) == 2
    tree = tracer.render_trace_tree(trace_id)
    assert "GenerateCode" in tree
    assert "tools: [read_file, edit_code]" in tree


# ==========================================
# 5. Tiered Memory with Provenance Tests
# ==========================================

def test_tiered_memory_provenance_and_promotion_gate():
    store = MemoryHierarchyStore()

    # Step 1: Write to Working Memory
    item = store.write(
        scope=MemoryScope.WORKING,
        value="Found potential fix for race condition in lock.acquire()",
        source_agent="builder-bot",
        task_id="task-fix-race",
        confidence=0.85,
    )
    assert item.scope == MemoryScope.WORKING
    assert len(item.signature) == 64  # SHA256 hex string
    assert not item.is_validated()

    # Attempting to promote to PROJECT memory without validations must fail
    with pytest.raises(MemoryValidationError, match="not validated by any critic"):
        store.promote(item.id, target_scope=MemoryScope.PROJECT, promoter_agent="builder-bot")

    # Step 2: Critic validates the memory
    store.add_validation(item.id, validator_agent="critic-bot", confidence_score=0.92, critique_notes="Verified logic against race condition")
    assert item.is_validated()

    # Step 3: Now promotion succeeds
    promoted = store.promote(item.id, target_scope=MemoryScope.PROJECT, promoter_agent="promoter-bot")
    assert promoted.scope == MemoryScope.PROJECT
    assert promoted.parent_id == item.id
    assert promoted.confidence >= 0.85


# ==========================================
# 6. Instinct Confidence Vector Tests
# ==========================================

def test_confidence_vector_calculation_and_decay():
    cv = ConfidenceVector(
        correctness=0.95,
        test_coverage=0.90,
        production_history=0.80,
        executions=100,
        rollbacks=2,
        age_days=10.0,
    )
    score = cv.composite_score()
    assert 0.70 <= score <= 0.95

    # Severe rollback penalty test
    cv_unstable = ConfidenceVector(
        correctness=0.95,
        test_coverage=0.90,
        production_history=0.80,
        executions=10,
        rollbacks=5,  # 50% rollbacks
        age_days=1.0,
    )
    assert cv_unstable.composite_score() < 0.60


def test_instinct_profile():
    profile = InstinctProfile(skill_name="patch_deadlock")
    profile.record_execution(success=True, has_tests=True, rolled_back=False)
    profile.record_execution(success=True, has_tests=True, rolled_back=False)

    assert profile.is_reliable(threshold=0.70)
    assert profile.executions == 2
    assert profile.failures == 0


# ==========================================
# 7. Agent Reputation Engine Tests
# ==========================================

def test_reputation_engine_performance_and_tier_authorization():
    engine = AgentReputationEngine()
    rep = engine.get_or_create(agent_id="bot-alpha", role="coder", domain="infra")

    # Baseline trust
    assert rep.trust_score == 0.70
    assert rep.can_execute_risk_tier(ToolRiskTier.MEDIUM)
    assert not rep.can_execute_risk_tier(ToolRiskTier.CRITICAL)  # Critical requires 0.95

    # High performance increases trust
    for _ in range(10):
        engine.record_outcome(agent_id="bot-alpha", success=True, tokens=1000, cost_usd=0.01)

    updated_rep = engine.get_or_create(agent_id="bot-alpha")
    assert updated_rep.trust_score > 0.75
    assert updated_rep.can_execute_risk_tier(ToolRiskTier.HIGH)

    # Rollback penalizes trust severely
    trust_before = updated_rep.trust_score
    engine.record_outcome(agent_id="bot-alpha", success=False, rolled_back=True)
    penalized_rep = engine.get_or_create(agent_id="bot-alpha")
    assert penalized_rep.trust_score <= trust_before - 0.20


# ==========================================
# 8. Separation of Powers Orchestrator Tests
# ==========================================

def test_separation_of_powers_violation():
    with pytest.raises(SeparationOfPowersViolationError, match="cannot be the Critic"):
        SeparationOfPowersCoordinator(
            builder_agent_id="agent-alice",
            critic_agent_id="agent-alice",  # Violation: Builder cannot be Critic!
            validator_agent_id="agent-bob",
            promoter_agent_id="agent-charlie",
        )


def test_harness_loop_orchestrator_7_phases():
    coordinator = SeparationOfPowersCoordinator(
        builder_agent_id="builder-01",
        critic_agent_id="critic-01",
        validator_agent_id="validator-01",
        promoter_agent_id="promoter-01",
    )
    orchestrator = HarnessLoopOrchestrator(coordinator=coordinator)

    result = orchestrator.execute_task(
        task_id="task-42",
        objective="Fix memory leak in buffer pool",
        context={"component": "buffer_pool"},
    )
    assert result.success is True
    assert result.current_phase == HarnessLoopPhase.MEMORIZE
    assert len(result.phases_completed) == 7
    assert result.memory_id is not None


# ==========================================
# 9. Digital Twin & Blast Radius Tests
# ==========================================

def test_digital_twin_blast_radius_prediction():
    twin = ProjectDigitalTwin(root_dir=".")
    blast = twin.calculate_blast_radius(
        target_files=["hermes/platform/kernel/contract.py"],
    )
    assert blast.total_impacted_files >= 1
    assert "hermes/platform/kernel/contract.py" in blast.target_files
    # Core kernel file requires council approval
    assert blast.requires_council_approval is True
    assert blast.risk_score > 0.3


# ==========================================
# 10. Simulation Mode Engine Tests
# ==========================================

def test_simulation_mode_engine():
    twin = ProjectDigitalTwin(root_dir=".")
    sim = SimulationEngine(digital_twin=twin)

    report = sim.simulate_task(
        task_prompt="Implement cache eviction policy in memory store",
        target_files=["hermes/platform/kernel/memory.py"],
    )
    assert report.simulation_id.startswith("sim-")
    assert report.estimated_tokens > 0
    assert report.estimated_cost_usd > 0
    assert len(report.predicted_steps) >= 5
    assert report.risk_level in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ==========================================
# 11. Safe Evolution Gate Tests
# ==========================================

def test_evolution_gate_protection_pipeline():
    gate = EvolutionGate()
    mutation = MutationProposal(
        proposal_id="mut-01",
        target_bot_id="coder-01",
        proposed_soul_delta="Inject aggressive automated refactoring instincts",
        target_files=["hermes/platform/kernel/orchestrator.py"],
        author_agent="evolver-bot",
    )

    # 1. Pipeline execution with successful evaluation
    receipt = gate.process_mutation(
        proposal=mutation,
        evaluation_fn=lambda p: True,  # Passes evaluation
        policy_approval_fn=lambda p: True,  # Passes policy check
    )
    assert receipt.promoted is True
    assert receipt.current_stage == EvolutionStage.PROMOTION
    assert receipt.rollback_triggered is False

    # 2. Pipeline execution when evaluation fails
    failed_mutation = MutationProposal(
        proposal_id="mut-02",
        target_bot_id="coder-01",
        proposed_soul_delta="Corrupted rule",
        target_files=["hermes/platform/kernel/orchestrator.py"],
        author_agent="evolver-bot",
    )
    failed_receipt = gate.process_mutation(
        proposal=failed_mutation,
        evaluation_fn=lambda p: False,  # Fails evaluation!
        policy_approval_fn=lambda p: True,
    )
    assert failed_receipt.promoted is False
    assert failed_receipt.rollback_triggered is True
    assert failed_receipt.current_stage == EvolutionStage.REJECTED


# ==========================================
# 12. Agent Evaluation Harness Tests
# ==========================================

def test_agent_evaluation_benchmark_harness():
    harness = AgentEvaluationHarness()
    tasks = [
        BenchmarkTask(
            task_id="bench-01",
            domain="refactoring",
            prompt="Refactor method to eliminate cognitive complexity",
            expected_outcome="Passes linter without warnings",
        ),
        BenchmarkTask(
            task_id="bench-02",
            domain="debugging",
            prompt="Find off-by-one error in loop index",
            expected_outcome="All tests pass",
        ),
    ]

    report = harness.run_suite(
        suite_name="CoreCodingBenchmark",
        tasks=tasks,
        agent_executor=lambda t: f"Executed {t.task_id} successfully",
    )
    assert report.total_tasks == 2
    assert report.passed_tasks == 2
    assert report.completion_rate == 1.0
    assert report.average_latency_ms > 0
