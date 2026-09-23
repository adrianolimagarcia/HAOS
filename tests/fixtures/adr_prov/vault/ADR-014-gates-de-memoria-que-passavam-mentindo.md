---
id: ADR-014
titulo: "Gates que passavam mentindo: frescor do destilador e regime por gold set"
status: "Aceito"
data: 2026-09-20
decidido_por: "human:adriano"
autor: "adriano"
contexto: "~/.haos/scripts/dream_distill.py · ~/.haos/evals/cases/dream-destilador.json · ~/.haos/evals/cases/tws-rag-mrr.json"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-004"
  - "adr:ADR-005"
  - "auditoria identificou gates verdes que não mediam frescor do destilador nem integridade real do RAG"
evidence:
  - "dream_distill estagnado devolvendo 0 lições com eval dream-destilador passando por tautologia"
  - "eval do RAG com limiares incompatíveis com o gold set congelado"
affects:
  - "scripts/dream_distill.py"
  - "evals/cases/dream-destilador.json"
  - "evals/cases/tws-rag-mrr.json"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:auditoria-frescor-gates-memoria"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-004"
  causado_by: "detecção de avaliações tautológicas e gates que mascaravam estagnação"
  affects:
    - "scripts/dream_distill.py"
    - "evals/cases/*"
  supersedes: []
  superseded_by: null
---

# ADR-014 — Gates que passavam mentindo: frescor do destilador e regime por gold set

**Status:** Aceito (decisão do dono, 2026-09-20: "faça" sobre o lote de 6 itens da auditoria do stack de memória)
**Data:** 2026-09-20
**Contexto técnico:** `~/.haos/scripts/dream_distill.py` · `~/.haos/evals/cases/dream-destilador.json` · `~/.haos/evals/cases/tws-rag-mrr.json`
**ADR relacionadas:** ADR-004 (dream não destila), ADR-005 (escopo dos grafos), ADR-001 (stack de memória)

## Contexto (auditoria de 2026-09-20)

Auditoria do stack de memória com execução real (não leitura de documentação) encontrou dois
gates **verdes que não mediam o que diziam medir**:

1. **O destilador do dream estava estagnado e o eval não via.** `okf/lesson_*.md` (a matéria-prima
   do destilador) está **vazio**, e o destilador devolveu **0 lições de 17 a 19/09** — o relatório
   diário já marcava `⚠️ META: destilou 0`. Causa medida: o gate P11 do dream promove a lição crua
   só com `confidence >= 0.85` (`MemoryCandidate.is_high_confidence()`), e a confiança vem do
   `InstinctStore` (**0.3 na primeira aparição, +0.2 por recorrência ENTRE sessões**). Como a
   "lição" extraída é `preview.split(".")[0][:140]` — texto único por sessão — a recorrência
   praticamente nunca acontece e **nada é promovido**: sem `lesson_*.md`, o destilador não tem o
   que ler. O caso `dream-destilador` continuava **PASSANDO** porque checava apenas a existência de
   lições antigas (183 no diretório) — tautológico para o objetivo declarado.
2. **O eval do RAG do TWS falhava por incompatibilidade de gold set, não por regressão.** O caso
   exigia `MRR >= 0.80` e `Hit@10 >= 0.93`. O artefato medido é o **controle congelado
   `blind_v3_slices` (n=262, MRR 0.7134 / Hit@10 0.8321)**, reproduzido 5× e registrado em
   `README-SESSAO-2026-09-19.md` §2.1 — limiares que **esse** controle nunca atingiu. O baseline
   local anterior (MRR 0.8214, n=70) era de outro conjunto. A suíte acusava "regressão" onde havia
   troca de régua.

## Decisão

### 1. A fila do destilador passa a incluir as sessões do staging de memória (gate canônico intacto)

`dream_distill.py` ganha `staging_candidates()`: além dos `okf/lesson_*.md` crus, a fila inclui as
**sessões com candidato `pending`** no staging P11 (`memory/staging/pending_candidates.json`) cujo
id de proveniência (`session://<sid>`) resolve em `state.db`. A entrada continua sendo o
**transcript real** da sessão e o filtro de qualidade continua sendo o **LLM** (0–3 lições, JSON
estrito, `Evidência` literal). **O gate 0.85 não foi afrouxado** — muda apenas *como a sessão entra
na fila*.

### 2. O caso `dream-destilador` ganha gate de frescor (7 dias)

Além de frontmatter/evidência/idempotência, o caso agora exige que a **lição destilada mais recente**
tenha menos de **7 dias**. Estagnação silenciosa passa a falhar (teste negativo executado: com o
limite forçado a −1 dia o caso falha nomeando "destilador estagnado").

### 3. O caso `tws-rag-mrr` passa a julgar por regime, não por limiar único

- Gold set **contratado** (controle congelado `blind_v3_slices`, n=262): PASS exige
  **reprodutibilidade** do controle (`|Δ| <= 1e-3` em MRR e Hit@10). Divergir = regressão real
  (teste negativo executado: MRR +0.02 → FAIL nomeando a divergência).
- Qualquer outro gold set: mantém o limiar de qualidade (`MRR >= 0.80`, `Hit@10 >= 0.93`), com o
  regime nomeado na saída e no baseline registrado.

## Alternativas descartadas

| alternativa | por quê não |
|---|---|
| Baixar o gate P11 de 0.85 para 0.3 | os candidatos pendentes medidos são **perguntas cruas do usuário** ("monte o google drive como uma unidade de disco", "como que o vigia do tws atua?") — promover isso encheria o OKF de ruído. O gate está certo; a extração é que é fraca. |
| Corrigir o extrator do dream (`preview.split(".")[0][:140]`) nesta ADR | é a correção certa, mas é mudança de semântica na camada canônica do dream (P11) e merece ADR própria com medição A/B. Fica registrado como pendência nomeada. |
| Remover o limiar do eval do TWS | passaria a aceitar qualquer número; o regime por gold set preserva o alarme (divergência do controle) sem falso positivo de régua. |
| Deixar `dream-destilador` como estava | é exatamente o caso que passou 3 dias verde enquanto a memória não recebia nada — o oposto da regra do dono ("alerta só em evento real", mas também "gate que não mede não é gate"). |

## Consequências

- **Executado e verificado em 2026-09-20:** fila do staging = 8 sessões resolvíveis; destiladas 21
  lições duráveis em 2 execuções (3 + 5 sessões, ~17 s e ~40 s, 0 erros); fila esgotada; reexecução
  devolve `idle` (idempotência por `.done` preservada); `okf/licoes` passou de 183 para 204 lições;
  suíte de evals: 28 ok / 0 falhas.
- O destilador deixa de depender de um gate que estruturalmente não promove: o pipeline volta a
  devolver conhecimento à memória mesmo com o P11 fechado.
- **Pendências nomeadas** (não resolvidas aqui):
  1. O extrator do dream ainda captura a **primeira pergunta do usuário** como "lição" crua — o
     staging está cheio desse material. Corrigir exige ADR própria (extração semântica no lugar de
     `split(".")[0][:140]`).
  2. `memory/staging/pending_candidates.json` contém fragmento com cara de credencial
     (`code01haos@gmail`). A regra da stack manda **descartar segredo na ingestão** — há lição
     canônica sobre isso; a limpeza do registro existente é operação sobre estado de memória
     (pendente de autorização explícita).
  3. 25 dos 33 ids de proveniência no staging não resolvem em `state.db` (sessões podadas/rotativas).
     O fallback ignora esses por construção; medir se isso representa perda de memória é trabalho
     separado.
