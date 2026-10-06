# Auditoria do pipeline de memória HAOS — Etapa 0

**Status:** AUDITADO (código + testes); implementação e dados de produção não foram alterados.
**Referência conceitual GBrain:** `garrytan/gbrain@9a0bae8d62cdd1e0dd6655e24e082fe6c69c5dac` (README pinado; commit identificado via URL pública). Fonte: <https://github.com/garrytan/gbrain/commit/9a0bae8d62cdd1e0dd6655e24e082fe6c69c5dac>. Uso apenas conceitual; nenhum código GBrain foi copiado.

## Resumo executivo

O HAOS já tem base reutilizável substancial: journal canônico SQLite, FTS+vetor/RRF, isolamento por scope, projeções/outbox, staging persistente com journal, promoção por confiança, proveniência e reconciliador. Não há justificativa para criar segundo store ou outro dream. Foram confirmadas lacunas críticas de vigência no recall, ausência de `observed_at`, ausência de frescor/conflito no render, cursor do dream baseado em `started_at` sem delta de mensagem e conflito de dream guardado apenas em staging sem superfície dedicada ao dono.

## Matriz de reuso e lacunas

| Área | Reuso disponível | Lacuna / conclusão | Evidência (arquivo:linha) |
|---|---|---|---|
| Fonte canônica | SQLite journal, registros com escopo/status, `valid_from`/`valid_until`, supersession e outbox | Reusar. Vigência de schema não equivale a filtro nas consultas. | `canonical_store.py:44-60, 89-99, 491-552`; DDL em `canonical_store.py:177-195` |
| Validade temporal | `KnowledgeItem.is_valid_at(timestamp)` existe | **GAP-01 confirmada para Memory Fabric.** `is_valid_at` não tem chamadores no pipeline; consultas FTS e ids exigem somente `status='active'`. `list_records` também não consulta limites temporais. `restore` limpa `valid_until` ao reativar, podendo ressuscitar prazo vencido. | `schemas.py:88-96`; `canonical_store.py:349-395, 491-552`; `packages/haos-edge/src/lib.rs:1088-1148`; busca por símbolo `is_valid_at` retornou só a definição no código HAOS. |
| Observed vs recorded | `MemoryRecord` guarda `valid_from`, `valid_until`; tabela contém `created_at` | **GAP-02 confirmada.** Não há campo `observed_at` no dataclass/DDL. `valid_from` é início da vigência, não captura semântica geral de observação. | `canonical_store.py:44-60, 177-195`; cópia DB real `/root/.haos/memory/fabric.db`, `PRAGMA table_info(memory_records)` mostra `valid_from`, `valid_until`, `created_at`, sem `observed_at`. |
| Render do recall | Hybrid retrieval já faz FTS+vetor/RRF, dedup e hard budget; `provenance=` é emitido | **GAP-03 confirmada, premissa refinada.** Render cita provenance, mas não frescor/idade, validade, conflitos nem nota de lacuna. Não é correto afirmar “sem citação”. | `retrieval.py:135-170, 172-176`; `format_context` devolve vazio em nenhum hit; `provider.py:128-161`. |
| Dream cursor | Cursor incremental persistido; lê no máximo 50 sessões recentes; staging idempotente por `candidate_key`/proveniência | **GAP-04 confirmada por fluxo.** `run_dream` seleciona `started_at > cursor`, cursor global avança ao maior `started_at`; sessão antiga com mensagens novas não cruza condição. Schema tem `last_activity_at` e timestamp por mensagem, mas não existe cursor por mensagem. DB real: 129 sessões, 223147 mensagens, 127 sessões com `last_activity_at > started_at` (observação isolada; não prova sessão reaberta após avanço de cursor). | `dream.py:379-390, 392-423, 445-449, 539`; `hermes_state_common.py:334-395` (inclui `last_activity_at`); mensagens em `hermes_state_common.py:397+`; query DB de produção read-only. |
| Conflitos | `detect_conflicts` existe; roteador chama detecção; staging tem snapshot+journal | **GAP-05 confirmada no caminho do Dream.** O conflito deixa o candidato `rejected` e `run_dream` o reinsere em staging com razão `conflict_with_canonical`. Não foi encontrado consumidor que alerte/inbox o dono para esses candidatos. Não equivale a “perda de dados”: fica persistido, mas sem superfície dedicada de revisão. | `consolidation.py:70+`; `router.py:153-168`; `dream.py:522-528`; `staging.py:208-242`; buscas por `STAGE_REASON_CONFLICT` só acharam definição + dream. |
| Staging e recuperação | Snapshot atômico e delta append-only; recuperação de delta; TTL | Reusar para propostas/conflitos, mas staging não é uma fila operacional visível. | `staging.py:70-149, 208-257` |
| Promoção | Confiança e `MemoryRouter.process_candidate`; consolidação no dream; reexecução de projeções | Reusar, adicionar testes explícitos de aprovação e idempotência. | `router.py:134-170`; `dream.py:523-554` |
| Proveniência | URI de sessão, `evidence_span`, extração datada; proveniência no registro e no render | Reusar. Lacuna não é ausência de origem; é frescor legível no recall. | `dream.py:493-495, 537-543`; `retrieval.py:173-176` |
| Caminho de contexto | `MemoryManager` faz prefetch; contexto é anexado ao conteúdo da mensagem do usuário e delimitado | Sem mutação de system prompt; preservar cache. A injeção só ocorre se contexto não vazio. | `agent/turn_context.py:762-790, 1107-1117`; `agent/memory_manager.py:266-280` |
| Filtros de acesso | Scopes e `MemoryAccessContext`; resolução de ids passa por store canônico | Reusar e manter em cada reader; validade temporal é questão independente de ACL. | `retrieval.py:49-94`; `canonical_store.py:541-552`; `access.py` |

