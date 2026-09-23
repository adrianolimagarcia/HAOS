# Plano — Migração total do escritor lógico de `state.db` para Rust

**Status:** proposto (aguarda aprovação do dono)
**Data:** 2026-09-23
**Contrato base:** `docs/haos/rust-readonly-sse-contract-v1.md` (commit `15373e618c`)
**Decisão em revisão:** "Python único escritor" deixa de ser regra final e vira trava temporária
deste plano. Alvo: **Rust como escritor lógico único; Python vira cliente + leitor.**

---

## 0. Princípios (atitude do harness)

1. **Uma fase por vez, cada uma com gate verde.** Nenhuma fase começa com a anterior vermelha.
2. **Evidência, não afirmação.** Toda fase fecha com: teste executado, saída real, hash/schema
   fingerprint, e o comando de rollback provado.
3. **Flag de corte, sempre.** Toda mudança de escritor é um `config.yaml`
   (`state_writer: python | rust | shadow`), nunca um deploy "mude e reze".
4. **Rollback = flip de flag + restore de backup.** Se rollback não couber numa linha, a fase
   está mal desenhada.
5. **Sem framework novo, sem serviço novo.** Tudo embute no `haos-edge` já existente.
6. **Não mexer no schema.** O schema é contrato; a migração muda *quem escreve*, não *o que* é
   escrito.

## 1. Alvo arquitetural final

```
Python (SessionDB) ──leitura──► state.db ◄──leitura/escrita── Rust (haos-edge, escritor único)
      │                                                              ▲
      └──escritas──► IPC local (HTTP 127.0.0.1 ou unix socket) ──────┘
```

- **Escritor lógico único:** um processo Rust (`haos-edge`) segura um lock de escritor
  (`state.db.writer.lock`, com PID + fingerprint do binário). Segundo holder → fail-closed.
- **Leitores livres:** Python e Rust leem direto em WAL read-only (já testado, 6/6).
- **IPC:** HTTP loopback no edge já existente. **Não** gRPC, **não** fila, **não** CDC/outbox.
- **Operações tipadas**, nunca SQL em texto passando do Python: cada operação espelha um método
  de escrita do `SessionDB` atual (`create_session`, `append_messages`, `update_session`,
  `compress`, ...). Payload versionado (`schema_version` obrigatório).

## 2. Inventário do ponto de partida (já medido)

