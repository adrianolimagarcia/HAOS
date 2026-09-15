# HAOS ADR Registry

**Authoritative index of every Architecture Decision Record in this repository.**
If a document and this index disagree, this index is wrong — fix the index in the same change.

---

## 1. Why this file exists

The fork was grafted into this repository in a single commit (`06b52e9ab6`). That graft brought
**four independent document series**, each written by a different author, each starting its own
`ADR-001` numbering: 38 documents, 34 of which were present twice as byte-identical copies
(72 files in total). The result: `ADR-001-*` matched **six files across three different
documents**, `ADR-012-*` matched four files across two different documents, and a bare
`ADR-012` reference was unresolvable.

The series are not competing canons of the same thing — they cover two different **axes**:

| Axis | What it records | Canonical ID space |
|---|---|---|
| **Platform** | The HAOS platform architecture and its phase trajectory (SOTA spec → freeze → phases 2–7) | `ADR-NNN` |
| **Governance** | The design invariants and operating decisions that govern *how* HAOS is built and run | `GOV-NNN` |

`GOV-NNN` is deliberately **not** a superstring of `ADR-NNN` (`grep ADR-012` does not match
`GOV-012`), so the two spaces stay separable by search as well as by reading.

**Rule:** never reuse a number inside a namespace. `ADR-` is reserved for the platform series;
new governance decisions take the next free `GOV-` number.

---

## 2. Platform series — `ADR-NNN` (canonical)

Location: `docs/architecture/`. These are the documents the rest of the repository points at as
canonical — see §5 for the citing sites.

| ID | File | Title | Status | Date |
|---|---|---|---|---|
| ADR-001 | `docs/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md` | HAOS — Hermes Agent Operating System SOTA Multi-Agent Architecture | Accepted (Canonical SOTA Specification) | 2026-09-07 |
| ADR-002 | `docs/architecture/ADR-002-ARCHITECTURE-FREEZE-V0.1.md` | Architecture Freeze v0.1 — Phase 1 Platform Kernel | Accepted (Frozen v0.1 Contracts) | 2026-09-08 |
| ADR-003 | `docs/architecture/ADR-003-PHASE-2-TEAM-RUNTIME.md` | Phase 2 — Multi-Agent Team Runtime Architecture | Accepted (Canonical Phase 2) | 2026-09-08 |
| ADR-004 | `docs/architecture/ADR-004-PHASE-3-ADAPTIVE-INTELLIGENCE.md` | Phase 3 — Adaptive Intelligence Platform (Ouroboros SOTA) | Accepted (Canonical Phase 3) | 2026-09-08 |
| ADR-005 | `docs/architecture/ADR-005-PHASE-4-PROTOCOL-FEDERATION-RUNTIME.md` | Phase 4 — Universal Protocol Gateway & Federation Runtime | Accepted (Canonical Phase 4) | 2026-09-08 |
| ADR-006 | `docs/architecture/ADR-006-PHASE-5-CONTROL-PLANE.md` | Phase 5 — Comprehensive Control Plane & Team Graph UI | Accepted (Canonical Phase 5) | 2026-09-08 |
| ADR-007 | `docs/architecture/ADR-007-PHASE-6-7-HARDENING-SCALE.md` | Phase 6 & 7 — Production Hardening, Chaos Resilience & Distributed Scale | Accepted (Canonical Hardening) | 2026-09-08 |

The series is self-governing: each document declares `Governed By:` its predecessors
(`ADR-001 through ADR-00N`). `ADR-001` is the root of the chain and has no predecessor.

---

## 3. Governance series — `GOV-NNN` (canonical)

Location: `architecture/ADRs/`. Formed by merging the two series that were already one
continuous lineage: the **Architecture Agent** set (`ADR-001..015`, 2026-09-08) and the
**Approved** set (`ADR-016..019`, 2026-09-09). The Approved set numbering starts at 016 —
immediately after 015 — which is the evidence that these two are one series, not two.
Numbers are unchanged; only the namespace prefix was added.

The merged lineage carries **two header schemas**, and this registry does not pretend otherwise
(retrofitting `Version:`/`Authors:` onto 016–019 would invent provenance they never had):

| Sub-block | `Status:` | `Version:` | `Authors:` | `Governed By:` |
|---|---|---|---|---|
| GOV-001..015 (Architecture Agent) | `Accepted` | `1.0.0` | present | absent |
| GOV-016..019 (Approved) | `Approved` | absent | absent | present |

Every document in the series shares `Canonical ID`, `Status` and `Date`; those three are the
fields a new `GOV-` document must carry.

