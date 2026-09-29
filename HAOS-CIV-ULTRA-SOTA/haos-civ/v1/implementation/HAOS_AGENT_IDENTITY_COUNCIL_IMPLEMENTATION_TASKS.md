# HAOS V1 — Implementation Tasks

## P0 — Contratos e persistência

- TASK-001: definir dataclasses/Pydantic models e schemas de `BotIdentityBundle`, `IdentityVersion`, `LeafIdentitySnapshot`, `CouncilSpec`, `CouncilSession`, `DecisionRecord` e EventEnvelope.
- TASK-002: migrations/tabelas/índices + repositories.
- TASK-003: event outbox/inbox e correlation IDs.

## P0 — Identidade

- TASK-010: `IdentityResolver` com path safety, hashes e cache.
- TASK-011: suporte opcional em BotSpec.
- TASK-012: bootstrap no Agent sem mutar sessão ativa.
- TASK-013: CLI/API `identity show/diff/versions`.

## P0 — Leafs

- TASK-020: snapshot imutável.
- TASK-021: temporary SOUL builder determinístico.
- TASK-022: trace/result/evidence refs.
- TASK-023: cancellation/retry/cleanup idempotente.

## P0 — Council

- TASK-030: CouncilSpec/Manager.
- TASK-031: session runtime + budgets.
- TASK-032: independent positions.
- TASK-033: synthesis/dissent capture.
- TASK-034: DecisionRecord + action gate.

## P1 — Memória e UX

- TASK-040: memory namespace por Bot/Council.
- TASK-041: inspector WebUI/CLI Council→Decision→Leaf.
- TASK-042: feature flags/canary controls.

## DoD por task

Cada task inclui migration/schema se necessário, trace IDs, erro tipado, test curto de happy+failure, doc atualizada e rollback path. Não bloquear a fase em test matrix enorme; priorizar invariantes e replay/idempotência.


## Harness de execução e qualidade

O plano deve ser implementado em fatias verticais pequenas, cada uma atravessando modelo de dados, serviço, evento, persistência, API/CLI e observabilidade. O objetivo não é maximizar quantidade de testes, e sim impedir classes inteiras de regressão com gates baratos.

**Gates mínimos por fatia:**

- schema e migração aplicam e revertem;
- serialização round-trip;
- replay de eventos produz o mesmo estado;
- retry não duplica efeito;
- crash/restart preserva invariantes;
- feature flag permite rollback funcional;
- logs/traces carregam `bot_id`, `council_id`, `leaf_id`, `decision_id` quando aplicável;
- snapshot de prompt/identidade permanece estável durante a sessão;
- uma simulação E2E curta prova o caminho feliz e um caso de falha.

**Estratégia de rollout:** `shadow -> opt-in -> canary -> default-on -> remove-legacy`. Cada transição possui métrica de promoção e condição explícita de rollback.

## Apêndice prático obrigatório

Antes de implementar este componente, o agente deve: localizar o símbolo real no commit alvo; registrar dependências; definir owner do estado; decidir o que é canonical vs projection; documentar idempotency key; definir restart/retry semantics; propagar correlation IDs; indicar feature flag; descrever rollback; e adicionar um teste curto que demonstre uma falha relevante além do happy path.

A implementação deve preferir contratos pequenos e explícitos. Nenhum componente pode depender de texto de prompt para autorização. Nenhuma projeção derivada pode se tornar fonte única de verdade. Qualquer cache usa chave com revisão/versionamento para impedir mistura de identidades ou estado obsoleto.


## Harness de execução e qualidade

O plano deve ser implementado em fatias verticais pequenas, cada uma atravessando modelo de dados, serviço, evento, persistência, API/CLI e observabilidade. O objetivo não é maximizar quantidade de testes, e sim impedir classes inteiras de regressão com gates baratos.

**Gates mínimos por fatia:**

- schema e migração aplicam e revertem;
- serialização round-trip;
- replay de eventos produz o mesmo estado;
- retry não duplica efeito;
- crash/restart preserva invariantes;
- feature flag permite rollback funcional;
- logs/traces carregam `bot_id`, `council_id`, `leaf_id`, `decision_id` quando aplicável;
- snapshot de prompt/identidade permanece estável durante a sessão;
- uma simulação E2E curta prova o caminho feliz e um caso de falha.

**Estratégia de rollout:** `shadow -> opt-in -> canary -> default-on -> remove-legacy`. Cada transição possui métrica de promoção e condição explícita de rollback.

