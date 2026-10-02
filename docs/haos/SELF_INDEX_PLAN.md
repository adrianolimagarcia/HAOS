# PLANO — SELF-INDEX no HAOS (chaves de acesso + portões de validação)

**Base documental:** `arXiv:2609.19656` (*Self-Evolving Search Index*, set/2026), absorvido em
`dsh-projetos/TEMP1/self-index-retrieval-augmented-agents.md`.
**Estado de partida:** `0.21.83` (`d86d5a9f73`), gate OKF com cobertura ≥ 50% de tags.
**Corpo de prova:** 102 queries gold cobrindo 100% dos 102 `.md` do vault (`/tmp/corpus_real/gold_102.json`).

---

## 0. Por que este plano existe (e por que ele NÃO começa pelo SELF-INDEX)

A matéria sugere: gerar chaves sintéticas por LLM, validar com 3 portões, aplicar operador `max`.
Antes de investir nisso, medi **de onde vêm os 24 misses**. A resposta não é a que o artigo prevê.

### 0.1 Partição medida dos 24 misses @1 (baseline 0.21.83)

| Classe | n | O que significa | Remédio |
| :--- | :---: | :--- | :--- |
| **Patologia de fusão** | 8 | o alvo **está no BM25/FTS** mas é **cortado** da lista léxica | corrigir a passada léxica (sem LLM, sem índice novo) |
| **Ausente das duas listas** | 15 | nenhum token da query casa com o texto do doc | **aqui** o SELF-INDEX se aplica |
| **Fonte não-gradável** | 1 | `RECONCILED_MEMORY` devolve payload sem caminho de arquivo | instrumentar (não é recall) |

Evidência da patologia (query `relatorio diario de manutencao hermes 13-09-2026`):

```
FTS top6:            -13.470  diario/13-09-2026.md   ← BM25 ACERTA o dia 13
                     -13.230  diario/13-09-2026.md
LEXICAL top9:          0.750  diario/30-09-2026.md   ← 9 diários EMPATADOS
                       ...    (todos 0.750, ordenados por created_at DESC)
13-09 na lexical?   None     ← cortado pelo cutoff limit*3 = 9
```

Dois defeitos compõem o estrago, ambos em `RAGFlowStore._build_rank_lists`:

1. **Score léxico não-discriminativo** — `matches / len(tokens)` trata `hermes`,
   `manutencao`, `2026` (df 21/21 nos diários) igual a `13`. Os 21 diários empatam em 0.750.
2. **Desempate por recência + truncamento** — `ORDER BY created_at DESC` + `sort` estável +
   `[: limit*3]`: o empate vira "o mais novo ganha", e o corte de 9 candidatos **exclui**
   o alvo da fusão. O RRF então soma a lista errada por cima do BM25 certo.

### 0.2 A correção que vale mais que o artigo inteiro (MEDIDO)

Ponderar a sobreposição léxica por IDF (`idf(t) = log(1 + N/df(t))`, `N` = nº de chunks):

| Configuração | hit@1 (alias-aware) | hit@3 |
| :--- | :---: | :---: |
| Baseline `0.21.83` | 78/102 (0.765) | 90/102 (0.882) |
| **+ IDF na passada léxica** | **88/102 (0.863)** | **96/102 (0.941)** |
| + operador `max` por documento (artigo §2), sobre IDF | 87/102 (0.853) | 96/102 (0.941) |

**+10 queries no rank 1 com ~10 linhas determinísticas, zero LLM, zero índice novo.**

> **Correção de um erro nosso anterior.** Em `haos-rag-evolution.md` (corrigenda 0.21.82)
> refutamos IDF citando `df[haos]=66`, `df[memoria]=15`. Aquela refutação é **válida e
> permanece válida** para o **gate OKF** (decidir se uma tag *corrobora* uma interceptação —
> lá, o alias legítimo depende justamente de tags genéricas). Ela **não se transfere** para
> **ranqueamento**: dentro de uma família de documentos quase idênticos, o token raro é o
> único que discrimina. IDF não serve para *autorizar* correspondência; serve para
> *ordená-la*. O plano separa os dois papéis explicitamente.

