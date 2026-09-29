# HAOS V3 — Implementation Tasks

- V3-001 ExperienceEvent schema + capture hooks.
- V3-002 lesson consolidator com provenance.
- V3-003 EvolutionProposal + diff format + risk class.
- V3-004 policy/approval state machine.
- V3-005 IdentityVersion activation for new sessions.
- V3-006 observation window/eval records.
- V3-007 rollback/promote commands.
- V3-008 lineage graph + export manifest.
- V3-009 lifecycle transitions.
- V3-010 UI de diff/evidence/lineage.
- V3-011 rate limits/cooldown/mutation budgets.
- V3-012 replay e migration hardening.


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

