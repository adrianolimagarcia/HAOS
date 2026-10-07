# Plano detalhado — absorção seletiva GBrain → HAOS (v2, pronto para subagentes)

Status: PROPOSTO (v2). Escopo: planejamento executável via delegate_task; não autoriza deploy, cron novo ou captura automática de conversas. Referência GBrain: commit fixado na Etapa 0 (não fixado ainda).

## 0. Estado real verificado (auditoria de 2026-10-06, código em /usr/local/lib/haos-agent)

O que JÁ existe (não recriar):

| Capacidade | Onde | Estado |
|---|---|---|
| Store canônico com journal, supersession, valid_from/valid_until, projections com lease/claim/ack/fail | `hermes/platform/context/memory/canonical_store.py` | Implementado; testes em `tests/platform/context/memory/test_canonical_store.py`, `test_supersession_policy.py` |
| Retriever híbrido FTS+vetor (RRF), ACL por escopo, budget com floor | `hermes/platform/context/memory/retrieval.py` | Implementado; `test_retrieval.py`, `test_recall_budget_and_sync.py` |
| Schema com `is_valid_at()` (ADR-022 TEMPR) | `hermes/platform/context/memory/schemas.py:88` | Implementado **mas sem chamadores no recall** (ver lacuna 1) |
| Dream incremental com cursor, staging P11, gate de confiança 0.85, reconciler, git store | `hermes/platform/memory/dream.py` (`DreamConsolidator`) | Implementado; `tests/platform/memory/test_dream_sanitization.py` |
| Destilador LLM das lições cruas (ADR-004), passo 3.7 da manutenção noturna 04:30 | `/root/.haos/scripts/dream_distill.py` + `hermes_daily_maintenance.py:482,715` | Implementado e agendado |
| Proveniência com âncoras verificáveis (`SourceRef`, `EvidenceChain`, `verify_ref`) | `hermes/platform/memory/provenance.py` | Implementado; eval `eval_proveniencia.py` |
| Gate de promoção com juiz LLM + evals + red-before | `hermes/platform/memory/promotion_gate.py` | Implementado |
| Staging persistente com journal, recover_from_delta, TTL 30d | `hermes/platform/context/memory/staging.py` | Implementado |
| Dedup + detecção de conflitos de candidatos | `hermes/platform/context/memory/consolidation.py` | Implementado |
| Shadow/cutover, migration dry-run, observability chaos | `hermes/platform/context/memory/{shadow,migration}.py` etc. | Implementado |

Lacunas REAIS verificadas (o que o plano ataca):

1. **Validade temporal não é aplicada no recall.** `search_fts`/`list_records`/`active_by_ids` filtram apenas `status='active'`; um registro com `valid_until` no passado continua ativo e é retornado. `is_valid_at()` não tem chamador fora de `schemas.py`. (Etapa 2)
2. **Sem distinção observed_at vs recorded_at.** `MemoryRecord` tem `valid_from/created_at`, mas nada separa "quando foi observado" de "quando foi registrado" — frescor de evidência fica ambíguo. (Etapa 1)
3. **Recall não declara lacunas/frescor.** `_render()` já emite `provenance=`, mas não há aviso de idade da evidência, conflitos conhecidos ou "não encontrado ≠ inexistente". (Etapa 3)
4. **Dream não processa mensagens de sessões antigas reabertas** (cursor é por `started_at`/timestamp de sessão; verificar comportamento exato na Etapa 0 do executor — hipótese a confirmar, não fato). (Etapa 4)
5. **Sem política de conflito automática vs. humana documentada no caminho do dream** — `detect_conflicts` existe em `consolidation.py`; falta verificar se o resultado conflituoso é surfacado ao dono ou engolido. (Etapa 4)

Correções de premissas da v1:
- "Consolidação depende de intervenção humana" — FALSO em parte: dream + destilador rodam sozinhos. O problema real é qualidade/promoção, não ausência.
- "Recall não cita fontes" — FALSO: `_render` já emite provenance. Falta frescor/lacuna, não citação.
- "TTL/retratação ausente" — PARCIAL: schema e store suportam; falta aplicação no recall e tombstone em reimportação.

