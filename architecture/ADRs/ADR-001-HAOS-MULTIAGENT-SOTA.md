# ADR-001: HAOS — Hermes Agent Operating System SOTA Multi-Agent Architecture

- **Status:** Accepted (Canonical SOTA Specification)
- **Date:** 2026-09-07
- **Authors:** HAOS Core Architecture Guild & DeepSeek Harness Systems Engineering
- **Context:** HAOS Multi-Agent Runtime & Autonomous Software Engineering System
- **Supersedes:** Legacy Single-Agent Hermes Loop (2024-2025)

---

## 1. Context & Problem Statement

Modern autonomous software engineering requires multi-turn execution across diverse domains: repository navigation, AST-level code manipulation, security vetting, dynamic test execution, cross-session memory preservation, and multi-agent coordination.

Traditional single-agent architectures (including legacy Hermes 1.x) suffer from fundamental cognitive and operational bottlenecks:
1. **Context Window Saturation & Memory Fragmentation:** Episodic conversations either bloat token usage or discard critical architectural decisions, lacking structured scoping, deduplication, and supersession.
2. **Model Fragility & Silent Degradation:** Typical failover logic switches to inferior models (e.g. failing over from Claude 3.7 to an 8B model) when rate limits hit, silently degrading architectural reasoning and code generation quality.
3. **Unchecked Blast Radius & Workspace Pollution:** Agents operating directly on single working directories cause git index collisions, dirty states, and undetected regressions across distant modules.
4. **Tool Sprawl & Incoherent Capability Interfaces:** Tools, MCP servers, LSP daemons, multimodal workers, and shell environments lack uniform lifecycle management, health checking, and sandboxed isolation.
5. **Absence of Autonomous Self-Improvement:** Execution traces are discarded after run termination rather than mined for reusable procedural workflows.
6. **Isolated Node Silos:** Distributed agent collaboration is either non-existent or implemented via ad-hoc, unauthenticated HTTP payloads lacking cryptographic trust boundaries.

To solve these systemic challenges, this Architecture Decision Record establishes **HAOS (Hermes Agent Operating System)**: a fault-tolerant, deterministic, multi-agent operating system founded upon seven foundational pillars.

---

## 2. Decision Drivers & Architectural Invariants

The design of HAOS is governed by eight non-negotiable architectural invariants:

1. **Invariant 1: Ontological Separation — `Task != Run`**  
   A `Task` is an immutable specification of user intent with acceptance criteria. A `Run` is an ephemeral attempt by a worker agent within a leased workspace. Tasks survive worker crashes; runs are disposable.
2. **Invariant 2: Identity Separation — `Model != Provider`**  
   A model is an intellectual identity defined by parameter weights, reasoning style, and context capabilities. A provider is an execution venue (API endpoint, pricing, rate limit). Failover must strictly preserve model identity.
3. **Invariant 3: Triad of Agent Core**  
   Every autonomous agent is powered by the tight synthesis of three decoupled engines: (a) Scoped Memory Fabric, (b) Procedural Skills Engine with strict Semantic Versioning, and (c) Universal Capability Registry.
4. **Invariant 4: Cryptographic Trust Boundaries**  
   All inter-agent, inter-process, and network messages must traverse verified trust boundaries (`KERNEL` > `LOCAL_SECURE` > `AGENT_SANDBOX` > `FEDERATED` > `UNTRUSTED`) secured with HMAC-SHA256 signatures.
5. **Invariant 5: Zero-Regression Workspace Concurrency (Lane Kilo)**  
   No code is committed directly to the main branch. Changes occur in ephemeral Git worktrees, are analyzed via LSP blast-radius calculation, verified by independent reviewer agents, and merged serially via a FIFO/Priority Merge Queue.
6. **Invariant 6: Ouroboros Closed-Loop Self-Evolution**  
   All execution traces are ingested, analyzed for recurring procedural success or failure patterns, distilled into candidate `SkillSpec` definitions, benchmarked in sandboxed eval gates, and auto-promoted through the Merge Queue.
7. **Invariant 7: Mutual Cryptographic Federation**  
   Cross-node communication operates over the Agent Network Protocol (ANP) with a 3-way mutual HMAC handshake (`SYN` $\rightarrow$ `SYN-ACK` $\rightarrow$ `ACK`), establishing encrypted ephemeral session keys.
8. **Invariant 8: Unified Control Plane & Observable Truth**  
   System state is completely observable via distributed trace IDs, real-time WebSocket/REST metrics, Obsidian Markdown Vault synchronization, and the `hermes haos` operational CLI.