### Readers e pontos de entrada verificados

- **Recall canônico primário:** `HybridMemoryRetriever.retrieve` chama `search_fts` (`retrieval.py:72`) e resolução vetorial por `active_by_ids` (`retrieval.py:88`); formatação por `format_context` (`retrieval.py:135`). Entrada do provider em `provider.py:128-161`, chamada pelo turno em `agent/turn_context.py:762-790`.
- **Readers diretos do store:** `federated_fabric.py:724,732` (projeções/listagens), `:856` (listagem incluindo superseded), `:1044` (listagem) e `:1066` (FTS). São caminhos de consulta/integração que devem ser revisados na etapa temporal.
- **Shadow:** retriever híbrido usado em `shadow.py:193`; `format_context` em `shadow.py:226` (telemetria/cutover, não injeção principal).
- **Python:** `search_fts`, `list_records`, `active_by_ids` definidos em `canonical_store.py:491,529,541`. Não há outro leitor de `active_by_ids` fora do retriever e a função delegadora em `canonical_store.py:167`.
- **Rust/C ABI:** `canonical_engine_search_fts_buffered` declara SQL somente com `status='active'` em `packages/haos-edge/src/lib.rs:1088-1148`. `canonical_engine_active_by_ids_buffered` não foi localizado; não assumir que existe. O C-ABI FTS é invocado em `canonical_store.py:498-516`, mas só existe no repo Rust e harness C-ABI em `lib.rs:1308+`.
- **Limite desta enumeração:** cobre readers canônicos do Memory Fabric e consumidores identificados por busca de símbolos no Python/Rust; não afirma cobrir GraphRAG/Obsidian e provedores externos, que são subsistemas separados.

## Confirmação/refutação das hipóteses do plano

1. **Validade temporal no recall — CONFIRMADA** para as consultas do Memory Fabric Python e FTS Rust. `is_valid_at` não tem chamador na implementação. `restore()` merece revisão adicional junto da vigência.
2. **Sem `observed_at` — CONFIRMADA** no `MemoryRecord` e tabela canônica.
3. **Render sem frescor/lacunas/conflitos — CONFIRMADA COM RESSALVA:** proveniência já é renderizada; frescor, conflitos e ausência honesta não.
4. **Sessão antiga reaberta — CONFIRMADA pela condição e cursor:** `started_at` fixo não representa atividade posterior. Verificação com `state.db` real foi somente leitura; não foi feita escrita/reabertura artificial (e não se deve alterar DB ativo). O schema oferece `last_activity_at` e `messages.timestamp`, que são candidatos a checkpoint.
5. **Destino de conflito — CONFIRMADA COM RESSALVA:** o conflito não é descartado; vira candidato staged persistente. O que falta é superfície ao dono / relatório operacional de pendências, não armazenamento.

