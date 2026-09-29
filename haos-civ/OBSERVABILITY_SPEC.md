# HAOS Civilization — Observability Specification

## Trace hierarchy
`request/trigger -> civilization command -> council session -> member/leaf run -> model/tool -> synthesis -> policy/action`.

Atributos: correlation/session/bot/identity-version/council/leaf IDs, provider/model, toolset_hash, prompt_hash, memory_snapshot_ref, budget e status.

Métricas: Council latency/fanout/quorum; Leaf TTFB/total/cancel; EventBus lag/retries/DLQ; Memory retrieval latency e source mix; projection freshness; evolution proposal/canary/rollback; policy denials; rebuild duration.

Structured logs por padrão não armazenam corpo de prompt/memory. Debug capture é opt-in, escopado, com TTL e redaction. Hashes/references são preferidos.

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
