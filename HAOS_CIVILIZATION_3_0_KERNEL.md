# HAOS Civilization 3.0 Agent Kernel: Forensic Substrate & Governance Architecture

## 1. Executive Summary

The **HAOS Civilization 3.0 Agent Kernel** represents an ultra-SOTA, deterministic, capability-governed, forensic substrate engineered for enterprise agent civilizations. Operating across both Python and high-performance Rust, the kernel enforces strict Separation of Powers, Ouroboros runaway evolution safeguards, digital twin blast-radius prediction, tiered memory provenance, and multi-dimensional trust reputation vectors.

Every action, state transition, tool execution, and evolutionary mutation executed within the civilization is cryptographically signed, traced in an OpenTelemetry-compatible span hierarchy, and verified against hard constitutional bounds.

---

## 2. Core Architectural Pillars

### 2.1 Agent Runtime Contract (P0 Standard)
The `AgentRuntimeContract` is the immutable specification for every spawned agent. It binds five inviolable dimensions:
1. **Identity Contract** (`AgentIdentityContract`): Immutable `agent_id`, `role` (`BUILDER`, `CRITIC`, `VALIDATOR`, `PLANNER`, `ANALYST`, `SECURITY`, `PROMOTER`), and `domain`.
2. **Capability Contract** (`AgentCapabilityContract`): Explicit allowlist of permissible tools (`allowed_tools`), maximum permissible risk tier (`max_risk_tier`), path boundary restrictions (`allowed_paths`), and network access permissions (`network_allowed`).
3. **Memory Contract** (`AgentMemoryContract`): Strict read/write scopes across the tiered hierarchy (`WORKING`, `SESSION`, `PROJECT`, `DOMAIN`, `GLOBAL`). Roles like `CRITIC` and `VALIDATOR` are prohibited from mutating global memory; only `PROMOTER` holds global memory write authorization.
4. **Budget Contract** (`AgentBudgetContract`): Hard upper bounds on execution tokens (`max_tokens`), timeouts (`timeout_seconds`), iteration limits (`max_iterations`), and financial cost limits (`max_cost_usd`).
5. **Validation Contract** (`AgentValidationContract`): Enforces mandatory independent multi-agent review, verification suites, and minimum confidence thresholds.

### 2.2 Deterministic Agent Lifecycle Finite State Machine (FSM)
Managed by `AgentStateMachine`, the lifecycle guarantees that agents cannot execute actions out-of-order or bypass governance gates:
- **Legal States**: `CREATED -> PLANNING -> READY -> EXECUTING -> VALIDATING -> REFLECTING -> COMPLETED` (Terminal).
- **Error States**: `FAILED`, `ABORTED`, `ROLLED_BACK`, `BUDGET_EXHAUSTED` (All Terminal).
- **Invariants**:
  - Direct transitions from `CREATED` to `EXECUTING` are strictly rejected; an agent must plan and be marked `READY`.
  - Terminal states are irreversible; no transitions may originate from a terminal state.
  - Complete transition history is immutably recorded with ISO timestamps, transition reasons, and contextual metadata.

### 2.3 Tool Sandbox with Governance Risk Tiers & Idempotency
All agent tool calls pass through `ToolSandbox`:
- **Tool Risk Tiers**:
  - `READ` (Level 1): Zero state mutation (e.g., `read_file`, `glob`, `grep`).
  - `LOW` (Level 2): Minor ephemeral mutations (e.g., `todo_write`, `list_tasks`).
  - `MEDIUM` (Level 3): Reversible project-level mutations (e.g., `write`, `edit`, `patch`).
  - `HIGH` (Level 4): Broad workspace mutations or shell executions (e.g., `bash`, `git_commit`).
  - `CRITICAL` (Level 5): Destructive, irreversible system commands (e.g., `rm -rf`, system reboots, database drop).
- **Operation Idempotency (`OperationLedger`)**:
  - Every mutating action requires a deterministic `operation_id`.
  - Re-execution attempts with identical `operation_id` are intercepted before execution, returning cached, deduplicated results.
- **Preflight Policy Enforcement**:
  - Sandboxed path confinement prevents directory traversal outside authorized paths.
  - Role-based risk bounds prevent low-privilege roles (e.g., `CRITIC`, `ANALYST`) from invoking mutating tools.

### 2.4 Distributed Tracing & Span Hierarchy (`DistributedTracer`)
Modeled after OpenTelemetry standards:
- Each root task generates a unique `trace_id`.
- Subagents and tool calls instantiate child spans linked via `parent_span_id`.
- Every span tracks start/finish timestamps, duration (ms), execution status, token usage, USD cost, tools invoked, and architectural decisions.
- Provides an ASCII tree renderer (`render_trace_tree`) for forensic inspection.

