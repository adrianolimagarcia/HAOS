# HAOS Civilization — START HERE

Este pacote é um **handoff de planejamento/implementação para outro agente**. O documento canônico é:

➡️ **[`haos-civ/README.md`](haos-civ/README.md)**

Não comece pelo `FILE_INDEX`, por um RFC isolado ou pelos exemplos. O README canônico conduz da descoberta do repositório até os gates V1, V2, V3 e V4 e contém links relativos para as especificações corretas em cada etapa.

## Caminho mínimo de leitura

1. [`haos-civ/README.md`](haos-civ/README.md) — mapa mestre e sequência completa.
2. [`haos-civ/MASTER_IMPLEMENTATION_PLAN.md`](haos-civ/MASTER_IMPLEMENTATION_PLAN.md) — arquitetura end-to-end.
3. [`haos-civ/IMPLEMENTATION_PLAYBOOK.md`](haos-civ/IMPLEMENTATION_PLAYBOOK.md) — como executar sem se perder.
4. [`haos-civ/DEPENDENCY_GRAPH.md`](haos-civ/DEPENDENCY_GRAPH.md) — dependências/barriers/paralelismo.
5. [`haos-civ/TRACEABILITY_MATRIX.md`](haos-civ/TRACEABILITY_MATRIX.md) — requisito→spec→implementação→teste.
6. [`haos-civ/TEST_AND_CHAOS_HARNESS.md`](haos-civ/TEST_AND_CHAOS_HARNESS.md) — harness, recovery, chaos e performance.
7. [`haos-civ/TASK_LEDGER.md`](haos-civ/TASK_LEDGER.md) — IDs de execução para status/PR.
8. Copiar [`haos-civ/IMPLEMENTATION_STATUS_TEMPLATE.md`](haos-civ/IMPLEMENTATION_STATUS_TEMPLATE.md) para o workspace e mantê-lo atualizado.
9. Ao transferir trabalho, preencher [`haos-civ/AGENT_HANDOFF_TEMPLATE.md`](haos-civ/AGENT_HANDOFF_TEMPLATE.md).

## Arquitetura em uma frase

```text
Bot persistente -> Leaf temporário rastreável -> Council auditável
-> sociedade baseada em evidência -> evolução governada/versionada
-> memória/world model coletivos -> constituição/policy enforced
```

## Regra mais importante

A implementação deve avançar por **barriers verificáveis**, não por quantidade de arquivos produzidos. O agente não deve iniciar V2 antes do gate V1, V3 antes do gate V2 ou V4 antes do gate V3.

## Conteúdo do pacote

`haos-civ/v1/` contém identidade/Council/Leaf foundations; `v2/` sociedade/reputação/debate; `v3/` evolução/lineage; `v4/` civilização/memória/world model/constituição. As pastas `runtime/`, `kernel/`, `intelligence/`, `operating-model/` e `docs-hardening/` dão especificações transversais.

O arquivo [`haos-civ/FILE_INDEX.md`](haos-civ/FILE_INDEX.md) é o inventário verificável. `MANIFEST.sha256` permite checar integridade do conteúdo e `VALIDATION.md` resume as validações do pacote.

## Para o agente implementador

Antes de editar código: confirme o commit alvo, localize os símbolos reais no repositório, capture baseline de testes/performance e atualize o code mapping. Os paths recuperados da sessão anterior são pistas e podem ter mudado.

Durante cada fatia: defina owner do estado, canonical source, idempotency key, recovery semantics, IDs de correlação, feature flag, security boundary e rollback. Implemente o menor caminho vertical possível e teste happy path + failure/retry/restart quando aplicável.

Ao final de cada barrier: execute o harness correspondente, atualize `IMPLEMENTATION_STATUS.md`, registre ADR quando uma decisão estrutural mudar e só então libere a próxima etapa.

## Invariantes resumidas

- SOUL/IDENTITY/VALUES persistentes pertencem ao Bot.
- Leaf recebe snapshot/temporary SOUL e nunca edita o pai diretamente.
- Council é entidade persistente de coordenação, não um “Bot chefe”.
- eventos relevantes são versionados e replayable;
- retry é idempotente;
- provenance não se perde;
- evolução é proposta/versionada, não mutação silenciosa;
- policy é enforcement fora do prompt do modelo;
- projeções/caches/GraphRAG não viram canonical state;
- rollback preserva histórico.

Abra agora **[`haos-civ/README.md`](haos-civ/README.md)** e siga a ordem definida nele.
