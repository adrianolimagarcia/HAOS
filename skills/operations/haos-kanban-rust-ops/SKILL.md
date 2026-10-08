---
name: haos-kanban-rust-ops
description: Auditoria e integridade do Kanban Rust nativo.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: operations
tags: [haos, kanban, rust, database, sqlite, operations, diagnostics]
---

# HAOS Kanban Rust Operations Skill

Diagnóstico governado, auditoria de integridade e runbook de reconciliação para o Kanban Rust nativo e store SQLite do HAOS.

## When to Use

Use para verificar a integridade da base de dados do Kanban nativo (`kanban.db` / `events.db`), auditar consistência entre eventos e estados de tarefas, checar locks de concorrência e executar reconciliação de dados sem corrupção.

## Prerequisites

- Ferramentas SQLite instaladas (`sqlite3` CLI ou bindings Python).
- Acesso de leitura à base de dados configurada (`HAOS_KANBAN_DB` ou diretório sob `get_hermes_home()`).
- Binário ou serviços relacionados ao motor Rust (`haos-edge` / `haos-civ`) acessíveis para validação de endpoints.

## How to Run

Execute diagnósticos de consistência e auditoria de forma não invasiva:

```bash
sqlite3 ~/.haos/kanban.db "PRAGMA integrity_check;"
sqlite3 ~/.haos/events.db "PRAGMA quick_check;"
```

## Quick Reference

| Verificação | Comando / Caminho | Ação |
|---|---|---|
| Integridade SQLite | `sqlite3 $DB "PRAGMA integrity_check;"` | Validação estrutural do arquivo |
| Tarefas ativas | `sqlite3 $DB "SELECT status, count(*) FROM tasks GROUP BY status;"` | Sumário de estados |
| Eventos órfãos | `sqlite3 $DB "SELECT count(*) FROM task_events WHERE task_id NOT IN (SELECT id FROM tasks);"` | Checagem referencial |
| Locks ativos | `fuser $DB 2>/dev/null` ou `lsof $DB` | Detectar escritores ativos |

## Procedure

1. **Diagnóstico Read-Only**:
   - Execute `PRAGMA integrity_check;` no banco de dados SQLite para checar páginas corrompidas.
   - Audite a contagem de cards por status (BACKLOG, READY, IN_PROGRESS, REVIEW, DONE, BLOCKED).
   - Verifique se existem tarefas presas em `IN_PROGRESS` sem subagente ou processo ativo associado.
   - Analise se o arquivo WAL (Write-Ahead Logging) não está crescendo indefinidamente.

2. **Pré-condições**:
   - Confirmar se o daemon escritor único (`haos-edge` ou runtime local) não está realizando checkpoints ou transações longas.
   - Assegurar que tanto `HAOS_HOME` quanto `HERMES_HOME` estão direcionados ao mesmo target canônico para evitar split-brain.

3. **Snapshot**:
   - Realizar backup binário consistente via SQLite backup API ou cópia com lock de leitura:
     `sqlite3 ~/.haos/kanban.db ".backup '/tmp/kanban_backup_$(date +%s).db'"`
   - Preservar os arquivos de log e journal correspondentes.

4. **Autorização**:
   - Qualquer operação de reconciliação manual (como `DELETE`, `UPDATE` direto de status ou `VACUUM INTO`) EXIGE autorização formal do operador.
   - O plano de reparo deve detalhar os IDs de registros afetados e a consulta SQL exata.

5. **Rollback**:
   - Em caso de inconsistência gerada por migração ou reparo, restaurar o arquivo de banco a partir do backup em snapshot:
     `cp /tmp/kanban_backup_<timestamp>.db ~/.haos/kanban.db`

## Pitfalls

- **Edição direta de SQLite com concorrência aberta**: gravar sem transação enquanto o motor Rust grava causa `database is locked` ou corrupção de WAL.
- **Divergência entre `HAOS_HOME` e `HERMES_HOME`**: scripts rodando em ambientes diferentes podem gravar em bancos separados, gerando tarefas fantasmas.
- **Apagar tarefas sem reconciliar eventos**: o modelo do HAOS baseia-se em Event Sourcing; deletar linhas da tabela de tarefas quebra a trilha canônica de auditoria.

## Verification

- Confirmar que `PRAGMA integrity_check` retorna exatamente `ok`.
- Verificar se a contagem de cards no WebUI/TUI bate com os totais extraídos do banco SQLite.
- Garantir que todas as foreign keys e índices continuam íntegros após qualquer manutenção.