| ID | File | Title | Status | Date |
|---|---|---|---|---|
| GOV-001 | `GOV-001-Hermes-Kernel.md` | Hermes Kernel | Accepted v1.0.0 | 2026-09-08 |
| GOV-002 | `GOV-002-Task-Lifecycle.md` | Task Lifecycle | Accepted v1.0.0 | 2026-09-08 |
| GOV-003 | `GOV-003-Task-vs-Run.md` | Task vs Run | Accepted v1.0.0 | 2026-09-08 |
| GOV-004 | `GOV-004-Model-Provider-Separation.md` | Model/Provider Separation | Accepted v1.0.0 | 2026-09-08 |
| GOV-005 | `GOV-005-Posture-Model-Binding.md` | Posture Model Binding | Accepted v1.0.0 | 2026-09-08 |
| GOV-006 | `GOV-006-Capability-Architecture.md` | Capability Architecture | Accepted v1.0.0 | 2026-09-08 |
| GOV-007 | `GOV-007-Context-Isolation.md` | Context Isolation | Accepted v1.0.0 | 2026-09-08 |
| GOV-008 | `GOV-008-Memory-Fabric.md` | Memory Fabric | Accepted v1.0.0 | 2026-09-08 |
| GOV-009 | `GOV-009-Artifact-Communication.md` | Artifact Communication | Accepted v1.0.0 | 2026-09-08 |
| GOV-010 | `GOV-010-Worker-Lanes.md` | Worker Lanes | Accepted v1.0.0 | 2026-09-08 |
| GOV-011 | `GOV-011-SQLite-first.md` | SQLite-first | Accepted v1.0.0 | 2026-09-08 |
| GOV-012 | `GOV-012-Plugin-first.md` | Plugin-first | Accepted v1.0.0 | 2026-09-08 |
| GOV-013 | `GOV-013-Runtime-vs-LLM-Responsibilities.md` | Runtime vs LLM Responsibilities | Accepted v1.0.0 | 2026-09-08 |
| GOV-014 | `GOV-014-Multimodal-Delegation.md` | Multimodal Delegation | Accepted v1.0.0 | 2026-09-08 |
| GOV-015 | `GOV-015-Evolution-Governance.md` | Evolution Governance | Accepted v1.0.0 | 2026-09-08 |
| GOV-016 | `GOV-016-Anti-Drift-Topologies.md` | Anti-Drift Multi-Agent Topologies & Cognitive Governance | Approved | 2026-09-09 |
| GOV-017 | `GOV-017-Scheduled-Routines.md` | Scheduled Routine Contracts & Boundary Approvals | Approved | 2026-09-09 |
| GOV-018 | `GOV-018-Ultrawork-Execution.md` | Ultrawork (ulw) Outcome-First & Evidence-Driven Execution | Approved | 2026-09-09 |
| GOV-019 | `GOV-019-Step-Lifecycle-Guard.md` | Step-Level Lifecycle Guard & Deterministic Loop Control | Approved | 2026-09-09 |

---

## 4. Parallel series — Guild (non-canonical, preserved)

Location: `architecture/ADRs/parallel/guild/`. The **HAOS Architecture Guild** set (originally
`ADR-001..012`, 2026-09-08, `Status: Accepted (Foundational Invariant)`) restates — with its own
numbering — decisions that the governance series already carries. It is kept for provenance and
because two of its documents have no exact `GOV-` counterpart.

The set carries its own **`GUILD-NNN`** namespace, so `ADR-NNN` now means the platform series and
nothing else anywhere in this repository. The files were renamed from `ADR-NNN` in the same change
that turned the governance set into `GOV-NNN`; each document records its original ID in the
`Canonical ID` line of its header.

