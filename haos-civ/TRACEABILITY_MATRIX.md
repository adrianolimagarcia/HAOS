# HAOS Civilization — Matriz de Rastreabilidade

Esta matriz conecta intenção arquitetural a artefatos verificáveis. O agente deve usar esta tabela para impedir implementação sem spec ou spec sem teste.

| ID | Capability/Invariante | Specs primárias | Implementação alvo | Evidência de teste/gate |
|---|---|---|---|---|
| F0-01 | mapa real do repo | README §5; runtime integration mapping | nenhum código ainda | baseline + symbols/owners registrados |
| V1-01 | Bot identity persistente | `IDENTITY_VERSIONING_SPEC.md`; V1 RFC | identity models/repository | round-trip + legacy compat |
| V1-02 | resolver único | `runtime/IDENTITY_ENGINE_SPEC.md`; identity services | IdentityResolver | path safety + cache isolation |
| V1-03 | sessão congela identidade | identity state-machine; master plan | agent bootstrap/system prompt | old session/new version test |
| V1-04 | Leaf temporário e rastreável | `LEAF_EXECUTION_PROTOCOL.md` | ShadowLeaf create/run/cleanup | lineage + retry/cancel/restart |
| V1-05 | event envelope | `EVENT_SYSTEM_SPEC.md`; event bus schema | event store/outbox/inbox | duplicate/reorder/replay |
| V1-06 | canonical vs projection | kernel state/event sourcing | repositories/projections | delete+rebuild equality |
| V1-07 | Council first-class | council engine + V1 RFC | CouncilSpec/Manager/Session | independent positions |
| V1-08 | decisão auditável | council protocol + API contracts | DecisionRecord | reproduce inputs/versions/evidence |
| V1-09 | policy separada | security + governance | action gate | unanimous Council denied by policy |
| V1-10 | observabilidade E2E | `OBSERVABILITY_SPEC.md` | traces/metrics/log fields | Council→Leaf→Decision trace |
| V1-11 | rollback | migrations + acceptance gates | feature flags/down path | flag-off legacy equivalence |
| V2-01 | relation graph temporal | V2 data model | relation events/projection | historical query + replay |
| V2-02 | reputation evidence-backed | reputation engine | scorer/projection | recompute from evidence |
| V2-03 | roles temporais | V2 RFC; collaboration model | selector | explanation contains factors |
| V2-04 | debate preserva independência | debate-flow | phase FSM | no pre-synthesis contamination fixture |
| V2-05 | dissent preservation | consensus | DecisionRecord fields | minority view survives synthesis |
| V2-06 | delegation budget-aware | task delegation; agent economy | scheduler/delegator | duplicate delegation deduped |
| V3-01 | experience is evidence | V3 evolution engine | ExperienceEvent/consolidator | repeated consolidation idempotent |
| V3-02 | evolution is proposal | V3 RFC | EvolutionProposal repository/FSM | reject leaves zero mutation |
| V3-03 | version apply atomic | identity versioning + V3 engine | apply service | crash mid-apply no double version |
| V3-04 | rollback preserves history | identity versioning | compensating version | previous sessions retain refs |
| V3-05 | lineage explicit | V3 lineage model | LineageEdge/fork service | parent/reason/policy query |
| V3-06 | lifecycle governed | kernel lifecycle | state machine | invalid transition denied |
| V4-01 | memory typed + provenance | V4 civilization memory | memory store/projections | compaction keeps source refs |
| V4-02 | conflicting facts coexist | V4 world model; KG | fact/edge model | contradictory facts queryable |
| V4-03 | world model rebuildable | world model engine | projection builder | wipe/reindex/replay |
| V4-04 | constitution versioned | constitution spec; governance kernel | policy bundle/version | action stores policy version |
| V4-05 | policy enforcement external | security/governance | policy engine/action executor | prompt injection cannot bypass |
| V4-06 | maintenance idempotent | civilization engine | scheduled jobs | same job key no duplicate effect |
| X-01 | secrets isolated | threat model/security boundaries | tool/secret broker | snapshot/log scan |
| X-02 | performance bounded | master plan/test harness | hot paths | baseline regression thresholds |
| X-03 | recovery rehearsed | failure recovery runbook | ops tooling | kill/restart/replay exercise |

## Como usar

Para cada task no código, associe ao menos um ID desta matriz no PR/status. Se uma task não possui ID, classifique-a como suporte/bugfix ou atualize esta matriz. Se um ID não tem teste objetivo, ele não está pronto para gate.

## Rastreabilidade por versão

### V1
Primary proof set: V1-01..V1-11 + X-01..X-03 aplicáveis.

### V2
V1 continua verde; adicionar V2-01..V2-06.

### V3
V1/V2 continuam verdes; adicionar V3-01..V3-06.

### V4
Todos os anteriores + V4-01..V4-06 + full recovery/security/performance run.

## Artefatos de evidência sugeridos

Em CI ou artifacts de PR, preserve quando possível:
- junit/test report;
- migration up/down log;
- replay state digest antes/depois;
- idempotency duplicate-run result;
- representative trace JSON sem segredo;
- benchmark JSON baseline/final;
- policy deny fixture;
- failure injection log;
- status/handoff Markdown.