## Princípios (inalterados)
- Estender produtores/readers existentes; proibir dream/store paralelo.
- Índices são projeções reconstruíveis; fonte canônica intocada.
- Memória é dado, não instrução (extração nunca altera permissões/identidade).
- Escopo/perfil delimita leitura/escrita/recall; segredos nunca saem do host.
- Zero mutação de contexto de conversas abertas (cache sagrado): filtros no recall explícito; injeção nova só na próxima sessão.
- Propostas não entram no recall canônico até promoção aprovada (gate existente).

---

## Etapa 0 — Auditoria profunda + baseline (subagente único, sequencial)

**Goal do subagente:** auditar o pipeline de memória ponta a ponta e produzir matriz de reuso + baseline de avaliação.

**Contexto obrigatório do brief:**
- Repo: `/usr/local/lib/haos-agent` (venv em `venv/`; testes via `scripts/run_tests.sh`).
- Ler primeiro: `hermes/platform/context/memory/{canonical_store,retrieval,schemas,provider,consolidation,staging,projection_runner}.py`, `hermes/platform/memory/{dream,promotion_gate,provenance,reconciler}.py`, `/root/.haos/scripts/{dream_distill.py,hermes_daily_maintenance.py}` (passo 3.7), ADRs 004/014/015/017/022 no vault `/root/.haos/obsidian_vault/adrs/`.
- Fixar commit do GBrain (`garrytan/gbrain`) usado como referência conceitual; registrar hash no relatório. Nada de código TS será copiado — apenas conceitos; se algum trecho for transcrito, registrar licença/autoria.

**Tarefas:**
1. Confirmar as 5 lacunas acima linha a linha (arquivo:linha) — corrigir o plano se alguma premissa cair.
2. Verificar comportamento do cursor do dream com sessão antiga reaberta (ler `run_dream` + query no `state.db` de teste).
3. Verificar destino de conflitos detectados (`detect_conflicts` → quem consome?).
4. Mapear todos os readers de recall (quem chama `HybridMemoryRetriever`, `search_fts`, `active_by_ids`, `format_context`) — lista exaustiva com arquivo:linha. Incluir caminhos Rust (`packages/haos-edge`, C-ABI `canonical_engine_search_fts_buffered`) — filtro de validade terá que valer nos dois.
5. Rodar baseline: `scripts/run_tests.sh tests/platform/context/memory/ tests/platform/memory/` (tempo, falhas pré-existentes).
6. Congelar corpus de avaliação: selecionar sessões autorizadas do `state.db` (sem segredos — aplicar `_sanitize_session_preview` como referência), gerar perguntas de recall SEM pistas de IDs/código, separar dev/eval.

**Entregáveis:** `AUDITORIA-MEMORIA.md` (matriz reuso/lacuna com arquivo:linha), baseline de testes, corpus congelado em `/root/.haos/evals/data/gbrain-absorcao/` com manifesto.
**Aceite:** cada lacuna confirmada ou refutada com evidência de código; lista de readers completa (Python E Rust); corpus com checksum no manifesto.
**Kanban:** card `gbrain-absorcao/etapa-0-auditoria`.

---

## Etapa 1 — Contrato de ciclo de vida temporal (subagente, após Etapa 0)

**Goal:** estender o schema existente com observed_at e política de vigência, sem migração destrutiva.

**Contexto:** `canonical_store.py` (tabela `memory_records`: `valid_from REAL NOT NULL, valid_until REAL`), `schemas.py` (`KnowledgeItem.is_valid_at`), ADR-015 (proveniência causal), ADR-022 (TEMPR). Migrações existentes em `_migrate()` — padrão aditivo.

**Tarefas:**
1. Adicionar `observed_at` (opcional, default = valid_from) em `MemoryRecord` + coluna via `_migrate()` aditiva. Semântica: `observed_at` = quando o fato foi verdadeiro no mundo; `valid_from`/`created_at` = quando registrado. Documentar no docstring da classe.
2. Definir política escrita (novo contrato OKF `okf/contratos/ciclo-de-vida-memoria-temporal.md`): estados proposed/active/superseded/retracted; expiração é calculada (`valid_until` no passado ⇒ inativo no recall), nunca reescrita; registros legados sem data ⇒ frescor desconhecido, nunca inventar vigência.
3. Relógio injetável: funções de validade recebem `now: float | None` (default `time.time()`) para testes determinísticos.
4. ADR nova (ADR-0xx) registrando a decisão, com bloco `prov:` no padrão ADR-015.

