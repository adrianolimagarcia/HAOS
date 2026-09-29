# HAOS Civilization — Implementation Status Template

> Copie para `IMPLEMENTATION_STATUS.md` no workspace de implementação. Atualize após cada fatia/barrier.

## Contexto
- Repository:
- Branch:
- Commit base:
- Commit atual:
- Agent/owner:
- Data/hora:
- Barrier atual: F0 / B1 / B2 / B3 / B4 / B5 / V1 / V2 / V3 / V4

## Baseline
- Test suite:
- TTFB/latency:
- Memory:
- Legacy behavior notes:
- Persistence version:

## Task atual
- Traceability ID:
- Objetivo:
- Specs lidas:
- Paths/symbols reais:
- Owner do estado:
- Canonical source:
- Projections/caches:
- Idempotency key:
- Correlation IDs:
- Security boundary:
- Feature flag:
- Rollback:

## Mudanças realizadas
- [ ] domain/contracts
- [ ] migration/storage
- [ ] services/runtime
- [ ] events
- [ ] API/CLI
- [ ] observability
- [ ] docs

## Evidências
- Unit:
- Integration:
- E2E:
- Retry/idempotency:
- Restart/recovery:
- Replay:
- Security:
- Performance:
- Migration up/down:
- Feature flag off:

## Decisões/ADR
- ADR criado/alterado:
- Razão:

## Riscos e pendências
- Blocking:
- Non-blocking:
- Debt intentionally accepted:

## Próximo passo exato
- Próxima task:
- Primeiro arquivo/símbolo a abrir:
- Dependência que precisa estar verde:

## Critérios para atualizar o status

Atualize este arquivo sempre que houver mudança de barrier, merge importante, migration nova, decisão de arquitetura, alteração de feature flag ou descoberta que invalide uma suposição do plano. Não deixe o status somente para o final da sessão: ele é a memória operacional do trabalho.

### Semântica sugerida
- `NOT_STARTED`: dependências ainda não satisfeitas.
- `READY`: dependencies verdes e Definition of Ready satisfeita.
- `IN_PROGRESS`: implementação ativa, ainda não mergeável.
- `BLOCKED`: bloqueio explícito com owner/próxima ação.
- `VERIFYING`: código concluído, gates em execução.
- `DONE`: DoD completo e evidência registrada.
- `DEFERRED`: deliberadamente fora do escopo, com justificativa.

## Ledger de tasks

| ID | Status | Owner | Commit/PR | Gate faltante | Observação |
|---|---|---|---|---|---|
| | | | | | |

## Mudanças de contrato desde o último checkpoint

Para cada alteração, anote consumidor impactado, compatibilidade e migration necessária. Se um contrato congelado em barrier mudou, reabra o barrier e rode novamente os contract/integration tests dependentes.

## Eventos / schemas alterados

| Event/schema | From | To | Compatibility | Replay impact |
|---|---|---|---|---|
| | | | | |

## Feature flags

| Flag | Default | Ambiente testado | Rollback validado? | Observação |
|---|---|---|---|---|
| | | | | |

## Último checkpoint recuperável

Registre o último commit no qual migrations, tests e rollback foram comprovadamente verdes. Se a sessão cair, o próximo agente deve iniciar por este ponto e comparar com o HEAD atual antes de continuar.