## Baseline de testes (observado)

Runner obrigatório: `scripts/run_tests.sh`. Foi necessário configurar `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python` porque o worktree não contém venv. Não foi invocado `pytest` diretamente para o baseline do repo.

- Comando: `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python ./scripts/run_tests.sh tests/platform/context/memory/ tests/platform/memory/` — **281 passed, 0 failed, 30 files**, 7.5 s, runner `-j 16`.
- Comando focal mais amplo, incluindo testes de fabric/dream e o corpus deste relatório: `./scripts/run_tests.sh evals/gbrain_memory/test_memory_baseline.py tests/platform/context/memory/ tests/test_haos_dream_memory.py tests/test_haos_hybrid_memory.py tests/test_haos_memory_reconciler.py tests/platform/context/test_federated_memory_fabric.py tests/platform/context/test_memory_fabric.py tests/platform/context/test_memory_candidates_and_router.py tests/platform/memory/test_memory_governance.py` — **197 passed, 0 failed, 25 files**, 13.4 s. Esse comando é baseline registrado para a Etapa 0.
- Resultado anterior, durante construção dos testes de contrato: 3 falhas nos asserts do teste (expectativas do próprio teste estavam equivocadas); foram corrigidas e a execução final acima passou. Não é falha pré-existente do produto.
- `tests/platform/memory/` não é recursivo em `tests/platform/context/memory/`; o diretório contém testes de subsistemas diferentes. O primeiro comando tinha `tests/platform/memory/` e o resultado real final foi 30 arquivos/281 testes.

## Corpus congelado

Local no checkout: `evals/gbrain_memory/fixtures/corpus_cases.json`; manifesto em `evals/gbrain_memory/fixtures/manifest.json`; harness determinístico em `evals/gbrain_memory/eval_harness.py`; testes de integridade/contrato em `evals/gbrain_memory/test_memory_baseline.py`.

O corpus é **sintético e sem dados de conversa**: não foi possível nem apropriado copiar sessões autorizadas do `/root/.haos/state.db` para o worktree de produção. Seus 12 casos cobrem as cinco hipóteses do plano, com consultas sem IDs na pergunta, separadas por categorias. O manifesto fixa SHA-256 de corpus: `9a314feec639ae68e68e48f2e8cc8af08451a6cac3b674c21b96f145931e0644`. Harness validado: integridade PASS, 12 casos carregados. Isto não é benchmark de qualidade de resposta LLM nem substitute por corpus real; deve ser complementado por corpus autorizado e sanitizado antes das comparações de recall da Etapa 3.

## Recomendação de sequência

Reutilizar `CanonicalMemoryStore`, `HybridMemoryRetriever`, `MemoryStagingStore`, `MemoryRouter`, `DreamConsolidator` e gate existente. Prioridade: contrato temporal aditivo; cobertura de todos readers Python+Rust e `restore`; delta do cursor por sessão/mensagem (usando `last_activity_at` ou timestamp de mensagem com idempotência); superfícies de revisão de conflitos; depois render de frescor sem exceder budget. Não criar store/dream paralelo. Fazer benchmark com dados autorizados só após operador confirmar escopos, remover segredos e guardar corpus fora do código de produção.

## Limitações / pendências

- `dream_distill.py`, `hermes_daily_maintenance.py`, ADRs 004/014/015/017/022 e `promotion_gate.py`/`provenance.py` não foram auditados integralmente nesta rodada; as afirmações de escopo foram limitadas ao código citado. Essa lacuna impede certificar o ciclo noturno e a política de promoção end-to-end.
- Corpus real de state.db não foi copiado, por minimização de dados; precisar autorização explícita e export sanitizado para cumprir a parte de sessões reais do briefing original.
- Não foi executado runner Rust independente; testes Python cobriram o C-ABI de aceleração, mas não substituem suite nativa do crate.