**Testes (invariantes, red no base onde aplicável):** limite de validade inclusivo/exclusivo (`[valid_from, valid_until)`), registro legado sem datas, migração aditiva em DB existente com dados, relógio injetado.
**Aceite:** `scripts/run_tests.sh tests/platform/context/memory/` verde; migração testada contra DB real de cópia; contrato OKF salvo.
**Kanban:** `gbrain-absorcao/etapa-1-contrato-temporal`.

---

## Etapa 2 — Retratação/validade aplicada em TODOS os readers (subagente, após Etapa 1)

**Goal:** nenhum caminho de recall retorna registro expirado ou retraído, inclusive após reindexação/restart.

**Contexto:** lacuna 1 confirmada. Readers mapeados na Etapa 0 (Python: `retrieval.py`, `canonical_store.py` queries; Rust: `packages/haos-edge` C-ABI `canonical_engine_search_fts_buffered`). `restore()` em `canonical_store.py:349` é o caminho de ressurreição — deve checar vigência.

**Tarefas:**
1. Adicionar filtro de vigência no SQL dos readers: `AND (valid_until IS NULL OR valid_until > ?now)` — no Python E na engine Rust (alterar os dois, mesmo contrato; teste de paridade).
2. `restore()` não pode reativar registro com `valid_until` expirado sem limpar/extender vigência — decidir: restore falha com motivo explícito (preferido: explícito e auditável).
3. Tombstone de reimportação: importação/reaquecimento de índice (vector_index, graphrag) consulta `status` + vigência do store canônico antes de emitir; registro morto nunca volta por reindexação.
4. "Retract" explícito: operação que marca `status='retracted'` + motivo + ator (hoje só existe supersession automática). Escrever via `append` de revisão ou update auditado — seguir padrão do journal.
5. Forget ≠ apagar: documento no contrato da Etapa 1; exclusão física fica fora do escopo.

**Testes (invariantes):** recall não retorna expirado (FTS, vetor, `active_by_ids`, `format_context`); restore de expirado falha com motivo; reindexação após retract não ressuscita; paridade Python/Rust no mesmo DB; A→B→A de perfis não vaza registro de um para outro.
**Aceite:** suite verde incluindo `test_supersession_policy.py` estendido; grep prova zero caminho de recall sem filtro (lista da Etapa 0 como checklist).
**Kanban:** `gbrain-absorcao/etapa-2-retratacao`.

---

## Etapa 3 — Recall fundamentado com frescor e lacunas (subagente, após Etapa 2)

**Goal:** bloco de recall declara origem, frescor da evidência e lacunas — sem inflar budget e sem gate LLM extra.

**Contexto:** `_render()` em `retrieval.py:156` já emite `[memory:<id> scope=<s> provenance=<uris>]`. Budget é contrato rígido (`format_context` hard-capped) — qualquer campo novo consome bytes do bloco injetado por turn.

**Tarefas:**
1. Estender `_render`: `observed=<data ISO curta>` (de `observed_at`; "freshness unknown" se ausente) + flag `expired-soon` se faltarem <7d para `valid_until`. Orçamento: header ≤ ~60 chars extras por hit; medir impacto no `test_recall_budget_and_sync.py`.
2. Conflitos: surfacar conflito REAL — registros ativos distintos com conteúdo contraditório detectados por `detect_conflicts` ganham nota `[conflict: <ids>]` no bloco. Fonte: query barata no store, não LLM.
3. Empty-result honesto: `format_context` retorna string vazia hoje; o provider (`provider.py::prefetch`) deve emitir bloco explícito "nenhuma memória canônica encontrada para esta consulta" APENAS quando há query (nunca em turn sem recall) — verificar onde `prefetch` é chamado no `gateway/run.py` para não poluir turns sem busca.
4. Diretriz de resposta (skill, não código): atualizar skill de memória/recall do HAOS com a regra "afirmação factual cita o memory:id; estado de infra exige checagem live; ausência nas fontes ≠ inexistência". Idade sozinha não prova obsolescência.
5. Cache: nada disso altera system prompt ou toolset — só o conteúdo do bloco de recall, que já é dinâmico por turn. Confirmar que o bloco é append ao user message (é, por `format_context`) — zero risco de cache.

