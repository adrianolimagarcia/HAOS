# HAOS V1 — Event Catalog

## Convenção
Namespace `civ.<aggregate>.<past-tense-event>`. Payload versionado independentemente das classes Python.

Eventos mínimos: `civ.bot.identity-version-created`, `civ.bot.identity-version-activated`, `civ.bot.identity-drift-detected`, `civ.leaf.created|started|completed|failed|canceled`, `civ.council.created|session-started|phase-changed|decision-recorded`, `civ.memory.recorded|superseded`, `civ.policy.denied|approval-required`.

Cada envelope inclui `event_id`, `stream_id`, `stream_seq`, `actor_id`, `causation_id`, `correlation_id`, `occurred_at`, `schema_version`. Prompt e memória completos não entram no evento; usar hashes/references. Mudança breaking incrementa schema e fornece upcaster. Evento histórico nunca é reescrito para corrigir projeção.

## Invariantes de implementação

- Identidade persistente e snapshots são versionados; prompt é projeção, não fonte de verdade.
- Council é multi-Bot; Leaf é temporário e deriva de um Bot pai.
- Eventos são imutáveis; projeções podem ser reconstruídas.
- Toda mutação relevante é idempotente e correlacionada.
- Evolução crítica é proposal + policy + nova versão, nunca self-write silencioso.
- Memória e conhecimento carregam provenance e escopo.
- Feature flags, canary e rollback fazem parte do desenho.


## Harness mínimo

Para cada fatia: validar schema/migration, round-trip, retry sem duplicação, crash/restart, feature flag, rollback, correlation IDs, ausência de secrets em logs e um E2E curto com happy path + falha representativa. Preferir poucos testes de invariantes a uma matriz enorme de testes superficiais.

## Checklist de implementação

1. localizar símbolo real no commit alvo; 2. definir owner do estado e contrato; 3. separar canonical de projection; 4. documentar idempotency/retry/restart; 5. propagar correlation; 6. criar feature flag; 7. documentar rollback; 8. validar compatibilidade legada.

## Nota de completude
Este documento é parte do plano executável e deve ser lido junto do `MASTER_IMPLEMENTATION_PLAN.md`. O implementador deve registrar qualquer desvio por ADR, manter compatibilidade, versionar contratos e preservar auditabilidade ponta a ponta.

## Nota de completude
Este documento é parte do plano executável e deve ser lido junto do `MASTER_IMPLEMENTATION_PLAN.md`. O implementador deve registrar qualquer desvio por ADR, manter compatibilidade, versionar contratos e preservar auditabilidade ponta a ponta.

## Nota de completude
Este documento é parte do plano executável e deve ser lido junto do `MASTER_IMPLEMENTATION_PLAN.md`. O implementador deve registrar qualquer desvio por ADR, manter compatibilidade, versionar contratos e preservar auditabilidade ponta a ponta.
