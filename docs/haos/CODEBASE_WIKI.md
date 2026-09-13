# HAOS Codebase Wiki: knowledge graph leve e offline para navegação de código

## 1. Visão Geral

O **HAOS Codebase Wiki** é uma capacidade própria e leve que transforma uma árvore de código
local (ou o próprio workspace do Hermes) num **mapa navegável por um agente**: um índice
`index.md` + um artigo markdown por módulo/comunidade, com os "god nodes" (conceitos mais
conectados) e um JSON persistente de grafo. É a alternativa **sem dependência externa** ao
graphify (Apache-2.0/MIT, ~67k LOC, deps tree-sitter p/ 20+ linguagens): aqui só **stdlib
Python + `ast`**, sem LLM no caminho de indexação e sem novas dependências nativas.

Objetivo operacional: reduzir tokens por pergunta de arquitetura ("o que chama X?", "onde
flui o dado de Y?") usando artigos pré-computados e rotulados, em vez de re-ler arquivos
crus — e persistir esse mapa entre sessões, dentro do profile.

### Restrições (herdadas do HAOS)

* 100% local/offline; sem Docker/Kubernetes; sem serviço remoto.
* Sem exigência de rede, git remoto ou API key no caminho de indexação.
* Sem LLM obrigatório: a indexação é determinística (AST). LLM é opcional e posterior (ver §8).
* Sem vendoring de projeto de terceiros; estender o que já existe no repo.

---

## 2. Por que não absorver o graphify

O [graphify](https://github.com/Graphify-Labs/graphify) é excelente (116k⭐, instalador oficial
Hermes `graphify hermes install`, extração AST determinística). Mas absorver o código dele no
core viola o Footprint Ladder e a regra "third-party product integrated into the core tree"
(AGENTS.md), além de trazer ~67k LOC e gramáticas tree-sitter de ~25 linguagens. O valor
núcleo para o HAOS é pequeno e reimplementável com baixo custo:

1. **God nodes** (conceitos de maior grau) → ordenação por centralidade de grau simples.
2. **Comunidades** (agrupamentos coesos) → cluster por diretório/prefixo de módulo.
3. **Wiki markdown navegável** (`index.md` + artigo por comunidade) → gerador de markdown.
4. **Cache incremental** (hash de arquivo → re-indexa só o que mudou) → dict de mtime/sha256.

Tudo isso cabe em ~600–800 LOC com `ast`, `json` e `hashlib` puros. Nada disso existe hoje no
Hermes como produto (o que existe é avaliação: `evals/codebase_navigability/*`, que mede o
problema, não o resolve).

---

## 3. Arquitetura

```
                      ┌───────────────────────────────┐
                      │       HAOS Agent (turno)      │
                      └──────────────┬────────────────┘
                                     │ lê artigos / chama CLI
                                     ▼
                      ┌───────────────────────────────┐
                      │    Codebase Wiki (nova pasta) │
                      │  hermes/platform/codebase/    │
                      │                               │
                      │  indexer.py   (AST local)     │
                      │  graph.py     (grafo + god)   │
                      │  clusters.py  (comunidades)   │
                      │  wiki.py      (markdown)      │
                      │  query.py     (consulta pura) │
                      │  runner.py    (pipeline/watch)│
                      │  mcp_export.py (F3: MCP)      │
                      └──────┬────────────────────────┘
                             │ escreve
                             ▼
              <HERMES_HOME>/codebase-wiki/
              ├── index.md        (entrada: módulos, god nodes, perguntas)
              ├── modules/        (um artigo por módulo/comunidade)
              ├── cache/          (indexação incremental por sha256)
              └── graph.json      (nós/arestas persistidos p/ consulta)
```

* **`indexer.py`** — varre a raiz configurada, ignora `node_modules`, `.git`, `.venv`,
  `__pycache__`, `dist`, `build` (mesma lista de exclusão do `static_metrics.py`), e extrai de
  cada arquivo `.py` via `ast`:
  * nós: módulo, classes (`ClassDef`), funções/métodos (`FunctionDef`/`AsyncFunctionDef`);
  * arestas: `importa` (imports resolvidos para módulos do próprio corpus), `chama`
    (chamadas com nome resolvível localmente), `herda` (bases), `define` (membro → classe);
  * por nó: `source_file` + `lineno` (para o artigo citar o caminho exato).
* **`graph.py`** — monta um grafo dirigido em dict puro (`adj[u][v] = {"rel": ..., "n": n}`)
  e computa **god nodes** = top-k por grau (entrada+saída), com o caminho do arquivo-fonte de
  cada um. Sem NetworkX (não é dependência garantida no runtime hermético).
* **`clusters.py`** — comunidades **sem Leiden**: agrupa nós por prefixo de pacote (2 primeiros
  segmentos do módulo, ex.: `hermes.platform`, `tools`, `gateway`), que no código real é um
  proxy de comunidade de altíssima fidelidade e custo zero. Opcional: conectar módulos que se
  importam mutuamente entre clusters como "pontes" listadas no artigo.
* **`wiki.py`** — gera:
  * `index.md`: god nodes com 1 linha de "por quê" (grau + lista dos vizinhos mais fortes),
    lista de comunidades com contagem, e 3–5 **perguntas sugeridas** que o grafo está
    posicionado para responder (ex.: "o que conecta X a Y?", "quem chama Z?");
  * `modules/<slug>.md`: um artigo por comunidade com seções "Responsabilidades", "Conceitos
    principais", "Relações internas", "Relações externas (pontes)" — cada item com
    `source_file:linha`.

### Exemplo de saída (artigo de módulo)

```markdown
# módulo: tools
- 214 símbolos · 38 arquivos · 12 pontes externas

## Conceitos principais (god nodes do cluster)
- `registry.ToolRegistry.register` (grau 89) — tools/registry.py:596
- `registry.dispatch` (grau 61) — tools/registry.py:810

## Relações externas (pontes)
- `tools` → `model_tools` (importa: registry → discover_builtin_tools)
- `gateway` → `tools` (importa: run.py → registry)
```

---

## 4. Escopo e fases

### Fase 1 — MVP (Python-only, AST puro, sem LLM)
* `indexer.py` + `graph.py` + `clusters.py` + `wiki.py` funcionando contra o próprio workspace
  Hermes e contra qualquer raiz `.py` local.
* Persistência em `<HERMES_HOME>/codebase-wiki/` com **cache incremental** por sha256.
* Saídas: `index.md`, `modules/`, `graph.json`.
* CLI `hermes codebase-wiki [raiz]` — re-executar o mesmo comando é o "update"
  (cache incremental por sha256: só arquivos mudados são re-parseados).

### Fase 2 — integração com o agente (sem mudar o schema de tools do core)
* Skill HAOS (`skills/autonomous-ai-agents/haos-codebase-wiki/SKILL.md`) ensinando o agente a:
  * consultar `index.md` antes de perguntas de arquitetura quando `codebase-wiki/` existir;
  * rodar `hermes codebase-wiki` de novo após ondas de edição (padrão `graphify update`).
* Sem tools novas no `_HERMES_CORE_TOOLS`: o acesso é por terminal + leitura de arquivos
  (mesma lógica de exposição por sessão do AGENTS.md).

### Fase 3 (concluída)
* Extratores não-Python **sem tree-sitter**: `.md` entra via `--docs` como nós de conceito —
  headings `#/##/###` viram símbolos `concept` do módulo doc, e ids pontuados no texto (fora
  de blocos de código) viram menções resolvidas contra símbolos confirmados como arestas
  `cita` (`EXTRACTED` se o símbolo existe, `INFERRED` se só o módulo foi confirmado). `.ts`/`.js`
  ficam fora por decisão de escopo (a spec permite "corpus misto só com Python + docs").
* Modo `--watch`: reindexa automaticamente quando a árvore muda (fingerprint mtime+size,
  intervalo `--poll`), com `stop` programável p/ testes.
* Modo `--mcp`: registra o wiki como servidor local no `LocalMCPAggregator` existente
  (`register_server_tools` + `register_dispatcher`), expondo `wiki_status`, `wiki_search`,
  `wiki_edges`, `wiki_path` e `wiki_god_nodes` federados como `codebase-wiki_*` — o dispatcher
  lê `graph.json` persistido a cada chamada (nenhuma tool nova no core, sem host MCP externo).
* Camada de consulta pura `query.py` (search/neighbors/shortest_path/god_nodes/community_stats/
  summarize) usada pelo `--mcp` e pelo helper da skill, testada contra payloads sintéticos.

---

## 5. Integração com o que já existe (extend, don't duplicate)

* Reutilizar o **conjunto de exclusão** e o padrão de varredura de
  `evals/codebase_navigability/static_metrics.py` (mantendo-o intacto).
* `graph.json` segue o mesmo espírito de dado persistente do `event_store`/memória HAOS: fica
  no profile, versionado por conteúdo, jamais no repo.
* Nada de tools novas no core: superfície = **CLI command + skill** (rung 2 do Footprint Ladder).
* Compatível com a filosofia de prompt-cache: o mapa é *dado lido sob demanda* (via skill),
  nunca texto injetado no system prompt.

---

## 6. Critérios de aceite (contratos, não snapshots)

1. **Corpus pequeno sintético (fixo):** dado um `fixtures/` com 3 módulos (`a.py` importa
   `b`, `b` chama `c`, `c` herda de base em `b`), o `graph.json` contém exatamente as arestas
   `a→b (importa)`, `b→c (chama)`, `c→base (herda)` e `index.md` lista `b` como god node de
   maior grau. (Comportamento contratual — nada de assert de contagens globais.)
2. **Incremental:** tocar 1 arquivo e rodar de novo re-indexa só ele (verificável por
   contagem de arquivos re-parseados no relatório), sem invalidar nós/arestas dos demais.
3. **Idempotência:** duas execuções seguidas sem mudanças produzem `graph.json` byte-idêntico.
4. **Fronteira:** caminhos de nós e arestas são relativos à raiz configurada (nunca absolutos
   com segmentos de máquina), e `source_file` sempre resolve dentro da raiz.
5. **Offline:** a suite roda sem rede e sem API key (testes unitários puros, tmp_path).
6. **Rótulos de confiança:** arestas vêm de `EXTRACTED` (import/chamada direta visível no AST)
   ou `INFERRED` (segundo passe de resolução); nada é inventado sem marcação.

---

## 7. Riscos e decisões em aberto

* **Resolução de chamadas** é o ponto frágil de qualquer AST-only: chamadas via
  `getattr(obj, name)`/imports dinâmicos ficam `INFERRED` ou de fora — documentar o limite no
  `GRAPH_REPORT.md`-equivalente (`index.md`, seção "Limites"), como o graphify faz.
* **Multi-linguagem:** padrão é `.py` apenas (espelha o foco do repo); `.md` entram via
  `--docs` como nós de conceito (headings) + arestas `cita` por menção, sem LLM; `.ts/.js`
  ficam de fora (escopo Fase 3: "só Python + docs").
* **`cita` resolve contra o corpus:** menção a símbolo de stdlib/lib externa (ex.: `json.loads`)
  não gera aresta — o doc cita o que o mapa conhece, e nada é inventado.
* **Onde mora a raiz:** por padrão a raiz do workspace ativo; configurável via config.yaml
  (`codebase_wiki.root`) ou argumento. Nunca em `~/.hermes` hardcoded — usar
  `get_hermes_home()`.
* **Tamanho de corpora gigantes:** índice cap por profundidade/arquivos com aviso explícito
  (nunca paginação silenciosa de leitura completa — regra do AGENTS.md sobre instructional
  tools não se aplica aqui porque é dado, mas o cap deve ser visível).

---

## 8. LLM opcional (fora do escopo do MVP)

A extração semântica de conceitos em docs/imagens (o diferencial do graphify) pode vir depois
como passe opcional que usa o próprio turno do agente ou um backend local (`ollama` via
config), nunca uma key externa obrigatória. Isso fica atrás de flag explícita
(`--semantic`) e não altera o caminho determinístico da Fase 1.

---

## 9. Arquivos (implementado)

```
hermes/platform/codebase/           # sem __init__.py (namespace package)
├── indexer.py     # varredura + AST python + cache sha256 + extrator .md
├── graph.py       # grafo dirigido + god nodes + resolução EXTRACTED/INFERRED
├── clusters.py    # comunidades por prefixo de módulo + pontes
├── wiki.py        # index.md + modules/<slug>.md + graph.json (renderização)
├── query.py       # camada de consulta pura sobre graph.json (Fase 3)
├── runner.py      # pipeline run_index + tree_signature/watch (Fase 3)
└── mcp_export.py  # registro do wiki no LocalMCPAggregator (Fase 3)
hermes_cli/codebase_wiki.py        # parser (root, --out, --include-tests, --docs,
                                   #   --max-files, --json, --watch, --poll, --mcp)
hermes_cli/codebase_wiki_impl.py   # run_cli / run_cli_watch / run_cli_mcp
tests/test_haos_codebase_wiki.py   # contratos §6 + Fase 3 (md, query, watch, mcp)
tests/test_haos_codebase_wiki_cli.py   # contratos do parser e dos modos CLI
skills/autonomous-ai-agents/haos-codebase-wiki/SKILL.md      # (Fase 2)
skills/autonomous-ai-agents/haos-codebase-wiki/scripts/haos_wiki_query.py
tests/skills/test_haos_codebase_wiki_skill.py                # (Fase 2)
```

### Uso

```bash
hermes codebase-wiki [raiz]                # índice padrão (.py; cache incremental)
hermes codebase-wiki [raiz] --docs         # + .md como nós de conceito/citações
hermes codebase-wiki [raiz] --watch        # reindexa ao salvar (Ctrl+C para parar)
hermes codebase-wiki [raiz] --mcp          # registra codebase-wiki_* no aggregator
hermes codebase-wiki --out <dir> --json    # saída custom / relatório JSON
```