**Testes:** render com observed_at presente/ausente/expirando; budget respeitado com headers novos; conflito surfacado; empty-result só com query; suite de retrieval verde.
**Aceite:** diff do bloco de recall ≤ limite acordado; latência/tokens medidos antes/depois no corpus da Etapa 0.
**Kanban:** `gbrain-absorcao/etapa-3-recall-fundamentado`.

---

## Etapa 4 — Dream incremental: mensagens novas + conflitos surfacados (subagente, após Etapa 1; paralelizável com 2 e 3)

**Goal:** evoluir o dream existente (não criar outro) para processar mensagens novas/alteradas e surfacar conflitos ao dono.

**Contexto:** `DreamConsolidator` (`dream.py:317`) com cursor `.dream_cursor`, staging P11, gate 0.85, `MemoryReconciler`. Destilador ADR-004 no passo 3.7. Lacunas 4 e 5 da auditoria.

**Tarefas:**
1. Cursor por mensagem: estender cursor para (session_id, last_message_ts) — sessão antiga reaberta com mensagens novas é reprocessada só no delta. Comportamento atual confirmado na Etapa 0; migração do formato do cursor com fallback legado.
2. Leitura SQLite limitada e consistente: query por `updated_at`/rowid das mensagens, teto de chars por sessão (padrão do destilador: ~1,2k/msg, 7k teto), lote máximo por execução.
3. Sanitização ANTES de qualquer envio a LLM: reusar `_sanitize_session_preview` + varredura de segredos do cofre (padrão do `dream_distill.py`); conteúdo de transcript é dado não confiável (injeção) — prompt de extração instrui tratar como dados.
4. Extração 0..N candidatos tipados (`MemoryCandidate` existente); "nenhum" é resposta válida; perguntas/hipóteses/alegações do agente NUNCA viram fatos — classificador existente (`classify_experience`, `MemoryRouter.classify_destination`) estendido com regra explícita.
5. Conflitos: `detect_conflicts` roda sobre candidatos novos vs. store ativo; conflito NÃO é resolvido por confiança de LLM — grava em `memory/conflicts/` (ou fila equivalente) para revisão do dono, com os dois lados + proveniência.
6. Idempotência/recuperação: checkpoint só após persistência do resultado (staging journal já dá isso — usar, não inventar); retry não duplica (candidate_key + idempotency_key do store).
7. Promoção continua pelo gate existente (`promotion_gate.py`): em modo proposta, nada promove sem aprovação; o gate automático só vale para o caminho atual já autorizado.

**Testes:** sessão reaberta processa só delta; crash entre extração e persistência recupera sem duplicata; segredo sintético no transcript não sai do host; pergunta não vira fato; conflito gera fila de revisão; dois perfis isolados.
**Aceite:** reexecução N vezes = mesmo resultado; suite `tests/platform/memory/` verde; relatório do dream inclui contagem de conflitos pendentes.
**Kanban:** `gbrain-absorcao/etapa-4-dream-incremental`.

---

## Etapa 5 — Piloto noturno e operação (dono + agente, após 2–4)

**Tarefas:**
1. Piloto manual: rodar dream incremental em dry-run sobre 1 semana de sessões; dono revisa propostas e conflitos; medir precisão de promoção e utilidade.
2. Teto monetário configurado ANTES de qualquer chamada paga (regra do dono); custo por execução no relatório.
3. Agendamento: reusar o timer existente (`hermes-daily-maintenance.timer`, 04:30) — passo novo no `hermes_daily_maintenance.py`, NÃO timer novo. Lock por escopo, timeout, lote/token/custo máximos, backoff limitado, `systemd-run --unit=<n> --collect` se lançado do Hermes (lição de memória: trabalho longo morre no fim do turno).
4. Alerta só em evento real: falha de execução, conflito novo pendente, teto excedido. Sucesso = silencioso + linha no relatório diário existente.
5. Janela de observação: 7 noites em modo proposta antes de qualquer promoção automática nova.
6. REGISTRY atualizado + ADR de operação; runbook em `okf/` (como diagnosticar dream travado, cursor corrompido, fila de conflitos).

