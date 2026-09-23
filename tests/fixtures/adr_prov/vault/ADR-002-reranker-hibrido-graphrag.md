---
id: ADR-002
titulo: "Re-ranker híbrido (grafo) no recall do GraphRAG"
status: "Aceito"
data: 2026-09-11
decidido_por: "human:adriano"
autor: "adriano"
contexto: "recall do GraphRAG (sdb/hermes/graphrag-lite/query.py)"
causado_by:
  - "adr:ADR-001"
  - "recall single-stage do GraphRAG ignorava estrutura relacional na ordenação de candidatos"
evidence:
  - "sessão contendo entidade exata perdia para termos comuns no FTS5 bm25 saturado"
  - "código query.py seed_sessions"
affects:
  - "sdb/hermes/graphrag-lite/query.py"
  - "mcp_graphrag_recall"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:otimizacao-recall-graphrag"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-001"
  causado_by: "recall single-stage do GraphRAG ignorava estrutura relacional"
  affects:
    - "sdb/hermes/graphrag-lite/query.py"
  supersedes: []
  superseded_by: null
---

# ADR-002 — Re-ranker híbrido (grafo) no recall do GraphRAG

**Status:** Aceito (dono ratificou em 2026-09-11: "faça o item 5 e item 3")
**Data:** 2026-09-11
**Contexto técnico:** `sdb/hermes/graphrag-lite/query.py` · consumido pelo MCP `mcp_graphrag_recall`
**ADR relacionada:** ADR-001 (stack de memória do HAOS)

## Contexto

O `recall()` do GraphRAG hoje é **single-stage**: `seed_sessions()` recupera sessões por FTS5/bm25 (perfis de chat) ou IDF+LIKE (markdown), intercala por perfil para diversidade e devolve **8** sessões. O grafo (entidades/relações) só entra **depois**, na expansão por hops — ou seja, a *ordenação* dos candidatos ignora completamente a estrutura relacional que é o motivo de existir do GraphRAG.

Sintoma observável: uma sessão que contém a entidade exata da pergunta pode perder para uma sessão que apenas repete termos comuns (o bm25 do FTS5 satura em termos genéricos como "post", "webhook", "config").

## Decisão

Adicionar **estágio 2 de re-ranking ciente do grafo**, determinístico e sem dependência nova:

1. **Pool maior**: `seed_sessions()` passa a devolver um pool (`GRAPHRAG_POOL`, default 24) em vez de 8.
2. **Re-rank** (`rerank()` em `query.py`): pontua cada candidato com
   - `0.5 × base_norm` (score FTS/IDF normalizado no pool),
   - `0.4 × overlap_entidades` (entidades do grafo ligadas à sessão ∩ entidades/nomes presentes na pergunta),
   - `0.1 × recência` (decay por `last_activity_at`),
   e então reaplica a intercalação por perfil para manter diversidade de corpus.
3. **Gate**: `GRAPHRAG_RERANK=1` (default ligado); `0` restaura o comportamento single-stage. `GRAPHRAG_RERANK_WEIGHTS` permite ajustar os pesos sem editar código.
4. **Transparência**: a saída do recall inclui uma linha de diagnóstico com `pool`, `top1 antes → depois` e o overlap medido. Sem isso, não há como auditar o efeito.
5. **Fallback**: sessão ausente do grafo recebe `overlap=0` e mantém seu score base — o re-rank **nunca remove candidato** (invariante: o conjunto selecionado é subconjunto do pool).

## Alternativas descartadas

| alternativa | por quê não |
|---|---|
| **Re-ranker neural `BAAI/bge-reranker-base` INT8** (como no tws-RAG) | exige `torch`+`transformers` (~2 GB) no venv do indexador; o ganho não justifica inflar o caminho crítico de um recall que hoje é stdlib+sqlite. Fica registrado como upgrade futuro **se** o gold set mostrar ganho relevante. |
| **Re-ranker por LLM (1 chamada extra)** | o recall já gasta 1 chamada LLM na resposta; dobraria custo e latência. |
| **Trocar FTS5 por embeddings** | muda o estágio 1 inteiro (re-indexação, novo storage) — fora do escopo desta decisão. |

## Contrato (spec)

- **Entrada:** `pergunta: str` (mesma assinatura pública do MCP `recall`).
- **Saída:** inalterada em formato (texto com `## Sessoes-fonte (seed FTS)` + contexto do grafo + resposta LLM), com a linha de diagnóstico do re-rank.
- **Invariantes verificáveis (viram casos de eval):**
  1. `GRAPHRAG_RERANK=0` reproduz exatamente a ordem single-stage.
  2. Com re-rank ligado, todo candidato selecionado pertence ao pool (nenhum candidato inventado).
  3. Duas execuções da mesma pergunta produzem o mesmo top-1 (determinismo).
  4. `pool > selecionados` (o re-rank realmente teve o que reordenar).
- **Custo:** zero chamadas LLM extras; +1 consulta SQLite por sessão candidata (cacheada em memória).

## Consequências

- Positivas: o grafo passa a influenciar a *seleção*, não só a expansão; determinístico; reversível por env; auditável.
- Negativas/limites: os pesos são heurísticos (não aprendidos); **falta um gold set** de perguntas→sessão correta para medir MRR do recall. Fica registrado como pendência: sem gold set, o ganho é argumentado, não medido.
- Reversão: `GRAPHRAG_RERANK=0` (ou restaurar `query.py.bak-20260911-rerank`).

## Critérios de aceite (EDD)

- [x] `query.py` compila e roda com `GRAPHRAG_RERANK=1` e `=0`.
- [x] Caso de eval `graphrag-rerank-contrato` verde (invariantes 2–4).
- [x] Demonstração real de reordenação numa pergunta concreta (antes → depois) registrada no REGISTRY.

**Convergido em 2026-09-11 21:05 -03** — suite de evals 8/8 PASS; MCP sem necessidade de restart (query.py roda como subprocesso por chamada).
