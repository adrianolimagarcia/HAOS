# Gold set graphrag-lite (backlog HAOS P1 — Etapa 2)

Gold set de 21 perguntas de avaliacao executaveis contra a maquinaria REAL do
repo (GraphRAG / RAGFlow / HybridRouter / Reconciler / sync vault). Cada caso
constroi fixture deterministica em tempdir, roda a API real e verifica um
CONTRATO de comportamento — nada de "gold set decorativo": toda pergunta tem
entrada deterministica e saida comparavel contra o codigo.

Origens (casos externos em `/root/workspace/haos-evals/cases/graphrag-*.json`):

| caso externo (instalacao viva) | porta in-repo (este gold set) |
|---|---|
| graphrag-corpus-okf | hibrido-okf-deterministico (okf alcancavel por precedencia) |
| graphrag-grafo-vivo | limite-fail-closed + graphrag-extrai-idempotente (pipeline presente/nao duplica) |
| graphrag-graph-seed | graphrag-recall-semantico + graphrag-multihop-alvo-de-aresta (termo so alcancavel por outra via) |
| graphrag-rerank-contrato | ragflow-rrf-contrato (pool fechado + determinismo + top) |
| graphrag-retry-preso | limite-http-erro-tipado (borda tipada para o retry) |
| graphrag-vault-adr | limite-vault-sync (vault -> grafo de ponta a ponta) |

O que foi CORRIGIDO em relacao aos casos externos:
- paths absolutos da instalacao viva (`/run/media/.../hermes/graphrag-lite`) ->
  fixtures em tempfile + HERMES_HOME isolado por caso (rodavel em CI);
- dependencia do `query.py`/`index.py` externos (fora do repo) -> APIs in-repo;
- thresholds de contagens de um grafo vivo -> contratos de comportamento exatos;
- nenhuma gravacao em `~/.hermes` (o runner isola HERMES_HOME por subprocesso).

## Rodar

```bash
python evals/graphrag_lite/runner.py            # gate: exit = numero de falhas (0 = verde)
python evals/graphrag_lite/runner.py --list     # pergunta de cada caso
python evals/graphrag_lite/runner.py --tag rag  # filtro por tag
python evals/graphrag_lite/runner.py --json     # saida para maquina
```

Em CI o gold set e executado por `tests/evals/test_graphrag_lite_gold_set.py`
via `scripts/run_tests.sh` (imports reais, mesma maquinaria de producao).

## Estrutura

- `cases/*.json` — os 21 casos no MESMO formato do runner de haos-evals
  (`id`, `pergunta`, `descricao`, `tags`, `timeout`, `grader{type:"python", code}`).
- `impl.py` — implementacao de cada caso (fixture + assercao de contrato).
- `runner.py` — harness de gate com raiz no repo (PYTHONPATH, HERMES_HOME
  isolado, TZ/LANG fixos).