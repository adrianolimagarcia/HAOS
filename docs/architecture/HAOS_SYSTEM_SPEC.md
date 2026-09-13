# HAOS System Specification (HAOS-SPEC-v1.0)

**Hermes Agent Operating System: Comprehensive Architecture and Technical Contract Specification**

- **Document Version:** 1.0.0
- **Canonical ADR Reference:** ADR-001 (`docs/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md`)
- **Status:** Canonical Living Specification
- **Engine Version:** HAOS 2026.1 / Hermes Turbo
- **Target Environments:** Local Workstations, Multi-GPU Linux Nodes, Distributed Cloud Clusters

---

## 1. System Overview & Formal Principles

HAOS (Hermes Agent Operating System) is a distributed, multi-agent operating system specifically engineered for deterministic autonomous software engineering. Unlike reactive conversational bots or simplistic tool-calling loops, HAOS provides an enterprise-grade execution fabric that abstracts memory, models, tools, protocols, workspaces, and evolutionary pipelines into rigorous operating system primitives.

### 1.1. Axiomatic Principles
1. **Separation of Task and Run:** Tasks are immutable declarative specifications; runs are ephemeral state machines executed by leased worker instances.
2. **Decoupling of Model and Provider:** Models represent intellectual capacity; providers represent transport and billing venues. Model identity is never silently degraded.
3. **Defense-in-Depth Trust Boundaries:** Privilege does not flow transitively. Every call between security rings is signed, verified, and rate-limited.
4. **Zero Dirty Commits on Main (Lane Kilo):** All modifications are produced in isolated Git worktrees, verified against LSP-derived blast radiuses, and committed serially through a deterministic Merge Queue.
5. **Autopoietic Evolution (Ouroboros):** The system continuously observes its own performance traces, mining successful workflows into versioned procedural skills.

---

## 2. Core System Architecture

```
                                  +------------------------------------------------------+
                                  |                 HAOS CONTROL PLANE                   |
                                  |  - Web UI (Reactive Dashboard)                       |
                                  |  - CLI Interface ('hermes haos')                     |
                                  |  - REST & WebSocket Telemetry APIs                   |
                                  +------------------------------------------------------+
                                                             |
                                                             v
+-------------------------------------------------------------------------------------------------------------------------------+
|                                                       PROTOCOL FABRIC                                                         |
|  - Unified Wire Bus (`ProtocolEnvelope`, HMAC-SHA256, Replay Protection)                                                      |
|  - CrossProtocolBridge (ACP <-> ANP <-> A2A <-> Internal Bus)                                                                 |
|  - Hierarchical Trust Boundaries: KERNEL (0) > LOCAL_SECURE (1) > AGENT_SANDBOX (2) > FEDERATED (3) > UNTRUSTED (4)           |
+-------------------------------------------------------------------------------------------------------------------------------+
         |                                                   |                                                 |
         v                                                   v                                                 v
+-----------------------------------+     +-----------------------------------+     +-----------------------------------+
|          AGENT CORE TRIAD         |     |      MODEL & PROVIDER FABRIC      |     |         WORKSPACE FABRIC          |
|                                   |     |                                   |     |            (LANE KILO)            |
| 1. Memory Fabric                  |     | - Axiom: Model != Provider        |     | - Ephemeral Git Worktrees         |
|    - Scopes (private/team/        |     | - ExactModelFailoverRouter        |     | - LSP Blast-Radius Engine         |
|      project/global)              |     | - Zero Silent Degradation Policy  |     | - AutoMergeGate Verifier          |
|    - SHA-256 Deduplication        |     | - Circuit Breaker State Machine   |     | - Serial Rebase MergeQueue        |
|    - Supersession Chains          |     | - Posture Assignments:            |     +-----------------------------------+
|    - Multi-Store Synchronization  |     |   * Architect: Claude 3.7         |                       |
|      (Hermes / Obsidian /         |     |   * Coder: DeepSeek-V3            |                       v
|       GraphRAG / DecisionStore)   |     |   * Reviewer: Claude 3.5          |     +-----------------------------------+
|                                   |     +-----------------------------------+     |        OUROBOROS EVOLUTION        |
| 2. Procedural Skills Engine       |                                               |                                   |
|    - `SkillSpec` Model            |                                               | - RunTrace / Span Discovery       |
|    - Semantic Versioning (SemVer) |                                               | - Failure Classifier (11 classes) |
|    - Lifecycle State Machine      |                                               | - SkillGenerator (Pattern Miner)  |
|    - Collision & Deprecation      |                                               | - Sandbox Evaluation Harness      |
|                                   |                                               | - MergeQueue Auto-Promotion       |
| 3. Universal Capability Registry  |                                               +-----------------------------------+
|    - Uniform Metadata Schema      |                                                                 |
|    - MCP / LSP / Kilo / Modality  |                                                                 v
|    - Sandboxing & Health Checks   |                                               +-----------------------------------+
+-----------------------------------+                                               |     FEDERATED HERMES NETWORK      |
                                                                                    |                                   |
                                                                                    | - Node Discovery & Directory      |
                                                                                    | - Mutual 3-Way HMAC Handshake     |
                                                                                    | - ANP Cryptographic Envelopes     |
                                                                                    +-----------------------------------+
```