---

## 3. Detailed Architecture Specification

```
+========================================================================================+
|                                    HAOS CONTROL PLANE                                  |
|   +-------------------+  +-------------------------+  +----------------------------+   |
|   |   Web Dashboard   |  |   CLI ('hermes haos')   |  |  EventStore & Telemetry    |   |
|   +-------------------+  +-------------------------+  +----------------------------+   |
+========================================================================================+
                                            |
                                            v
+========================================================================================+
|                                  PROTOCOL FABRIC                                       |
|             Unified Wire Bus  |  CrossProtocolBridge  |  HMAC-SHA256 Signatures        |
|   [KERNEL] -----> [LOCAL_SECURE] -----> [AGENT_SANDBOX] -----> [FEDERATED] -> [UNTRUSTED] |
+========================================================================================+
                                            |
         +----------------------------------+----------------------------------+
         |                                  |                                  |
         v                                  v                                  v
+=======================+        +=======================+        +=======================+
|   AGENT CORE TRIAD    |        | MODEL & PROVIDER      |        |  WORKSPACE FABRIC     |
|                       |        | FABRIC                |        |  (LANE KILO)          |
| 1. Memory Fabric      |        |                       |        |                       |
|    - 4 Scopes         |        | - Model != Provider   |        | - Ephemeral Worktrees |
|    - Deduplication    |        | - ExactModelRouter    |        | - LSP Blast Radius    |
|    - Supersession     |        | - Zero Degradation    |        | - AutoMergeGate       |
|    - Multi-Store Sync |        |                       |        | - Serial MergeQueue   |
|                       |        | Posture Matrix:       |        +=======================+
| 2. Procedural Skills  |        | - Architect: C 3.7    |                    |
|    - SkillSpec        |        | - Coder: DeepSeek-V3  |                    v
|    - SemVer 2.0       |        | - Reviewer: C 3.5     |        +=======================+
|    - Lifecycle Pipe   |        +=======================+        | OUROBOROS EVOLUTION   |
|                       |                                         |                       |
| 3. Universal Registry |                                         | - RunTrace Discovery  |
|    - MCP / LSP        |                                         | - Skill Generation    |
|    - Kilo / Modality  |                                         | - Sandbox Eval Gate   |
|    - Browser / Plugin |                                         | - Merge Auto-Promote  |
+=======================+                                         +=======================+
                                            |
                                            v
+========================================================================================+
|                               FEDERATED HERMES NETWORK                                 |
|    Node Discovery  |  Mutual 3-Way HMAC Handshake  |  ANP Wire Envelopes (Ed25519/HMAC)|
+========================================================================================+
```

---

### 3.1. The Triad of Agent Core

The agent core unifies three decoupled, resilient engines:

#### 3.1.1. Memory Fabric (`hermes.platform.context.memory`)
- **Strict Scoping Hierarchy:**
  * `private`: Ephemeral run context, local agent scratchpad, discarded upon task termination.
  * `team`: Shared memory between collaborative agents within a single posture execution (e.g., Coder and Reviewer).
  * `project`: Repository and workspace-wide persistent knowledge (conventions, build targets, architectural constraints).
  * `global`: System-wide cross-project facts, user preferences, and global invariant policies.
- **Content-Hash Deduplication:** Facts are indexed by canonical cryptographic content hashes (SHA-256). Redundant insertions of identical semantic statements are collapsed without storage explosion.
- **Temporal Supersession Chains:** When facts evolve, old facts are not blindly deleted. Instead, the new fact references `supersedes_id`, marking the predecessor as `superseded` while maintaining full historical auditability.
- **Multi-Store Synchronization:** Managed by `FederatedMemoryCoordinator`, synchronizing across four backends:
  1. *Hermes Memory Engine:* Fast key-value and vector index for in-turn retrieval.
  2. *Obsidian Vault:* Human-auditable Markdown files with YAML frontmatter located in `.hermes/obsidian_vault` (Canonical Human Truth).
  3. *GraphRAG:* Relational and conceptual entity graph connecting architectural components, APIs, and dependencies.
  4. *DecisionStore:* Chronological ledger of Architectural Decision Records (ADRs).

#### 3.1.2. Procedural Skills Engine (`hermes.platform.skills.procedural_engine`)
- **`SkillSpec` Data Model:** Structured specification containing:
  * Unique Identifier (`skill_id`), Name, Description.
  * Strict Semantic Versioning (`major.minor.patch`).
  * Trigger conditions (intent matching, file patterns, error signatures).
  * Executable action steps with parameter validation schemas.
  * Rollback and compensation logic.
  * Required capability tags and security permissions.