### 0.3 O que a medição refuta do artigo (não vamos construir)

* **Operador `max` por documento** (artigo §2): medido, **−1 query** no rank 1 sobre este
  corpus. As listas já são de *chunks* do mesmo documento; fundi-las em nível de documento
  perde granularidade sem ganhar precisão. **Não entra no escopo.** Se alguém repropuser,
  precisa primeiro bater os 88/102 acima.
* **Simulador de queries com LLM como caminho principal**: os 14 misses residuais são
  ~11 documentos. Rodar geração+validação por LLM sobre 11 docs é barato, mas o ganho
  mensurável é de até +11 pp — **inferior ao ganho de +9.8 pp do Phase 1**, que é grátis.
  O LLM entra como **seam opcional**, nunca como default (precedente no repo:
  `raptor_memory.py` tem `summarizer` com default extrativo, determinístico).

---

## 1. Escopo: o que de fato se aproveita do SELF-INDEX

| Conceito do artigo | Adotado? | Onde no HAOS |
| :--- | :---: | :--- |
| **Document-Key** `K(d) = {d, k₁…kₘ}` — documento imutável + chaves de acesso | **Sim** | tabela nova `haos_rag_keys`; `haos_rag_chunks` **intocado** |
| **Otimização seletiva** (só docs com falha empírica) | **Sim** | gerador recebe somente docs invisíveis no bench |
| **Gate de Especificidade** (a chave, virando query, recupera o doc?) | **Sim** | determinístico, usa o próprio retriever — é o portão que importa aqui |
| **Gate de Separação** (não confundir com vizinhos) | **Sim** | Jaccard de tokens + teste contra a família (`diario/*`) |
| **Gate de Fidelidade** (juiz LLM 0–3) | **Parcial** | default determinístico (cobertura de tokens da chave no corpo do doc); LLM como seam |
| **Perfis de co-recuperação** `C_k` | **Sim, barato** | derivado do próprio bench: quais docs co-aparecem no top-3 (ex.: `curadoria/registry-espelho.md` satura 4 queries) |
| **Filtro de dissimilaridade** Jaccard < 0.8 | **Sim** | evita chaves gêmeas |
| **Operador `max` por documento** | **Não** | refutado por medição (§0.3) |
| **Reprocessar o corpus inteiro** | **Não** | contrário à economia do método e ao custo do repo |

---

## 2. Fases

Cada fase fecha sozinha: mede, testa, commita, bumpa versão. Nenhuma fase depende da seguinte
para entregar valor. **Se a Fase 1 existir sozinha, o plano já pagou o ingresso.**

### FASE 0 — Promover o corpo de prova ao repositório *(pré-requisito de tudo)*

**Problema real, não teórico:** durante este diagnóstico o harness em `/tmp` **subestimou o
efeito do IDF em 4 queries** porque `paths_of()` lia `doc["filepath"]`, e `OKFDocument.to_dict()`
expõe a chave `"path"` (`relative_path`). Métrica errada em script descartável = decisão errada.

| Item | Detalhe |
| :--- | :--- |
| Arquivos | `benchmarks/haos_memory_gold_102.json` (dados) + `scripts/bench_haos_retrieval.py` (harness) |
| Conteúdo do harness | as 102 pares `(query, target)`, `APPROVED_ALIASES` **com proveniência por alias** (o alias sem justificativa documentada vira dívida), métricas strict/alias hit@1/hit@3, **partição automática de misses** (fusion / mismatch / fonte não-gradável), `--json`, `--baseline <arquivo>` para gate de regressão |
| Invariante do harness | falhar (`exit != 0`) se alguma fonte conhecida do router não for gradável — foi exatamente assim que o bug do `filepath` passou despercebido |
| Aceite | `python scripts/bench_haos_retrieval.py --json` reproduz 78/90 no HEAD atual |
| Esforço | 1–2 h |