---

## 3. Subsystem Specifications

### 3.1. Triad of Agent Core

The Agent Core Triad provides the memory, procedural knowledge, and execution capabilities required by every operational posture.

#### 3.1.1. Memory Fabric (`hermes.platform.context.memory`)
- **Scopes:**
  * `private`: Process-local scratchpad. Lifetime: single run.
  * `team`: Shared memory among posture teammates working on a single task (e.g. Coder and Reviewer notes).
  * `project`: Repository-level context (build recipes, architectural conventions, file layouts).
  * `global`: Cross-project knowledge (user preferences, system invariants).
- **Deduplication Engine:**
  * Generates a canonical SHA-256 digest over normalized fact payloads.
  * Identical facts within the same scope resolve to existing IDs, incrementing access counters rather than storing duplicates.
- **Supersession & Lineage:**
  * Facts support state flags: `ACTIVE`, `SUPERSEDED`, `ARCHIVED`.
  * Superseding a fact requires populating `supersedes_id` with the predecessor's UUID. Retrieval queries by default return only active heads unless historical lineage is explicitly requested.
- **Multi-Store Coordinator (`FederatedMemoryCoordinator`):**
  * Coordinates concurrent writes and reads across four backends:
    1. **Hermes Native Memory:** Fast vector and SQLite KV index for prompt assembly.
    2. **Obsidian Vault:** Human-auditable Markdown files with YAML frontmatter at `.hermes/obsidian_vault` (Canonical Human Source of Truth).
    3. **GraphRAG:** Dynamic knowledge graph capturing semantic entities, component relationships, and API dependencies.
    4. **DecisionStore:** Chronological ledger of architectural choices and ADRs.

#### 3.1.2. Procedural Skills Engine (`hermes.platform.skills.procedural_engine`)
- **`SkillSpec` Data Contract:**
  ```python
  @dataclass
  class SkillSpec:
      skill_id: str                     # Unique identifier (e.g. "git-rebase-safe")
      name: str                         # Human-readable display name
      version: str                      # Strict SemVer 2.0 (e.g. "1.2.0")
      description: str                  # Operational purpose and capabilities
      trigger_patterns: List[str]       # Intent or regex matchers
      steps: List[Dict[str, Any]]       # Deterministic action sequence
      inputs_schema: Dict[str, Any]     # JSON Schema for parameters
      outputs_schema: Dict[str, Any]    # JSON Schema for return values
      capabilities_required: List[str]  # e.g. ["kilo:worktree", "lsp:python"]
      rollback_steps: List[Dict[str, Any]] # Compensation actions on failure
      metadata: Dict[str, Any]          # Author, provenance, confidence metrics
  ```
- **SemVer Compliance:**
  * Enforces `(major, minor, patch)` parsing.
  * Version queries resolve highest matching semver: exact (`1.2.0`), minor compatible (`^1.2.0`), or latest (`*`).