| Guild file | Canonical counterpart | Note |
|---|---|---|
| `parallel/guild/GUILD-001-HERMES-REMAINS-KERNEL.md` | GOV-001 | Same decision |
| `parallel/guild/GUILD-002-KANBAN-TASK-LIFECYCLE-AUTHORITY.md` | GOV-002 | Near — adds the Kanban-as-authority framing |
| `parallel/guild/GUILD-003-MODEL-PROVIDER-SEPARATION.md` | GOV-004 | Same decision, different number |
| `parallel/guild/GUILD-004-CAPABILITIES-INSTEAD-OF-TOOL-COUPLING.md` | GOV-006 | Same decision, different number |
| `parallel/guild/GUILD-005-TASK-RUN-SEPARATION.md` | GOV-003 | Same decision, different number |
| `parallel/guild/GUILD-006-ARTIFACT-DRIVEN-COMMUNICATION.md` | GOV-009 | Same decision, different number |
| `parallel/guild/GUILD-007-CONTEXT-ISOLATION-PROMPT-CACHING.md` | GOV-007 | Same decision |
| `parallel/guild/GUILD-008-MEMORY-FABRIC-ARCHITECTURE.md` | GOV-008 | Same decision |
| `parallel/guild/GUILD-009-EXACT-MODEL-FAILOVER-CIRCUIT-BREAKER.md` | GOV-005 | Near — failover/circuit-breaker detail has no exact counterpart |
| `parallel/guild/GUILD-010-WORKER-LANE-EXECUTION-MODEL.md` | GOV-010 | Same decision |
| `parallel/guild/GUILD-011-PLUGINS-EXTEND-UPSTREAM.md` | GOV-012 | Same decision, different number |
| `parallel/guild/GUILD-012-SQLITE-FIRST-OPERATIONAL-STORE.md` | GOV-011 | Same decision, different number |

Where a Guild document and its `GOV-` counterpart differ in detail, **the `GOV-` document is
normative.** Consolidating (or deleting) the Guild set is a separate content decision that this
registry does not make.

---

## 5. Citations that depend on the numbering

These are the sites that actually bind a number to a meaning. Changing a canonical ID means
updating every one of them.

| ID | Meaning required by the citing site | Site |
|---|---|---|
| ADR-001 | HAOS SOTA multi-agent architecture | `docs/architecture/HAOS_SYSTEM_SPEC.md`, `docs/haos/ARCHITECTURE.md`, `.hermes/obsidian_vault/architecture/` mirror, `tests/platform/test_canonical_adr_contract.py` |
| ADR-002 | Architecture Freeze v0.1 (the 10 frozen contracts) | `tests/platform/test_adr_002_freeze_contract.py`, `docs/architecture/MASTER-PLAN-EXECUTION-BLUEPRINT-P1-P2.md`, and the `# Canonical ADR-002 alias` comments in `hermes/platform/` |
| ADR-003 | Phase 2 Team Runtime | `docs/architecture/MASTER-PLAN-EXECUTION-BLUEPRINT-P1-P2.md` |
| ADR-006 | Phase 5 Control Plane & Team Graph | `audit_team_graph_haos.md`, `.haos/result.json` |
| GOV-001 | Hermes remains the kernel | `architecture/core-patches.md` |
| GOV-004 | Model ≠ Provider axiom | `hermes/platform/evals/golden_tasks.py` (G003) |
| GOV-010 | GenericWorkerLane contract | `hermes/platform/execution/lane_generic.py`, `hermes/platform/evals/golden_tasks.py` |
| GOV-012 | Plugin-first / minimum permanent core diff | `architecture/core-patches.md` |

---

## 6. Scope boundaries

- **`ADR-018`, `ADR-042`, `ADR-001-auth`, `ADR-100`, `ADR-200`** appear in `evals/graphrag_lite/`,
  `plugins/haos/dashboard/plugin_api.py` and the platform tests. Those are **illustrative fixture
  IDs** for a vault graph, not references to documents in this registry. `ADR-042` has no document
  at all. Do not "fix" them to match this table.
- **`evals/graphrag_lite/` also uses `ADR-001` and `ADR-002` as graph entity IDs** — synthetic vault
  notes under `obsidian://20-Architecture/ADR-00N.md` (`impl.py`, and the case descriptions in
  `cases/*.json`). Unlike the IDs above, these **do** collide in number with real documents in the
  platform series. They are still fixtures, living in a different URI space: resolve them by their
  `obsidian://` path, never by number.
- **`hermes/platform/memory/vault_fts.py`** mentions `ADR-018` only as an example string inside the
  docstring that illustrates trigram substring matching. It is neither a fixture nor a citation.
- **`distro/haos-linux/config/includes.chroot/etc/skel/.haos/obsidian_vault/adrs/ADR-001-haos-architecture.md`**
  is a fifth, runtime-scoped numbering: a *seed* ADR shipped inside the appliance vault and
  resolved by ID at runtime (`hermes/platform/memory/obsidian.py`). It is data, not repo
  documentation, and is intentionally outside this registry.
- **`architecture/change_requests/ACR-0001-INITIAL-FREEZE-V0.1.md`** uses the separate `ACR-NNN`
  scheme for architecture change requests. Not an ADR; not in this registry.
- **`docs/ADR.md`** is the *upstream* Hermes ADR file and is unrelated to HAOS ADRs.