> Nota de nomenclatura: `scripts/bench_haos_memory.py` **já existe** e é sobre alocador
> de memória (jemalloc vs glibc). Não reutilizar esse nome nem esse arquivo.

### FASE 1 — IDF na passada léxica *(o maior ganho, o menor risco)*

**Arquivo:** `hermes/platform/memory/ragflow_engine.py`, método `_build_rank_lists`.

**Mudança:** substituir `score = matches / len(tokens)` por sobreposição ponderada por IDF
computado **por consulta** sobre os chunks já carregados na passada:

```python
# df(t) = quantos chunks contêm t;  N = nº de chunks;  idf(t) = log(1 + N / df(t))
hits = [t for t in tokens if t in blob]
score = sum(idf[t] for t in hits) / max(sum(idf.values()), 1e-9)
if clean_q.lower() in blob:
    score += 1.0                      # boost de frase exata: preservado, byte a byte
```

**Três decisões que precisam ser tomadas e documentadas (não são detalhe):**

1. **Custo.** O `df` exige uma passada extra sobre os blobs. A passada léxica **já** lê todas
   as linhas e monta os blobs — calcular `df` no mesmo loop e pontuar num segundo loop
   **não adiciona I/O**, só CPU. Baseline medido: 24.8 ms/query em 528 chunks.
   **A latência da variante com IDF ainda não foi medida** — medir no aceite da Fase 1
   (teto: p95 ≤ baseline + 10%). Se o corpus crescer, o `RetrievalBudget` (P6) continua
   sendo o kill-switch.
2. **Desempate.** Hoje o empate é resolvido por `created_at DESC` — comportamento **invisível e
   não intencional** (sort estável sobre SQL ordenado por recência). Com IDF os empates somem
   quase todos, mas o desempate precisa virar **explícito**: ordenar por `(score, -bm25_rank,
   doc_path)`. Recência só pode desempatar quando nada mais discrimina — e isso tem de estar
   escrito, não implícito.
3. **Cutoff.** `[: limit * 3]` corta candidatos que a outra lista ranqueou bem. Duas opções
   honestas: (a) manter o cutoff e **não** deixar que uma lista truncada vote contra a outra
   no RRF; (b) alargar o cutoff. **Medido: só alargar o cutoff (factor 10 e 30) não moveu o
   hit@1** — o que resolvia era o IDF. Logo: manter `limit*3`, não tocar no cutoff, registrar
   o motivo. Não "consertar" o que a medição disse ser neutro.

**Aceite:** `hit@1 ≥ 88/102` e `hit@3 ≥ 96/102`; nenhum dos 78 acertos anteriores perdido;
latência p95 ≤ baseline + 10%.
**Esforço:** 2–3 h (inclui testes red-on-base).

### FASE 2 — Instrumentar a fonte que não se consegue gradar

`_step_reconciled` (`hybrid_router.py:106`) devolve `memory_id`, `topic`, `status`, `content`
— **nenhum caminho de arquivo**. Consequência: a query
`como reconciliar trilhas a2a nas duas pontas audit inbound e outbound` (alvo
`curadoria/2026-09-13.md`) **não pode ser classificada** como acerto nem como erro.

**Mudança:** expor proveniência no payload (`source_paths` / `provenance` já existentes no
registro reconciliado, se houver; se não houver, é um gap de *governança de procedência*, não
de busca — e aí o registro é abrir issue, não inventar campo).

**Aceite:** o bench não reporta mais "fonte não-gradável". **Não se assume que isso ganha
query** — pode continuar sendo miss; o objetivo é saber.
**Esforço:** 1 h.

### FASE 3 — Document-Key: chaves de acesso para os documentos invisíveis

**Alvo real e pequeno (14 misses, dos quais ~11 documentos):**

