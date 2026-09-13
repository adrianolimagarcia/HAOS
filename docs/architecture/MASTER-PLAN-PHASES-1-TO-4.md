# HAOS Implementation Master Plan (Phases 1 to 4)

- **Date:** 2026-09-08
- **Scope:** Complete architectural mapping of Phase 1 (Platform Kernel), Phase 2 (Team Runtime), Phase 3 (Adaptive Intelligence), and Phase 4 (Protocol & Federation).
- **Target Audience:** Hermes Executive, Lead Architects, Autonomous Agent Swarms, and Reviewers.
- **Governed By:** ADR-001 through ADR-005.

---

## 1. Executive Summary & Epics Map

The HAOS architecture is structured into 4 implemented, tested, and green horizontal epics:

```
┌────────────────────────────────────────────────────────────────────────┐
│             EPIC 4: Universal Protocol Gateway & Federation            │
│         (ANP Mesh, A2A Directory, ACP Client, Reputation Tracker)     │
├────────────────────────────────────────────────────────────────────────┤
│             EPIC 3: Adaptive Intelligence Platform (Ouroboros)         │
│   (FailurePatternDetector, RoutingOptimizer, Affinity, A/B Rollback)   │
├────────────────────────────────────────────────────────────────────────┤
│             EPIC 2: Multi-Agent Team Runtime                           │
│     (Town Mayor, Sub-Orchestrator, SpecialistPool, Epistemic Isolation)│
├────────────────────────────────────────────────────────────────────────┤
│             EPIC 1: Platform Kernel & 7 Real Adapters                  │
│ (TaskSpec, MemoryFabric, CapabilityRegistry, ExactModelClient, Kilo)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Module & Interface Matrix

| Epic | Subsystem Module | Key Interfaces / Contracts | Storage / Persistence |
|---|---|---|---|
| **Epic 1: Kernel** | `hermes/platform/tasks/spec.py` | `TaskSpec`, `AcceptanceCriterion`, `DependencyEdge` | KanbanAdapter (`kanban.db`) |
| **Epic 1: Kernel** | `hermes/platform/context/memory/federated_fabric.py` | `KnowledgeItem`, `MemoryScope`, `FederatedMemoryCoordinator` | SQLite + Obsidian + GraphRAG |
| **Epic 1: Kernel** | `hermes/platform/capabilities/universal_registry.py` | `CapabilityMetadata`, `UniversalCapabilityRegistry` | In-memory with TTL health-cache |
| **Epic 1: Kernel** | `hermes/platform/models/client.py` | `ExactModelClient`, `ModelIdentity`, `ProviderRoute` | In-memory circuit breaker |
| **Epic 1: Kernel** | `hermes/platform/workspaces/git_worktree.py` | `GitWorktreeManager`, `MergeQueue`, `AutoMergeGate` | Git Worktrees (`.worktrees/task-*`) |
| **Epic 2: Team** | `hermes/platform/execution/team_runtime.py` | `SpecialistPool`, `DomainSubOrchestrator`, `MultiAgentTeamRuntime` | `EventStore` (`events.db`) |
| **Epic 3: Adaptive**| `hermes/platform/evolution/adaptive_intelligence.py` | `FailurePatternDetector`, `AdaptiveRoutingOptimizer`, `AgentTaskAffinity`, `ExperimentFramework` | `EventStore` + Metrics Projection |
| **Epic 4: Gateway** | `hermes/platform/protocols/gateway.py` | `AgentCard`, `FederatedCapabilityResolver`, `RemoteAgentReputationTracker`, `UniversalProtocolGateway` | In-memory + HMAC signatures |

---

## 3. Strict Dependency Graph & Boot Order

1. **Layer 0 (Core Invariants):**
   - PEP-420 namespace compliance (`zero` `__init__.py` under `hermes/platform/`).
   - Standard library only at module level.
   - Prompt caching byte-stability (system prompt never mutated mid-turn).
2. **Layer 1 (Data & Storage Contracts):**
   - `EventStore` initialized with SQLite WAL.
   - `UniversalCapabilityRegistry` bootstraps built-in plugins (`kilo`, `lsp`, `mcp`, `docker`).
3. **Layer 2 (Model & Provider Routing):**
   - `ModelResolver` loads profiles (`deepseek-v4-flash` bound to `a6api` primary).
   - `ExactModelClient` establishes OpenAI-compatible HTTPS transport with circuit breaker.
4. **Layer 3 (Team Runtime & Pools):**
   - `SpecialistPool` pre-warms idle specialists (`coder`, `reviewer`, `researcher`).
   - `DomainSubOrchestrator` ready to decompose `DomainSubGoal` into DAGs.
5. **Layer 4 (Adaptive Intelligence):**
   - `AdaptiveIntelligenceCoordinator` connects to `EventStore` stream.
   - Background failure clustering and route recommendations active.
6. **Layer 5 (Protocol Gateway):**
   - `UniversalProtocolGateway` binds socket endpoints for ANP, A2A, and ACP.
   - `FederatedCapabilityResolver` links local registry with peer agent cards.

---

## 4. Test Matrix & Acceptance Criteria

- **Total Test Files:** 86 files.
- **Total Tests Passing:** 615 tests (100% green via `scripts/run_tests.sh`).
- **Key Invariant Tests:**
  - `tests/platform/test_adr_002_freeze_contract.py` (Freeze contracts)
  - `tests/platform/execution/test_vertical_slice_phase1.py` (Vertical slice E2E)
  - `tests/platform/test_seven_real_adapters.py` (7 real adapters)
  - `tests/platform/execution/test_team_runtime.py` (Phase 2 MultiAgent runtime)
  - `tests/platform/evolution/test_adaptive_intelligence.py` (Phase 3 Adaptive intelligence)
  - `tests/platform/protocols/test_protocol_gateway.py` (Phase 4 Protocol gateway)
  - `tests/platform/federation/test_federated_mesh_live.py` (Live socket ANP handshake)

---

## 5. Parallel Agent Assignments (Execution Playbook)

When an autonomous swarm is dispatched on this codebase:
- **Agent Architect:** Owns ADRs in `docs/architecture/` and contract validation.
- **Agent Coder:** Operates in ephemeral worktrees (`haos/task-<id>`) produced by `GitWorktreeManager`. Produces strictly typed diffs.
- **Agent Reviewer (Witness):** Evaluates diffs in fresh context, computes LSP blast radius, verifies that no prompt caching regressions occurred.
- **Agent Integrator (Refinery):** Queues valid commits into `MergeQueue` and coordinates serial rebase against target branch.