- **Lifecycle Pipeline:** Formal state machine governing skill maturation:
  $$\text{CANDIDATE} \longrightarrow \text{SANDBOX\_TEST} \longrightarrow \text{EVALUATED} \longrightarrow \text{ACTIVE} \longrightarrow \text{DEPRECATED} \longrightarrow \text{RETIRED}$$
- **Dynamic Registry (`SkillRegistry`):** Supports SemVer range queries (`^1.2.0`), collision prevention, deprecation schedules, and checksum verification.
- **Automated Skill Generation (`SkillGenerator`):** Analyzes recurring multi-step execution traces and synthesizes parameterized procedural workflows.

#### 3.1.3. Universal Capability Registry (`hermes.platform.capabilities.universal_registry`)
- **Unified Capability Abstraction:** Replaces fragmented tool registration by normalizing all capabilities under `CapabilityMetadata`:
  * `mcp:*`: Model Context Protocol servers (GitHub, PostgreSQL, Fetch, Filesystem).
  * `lsp:*`: Language Server Protocol daemons (Python Pyright/LSP, TypeScript TSServer, Rust Analyzer).
  * `kilo:*`: Workspace git worktree management and merge facilities.
  * `modality:*`: Multimodal sensory engines (Vision Analyzer, Audio Transcriber).
  * `browser:*`: Sandboxed Playwright browser drivers.
  * `plugin:*`: Hermes dynamic ecosystem plugins.
- **Sandbox Isolation Policies:**
  * `STRICT_SANDBOX`: Zero filesystem and egress access (Wasm/Seccomp/Docker).
  * `CONTAINER`: Isolated container with mounted ephemeral worktree.
  * `PROCESS_ISOLATED`: Subprocess with restricted environment variables and resource limits.
  * `TRUSTED_HOST`: Native kernel invocation reserved for verified internal platform modules.
- **Dynamic Health Checking:** Active probing via `HealthStatus` preventing broken tools from poisoning the agent prompt.

---

### 3.2. Model & Provider Fabric

#### 3.2.1. Architectural Axiom: `Model != Provider`
A fundamental architectural tenet of HAOS is the strict decoupling of **Model Identity** from **Provider Venue**:
- **Model Identity:** The immutable intelligence profile (architecture, weights, parameter size, tokenizer, training cutoff, native reasoning token mechanics). Examples: `anthropic/claude-3-7-sonnet`, `deepseek/deepseek-chat-v3`.
- **Provider Venue:** The physical or virtual API endpoint providing inference execution (Anthropic Direct, OpenRouter, AWS Bedrock, DeepSeek Direct, Groq, Local vLLM/Ollama). Each venue has distinct latency, rate limits, pricing, and error modes.

#### 3.2.2. ExactModelFailoverRouter (Zero Silent Degradation)
Legacy routers degrade to smaller, cheaper models when encountering rate limits or outages (e.g. falling back from Sonnet to Haiku). This causes critical reasoning failure in complex coding tasks.

`ExactModelFailoverRouter` enforces deterministic failover:
1. When Provider A for Model $M_1$ trips its circuit breaker (e.g., Anthropic API 429/500), the router fails over **strictly** to Provider B hosting the **exact same Model Identity** $M_1$ (e.g., OpenRouter `anthropic/claude-3-7-sonnet` or AWS Bedrock `anthropic.claude-3-7-sonnet-v1:0`).
2. If all providers for $M_1$ are exhausted, the router raises `ExactModelRoutingExhausted` rather than silently degrading to an inferior model, unless explicit multi-model substitution is explicitly configured (`substitute_allowed=True`).

#### 3.2.3. Posture-Based Agent Assignments
Agents are instantiated with specialized operational postures mapped to optimal model identities:
- **`architect` $\rightarrow$ Claude 3.7 Sonnet:** Deep reasoning, structural decomposition, invariant formulation, and architectural synthesis.
- **`coder` $\rightarrow$ DeepSeek-V3 / DeepSeek-R1:** High-throughput syntax synthesis, AST refactoring, rigorous implementation of complex logic.
- **`reviewer` $\rightarrow$ Claude 3.5 Sonnet / Claude 3.7:** Adversarial critique, security audit, blast-radius verification, and test adequacy assertion.

---

### 3.3. Protocol Fabric & Cryptographic Trust Boundaries

