# HAOS Civilization — Detailed Task Ledger

Este ledger complementa as task lists V1–V4 com dependências e critérios verificáveis. IDs são estáveis para status/PRs.

## EPIC F0 — Discovery
- **F0-01** congelar commit/branch e baseline de testes/perf.
- **F0-02** mapear BotSpec, Bot manager, ShadowLeaf, AIAgent, prompt, memory, DB, scheduler/event, API/WebUI.
- **F0-03** identificar canonical stores, projections e feature-flag mechanism.
- **F0-04** revisar threat boundaries existentes.
Exit: code map + baseline documentados.

## EPIC V1-A — Contracts/Persistence
- **V1-01** domain IDs/models/schema versions.
- **V1-02** BotSpec optional identity compatibility.
- **V1-03** migrations core.
- **V1-04** EventEnvelope + event store.
- **V1-05** outbox/inbox + dedupe.
- **V1-06** projection rebuild primitive.
Exit: B1/B2.

## EPIC V1-B — Identity
- **V1-10** IdentityResolver/path safety/hash.
- **V1-11** version repository + cache key.
- **V1-12** prompt bootstrap pre-session.
- **V1-13** active-session freeze/pending version.
- **V1-14** API/CLI inspect/diff.
Exit: B3.

## EPIC V1-C — Leaf
- **V1-20** LeafIdentitySnapshot.
- **V1-21** deterministic temporary SOUL builder.
- **V1-22** lifecycle events/status.
- **V1-23** cancellation/timeout/retry/restart.
- **V1-24** result/evidence linkage.
Exit: B4.

## EPIC V1-D — Council
- **V1-30** CouncilSpec/repository.
- **V1-31** CouncilSession FSM/budgets.
- **V1-32** independent positions.
- **V1-33** synthesis + dissent.
- **V1-34** DecisionRecord.
- **V1-35** policy/action boundary.
- **V1-36** inspector/tracing.
Exit: B5/V1.

## EPIC V2 — Society
- **V2-01** relationship event/model/projection.
- **V2-02** reputation vector + evidence + decay.
- **V2-03** selector/role assignments.
- **V2-04** debate phase FSM.
- **V2-05** delegation matching/budget/idempotency.
- **V2-06** social metrics/inspection.
Exit: V2 gate.

## EPIC V3 — Evolution
- **V3-01** ExperienceEvent pipeline.
- **V3-02** lesson candidate consolidation.
- **V3-03** EvolutionProposal model/FSM.
- **V3-04** validation/approval policy.
- **V3-05** atomic apply + new IdentityVersion.
- **V3-06** compensating rollback.
- **V3-07** LineageEdge/fork/export policy.
- **V3-08** Bot lifecycle transitions.
Exit: V3 gate.

## EPIC V4 — Civilization
- **V4-01** typed civilization memory.
- **V4-02** provenance-preserving compaction.
- **V4-03** temporal/disputed knowledge graph.
- **V4-04** rebuildable world-model projection.
- **V4-05** constitution/policy bundle versioning.
- **V4-06** policy enforcement/action gate integration.
- **V4-07** autonomous maintenance jobs.
- **V4-08** civilization health metrics/operator inspection.
Exit: V4 gate.

## EPIC X — Hardening
- **X-01** migration/replay/idempotency CI.
- **X-02** chaos/failure injection matrix.
- **X-03** security/adversarial fixtures.
- **X-04** performance baseline/regression.
- **X-05** backup/recovery rehearsal.
- **X-06** docs/code path reconciliation.
- **X-07** canary/default-on/legacy removal plan.

## Dependency shorthand
`F0 -> V1-A -> V1-B -> V1-C -> V1-D -> V1 gate -> V2 -> V2 gate -> V3 -> V3 gate -> V4 -> hardening/default-on`.

Hardening X-01..X-04 roda continuamente, não apenas no fim.
