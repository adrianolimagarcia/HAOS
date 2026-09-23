---
id: ADR-012
titulo: "O corpus REST é classificação, não recuperação; e não existe peso único de fusão"
status: "Aceito"
data: 2026-09-19
decidido_por: "human:adriano"
autor: "adriano"
contexto: "tws-RAG-PRONTO (fusão RRF entre doc geral e spec REST)"
causado_by:
  - "adr:ADR-002"
  - "divergência de comportamento na fusão lexical e densa entre documentação textual e operações REST"
evidence:
  - "varredura experimental de RAG_RRF_W demonstrando curvas monotônicas opostas entre os dois corpora"
affects:
  - "tws-RAG-PRONTO/rag_fusion.py"
  - "tws-RAG-PRONTO/config.py"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:otimizacao-rag-rrf-tws"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-002"
  causado_by: "incompatibilidade de peso de fusão único para corpora de naturezas distintas"
  affects:
    - "tws-RAG-PRONTO/*"
  supersedes: []
  superseded_by: null
---

# ADR-012 — O corpus REST é classificação, não recuperação; e não existe peso único de fusão

**Status:** Aceito (medido, 2026-09-19)
**Data:** 2026-09-19
**Escopo:** `tws-RAG-PRONTO` (o corpus REST derivado da spec OpenAPI e o peso da fusão RRF)
**ADR relacionadas:** ADR-002 (reranker híbrido do GraphRAG — o padrão de fusão que este projeto replica)

## Contexto

O RAG deste projeto tem duas fontes com naturezas diferentes no mesmo índice:

- **documentação geral** — prosa, predominantemente PT, perguntas PT (sobreposição lexical alta)
- **superfície REST** — derivada da spec OpenAPI: 276 operações com método/path/família, em EN

O projeto acumulou evidência de que a fusão lexical×densa (RRF `k=60`) era o ponto onde ele
**mais se enganou** — inclusive um "+9" ilusório medido contra baseline diferente. A pergunta
aberta era: **um peso único de fusão serve aos dois corpora?**

## Decisão

### 1. Não existe peso único. Medido, não inferido.

Varredura de `RAG_RRF_W` em {0.0, 0.5, 1.0}, 5 conjuntos, controle OK, mesmo harness e mesmo
corpus. As curvas são **monotônicas em direções opostas**:

| conjunto | w=0.0 | w=0.5 | w=1.0 |
|---|---|---|---|
| geral_blind_v3 | **0.6565** | 0.6336 | 0.5496 |
| holdout_100 | **0.9100** | 0.8400 | 0.7400 |
| blind_holdout_50 | **0.9600** | 0.9600 | 0.9200 |
| realistic_30 | **0.9333** | 0.9333 | 0.8000 |
| **rest_ops_40** | 0.0250 | 0.1500 | **0.2500** |

`peso_otimo_estavel_no_tune = False`. **Um índice único com um peso único está
estruturalmente errado para esta mistura de fontes**: qualquer valor é compromisso que paga
preço nos dois lados.

### 2. O REST sai do índice e vira classificação.

O espaço de resposta do REST é **fechado e enumerado** (276 operações). Isso não é busca — é
escolha entre alternativas conhecidas. Medido no **mesmo** benchmark e **mesmo** corpus:

| método | @1 |
|---|---|
| recuperação, melhor ramo (denso puro) | 0.2500 |
| **classificação, 276 classes** | **0.9250** (37/40) |
| classificação, sem as perguntas com vazamento (34/37) | 0.9189 |

**3,7×.** O vazamento de rótulo (3/40) não sustenta o número. `deepseek-v4-flash` via a6api,
0 erros de API, 40 chamadas (piloto de 5 antes).

### 3. Consequência arquitetural

Com o REST fora do índice, o problema da fusão **desaparece** do lado REST em vez de ser
calibrado, e o corpus geral opera no seu ramo ótimo — **lexical puro**, onde ganha nos quatro
conjuntos. A recomendação é `RAG_HYBRID` OFF como default para o corpus geral e classificação
como caminho do REST.

## Por que a leitura por ramo importa

No corpus geral o denso **gera candidatos úteis mas atrapalha a ordenação no topo**:
`recall@15` melhora com w=0.5 em dois conjuntos, enquanto o `@1` só piora. Ou seja, o gargalo
é **ordenação**, não geração de candidatos — consistente com o `recall@50` por ramo já medido
(união 93,8%). No REST o esparso não tem o que casar (cobertura lexical **mediana 0,00** — as
perguntas são PT, os registros são EN); é o denso que recupera.

## Armadilhas registradas

- **Índice denso inconsistente com o corpus.** O braço REST da varredura nasceu inválido usando
  o índice v5 (24 registros de família, **zero** das 276 operações). Com
  `RAG_DENSE_MASK_TO_CORPUS=1` o ramo denso é zerado pela máscara → `w=1.0` devolvia `0/40`,
  que **parece** resultado. Regra: conferir `n_docs` do índice contra `n_docs` do corpus ANTES
  de concluir; zero silencioso não é resultado.
- **Controle de fusão obsoleto.** O controle histórico (`@1 183/262`) media um corpus que não
  existe mais (6979 docs em `ffc4a75`; o corpus foi reconstruído depois). O controle correto do
  corpus atual é `@1 172/262` (6732 docs). A regra que importa não é o número: é que **todas as
  variantes de uma rodada passem pelo mesmo harness e o mesmo corpus**.
