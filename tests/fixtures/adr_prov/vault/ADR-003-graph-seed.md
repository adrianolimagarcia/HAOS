---
id: ADR-003
titulo: "Semeadura do recall pelo grafo (graph seed)"
status: "Aceito e implementado"
data: 2026-09-11
decidido_por: "human:adriano"
autor: "adriano"
registro: "retroativo (2026-09-12) — decisão já implementada, documentada a partir da evidência do código"
contexto: "recall do GraphRAG (mcp_graphrag_recall)"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-002"
  - "sessões antigas existiam só no grafo (161 de 190 sessões) e eram inalcançáveis por palavra-chave no corpus"
evidence:
  - "backup query.py.bak-20260911-graphseed"
  - "eval graphrag-graph-seed"
  - "161 das 190 sessões do perfil default existiam apenas no grafo"
affects:
  - "sdb/hermes/graphrag-lite/query.py"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:graph-seed-recall"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-002"
  causado_by: "161 das 190 sessões existiam apenas no grafo e eram inalcançáveis por palavra-chave"
  affects:
    - "sdb/hermes/graphrag-lite/query.py"
  supersedes: []
  superseded_by: null
---

# ADR-003 — Semeadura do recall pelo grafo (graph seed)

> **Nota de registro**: esta ADR é **retroativa**. A decisão foi implementada em
> 2026-09-11 (`query.py`, backup `.bak-20260911-graphseed`) e referenciada no
> `REGISTRY` e no próprio código, mas o arquivo nunca foi gravado no vault
> canônico — o vault tinha apenas ADR-001, ADR-002 e ADR-004. Reconstruída em
> 2026-09-12 a partir de: comentário do código, `query.py` e o caso de eval
> `graphrag-graph-seed`. Nenhum comportamento novo é introduzido por esta ADR.

## Contexto

O snapshot do corpus (`corpus/*.db`) contém apenas as sessões **atuais**. O grafo
(`graph.db`) guarda chunks de sessões **antigas**: **161 das 190 sessões** do
perfil `default` existem só no grafo. Consequência medida: um termo que aparece
apenas numa dessas sessões era **inalcançável por palavra-chave** — só poderia
aparecer por expansão de entidade a partir de outra semente, ou seja, dependia de
sorte topológica.

## Decisão

Semear candidatos **direto dos chunks do grafo**, além do caminho normal (FTS5
`bm25()` para perfis de chat, IDF/LIKE para perfis markdown).

Gates (variáveis de ambiente, com defaults ativos):

| gate | default | efeito |
|---|---|---|
| `GRAPHRAG_GRAPH_SEED` | `1` | liga/desliga a semeadura pelo grafo |
| `GRAPHRAG_GRAPH_SEED_WEIGHT` | `0.5` | teto do score normalizado da semente de grafo (o chamador reescala para a escala do corpus) |
| `GRAPHRAG_POOL_CAP` | `6` | máximo de sessões por perfil no pool |

Mecânica: para cada termo, `df` = nº de chunks que contêm o termo (`LIKE`), peso
`1/sqrt(df)` (IDF), agregado por `(profile, session_id)`, limitado a 12 hits e
normalizado para `0..GRAPH_SEED_WEIGHT`.

## Evidência de convergência

- Verificação direta registrada no `REGISTRY`: "termo que só existe em sessão
  antiga passou de inalcançável → alcançável; pool 7 → 22 candidatos".
- Caso de eval `graphrag-graph-seed` (PASS): prova que a sessão só-no-grafo é
  alcançada **com** `GRAPHRAG_GRAPH_SEED=1` e **não** é alcançada **com** `=0`.
  O caso falha explicitamente se a premissa mudar ("nenhuma sessão só-no-grafo
  encontrada").
- Referências no código: `query.py:91` (bloco) e `query.py:190` (uso no `main`).

## Consequências

- **Positivas**: ~65% do histórico deixa de ser inalcançável por palavra-chave;
  recall deixa de depender de expansão de entidade.
- **Custos**: uma varredura `LIKE` por termo sobre `chunks` (sem índice FTS no
  grafo) — aceitável no tamanho atual (1.154 chunks), a revisar se o grafo
  crescer uma ordem de grandeza.
- **Limite conhecido**: a semeadura é lexical (IDF+LIKE), não semântica; termos
  com erro de digitação ou sinônimos não são recuperados por esta via.

## Critérios de aceite

- [x] `graph_seed_hits()` implementada com IDF e teto de score
- [x] Gates ligados por padrão, com rollback por variável de ambiente
- [x] Caso de eval `graphrag-graph-seed` cobrindo o antes/depois
- [x] Pool ampliado medido (7 → 22 candidatos)
- [ ] Medição de **qualidade** do recall (gold set) — pendente, ver avaliação de
      memória de 2026-09-12 (mensurabilidade 4/10)