| Item | Estado hoje |
|---|---|
| `state.db` schema | complexo: FTS5 (3 variações), triggers de `display_order`/`display_identity`, índices de compressão, admission de rebuild FTS multi-processo |
| Escritor Python | `hermes_state*.py` — WAL, quarantine, generation stamp, detecção de arquivo substituído, lock cross-process |
| Escritor Rust atual | `event_hub.rs` → `events.db` (separado); `server.rs` → `INSERT INTO tasks/events` (state.db) — **já há escritor Rust parcial** |
| Leitura Rust | `/api/sessions/fast` e `search` — **sem auth** (bloqueio #1), perfil não vinculado (bloqueio #2), paridade divergente (bloqueio #3) |
| Testes | SQLite/WAL 6/6 ✓; E2E SSE ✓; paridade BLOCKED; A→B→A incompleto (timeout) |
| Multi-processo real | gateway + `haos serve` desktop + cron ticker compartilham o mesmo `state.db` |

## 3. Fases

### Fase 0 — Contrato congelado (1–2 dias, sem tocar em código de escrita)

**Escopo:**
- Extrair `schema.sql` canônico + pragmas + inventário de triggers/índices do `state.db` real →
  fixture versionada.
- Dataset golden: banco fixture com sessões, mensagens, `archived`, `branch`, `hidden`,
  `parent_session_id`, linhas FTS, compressão.
- Golden JSON: saída de cada operação de leitura/escrita atual gravada como esperado.
- Estender o contrato v1 para `rust-writer-contract-v2.md`: operações tipadas, erros,
  `schema_version`, idempotência (`idempotency_key`), timeout.

**Gate:** teste de fingerprint de schema verde; golden fixtures commitadas.
**Rollback:** nada executado, não aplica.

### Fase 1 — IPC + flag (2–3 dias)

**Escopo:**
- Endpoints internos no `haos-edge` (loopback): escritas tipadas + lock de escritor.
- Cliente Python `WriterClient` nos métodos de escrita do `SessionDB`.
- Flag `state_writer` (default `python` → comportamento 100% idêntico ao hoje).
- **Fail-closed:** modo `rust` sem edge vivo = erro explícito, nunca fallback silencioso para
  escrita Python.

**Gate:** suíte Python existente verde com flag `python`; com `rust`, erro correto com edge
abaixo; `cargo test` verde.
**Rollback:** flag `python`.

### Fase 2 — Shadow (comparação, sem risco)

**Importante:** shadow = **banco separado**, nunca dois escritores no mesmo arquivo.
- Python escreve `state.db` (produção, inalterado).
- Rust replica cada operação num `state.shadow.db` (mesmo schema).
- Diff tool: após N operações, dump JSON das tabelas afetadas → comparador determinístico.

**Gate:** 0 divergências em replay de 48h de operações reais (ou fixture longa); divergências
viram teste de regressão antes de qualquer corte.
**Rollback:** apagar shadow, flag continua `python`.

### Fase 3 — Corte por domínio (ordem de risco crescente)

| # | Domínio | Corte | Gate específico |
|---|---|---|---|
| 3a | `events`/`tasks` (já parcialmente Rust) | declarar Rust autoritativo, remover escritor Python equivalente | E2E SSE + readback no SQLite (fecha o bug do evento que não aparecia) |
| 3b | Metadados de sessão (create/update/list) | flag `rust` para esse domínio | paridade payload OK; **A→B→A** completo |
| 3c | Auth/perfil | binding `HERMES_HOME`→`data_dir` + auth obrigatória em todo handler | 401 sem cookie em todas as rotas; A→B→A |
| 3d | Append de mensagens | flag `rust` | golden + compressão + resume/export |
| 3e | FTS/triggers/`display_order`/compressão | **último** | rebuild FTS, triggers, admission multi-processo, recovery |

Cada corte: backup `state.db`+`-wal`+`-shm` → flip flag → probes → rollback = flip de volta.
**Bloqueadores pré-existentes que corte nenhum ignora:** auth em `/api/sessions/fast`, binding
de perfil, paridade de contrato.

### Fase 4 — Remoção dos writes Python

- `SessionDB` vira leitor + `WriterClient`.
- Gate de CI (advisory, estilo `check_profile_scope_patterns.py`): proíbe `INSERT/UPDATE/DELETE`
  em `state.db` fora do cliente.
- Teste de escritor único: probe do lock; dois holders = falha.

**Gate:** suíte completa (Python + Rust) verde; `A→B→A`; gateway 167/167.

### Fase 5 — Operação

- Runbook: backup, rollback, checkpoint WAL (propriedade do escritor Rust), recovery
  (`haos sessions recover`), arquivo substituído / WAL deletado → fail-closed (espelhar os
  guards atuais do Python).
- Observabilidade mínima: latência p95 por operação (baseline já existe: draft p95 6,04 ms) e
  contagem de falhas de lock.

## 4. Critérios de aceitação globais (antes de declarar migração concluída)

1. Schema fingerprint idêntico ao inicial.
2. Golden JSON 100% idêntico Python↔Rust em todas as operações.
3. A→B→A completo e repetível.
4. Suítes Python + Rust verdes; gateway 167/167.
5. Escritor único provado por lock (teste com dois candidatos).
6. p95 de escrita não piora vs. baseline.
7. Rollback ensaiado de verdade, uma vez, em staging.

## 5. O que NÃO fazer (anti over-engineering)

- ❌ gRPC, Kafka, NATS, outbox, CDC, "event sourcing" novo.
- ❌ Novo serviço/daemon separado — embuter no `haos-edge`.
- ❌ Passar SQL bruto do Python ao Rust.
- ❌ Reescrever/migrar o schema durante a migração.
- ❌ Dual-write no **mesmo** arquivo SQLite.
- ❌ Corte grande ("tudo de uma vez") — a ordem 3a→3e existe por motivo de risco.
- ❌ Declarar ganho de RAM/performance sem baseline controlado.

## 6. Riscos principais

| Risco | Mitigação |
|---|---|
| FTS/triggers quebram silenciosamente | Fase 3e por último + golden diff |
| Multi-processo (gateway/desktop/cron) | lock de escritor + admission FTS preservada |
| Perfil errado (A→B→A) | binding explícito antes de qualquer corte (3c) |
| Auth ausente hoje | corrigir antes de 3b (bloqueio conhecido) |
| Resiliência: arquivo substituído/WAL morto | portar guards do Python para fail-closed em Rust |

## 7. Próximo passo imediato

**Fase 0** (contrato + fixtures + golden), 1–2 dias, zero risco de escrita — pode ser disparada
com subagentes em paralelo (fixture/schema, golden de leitura, contrato v2).