### 2.5 Tiered Memory Hierarchy with SHA-256 Provenance
Structured as a five-tier storage architecture (`MemoryHierarchyStore`):
1. **WORKING**: In-flight task scratchpad.
2. **SESSION**: Active turn and session context.
3. **PROJECT**: Repository-level solutions and conventions.
4. **DOMAIN**: Cross-project architectural patterns.
5. **GLOBAL**: Civilization-wide constitutional rules and instincts.

**Cryptographic Provenance Gate**:
- Every recorded memory item generates a SHA-256 digital signature over `key:value:task_id:source_agent:scope`.
- Promotion past `SESSION` scope strictly requires independent sign-off from `VALIDATOR` and `CRITIC` agents.
- Promotion to `GLOBAL` scope is restricted exclusively to `PROMOTER` agents.

### 2.6 Multi-Dimensional Confidence Vector & Half-Life Decay
Instincts, learned skills, and patterns maintain empirical metrics via `ConfidenceVector`:
- Factors: `correctness` (pass rate), `test_coverage`, `execution_volume`, and `rollback_rate`.
- **Severe Rollback Penalty**: Rollbacks penalize confidence heavily ($1.0 - 0.90 \times \text{rollback\_rate}$).
- **Exponential Age Decay**: Decays via $2^{-\Delta t / t_{1/2}}$ (configurable half-life, default 30 days) to prevent stale instincts from dominating modern codebases.

### 2.7 Agent Reputation Engine (`AgentReputationEngine`)
- Computes dynamic trust scores (default 0.70 baseline, bounded in $[0.0, 1.0]$).
- Successful task executions and cost efficiency reward trust score.
- Unsuccessful executions and rollbacks trigger severe penalties ($\ge 0.20$ drop).
- Gates tool authorization: high-risk tools require $\ge 0.75$ trust; critical tools require $\ge 0.95$ trust.

### 2.8 7-Phase Ultra-SOTA Harness Loop (`HarnessLoopOrchestrator`)
Enforces strict Separation of Powers across 7 discrete phases:
$$\text{UNDERSTAND} \longrightarrow \text{PLAN} \longrightarrow \text{EXECUTE} \longrightarrow \text{VERIFY} \longrightarrow \text{CRITIQUE} \longrightarrow \text{REFLECT} \longrightarrow \text{MEMORIZE}$$
- **Anti-Confirmation Bias Rule**: Builder $\neq$ Critic $\neq$ Validator. If Builder attempts to critique its own solution, the orchestrator immediately halts execution with `SeparationOfPowersViolationError`.
- **Phase Flow**:
  1. *Understand*: Decompose problem statement and bounds.
  2. *Plan*: Produce verifiable steps and blast radius estimate.
  3. *Execute*: Builder agent constructs code or artifacts.
  4. *Verify*: Independent Validator runs tests and static analysis.
  5. *Critique*: Independent Critic audits logic, security, and edge cases.
  6. *Reflect*: Synthesize lessons learned and update trust metrics.
  7. *Memorize*: Promoter stores validated knowledge in Project/Domain memory.

### 2.9 Digital Twin & AST Blast Radius Prediction (`ProjectDigitalTwin`)
- Static AST parser traversing the codebase to build directed dependency and reverse-dependency graphs.
- Given a proposed file modification, performs BFS across import relationships to calculate:
  - Impacted dependent files.
  - Associated test files that must be triggered.
  - Composite risk score ($0.0 - 1.0$).
  - Automatic flag for Council Ratification if risk score $\ge 0.70$ or core kernel files (`kernel`, `contract`, `security`, `governance`) are touched.

### 2.10 Zero-Mutation Simulation Mode (`SimulationEngine`)
- Allows agents to dry-run tasks without applying disk mutations or executing destructive tools.
- Evaluates estimated token expenditures, costs, predicted execution steps, and blast radius.

### 2.11 Safe Autonomous Evolution Gate (`EvolutionGate`)
- Prevents runaway self-mutation (Ouroboros loop).
- Pipeline:
  $$\text{Proposal} \longrightarrow \text{Simulation} \longrightarrow \text{Evaluation} \longrightarrow \text{Policy Approval} \longrightarrow \text{Promotion}$$
- If a proposed mutation impacts critical kernel infrastructure or exhibits an evaluation pass rate $< 90\%$, it is instantly rejected and rolled back.

---

## 3. Rust & Python Parity