| Família | n | Sintoma | Chave que resolve |
| :--- | :---: | :--- | :--- |
| `10-Memory/project/<uuid>.md` | 7 | arquivo nomeado por UUID; o fato pessoal descrito na query não tem ponte léxica com o log da sessão | handle natural-language do fato + tokens discriminativos |
| `20-Architecture/<uuid>.md` | 3 | idem; colide com o ADR de mesmo assunto | handle + tipo de artefato |
| `adrs/ADR-001` | 1 | perde para ADR-015 (ambos citam OKF/audit) | chave com o escopo exclusivo do stack |
| `pesquisas/PESQUISA-ABSORCAO-HEADROOM…` | 1 | perde para `PLANO-DIRETOR-THE-EYE…` (mesma família) | chave com o objeto específico |
| `diario/11-09-2026` | 1 | query é **sobre o conteúdo** do dia, não sobre a data | chave com os 3 eventos discriminativos do dia |

**Esquema (nada em `haos_rag_chunks` muda):**

```sql
CREATE TABLE IF NOT EXISTS haos_rag_keys (
    key_id        TEXT PRIMARY KEY,   -- sha1(doc_path|kind|key_text)[:12]
    doc_path      TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- 'discriminative' | 'handle' | 'simulated' | 'manual'
    key_text      TEXT NOT NULL,
    generator     TEXT NOT NULL,      -- 'discriminative-v1' | 'llm:<model>' | 'manual'
    content_hash  TEXT NOT NULL,      -- sha256 do arquivo na geração
    created_at    REAL NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_rag_keys_doc ON haos_rag_keys(doc_path);
CREATE VIRTUAL TABLE IF NOT EXISTS haos_rag_keys_fts USING fts5(
    key_id UNINDEXED, doc_path UNINDEXED, key_text, tokenize='unicode61'
);
```

**Ciclo de vida (invariante, não detalhe):** chave vive exatamente tanto quanto a versão do
índice que a produziu. `index_document()` já faz `DELETE FROM haos_rag_chunks WHERE doc_path=?`
— passa a fazer o mesmo em `haos_rag_keys`/`haos_rag_keys_fts`.
`remove_documents_missing_on_disk()` idem. **Nada de `content_hash` checado em tempo de
consulta** (seria I/O de arquivo no caminho quente): o hash serve para o `--stale` do build.

**Consulta:** as chaves entram como **listas de rank adicionais** no RRF existente
(`fts_keys`, `lexical_keys` ponderada por IDF), **não** como operador `max` por documento
(refutado em §0.3). O chunk retornado continua sendo o chunk do documento — o payload do
router (`doc_path`, `provenance_anchor`, `chunks`) não muda de forma.

**Gerador determinístico default (`discriminative-v1`), sem LLM:**
1. família = docs do mesmo diretório de topo;
2. tokens do doc (reaproveitar `_fold`/`_tokens` de `okf.py` — NFKD, hífen preservado — **não**
   reimplementar normalização);
3. escolher tokens com `df_família ≤ 2` (discriminativos dentro da família), sem stopword;
4. montar `key_text = "<tipo de artefato> <stem/título> · <tokens discriminativos>"`;
5. teto de **3 chaves por documento** (102 docs → ≤ 306 linhas; custo de varredura desprezível).

**Seam opcional de LLM** (o simulador do artigo, 2 estágios: abstração neutra do problema →
formulação da pergunta), com **o mesmo default determinístico** do padrão `summarizer` do
RAPTOR. CI roda sem rede.

**Aceite:** hit@1 ≥ 92/102 **sem perder nenhum** dos 88 da Fase 1; toda chave persistida
passou nos 3 portões; `haos_rag_keys` vazia ⇒ resultado **byte-idêntico** à Fase 1.
**Esforço:** 1–2 dias.

### FASE 4 — Os 3 portões dentro do `dream.py` (blindagem do OKF)

