---
id: ADR-004
titulo: "O dream não destila: é cópia determinística por palavra-chave"
status: "Aceito e implementado"
data: 2026-09-11
decidido_por: "human:adriano"
autor: "adriano"
contexto: "hermes/platform/memory/dream.py (DreamConsolidator.run_dream)"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-002"
  - "auditoria de consolidação de memória revelou que dream gerava cópias brutas de 200 caracteres sem chamar LLM"
evidence:
  - "33 sessões consolidadas geraram 33 arquivos okf/lesson_*.md com transcrições cruas dos primeiros 200 caracteres"
  - "memory/instincts/ vazio (0 arquivos)"
  - "análise de código de DreamConsolidator.run_dream"
affects:
  - "hermes/platform/memory/dream.py"
  - "okf/lesson_*.md"
  - "memory/instincts/"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:auditoria-dream-consolidator"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-001"
  causado_by: "dream não realizava destilação via LLM gerando licoes brutas inúteis"
  affects:
    - "hermes/platform/memory/dream.py"
    - "okf/lesson_*.md"
  supersedes: []
  superseded_by: null
---

# ADR-004 — O dream não destila: é cópia determinística por palavra-chave

**Status:** Aceito e implementado (dono autorizou uso de LLM por API em 2026-09-11: "podemos usar um llm por api se quiser")
**Data:** 2026-09-11
**Contexto:** `hermes/platform/memory/dream.py` (`DreamConsolidator.run_dream`)
**ADR relacionadas:** ADR-001 (stack de memória), ADR-002 (re-ranker)

## Contexto e evidência

O `dream` roda 1×/dia (04:30, dentro do `haos-nightly-maintenance`) e é o único passo de
**consolidação** da memória canônica. Auditoria de 2026-09-11 sobre o resultado dele:

- consolidou **33 sessões** e produziu **+2 memórias** — ambas **perguntas cruas do usuário**
  ("quais containers nao estamos usando de fato", "acesse o agentia e veja o tamanho do banco").
- gerou **33 arquivos `okf/lesson_*.md`** que são **cópias dos primeiros ~200 caracteres** da sessão.
  Exemplo literal do `lesson_0596dd.md`:
  `# Compatibilidade de Protocolos em Proxy SOCKS` + `proxy socks nos podemos usar todo tipo de
  protocolo para usa...` — transcrição crua, sem síntese, sem evidência, sem critério de utilidade.
- `memory/instincts/` está **vazio** (0 arquivos): a extração de instinct exige que o preview
  contenha "sempre"/"nunca"/"erro" — e falhas são engolidas em `logger.debug`.

## Causa raiz (código, não hipótese)

`run_dream()` **não chama LLM nenhum**. O algoritmo é:

1. pega as sessões com `started_at > cursor` (limite 50);
2. monta um `preview` = concatenação dos primeiros 200 chars das mensagens;
3. se `len(preview) >= 20`, escreve `okf/lesson_<slug>.md` com **título + preview cru**;
4. **se** o preview contiver `"sempre" | "nunca" | "erro"` → grava um instinct cujo `rule` é
   `preview.split(".")[0][:140]` (a primeira frase crua);
5. **se** o preview contiver `"preferência" | "banco" | "usando" | "migramos" | "framework" |
   "agora usamos" | "não usamos"` → reconcilia um `session_fact` cujo `content` é
   `preview.split(".")[0][:180]` (a primeira frase crua).

Ou seja: **"lição" = primeira frase da sessão que casou uma lista de palavras-chave.** Não há
extração de conhecimento, nem deduplicação semântica, nem descarte de ruído. O gate de palavras
explica por que só 2 de 33 sessões viraram memória, e por que ambas são perguntas.

## Opções

| opção | custo | ganho |
|---|---|---|
| **A. Destilador LLM (recomendado)** — passo novo, pós-dream, que lê as `lesson_*.md` não destiladas e pede 0–3 lições duráveis em JSON estrito (permitindo "nenhuma"), gravando em `okf/licoes/` e reconciliando | 1 chamada LLM por sessão nova (batching de 5–10 sessões por chamada reduz a custo desprezível; deepseek-flash) | lições de fato reutilizáveis; ruído descartado |
| B. Ampliar as listas de palavras-chave | zero | continua cópia crua — não resolve |
| C. Aceitar o dream como arquivo bruto e destilar só sob demanda | zero | mantém 33 arquivos de baixo valor no OKF, que entram no índice |
| D. Desligar o dream | zero | perde-se o cursor/histórico de consolidação |

## Recomendação

**A**, com três travas: (1) o LLM pode responder "nenhuma lição" e aí **não** se grava arquivo;
(2) toda lição cita a sessão de origem (`source_session`) e um trecho de evidência; (3) o passo
roda depois do dream e é idempotente (arquivo destilado não é refeito).

## Implementação (convergida em 2026-09-11)

- **Script**: `/root/.haos/scripts/dream_distill.py` — CLI `--limit N --min-confidence 0.5 --dry-run --json --session ID`.
- **Fluxo**: descobre `okf/lesson_*.md` sem marcador → lê o transcript real da sessão do `state.db`
  (user+assistant, `active=1`, ~1,2k chars/mensagem, teto de 7k chars) → 1 chamada LLM
  (`deepseek-v4-flash`, `temperature 0.2`, JSON estrito) → grava apenas lições com
  `confianca >= 0.5` em `okf/licoes/<slug>-<n>.md` → marcador `okf/licoes/<slug>.done`.
- **Travas implementadas**: o modelo pode responder `{"licoes":[]}` (nada é gravado); toda lição
  carrega `source_session`, `confidence`, `model`, `extracted_at` e um bloco **Evidência** com
  trecho literal; idempotência por marcador; falha de API/JSON **não** grava marcador (retenta).
- **Integração**: passo 3.7 do `hermes_daily_maintenance.py` (04:30), limite 10 sessões/execução,
  com linha no relatório do Telegram.
- **Custo medido**: ~5,5s por sessão (1 chamada); 33 sessões pendentes ≈ 3 min; tokens ~2k in/300 out
  por sessão no `deepseek-v4-flash` (desprezível).
- **Resultado**: as lições cruas ("proxy socks nos podemos usar todo tipo de protocolo...") passaram a
  sair como conhecimento acionável — ex.: *"merino é o SOCKS5 server adotado no HAOS"*, *"Deploy do
  merino via systemd bind-only Tailscale"*, *"Registrar mudanças de infra no REGISTRY.md"*.
- **Eval**: caso `dream-destilador` (frontmatter completo, bloco de evidência, idempotência).
- **Pendência**: os 33 `lesson_*.md` crus continuam no OKF (arquivo histórico do dream); arquivá-los
  ou removê-los é decisão do dono (operação destrutiva).

## Consequências

- Positivas: a camada canônica (hoje praticamente vazia: 2 memórias, 8 entidades) passa a acumular
  conhecimento; o OKF deixa de ser depósito de transcrições cruas.
- Negativas: +1 dependência de LLM no caminho noturno; exige validação de custo/tempo antes de
  entrar em cadência (regra do dono: medir antes de agendar).
- Pendência relacionada: os 33 `lesson_*.md` crus continuam no OKF (e no índice) até serem
  substituídos/arquivados — decisão do dono (remoção é destrutiva).
