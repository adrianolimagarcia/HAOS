# HAOS Civilization — Failure & Recovery Runbook

## Cenários
**Crash durante Council:** retomar FSM da última fase persistida; inbox/idempotency evita repetir side effects.

**Leaf órfão:** heartbeat/lease expira, reconciler marca attempt órfão, cancela recursos e cria novo attempt somente conforme policy; logical leaf mantém lineage de attempts.

**Event backlog:** preservar append log, reduzir consumers não críticos, recuperar lag e reconstruir projections se necessário.

**Projection corrupta:** retirar projection do caminho decisório e reconstruir de canonical records/events.

**Identity drift:** hash em disco diferente da versão ativa gera evento; sessão atual permanece no snapshot antigo e novas sessões seguem policy de bloqueio/import/proposal.

**Memory index down:** fallback para FTS/canonical sem jamais pular ACL.

**Policy down:** ações críticas fail-closed; demais seguem degradation matrix.

**Disaster recovery:** restaurar canonical DB/event log, validar manifest, replay projections, rebuild FTS/vector/graph, validar active identity pointers e só então reabrir schedulers.

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