**Por que esta fase é a mais importante para a *saúde* do sistema** (e não para a métrica):
a ablação do artigo (Tabela 5) mostra que **remover os portões deixa o índice pior que o
índice base** (26.6 → 16.1). Nós vivemos isso empiricamente: o destilador do `dream.py`
escrevia lições com tags permissivas e produziu **28 falsos-positivos** de interceptação,
corrigidos a posteriori no gate 0.21.80→0.21.83. Corrigimos o **sintoma no leitor**;
o portão falta no **escritor**.

**Pontos exatos:** `dream.py:247` (`store.save_document(... tags=["session","dream",...] )`)
e `dream.py:431` (`tags=["dream-distilled"]`).

| Portão | Implementação no HAOS | Custo |
| :--- | :--- | :--- |
| **Especificidade** | a chave/título proposto vira query no retriever real; exige o doc no top-3 | determinístico, já temos o harness |
| **Separação** | Jaccard(tokens) ≥ 0.8 contra chave/título de lição **existente** ⇒ **merge**, não documento novo | determinístico |
| **Fidelidade** | cobertura dos tokens da proposta no corpo da evidência (`evidence_span` já existe no P5); juiz LLM como seam opcional | determinístico por default |

**Comportamento em falha (explícito, como no artigo):** se nenhuma proposta passa nos três,
**aborta a escrita** e mantém o estado anterior. O `dream.py` atual escreve e o gate do leitor
segura — inverter isso é o ponto: parar de poluir o corpus.

**Aceite:** `dream --dry-run` reporta os portões; uma lição duplicada de lição existente é
**fundida**, não duplicada; o bench de 102 não regride.
**Esforço:** 1 dia.

### FASE 5 — Documentação e decisão registrada

* Adendo em `docs/architecture/haos-rag-evolution.md` com os números medidos **incluindo o
  `max` refutado** (§0.3) — um adendo que só mostra vitórias é o tipo de documento que
  produz a próxima decisão errada.
* **ADR novo**: *"IDF ordena, gate autoriza — papéis distintos na cascata de memória"*.
  Este ADR existe porque já confundimos os dois uma vez (0.21.82) e a distinção não é
  óbvia no código.
* Absorção da matéria em `curadoria/` com as lições efetivamente aplicadas.

---

## 3. Testes (red-on-base obrigatório)

`tests/test_haos_retrieval_fusion.py` (novo) e `tests/test_haos_self_index.py` (novo):

| # | Teste | Estado esperado no HEAD atual |
| :--- | :--- | :--- |
| T1 | dois docs da mesma família com sobreposição léxica idêntica: o token **raro** decide a ordem, não `created_at` | **RED** |
| T2 | alvo presente no BM25 não pode ser **excluído** da fusão por truncamento da outra lista | **RED** |
| T3 | `haos_rag_keys` vazia ⇒ `hybrid_search` idêntico ao comportamento atual | GREEN (guarda de não-regressão) |
| T4 | chave que **não** recupera o próprio doc no top-3 (portão de especificidade) **não é persistida** | **RED** |
| T5 | chave com Jaccard ≥ 0.8 com chave de **outro** doc é rejeitada (portão de separação) | **RED** |
| T6 | `index_document()` de um doc reindexado **cascadeia** as chaves antigas | **RED** |
| T7 | `remove_documents_missing_on_disk()` não deixa chave órfã | **RED** |
| T8 | `RetrievalBudget` limita a passada de chaves (kill-switch do P6 vale para as duas) | **RED** |
| T9 | chave sintética **não** altera a ordem da cascata: OKF determinístico continua antes de RAGFlow | GREEN |
| T10 | gate do `dream.py`: proposta duplicada de lição existente vira **merge**, não arquivo novo | **RED** |

Regras do repo aplicáveis: comportamento contratuais, não snapshots (T1/T2 afirmam *relações*
entre dois docs, não um valor congelado); E2E com `HERMES_HOME` temporário e schema real do
SQLite, não mock.

---