**Aceite:** 7 noites sem sobreposição/impacto interativo; orçamento respeitado; conflitos revisados pelo dono; rollback ensaiado.

---

## Avaliação e gates (transversal)

- Corpus congelado da Etapa 0; perguntas sem pistas; dev/eval separados.
- Casos obrigatórios: preferência corrigida (supersession), regra temporária expirada, contradição, evidência antiga, sem data, segredo, injeção em transcript, sessão reaberta, falha parcial, dois perfis.
- Métricas: precisão de promoção (revisão humana), recall relevante, citações válidas, **zero retorno de retraído/expirado**, erros de frescor, duplicação, custo, latência, recuperação pós-crash. Confiança autodeclarada de LLM não é prova.
- Gates absolutos: zero vazamento de perfil/segredo; zero recall de retraído nos casos definidos; zero promoção não aprovada no piloto.
- Runner: `scripts/run_tests.sh` (nunca pytest direto); Rust: runner do crate confirmado na Etapa 0. E2E com dois HERMES_HOME temporários (A→B→A), não só mocks.

## Sequenciamento e execução via delegate_task

```
Etapa 0 (auditoria) ──┬─→ Etapa 1 (contrato) ──┬─→ Etapa 2 (retratação) ──→ Etapa 3 (recall)
                      └─→ (confirma lacunas)    └─→ Etapa 4 (dream)  [paralelo com 2/3]
                                                        └──────→ Etapa 5 (piloto, dono)
```
- Etapa 0: 1 subagente, sequencial, bloqueia todo o resto.
- Etapas 2 e 3: MESMO subagente em sequência (toca os mesmos arquivos de retrieval — evitar conflito de escrita).
- Etapa 4: subagente separado em paralelo com 2/3 (arquivos disjuntos: `dream.py`/`consolidation.py` vs `retrieval.py`/`canonical_store.py` — EXCETO `canonical_store.py` se Etapa 4 precisar de query nova; resolver na hora: Etapa 4 espera merge da 2 nesse arquivo).
- Cada subagente: workspace isolado, branch própria, commit por etapa, relatório com evidência (arquivo:linha + output de teste). Kanban: criar card ao despachar, completar com evidência.
- Conflito de merge entre 4 e 2/3: Etapa 4 rebase e roda suite completa antes de entregar.

## Rollback
- Feature flags no config (padrão `flags.py` existente), desligadas por default.
- Migrações aditivas; backup do `state.db`/stores antes de cada etapa com escrita.
- Índices reconstruíveis respeitam tombstones mesmo com flag off.
- Rollback nunca reativa fato retirado (tombstone sobrevive a downgrade de código).
- Parar job/promoção ≠ apagar fontes.

## Fora do escopo (inalterado)
Instalar GBrain; reescrever runtime; novo banco canônico; captura irrestrita de chats; importação automática de emails/reuniões; exclusão destrutiva; otimização Rust sem bottleneck medido; timer novo ao lado do existente.

## Backlog de execução (cards Kanban)
1. ~~`gbrain-absorcao/etapa-0-auditoria`~~ — DONE (corpus congelado, AUDITORIA-MEMORIA.md)
2. ~~`gbrain-absorcao/etapa-1-contrato-temporal`~~ — DONE no main (`b5884daf9e`, 2026-10-07; ADR-028 + OKF ciclo-de-vida + test_temporal_lifecycle.py)
3. ~~`gbrain-absorcao/etapa-2-retratacao`~~ — DONE no main (`7749a0087f`, 2026-10-07; C-ABI v2 com filtro valid_from/valid_until; 152 testes verde)
4. `gbrain-absorcao/etapa-3-recall-fundamentado` — READY (próxima; mesmo arquivo de retrieval que a 2, rodar após este merge)
5. `gbrain-absorcao/etapa-4-dream-incremental` — READY (paralelizável com a 3; arquivos disjuntos)
6. `gbrain-absorcao/etapa-5-piloto-operacao` — bloqueada por 3 e 4 (dono + agente)