- **Lifecycle Pipeline:**
  $$\text{CANDIDATE} \xrightarrow{\text{sandbox test}} \text{SANDBOX\_TEST} \xrightarrow{\text{eval benchmarks}} \text{EVALUATED} \xrightarrow{\text{promotion}} \text{ACTIVE} \xrightarrow{\text{deprecation}} \text{DEPRECATED} \xrightarrow{\text{retirement}} \text{RETIRED}$$

#### 3.1.3. Universal Capability Registry (`hermes.platform.capabilities.universal_registry`)
- **Capability Metadata Schema:**
  * Categories: `MCP`, `LSP`, `KILO`, `MODALITY`, `BROWSER`, `PLUGIN`.
  * Isolation Levels: `STRICT_SANDBOX`, `CONTAINER`, `PROCESS_ISOLATED`, `TRUSTED_HOST`.
  * Dynamic Health Checks: Periodic non-blocking ping returning `HealthStatus(healthy=True/False, latency_ms=...)`. Broken capabilities are flagged and bypassed during tool discovery.

---

### 3.2. Model & Provider Fabric

#### 3.2.1. The Model != Provider Axiom
- **Model Identity:** Uniquely identifies weights and reasoning capabilities (`vendor/family-version`, e.g., `anthropic/claude-3-7-sonnet`).
- **Provider Venue:** Represents an execution host (`anthropic`, `openrouter`, `bedrock`, `deepseek-direct`) with individual rate limits, endpoint URIs, and cost models.

#### 3.2.2. ExactModelFailoverRouter
- **Failover Invariant:** When an active provider fails (circuit breaker open, HTTP 429/500/503), the router searches the prioritized provider list for the **exact same model identity**.
- **No Silent Degradation:** If all providers for the requested model are exhausted, the router **must not** downgrade to an inferior model (e.g. Claude 3.7 -> Claude 3.5 Haiku) unless `substitute_allowed=True` is explicitly authorized in the task configuration. If exhausted, it raises `ExactModelRoutingExhausted`.

#### 3.2.3. Posture Assignment Matrix
| Posture | Primary Model Identity | Primary Provider | Fallback Providers | Responsibilities |
|---|---|---|---|---|
| `architect` | `anthropic/claude-3-7-sonnet` | Anthropic Direct | OpenRouter, AWS Bedrock | System decomposition, invariant definition, ADR formulation, planning |
| `coder` | `deepseek/deepseek-chat-v3` (or R1) | DeepSeek Direct | OpenRouter, Local vLLM | AST implementation, test authoring, code refactoring |
| `reviewer` | `anthropic/claude-3-5-sonnet` | Anthropic Direct | OpenRouter, AWS Bedrock | Adversarial review, blast-radius verification, security inspection |

---

### 3.3. Protocol Fabric & Trust Boundaries

#### 3.3.1. Wire Bus & Envelopes (`hermes.platform.protocols.unified_bus`)
- **Envelope Wire Format:**
  * Header: `envelope_id` (UUIDv4), `timestamp` (float UTC), `protocol_type` (`ACP`, `ANP`, `A2A`, `INTERNAL_BUS`), `trust_boundary` (enum).
  * Routing: `sender` (`node:agent`), `recipient` (`node:agent`).
  * Integrity: `payload_hash` ($\text{SHA-256}(\text{json\_bytes}(\text{payload}))$), `signature` ($\text{HMAC-SHA256}(\text{payload\_hash}, \text{secret\_key})$).
- **Replay Protection:** Envelopes older than $\Delta t_{\text{max}} = 300\text{s}$ or with previously seen nonces are rejected.

#### 3.3.2. Hierarchical Trust Boundaries
```
[KERNEL (Level 0)]
       ^
       | (Verified Elevation Gate)
[LOCAL_SECURE (Level 1)]
       ^
       | (Local Sandboxing & HMAC Signature)
[AGENT_SANDBOX (Level 2)]
       ^
       | (Mutual 3-Way HMAC Handshake + ANP Envelope)
[FEDERATED (Level 3)]
       ^
       | (Egress/Ingress Filtering & Schema Sanitization)
[UNTRUSTED (Level 4)]
```
- **Enforcement Rule:** Any message attempting to invoke an interface at level $N$ from level $M$ where $M > N$ must pass through cryptographic verification and strict schema validation in `CrossProtocolBridge`. Tampered signatures raise `ProtocolSecurityError`.