#### 3.3.1. Unified Wire Bus & Envelopes (`hermes.platform.protocols.unified_bus`)
All communication—whether intra-process, inter-process, or cross-network—is wrapped in canonical `ProtocolEnvelope` records:
- `envelope_id`: Unique UUIDv4 identifier.
- `timestamp`: Monotonic UTC epoch timestamp.
- `protocol_type`: Protocol discriminator (`ACP`, `ANP`, `A2A`, `INTERNAL_BUS`).
- `sender` & `recipient`: Fully qualified agent identities (`node_id:agent_id`).
- `payload`: Structured JSON payload.
- `payload_hash`: SHA-256 digest of normalized payload bytes.
- `signature`: HMAC-SHA256 signature generated with the sender's verified secret.
- `trust_boundary`: Assigned privilege level.

#### 3.3.2. Hierarchical Trust Boundaries
HAOS enforces a five-tier unidirectional trust hierarchy:
$$\text{KERNEL} \succ \text{LOCAL\_SECURE} \succ \text{AGENT\_SANDBOX} \succ \text{FEDERATED} \succ \text{UNTRUSTED}$$

- **`KERNEL`:** Unrestricted orchestrator privilege (Kanban task state, SQLite session store, process lifecycle).
- **`LOCAL_SECURE`:** Trusted local daemons (Obsidian Vault, Keyring / Secret Broker).
- **`AGENT_SANDBOX`:** Isolated worker processes executing inside ephemeral worktrees or Docker containers.
- **`FEDERATED`:** Remote authenticated peer nodes within the mesh.
- **`UNTRUSTED`:** Inbound external webhooks, unverified internet endpoints, third-party MCP servers.

**Boundary Invariant:** An envelope with a lower trust level cannot directly invoke handlers at a higher trust level without passing through an explicit validation and sanitization gate in `CrossProtocolBridge`. Tampered signatures or mismatched digests trigger immediate rejection with `ProtocolSecurityError`.

#### 3.3.3. CrossProtocolBridge
Provides seamless message translation between:
- **ACP (Agent Client Protocol):** Editor integration (VSCode, JetBrains, Zed).
- **ANP (Agent Network Protocol):** Node-to-node federated mesh communication.
- **A2A (Agent-to-Agent):** Standardized external inter-agent delegation protocols.
- **Internal Bus:** In-memory asynchronous message dispatch.

---

### 3.4. Workspace Fabric & Lane Kilo

#### 3.4.1. Ephemeral Git Worktrees (`hermes.platform.workspaces.git_worktree`)
To eliminate Git lock collisions and workspace corruption:
- Each Coder agent receives an isolated, ephemeral Git worktree created from `origin/main` on a dedicated branch (`kilo/<task_id>-<run_id>`).
- Worktrees are scrubbed and reclaimed automatically upon run completion or failure.

#### 3.4.2. LSP Blast-Radius Calculation (`hermes.platform.capabilities.lsp.unified_intelligence`)
Before code changes can be considered for integration, `AutoMergeGate` invokes LSP Unified Intelligence:
1. Computes the unified git diff of the worktree branch against `main`.
2. Parses AST definitions to identify modified classes, methods, and functions.
3. Queries Language Server Protocol (Pyright/TSServer) for inbound call hierarchies and symbol references.
4. Calculates the **Blast Radius**: the complete set of source modules and existing test files dependent upon modified symbols.
5. Injects the computed blast radius into the test runner, guaranteeing that all transitively affected tests are executed alongside targeted unit tests.

#### 3.4.3. AutoMergeGate & MergeQueue (`hermes.platform.workspaces.merge_queue`)
- **`AutoMergeGate`:** Asserts three preconditions:
  1. Zero test failures across the full blast radius.
  2. Cryptographically signed review approval from a `reviewer` agent.
  3. Clean mergeability without manual conflict resolution markers.
- **`MergeQueue`:** FIFO/Priority ordered queue that:
  1. Re-bases candidate branches serially against the current tip of `main`.
  2. Re-executes the test suite against the rebased worktree.
  3. Merges atomically into `main` (fast-forward or squash), advancing Lane Kilo.

---

### 3.5. Ouroboros Closed-Loop Self-Evolution

The Ouroboros engine (`hermes.platform.evolution.ouroboros_lifecycle`) transforms HAOS into a self-improving system:

