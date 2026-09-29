# HAOS V1 — Migration Specification

## Sequência segura
1. adicionar tabelas/índices; 2. deploy capaz de operar com tabelas vazias; 3. dual-write/audit em shadow mode; 4. opt-in de um Bot cria IdentityVersion explicitamente; 5. comparar read models; 6. ativar Council por flag; 7. canary; 8. backfill somente se necessário.

Migration não inventa SOUL. Schema migration e data migration são separadas. Backfills grandes são batch, resumíveis e idempotentes, com cursor persistido. Downgrade funcional pode voltar ao read path legado sem destruir dados civilizacionais já gravados.

### Rollback
Kill switch troca leitura/execução para legado; tabelas novas permanecem intactas para investigação. Se clientes já dependem de novos campos, manter compatibility adapter durante rollback.

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