---

### 3.4. Workspace Fabric & Lane Kilo

#### 3.4.1. Ephemeral Worktrees (`hermes.platform.workspaces.git_worktree`)
- Every execution run creates an isolated worktree via `git worktree add -b kilo/<task_id>-<run_id>`.
- Prevents workspace lock collisions during parallel agent execution.

#### 3.4.2. LSP Blast-Radius Calculation (`hermes.platform.capabilities.lsp.unified_intelligence`)
1. **Diff Parsing:** Extracts all modified files and symbols ($\Delta \text{Symbols}$) from the branch diff.
2. **AST & Call Graph Traversal:** Queries LSP servers to compute:
   $$\text{BlastRadius}(\Delta \text{Symbols}) = \Delta \text{Symbols} \cup \text{Callers}(\Delta \text{Symbols}) \cup \text{Subclasses}(\Delta \text{Symbols}) \cup \text{Importers}(\Delta \text{Symbols})$$
3. **Test Mapping:** Determines all test files whose execution paths intersect with the blast radius.

#### 3.4.3. AutoMergeGate & MergeQueue (`hermes.platform.workspaces.merge_queue`)
- **Gate Preconditions:**
  1. Reviewer posture verdict is `APPROVED`.
  2. Test execution over `BlastRadius` has $0$ failures and $0$ errors.
  3. Worktree has no uncommitted files or merge conflicts.
- **MergeQueue Pipeline:**
  * Candidate commits are ordered by priority and FIFO timestamp.
  * Commits are sequentially rebased on `origin/main`.
  * The blast-radius test suite is re-executed on the rebased state.
  * Successful rebases are atomically merged (fast-forward or squash) into `main`.

---

### 3.5. Ouroboros Closed-Loop Self-Evolution (`hermes.platform.evolution.ouroboros_lifecycle.OuroborosLifecycleManager`)

#### 3.5.1. Trace Mining & Failure Classification
- Ingests `RunTrace` records containing hierarchical `TraceSpan` events.
- Maps run anomalies into 11 canonical failure categories:
  `PROVIDER_ERROR`, `TOOL_TRANSIENT`, `BLAST_RADIUS_VIOLATION`, `TEST_REGRESSION`, `REVIEW_REJECTION`, `SYNTAX_ERROR`, `TIMEOUT`, `PERMISSION_DENIED`, `ENVIRONMENT_CORRUPT`, `WORKSPACE_CONFLICT`, `UNKNOWN`.

#### 3.5.2. Automated Skill Synthesis & Evaluation
- `SkillGenerator` scans execution traces for successful multi-step action sequences repeated across independent tasks.
- Generates a candidate `SkillSpec` and initiates an evaluation run in `AGENT_SANDBOX`.
- Evaluates regression benchmarks: if the candidate skill achieves $\ge 95\%$ benchmark pass rate with no regressions, it is emitted as an `EvolutionProposal`.
- In production, proposals are reviewed by human engineers or merged automatically if full autonomous evolution is unlocked.

---

### 3.6. Federated Hermes Network (`hermes.platform.federation.orchestrator.FederatedOrchestrator`)

#### 3.6.1. Node Discovery
- Nodes maintain an active directory (`FederatedAgentDirectory`) mapping peer node IDs to ANP endpoint URIs, latency metrics, and advertised capabilities.

#### 3.6.2. Mutual 3-Way HMAC Handshake Protocol
```
Node A (Initiator)                               Node B (Responder)
       |                                                 |
       |  1. INITIATE: Nonce_A, Node_A_ID, Timestamp     |
       |------------------------------------------------>|
       |                                                 | (Verifies Node_A,
       |                                                 |  generates Nonce_B,
       |                                                 |  computes HMAC(K, Nonce_A || Nonce_B))
       |  2. CHALLENGE_RESP: Nonce_B, HMAC_AB            |
       |<------------------------------------------------|
       |                                                 |
(Verifies HMAC_AB,                                       |
 computes HMAC(K, Nonce_B || Nonce_A))                   |
       |  3. ESTABLISHED: HMAC_BA                        |
       |------------------------------------------------>|
       |                                                 | (Verifies HMAC_BA,
       |                                                 |  transitions to ESTABLISHED)
       v                                                 v
[Secure Cryptographic Session Established for ANP Envelopes]
```