1. **Trace Ingestion:** `RunTrace` telemetry aggregates detailed `TraceSpan` records (tool calls, arguments, outputs, model reasoning tokens, errors).
2. **Failure Analysis & Opportunity Mining:** `OuroborosAnalyzer` categorizes run failures across 11 deterministic categories (e.g. `PROVIDER_ERROR`, `TOOL_TRANSIENT`, `BLAST_RADIUS_VIOLATION`) and identifies repetitive manual workflows.
3. **Candidate Skill Generation:** `SkillGenerator` synthesizes parameterized `SkillSpec` definitions from high-confidence successful traces.
4. **Sandbox Evaluation Gate:** Candidate skills are loaded into an isolated test environment and evaluated against regression benchmarks.
5. **Promotion & Integration:** Validated skills are submitted to the `SkillRegistry` and checked into the codebase via the `MergeQueue`. In *Shadow Mode*, proposals are logged for human audit before activation.

---

### 3.6. Federated Hermes Network

Distributed collaboration between independent HAOS nodes is governed by `FederatedOrchestrator`:

1. **Decentralized Node Discovery:** Nodes register endpoint URLs, cryptographic public keys, and advertised capability sets in `FederatedAgentDirectory`.
2. **Mutual 3-Way HMAC Handshake:**
   - **Step 1 (`INITIATE`):** Initiator sends a cryptographic nonce $N_A$ signed with pre-shared node identity key.
   - **Step 2 (`CHALLENGE_RESPONSE`):** Receiver validates $N_A$, generates nonce $N_B$, computes $\text{HMAC}(K, N_A \parallel N_B)$, and replies.
   - **Step 3 (`ESTABLISHED`):** Initiator verifies response, sends $\text{HMAC}(K, N_B \parallel N_A)$, and transitions session to `ESTABLISHED`.
3. **Cryptographic ANP Wire Envelopes:** Inter-node task delegation, remote workspace execution, and memory replication travel inside signed ANP envelopes with replay prevention.

---

### 3.7. Control Plane & Dashboard

The HAOS operational interface provides total visibility:

1. **Real-Time Web Dashboard:** Built with reactive components displaying:
   - **Kanban Board:** Task statuses, active worker assignments, run counts, acceptance status.
   - **Teams & Postures:** Active agent roles (`architect`, `coder`, `reviewer`), model allocations, health.
   - **Lane Kilo & Merge Queue:** Active Git worktrees, pending merge items, rebase status.
   - **Ouroboros Evolution Ledger:** Candidate skills, evaluation results, shadow-mode proposals.
   - **Federated Mesh:** Discovered peer nodes, active handshake sessions, remote task status.
   - **Memory & Obsidian:** Scoped facts, deduplication metrics, supersession trees, Obsidian vault sync.
2. **`hermes haos` Operational CLI:**
   - `hermes haos status [--json]`: Complete system state inspection.
   - `hermes haos federation ping <peer_id>`: Probe mesh connectivity and handshake verification.
   - `hermes haos skills list`: Enumerate registered procedural skills and versions.
   - `hermes haos skills promote <skill_id>`: Promote candidate skills from shadow mode to active production.
   - `hermes haos dispatch`: Trigger autonomous lane execution.
   - `hermes haos constructor <objective>`: Scaffold and structure autonomous engineering projects.

---

## 4. Consequences & Trade-offs

### 4.1. Positive Consequences
- **Deterministic Reliability:** Zero unverified code commits, elimination of silent model degradation, and guaranteed test coverage over transitive blast radiuses.
- **Cognitive Scalability:** Autonomous specialization across postures allows each agent to operate with clean context windows and optimal model profiles.
- **Auditability:** Complete historical traceability through Obsidian Markdown Vault, immutable event ledgers, and signed wire envelopes.
- **Continuous Growth:** Self-evolving skill discovery ensures system capabilities compound over time without manual code intervention.

### 4.2. Operational Trade-offs & Mitigations
- **Increased Test Latency:** Running full blast-radius test suites and serial merge re-validation adds merge queue latency.  
  *Mitigation:* Parallel test execution with `run_tests_parallel.py` and intelligent LSP dependency pruning.
- **Multi-Model API Overhead:** Maintaining active provider keys across Anthropic, DeepSeek, and OpenRouter requires key management.  
  *Mitigation:* Unified `SecretBroker` with automated fallback validation.

---

## 5. Architectural Compliance & Validation

This ADR is backed by an automated canonical contract test suite:
- **Test File:** `tests/platform/test_canonical_adr_contract.py`
- **Verification Mandate:** Every invariant defined herein is asserted programmatically against active platform implementations.
- **Compliance Rules:**
  1. Strictly stdlib-only imports in core platform modules.
  2. Strictly PEP-420 namespace compliance (zero `__init__.py` files in `hermes/platform/`).
  3. 100% green execution under `scripts/run_tests.sh`.
