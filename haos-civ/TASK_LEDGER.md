# HAOS Civilization — Detailed Task Ledger

Este ledger complementa as task lists V1–V4 com dependências e critérios verificáveis. IDs são estáveis para status/PRs.

## EPIC F0 — Discovery [CONCLUÍDO]
- [x] **F0-01** congelar commit/branch e baseline de testes/perf.
- [x] **F0-02** mapear BotSpec, Bot manager, ShadowLeaf, AIAgent, prompt, memory, DB, scheduler/event, API/WebUI.
- [x] **F0-03** identificar canonical stores, projections e feature-flag mechanism.
- [x] **F0-04** revisar threat boundaries existentes.
Exit: code map + baseline documentados.

## EPIC V1-A — Contracts/Persistence [CONCLUÍDO]
- [x] **V1-01** domain IDs/models/schema versions (`packages/haos-civ/src/models.rs`, `hermes/platform/bots/identity.py`).
- [x] **V1-02** BotSpec optional identity compatibility.
- [x] **V1-03** migrations core.
- [x] **V1-04** EventEnvelope + event store (`CivEventStore` WAL + SQLite, `EventStore`).
- [x] **V1-05** outbox/inbox + dedupe.
- [x] **V1-06** projection rebuild primitive (`get_all()` replay).
Exit: B1/B2 aprovados.

## EPIC V1-B — Identity [CONCLUÍDO]
- [x] **V1-10** IdentityResolver/path safety/hash (`identity_resolver.rs` + `identity_resolver.py`).
- [x] **V1-11** version repository + cache key (`identity_manager.rs` + `identity_manager.py`).
- [x] **V1-12** prompt bootstrap pre-session (`prompt_builder.py` bot identity context).
- [x] **V1-13** active-session freeze/pending version.
- [x] **V1-14** API/CLI inspect/diff (`haos civ bots`, `haos civ show`).
Exit: B3 aprovado.

## EPIC V1-C — Leaf [CONCLUÍDO]
- [x] **V1-20** LeafIdentitySnapshot (`leaf_protocol.rs` + `leaf_protocol.py`).
- [x] **V1-21** deterministic temporary SOUL builder (`build_temporary_soul`).
- [x] **V1-22** lifecycle events/status.
- [x] **V1-23** cancellation/timeout/retry/restart.
- [x] **V1-24** result/evidence linkage.
Exit: B4 aprovado.

## EPIC V1-D — Council [CONCLUÍDO]
- [x] **V1-30** CouncilSpec/repository (`council_manager.rs` + `hermes/platform/council/`).
- [x] **V1-31** CouncilSession FSM/budgets.
- [x] **V1-32** independent positions.
- [x] **V1-33** synthesis + dissent.
- [x] **V1-34** DecisionRecord.
- [x] **V1-35** policy/action boundary.
- [x] **V1-36** inspector/tracing (`haos civ council`).
Exit: B5/V1 gate aprovado.

## EPIC V2 — Society [CONCLUÍDO]
- [x] **V2-01** relationship event/model/projection (`society.rs`, `society_manager.rs`, `hermes/platform/society/`).
- [x] **V2-02** reputation vector + evidence + decay (`ReputationVector`).
- [x] **V2-03** selector/role assignments (`RoleAssignment`, select_specialist).
- [x] **V2-04** debate phase FSM (`deliberate_with_dissent`).
- [x] **V2-05** delegation matching/budget/idempotency.
- [x] **V2-06** social metrics/inspection (`haos civ society`).
Exit: V2 gate aprovado.

## EPIC V3 — Evolution [CONCLUÍDO]
- [x] **V3-01** ExperienceEvent pipeline (`evolution.rs`, `bot_evolution.py`).
- [x] **V3-02** lesson candidate consolidation.
- [x] **V3-03** EvolutionProposal model/FSM (`EvolutionProposal`).
- [x] **V3-04** validation/approval policy (risk classes `identity-critical` / `values-critical`).
- [x] **V3-05** atomic apply + new IdentityVersion (stale base_version rejection).
- [x] **V3-06** compensating rollback (`rollback_proposal`).
- [x] **V3-07** LineageEdge/fork/export policy.
- [x] **V3-08** Bot lifecycle transitions.
Exit: V3 gate aprovado.

## EPIC V4 — Civilization [CONCLUÍDO]
- [x] **V4-01** typed civilization memory (`civilization.rs`, `hermes/platform/civilization/`).
- [x] **V4-02** provenance-preserving compaction.
- [x] **V4-03** temporal/disputed knowledge graph (`KnowledgeAssertion`).
- [x] **V4-04** rebuildable world-model projection.
- [x] **V4-05** constitution/policy bundle versioning (`ConstitutionVersion`, `ConstitutionRule`).
- [x] **V4-06** policy enforcement/action gate integration (`evaluate_action` hard-deny / advisory).
- [x] **V4-07** autonomous maintenance jobs.
- [x] **V4-08** civilization health metrics/operator inspection (`haos civ constitution`, `haos civ memory`).
Exit: V4 gate aprovado.

## EPIC X — Hardening [CONCLUÍDO]
- [x] **X-01** migration/replay/idempotency CI (Replay verificado em SQLite e EventStore).
- [x] **X-02** chaos/failure injection matrix (Stale base version, drift detection, path traversal guard).
- [x] **X-03** security/adversarial fixtures (Path traversal "..", anti-self-endorsement, constitution hard-deny).
- [x] **X-04** performance baseline/regression (Rust native C-ABI bridge + pure Python fallback).
- [x] **X-05** backup/recovery rehearsal (Append-only SQLite WAL + idempotent deduplication).
- [x] **X-06** docs/code path reconciliation (`IMPLEMENTATION_STATUS.md`, `TASK_LEDGER.md`).
- [x] **X-07** CLI control plane (`haos civ` subcommands: status, bots, council, society, memory, constitution).
- [x] **X-08** Live E2E lifecycle simulation (`tests/platform/civilization/test_civ_e2e_simulation.py` & `scripts/demo_civ_simulation.py` cobrindo os 7 estágios do ciclo civilizacional).

## Dependency shorthand
`F0 -> V1-A -> V1-B -> V1-C -> V1-D -> V1 gate -> V2 -> V2 gate -> V3 -> V3 gate -> V4 -> hardening/default-on`.

Hardening X-01..X-04 roda continuamente, não apenas no fim.
