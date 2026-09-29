# HAOS Civilization — Checklist One-Shot para Agente Implementador

> **Navegação canônica:** comece por [`README.md`](README.md). Este documento é uma especialização do mapa mestre; não deve ser usado isoladamente para pular barriers/gates.

## Antes de editar
- registrar commit/branch; ler AGENTS/instruções; revalidar Code Mapping; mapear símbolos reais e stores existentes.

## Por fatia
- contrato/schema; migration/repository; domain service; event/correlation; API/CLI/UI mínima; feature flag; failure path; observability; rollback note.

## Antes de merge
- legacy path intacto quando flag off; retry idempotente; restart recuperável; secrets ausentes de snapshot/log; prompt cache preservado; Council usa Bots distintos; Leaf prova parent + identity version + hashes; docs apontam código real.

## Gate entre versões
V2 só após replay/idempotency V1; V3 só após evidence/provenance V2; V4 só após canonical memory reconstruível e governance gate.

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