---

### 3.7. Control Plane & User Interface

#### 3.7.1. Dashboard Web UI
- Reactive Web interface providing real-time operational views:
  1. **Kanban Board:** Multi-lane view tracking task lifecycle from ingestion to completion.
  2. **Teams & Postures:** Visualizer of active agent postures, token utilization, and health.
  3. **Lane Kilo & Merge Queue:** Interactive queue display showing rebases, blast-radius graphs, and merges.
  4. **Ouroboros Ledger:** Active evolutionary proposals, evaluation benchmarks, and skill candidate histories.
  5. **Federated Mesh:** Node graph visualizing peer links, latencies, and active remote tasks.
  6. **Memory & Obsidian:** Scoped facts explorer and Obsidian vault sync status.

#### 3.7.2. Operational CLI (`hermes haos`)
- `hermes haos status [--json]`: Comprehensive state inspection of lanes, queues, models, and mesh.
- `hermes haos federation ping <peer_id>`: Verifies 3-way handshake and measuring round-trip latency.
- `hermes haos skills list`: Lists all registered skills, versions, and lifecycle stages.
- `hermes haos skills promote <skill_id>`: Manually promotes candidate skill to active production.
- `hermes haos dispatch`: Forces immediate dispatch of queued tasks to available postures.
- `hermes haos constructor <objective>`: Interactively breaks down complex project goals into structured Kanban tasks.

---

## 4. Contract Verification Matrix

| Architecture Invariant | Platform Implementation Module | Contract Test Assertion |
|---|---|---|
| Memory Fabric Scopes & Dedup | `hermes.platform.context.memory.federated_fabric` | `test_memory_fabric_scopes_and_deduplication` |
| Memory Supersession Lineage | `hermes.platform.context.memory.federated_fabric` | `test_memory_supersession_lineage` |
| Procedural Skills & SemVer | `hermes.platform.skills.procedural_engine` | `test_procedural_skills_semver_lifecycle` |
| Universal Capability Registry | `hermes.platform.capabilities.universal_registry` | `test_universal_capability_registry` |
| Axiom Model != Provider | `hermes.platform.models.unified_fabric` | `test_model_not_equal_provider_axiom` |
| ExactModelFailoverRouter (No Silent Degradation) | `hermes.platform.models.unified_fabric` | `test_exact_model_router_zero_degradation` |
| Posture Assignments | `hermes.platform.models.unified_fabric` | `test_posture_model_assignments` |
| Protocol Wire Bus & Envelopes | `hermes.platform.protocols.unified_bus` | `test_protocol_envelope_wire_bus` |
| HMAC-SHA256 & Trust Boundaries | `hermes.platform.protocols.unified_bus` | `test_trust_boundaries_and_signatures` |
| Workspace Fabric & Blast Radius | `hermes.platform.workspaces.automerge` & `merge_queue` | `test_workspace_lane_kilo_merge_queue` |
| Ouroboros Self-Evolution Loop | `hermes.platform.evolution.ouroboros_lifecycle` | `test_ouroboros_lifecycle_closed_loop` |
| Federated Hermes 3-Way Handshake | `hermes.platform.federation.orchestrator` | `test_federated_hermes_3way_handshake` |
| Control Plane & HAOS CLI | `hermes_cli.haos_cmd` | `test_control_plane_and_cli_contracts` |
| PEP-420 Namespace Compliance | Repository Filesystem (`hermes/platform/`) | `test_pep420_no_init_in_platform` |
| Standard Library Only In Platform | AST Analysis (`hermes/platform/`) | `test_stdlib_only_in_platform_core` |

---

## 5. Revision & Governance History

- **v1.0.0 (2026-09-07):** Initial canonical system specification ratified in ADR-001. All invariants validated 100% green against platform contract test suite.
