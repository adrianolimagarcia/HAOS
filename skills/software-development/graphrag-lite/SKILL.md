---
name: graphrag-lite
description: "Recupera fatos e relacoes do grafo de memorias local."
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: software-development
metadata:
  hermes:
    category: software-development
    tags: [graphrag, memory, retrieval, knowledge-graph, rag, hybrid]
    related_skills: [haos-codebase-wiki]
---

# GraphRAG-Lite Skill

Recupera conhecimento relacional ja persistido no store SQLite canonico do
GraphRAG (`$HERMES_HOME/memory/graphrag.db`) usando as ferramentas de memoria
existentes (`graphrag_query`, `haos_hybrid_memory_query`, `haos_okf_save_document`) —
sem criar ferramenta nova e sem desmontar o grafo. A maquinaria vive no repo
(`hermes/platform/memory/graphrag.py`, `hybrid_router.py`,
`hermes/platform/context/memory/graphrag_store.py` + `incremental_graphrag.py`);
esta skill apenas ensina o fluxo de consulta e verificacao.

E uma skill de CONSULTA. Ela nao escreve no grafo diretamente: fatos novos
entram pelo vault (`obsidian_save_note`), por contratos OKF
(`haos_okf_save_document`) ou pelo fluxo de KnowledgeEvent da stack A; o grafo
e uma projecao derivada (GOV-008), nunca editado na mao.

## When to Use

Use quando a pergunta e RELACIONAL ou de ENTIDADE:

- "quais componentes dependem de X?" / "o que conecta X a Y?"
- "qual ADR referencia X?" / "o que X supersede?"
- "qual tecnologia o modulo Y usa?" (extraido para o grafo)
- "qual contrato canonico cobre X?" (camada OKF)

NAO use para decisoes de codigo em aberto (use `search_files`) nem para
historico de sessoes (use o sistema de memoria de sessoes).

## Prerequisites

- Toolset `memory` habilitado (`haos tools`): expoe `graphrag_query`,
  `haos_hybrid_memory_query`, `haos_okf_save_document`, `obsidian_get_adr`,
  `obsidian_save_note`.
- `$HERMES_HOME/memory/graphrag.db` presente (produzido pela stack A: notas do
  vault sincronizadas via `obsidian_save_note` ou eventos de conhecimento). Sem
  o arquivo, `graphrag_query` falha de forma controlada (fail-closed) — ver Pitfalls.
- Fallback opcional: `$HERMES_HOME/graphrag/entities.csv` + `relationships.csv`
  (indice determinístico para CI/demo).

## How to Run

1. Rode a prova de saude do toolchain (self-contained, usa tempdir — nao toca
   o home real):

   ```
   terminal: python3 skills/software-development/graphrag-lite/scripts/recall_probe.py --self-contained
   ```

2. Consulte o grafo real do home (read-only):

   ```
   terminal: python3 skills/software-development/graphrag-lite/scripts/recall_probe.py
   ```

## Quick Reference

| Ferramenta | Uso |
|---|---|
| `haos_hybrid_memory_query` (mode=hybrid) | Resposta fundida: memoria reconciliada -> OKF deterministico -> RAGFlow -> GraphRAG. Comece por aqui. |
| `haos_hybrid_memory_query` (mode=okf) | So a camada OKF deterministica (ignora o grafo). |
| `graphrag_query` (mode=local) | Entidade + relacoes imediatas (1 salto). |
| `graphrag_query` (mode=global) | Mesmo grafo, metodo de resposta global (comunidades). |
| `haos_okf_save_document` | Persiste contrato canonico na camada OKF. |
| `obsidian_save_note` | Grava nota/ADR no vault e sincroniza os stores derivados na hora. |

## Procedure

1. **Primeiro a fusao**: chame `haos_hybrid_memory_query` com a pergunta. O
   router responde com a fonte vencedora (`RECONCILED_MEMORY` >
   `OKF_CANONICAL` > `RAGFLOW_HYBRID` > `RAG_PROBABILISTIC` > `OKF_BROAD_MATCH`).
   `source` no JSON diz qual camada ganhou — use isso como trilha de origem.
2. **Aprofunde no grafo**: para entidade/relacao, chame `graphrag_query`
   com mode=local e o nome exato da entidade (a busca por entidade e case
   sensitive). Para tema amplo, mode=global.
3. **Salto multiplo (multihop)**: `graphrag_query` devolve 1 salto por chamada.
   Para cadeias A->B->C, consulte A, depois consulte B com o nome que apareceu
   nas relacoes de A — cada chamada fecha um salto.
4. **Confira supersessao**: se a entidade aparecer com `superseded_by`, o fato
   obsoleto nao e resposta final — siga o ponteiro para o fato novo.
5. **Persista o que faltar**: contrato canonico -> `haos_okf_save_document`;
   nota/ADR -> `obsidian_save_note` (sincroniza o grafo na hora).

## Pitfalls

- **Fail-closed**: sem store nem CSV, `graphrag_query` devolve
  `{"error": "GraphRAG indice local nao disponivel"}` — nao invente resposta.
  A skill `recall_probe.py --self-contained` isola toolchain saudavel de home
  vazio.
- **Case sensitive**: `query_local` do adapter casa o nome exato da entidade
  (`ProtocolAdapter`, nao `protocoladapter`); as relacoes sao case insensitive.
- **1 salto por chamada**: o client nao fecha cadeias multihop numa chamada —
  encadeie chamadas.
- **`found` do router**: na rota `RAG_PROBABILISTIC` o campo `found` e a
  verdade do dict (sempre True quando o client responde); leia `entities` para
  saber o que casou de fato.
- **Supersedidas permanecem**: o store nao apaga — marca `superseded_by`;
  siga o ponteiro.
- **Nao desmontar/editar o grafo na mao** e **nao criar ferramenta de memoria
  nova**: a maquinaria existente ja cobre extracao e consulta (nao-objetivos
  da pesquisa).

## Verification

- `python3 skills/software-development/graphrag-lite/scripts/recall_probe.py --self-contained`
  -> exit 0 com `PASS <n> checks` (prova que extracao, persistencia e consulta
  funcionam de ponta a ponta sem rede).
- `python3 skills/software-development/graphrag-lite/scripts/recall_probe.py`
  -> exit 0 reporta o store real do home; exit != 0 com mensagem clara quando
  ausente (fail-closed).
- O gold set de avaliacao vive em `evals/graphrag_lite/cases/` (rodado por
  `tests/evals/test_graphrag_lite_gold_set.py` em CI).