## 4. Riscos, mitigação e invariantes

| Risco | Mitigação |
| :--- | :--- |
| Chaves LLM contaminarem o índice (a ablação do artigo é literal sobre isso) | portões **antes** da persistência; default determinístico; teto de 3/doc; `--dry-run` |
| Vazamento do gold para as chaves (fitar o teste) | **invariante dura:** `key_text` é derivado **do documento**, nunca da query gold. O gold serve só para **eleger** o documento como deficitário (o que o artigo faz com queries simuladas) |
| Fase 1 mudar ranking de queries fora do bench | A/B com o harness de 102 + conjunto sintético de `scripts/bench_memory_rag.py` (perf/determinismo) + flag `memory.self_index.enabled` em `config.yaml` (**não** `HERMES_*` — AGENTS.md proíbe env var para config comportamental) |
| Passada léxica virar O(n·m) com df por query | mesmo loop de blobs, sem I/O extra; `RetrievalBudget` preservado; medir p95 |
| Custo de LLM no `dream.py` | portões determinísticos primeiro; LLM só no seam, e só em documentos eleitos como invisíveis |
| Métrica errada decidir o trabalho | Fase 0 vem **primeira** por isso |

**Invariantes que este plano não toca:**
* prompt caching por conversa é sagrado — nada aqui muda system prompt nem recarrega memória no meio de uma conversa;
* cascata `RECONCILED_MEMORY → OKF_CANONICAL → RAGFLOW_HYBRID → GRAPHRAG → OKF_BROAD` mantém a ordem;
* `haos_rag_chunks` permanece imutável (chaves vivem em tabela própria);
* RAGFlow é o índice canônico; RAPTOR o lê sem alterá-lo — as chaves seguem o mesmo princípio.

---

## 5. Não-metas (deliberado)

* **Não** implementar operador `max` por documento — refutado por medição (§0.3).
* **Não** reindexar o corpus inteiro com chaves — otimização seletiva é o ponto do método.
* **Não** gerar chaves no caminho síncrono de `router.query` — pipeline offline apenas.
* **Não** reabrir a política de artefatos (tipo de artefato do OKF vs alvo da query) agora:
  com o gate de cobertura 0.21.83 as interceptações caíram de 35 → 4 e **todas as 4 são
  aliases legítimos**. Não existe evidência de falso-positivo que justifique a máquina.
  Reavaliar **depois** da Fase 1, quando a base de comparação muda.
* **Não** adicionar ferramenta core nem novo `HERMES_*`. Superfície: `hermes memory self-index
  {build|report|prune}` no `hermes_cli/subcommands/memory.py`, espelhando `memory raptor build`.

---

## 6. Sequência, esforço e resultado esperado

| Fase | Entrega | Esforço | hit@1 (projeção) |
| :--- | :--- | :---: | :---: |
| 0 | bench no repo + partição automática de misses | 1–2 h | 78 (medido, agora confiável) |
| 1 | IDF na passada léxica + desempate explícito | 2–3 h | **88** (medido) |
| 2 | proveniência no `RECONCILED_MEMORY` | 1 h | 88 (gradabilidade, não recall) |
| 3 | `haos_rag_keys` + gerador determinístico + portões | 1–2 d | 92–96 |
| 4 | portões na escrita do `dream.py` | 1 d | — (previne regressão futura) |
| 5 | docs + ADR | 2 h | — |

**Total: ~4 dias úteis.** O resultado de maior impacto (Fase 1) cabe em **uma tarde** e é
determinístico. A Fase 3 é o que o artigo realmente ensina; ela vem depois porque só faz
sentido medir o mismatch **depois** de remover a patologia de fusão — hoje, 8 dos 24 misses
nem são mismatch, são um bug nosso.

**Decisão pedida ao dono:** seguir com Fase 0 + Fase 1 imediatamente (baixo risco, ganho
medido de +9.8 pp no hit@1), ou revisar o plano antes?