The core invariants are implemented with dual parity:
- **Python Kernel**: `hermes/platform/kernel/`
  - `contract.py`: Dataclasses, validation invariants, risk tiers.
  - `fsm.py`: Deterministic FSM.
  - `tool_sandbox.py`: Sandboxing, idempotency ledger, governance policy.
  - `tracer.py`: OpenTelemetry tracing, span trees.
  - `memory.py`: Tiered storage, promotion gate, SHA-256 provenance.
  - `instincts.py`: Confidence vectors, half-life decay.
  - `reputation.py`: Agent reputation store.
  - `orchestrator.py`: 7-Phase harness loop, separation of powers.
  - `digital_twin.py`: AST dependency graph, blast radius analyzer.
  - `simulation.py`: Dry-run simulation engine.
  - `evolution_gate.py`: Safe evolution pipeline.
  - `agent_eval.py`: Benchmark evaluation harness.
- **Rust Kernel**: `packages/haos-civ/src/kernel.rs`
  - `ToolRiskTier` with ordering and risk authorization.
  - `AgentState` and `AgentStateMachine` with transition legality validation.
  - `ConfidenceVector` with volume scaling, rollback penalties, and exponential decay.
  - Exposed via `haos-civ` crate root (`pub mod kernel;`).

---

## 4. Verification Matrices

### 4.1 Python Unit & Integration Tests
File: `tests/platform/kernel/test_civilization_kernel.py`
Execution: `./scripts/run_tests.sh tests/platform/kernel/test_civilization_kernel.py`
Status: **19/19 PASSED (100%)**

| Test Name | Component | Status |
|---|---|---|
| `test_contract_validation_success` | AgentRuntimeContract | PASSED |
| `test_contract_unauthorized_tool_rejected` | AgentRuntimeContract | PASSED |
| `test_contract_unauthorized_memory_write_rejected` | AgentRuntimeContract | PASSED |
| `test_fsm_happy_path_lifecycle` | AgentStateMachine | PASSED |
| `test_fsm_invalid_transition_rejected` | AgentStateMachine | PASSED |
| `test_fsm_failure_and_rollback` | AgentStateMachine | PASSED |
| `test_tool_sandbox_execution_and_idempotency` | ToolSandbox & Ledger | PASSED |
| `test_tool_sandbox_risk_tier_enforcement` | ToolSandbox & Policy | PASSED |
| `test_distributed_tracing_hierarchy` | DistributedTracer | PASSED |
| `test_tiered_memory_provenance_and_promotion_gate` | MemoryHierarchyStore | PASSED |
| `test_confidence_vector_calculation_and_decay` | ConfidenceVector | PASSED |
| `test_instinct_profile` | InstinctProfile | PASSED |
| `test_reputation_engine_performance_and_tier_authorization` | AgentReputationEngine | PASSED |
| `test_separation_of_powers_violation` | SeparationOfPowersCoordinator | PASSED |
| `test_harness_loop_orchestrator_7_phases` | HarnessLoopOrchestrator | PASSED |
| `test_digital_twin_blast_radius_prediction` | ProjectDigitalTwin | PASSED |
| `test_simulation_mode_engine` | SimulationEngine | PASSED |
| `test_evolution_gate_protection_pipeline` | EvolutionGate | PASSED |
| `test_agent_evaluation_benchmark_harness` | AgentEvaluationHarness | PASSED |

### 4.2 Rust Kernel Suite
Manifest: `packages/haos-civ/Cargo.toml`
Execution: `cargo test --manifest-path packages/haos-civ/Cargo.toml`
Status: **19/19 PASSED (100%)**

---

## 5. Operator Control Plane (CLI Reference)

The kernel exposes forensic operator commands via `hermes civ`:

### 5.1 Dry-Run Simulation
Simulate a proposed task and calculate blast radius before making any file changes:
```bash
hermes civ simulate --task "Refactor authentication layer" --target-files hermes_cli/auth.py
```
Output:
```
=== Civilization 3.0 Simulation Report ===
Simulation ID:     sim-6b921dd322
Risk Level:        MEDIUM
Policy Status:     APPROVED_FOR_AUTONOMY
Estimated Cost:    $0.0450
Estimated Tokens:  9000
Predicted Steps (6):
  - 1. Understand requirements and bounded scope
  - 2. Inspect target codebase
  - 3. Draft deterministic patch isolating dependencies
  - 4. Run validation suite covering associated tests
  - 5. Submit patch to Critic Agent for independent verification
  - 6. Promote validated solution into Project Memory
Blast Radius:
  - Target files:    1
  - Dependent files: 12
  - Tests to run:    5
  - Risk score:      0.65
  - Council req:     False
```

### 5.2 Agent Reputation Inspection
Audit the current trust score, task execution statistics, and risk-tier clearance of agents:
```bash
hermes civ reputation [--agent-id <id>]
```

### 5.3 Forensic Execution Tracing
Inspect hierarchical OpenTelemetry trace trees of kernel executions:
```bash
hermes civ kernel-trace --trace-id <trace_id>
```

### 5.4 Memory Provenance Audit
Verify cryptographic signatures and validation chains for recorded memories:
```bash
hermes civ memory-provenance --memory-id <memory_id>
```