- **Controle declarado com MRR errado.** Ver a segunda nota de contexto abaixo.

## Verificação

- Controle reproduzido exatamente: baseline lexical `172/262`, corpus 6732, `@10 218/262`,
  `@15 221/262`, `recall@15 0,7345`, `MRR 0,7134`.
- 15 células medidas na mesma rodada; nenhuma comparada contra tabela histórica de outro corpus.
- Classificação: 37/40 com 0 erros de API; subconjunto sem vazamento 34/37.
- Evidências em `data/evidence/lab-validation-2026-09-19-*.jsonl` (JSON válido, 0 violações de
  sanitização); runbook em `docs/lab-protocols/runbook-medir-fusao-e-peso-da-fusao.md`.

## O que este ADR NÃO decide

- Não decide a integração em produção nem latência da classificação (não medidos).
- Não decide o default de `RAG_HYBRID` no código — a recomendação está dada, a mudança de
  default exige rodada própria com o corpus congelado.
- Não mede classificação em granularidade de família (24 classes) — o número reportado é de
  **operação** (276 classes), que é o caso difícil.

---

## Nota de contexto (2026-09-19, mesma data) — RETRATAÇÃO PARCIAL DA §3

O corpo acima **não é editado** (ADR aceita é imutável). Esta nota corrige a §3.

**A recomendação "`RAG_HYBRID` OFF como default para o corpus geral" está RETRATADA.** Ela se
apoiava em `@1` sozinho. A segunda métrica da mesma rodada diz o **contrário**:

| conjunto | Δ`@1` (híbrido vs lexical puro) | Δ`recall@15` |
|---|---|---|
| geral_blind_v3 | −2,29pt | **+5,40pt** |
| holdout_100 | −7,00pt | **+1,50pt** |
| blind_holdout_50 | 0,00 | 0,00 (teto) |
| realistic_30 | 0,00 | −4,44pt |

O híbrido **perde `@1` em dois conjuntos e ganha `recall@15` em dois**. A produção entrega
**15 documentos** (`top_n=15`, pool de 20 do RRF, em `evaluate_pure_virgin_hybrid_cpu.py`). Se
o consumidor recebe 15, a pergunta que importa é "a resposta está entre os que recebi?" — que é
`recall@15`, onde o híbrido ganha em `blind_v3` (+5,40pt) e `holdout_100` (+1,50pt).

O que este ADR **não** tinha notado é que a §"Por que a leitura por ramo importa" **já
registrava** o ganho de `recall@15` — e a §3 recomendou desligar assim mesmo. A contradição
estava dentro do próprio documento.

**Consequências:**

1. **Nenhum default de produção foi alterado.** Desligar `RAG_HYBRID` com base numa métrica só
   seria repetir o erro que este projeto já cometeu (baseline diferente, métrica que agrada).
2. O default `0.5` foi escolhido por **convenção** ("peso igual por default" é o padrão da
   indústria; o Qdrant recomenda `1.0/1.0` sem conjunto de avaliação), **não por medição**.
   Agora está medido — e o resultado é uma **troca**, não um vencedor.
3. A §1 (não existe peso único) e a §2 (REST é classificação) **permanecem válidas**: não
   dependem da métrica escolhida — as curvas opostas e o 3,7× valem em `@1` e em `recall@15`.

**Pendência real (decisão de produto, não de laboratório):** o consumidor de produção usa a
**ordem** (`@1`) ou a **presença na janela** (`recall@15`)? Isso decide se o default deve mudar.
Enquanto não for respondido, mudar o default é escolher uma métrica para justificar uma
recomendação — exatamente o que a lição
`licoes/medicao-de-recuperacao-zero-silencioso-controle-obsoleto-e-recomendacao-por-metrica-unica.md`
descreve como armadilha 3.

---

## Nota de contexto (2026-09-19) — CORREÇÃO DO MRR DO CONTROLE

O controle re-baselinado foi declarado com `MRR 0,7133`. **Estava errado: o valor do evaluator
é `0,7134`.**

O `0,7133` veio de `scripts/measure_fusion_v3.py`, que replica o pipeline lexical por conta
própria. Ele concorda em `@1` (172/262) e `@10` (218/262) e divergia na **quarta decimal** do
MRR. O erro de processo: a segunda fonte foi usada como confirmação sem ser confrontada na
métrica inteira — concordância parcial foi lida como concordância total.

Verificado rodando o código do **HEAD sem** a refatoração do `rag_core`: também dá `0,7134`.
Portanto o `0,7133` não é reprodutível a partir do estado atual, por nenhum dos dois caminhos de
código. Se um dia ele for vinculante, falta identificar **qual** estado de código/corpus o
produziu — o HEAD não é esse estado.

Controle correto, com o ambiente explícito (`PYTHONHASHSEED=0`,
`RAG_MEASURE_EXCLUDE_EVIDENCE=1`, `RAG_INGEST_REST_API=1`, `RAG_BENCHMARK_FILE=blind_v3_slices`):

```
corpus 6732 · @1 172/262 · @10 218/262 · @15 221/262 · recall@15 0,7345 · MRR 0,7134
```

**Distinção que faltava no runbook:** o controle **não** é o `w=0.0` da varredura. `w=0.0` zera
o peso denso mas ainda passa pelo caminho híbrido e pelo `second_stage_rerank` — o `@1` coincide
(172/262) e `@10`/`@15` **não** (217/262 e 220/262 contra 218/262 e 221/262). Não usar um no
lugar do outro ao declarar controle.
