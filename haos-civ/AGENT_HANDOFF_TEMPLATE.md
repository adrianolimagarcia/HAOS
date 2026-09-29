# HAOS Civilization — Agent Handoff Template

Use este documento para entregar trabalho entre agentes ou encerrar uma fase. O objetivo é eliminar dependência da conversa original.

## 1. Identificação
- Repo / remote:
- Branch:
- Base SHA:
- Head SHA:
- PR(s):
- Barrier alcançado:
- Feature flags/defaults:

## 2. O que foi concluído
Liste por Traceability ID e link para commit/PR. Não use “feito” sem evidência.

## 3. O que não foi concluído
Para cada item: motivo, risco, dependência, próxima ação concreta.

## 4. Mudanças de arquitetura
- canonical state decisions;
- event/schema decisions;
- lifecycle/idempotency decisions;
- security/policy decisions;
- ADR links.

## 5. Persistência/migrations
- migrations adicionadas;
- up/down test status;
- backfill;
- compatibility window;
- data-loss risks.

## 6. Testes executados
| Suite/scenario | Resultado | Evidência |
|---|---|---|
| contracts | | |
| migration | | |
| integration | | |
| E2E | | |
| replay | | |
| retry/idempotency | | |
| restart/chaos | | |
| security | | |
| performance | | |

## 7. Benchmarks
Baseline vs current para caminhos afetados. Indique ambiente para evitar comparação falsa.

## 8. Rollback
Passos exatos para desabilitar feature, reverter deployment e/ou migration. Explique side effects externos que exigem compensation.

## 9. Observabilidade
Inclua um exemplo sanitizado de trace/correlation que permita seguir Council→Bot→Leaf→Decision→Action, se a fase já tiver esses componentes.

## 10. Riscos conhecidos
Classifique: correctness, data integrity, security, performance, operability, compatibility.

## 11. Próximo agente
- primeiro documento a ler: [`README.md`](README.md)
- status file atual:
- próxima Traceability ID:
- primeiro arquivo/símbolo real a abrir:
- comando/teste recomendado antes de editar:

## 12. Gate verdict
Marque apenas com evidência:
- [ ] F0
- [ ] B1 contracts
- [ ] B2 persistence/replay
- [ ] B3 identity
- [ ] B4 Leaf
- [ ] B5 Council
- [ ] V1
- [ ] V2
- [ ] V3
- [ ] V4

## 13. State ownership map

Liste qualquer owner que tenha mudado ou sido confirmado durante a implementação:

| Estado | Canonical owner/store | Projection/cache | Writer(s) autorizados |
|---|---|---|---|
| identity | | | |
| council | | | |
| events | | | |
| memory | | | |
| reputation | | | |
| evolution | | | |
| world model | | | |
| constitution | | | |

## 14. Idempotency/recovery map

| Operação | Idempotency key | Commit boundary | Recovery/compensation |
|---|---|---|---|
| Leaf create | | | |
| Council decision | | | |
| privileged action | | | |
| proposal apply | | | |
| maintenance job | | | |

## 15. Compatibilidade e rollout

Descreva quais consumers continuam no caminho legado, quais estão em shadow/canary e qual métrica permite promoção. Não marque `default-on` se rollback não foi exercitado no mesmo schema/deployment family.

## 16. Arquivos que o próximo agente NÃO deve alterar sem reabrir decisão

Liste contratos/ADRs congelados, se houver. Isso evita que um sucessor “simplifique” uma invariável já decidida e provoque regressão sistêmica.

## 17. Checagem de auto-consistência antes de entregar

- [ ] README e mappings apontam para paths reais do HEAD.
- [ ] Todos os links novos entre docs resolvem.
- [ ] Não há migration sem teste de upgrade.
- [ ] Nenhum side effect mutável depende apenas de retry cego.
- [ ] Feature flags/defaults documentados.
- [ ] Nenhum segredo aparece em fixtures/traces anexados.
- [ ] Pendências têm owner e próxima ação, não apenas TODO.
- [ ] O primeiro passo do próximo agente é inequívoco.
