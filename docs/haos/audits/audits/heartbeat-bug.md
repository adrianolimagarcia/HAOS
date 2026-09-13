# Auditoria: Heartbeat Claim Bug & Teste de Regressão

## 1. Escopo
Auditoria e correção do mecanismo de renovação de lease (`heartbeat`) entre a camada de adaptação de tarefas (`KanbanAdapter`), o banco de dados canônico (`kanban_db`), e a execução orquestrada (`wait_with_heartbeat` / `Dispatcher`).

## 2. Bug Raiz
- **Onde**: `hermes/platform/tasks/kanban_adapter.py`, método `heartbeat`, linha ~252.
- **Causa**: A função `kb.heartbeat_claim(conn, task_id, claimer=worker_id)` em `hermes_cli/kanban_db.py` retorna um booleano estrito (`True` se a query atualizou o claim ativo, `False` se a task não existe, já finalizou ou pertence a outro worker). No `kanban_adapter.py`, a verificação estava codificada como:
  ```python
  result = kb.heartbeat_claim(conn, task_id, claimer=worker_id)
  ok = result is not None
  ```
  Como tanto `True` quanto `False` são diferentes de `None`, a variável `ok` avaliava **sempre como `True`**, mascarando rejeições legítimas de heartbeat.

## 3. Achados-Chave
1. **Liveness Quebrada**: Em `hermes/platform/execution/heartbeat.py`, `wait_with_heartbeat` monitora subprocessos via `heartbeat_fn`. Se o claim expirar ou for perdido para outro worker, o esperado é levantar `HeartbeatLostError` e matar o grupo de processos do filho. Com `ok = True`, o processo zumbi nunca era finalizado.
2. **Atualização Fantasma de Run**: `_touch_run(task_id)` continuava sendo invocado mesmo quando a posse do card havia sido revogada no SQLite.
3. **Inconsistência no Dispatcher**: Em `hermes/platform/execution/dispatcher.py`, `_run_and_complete_with_heartbeat` delegava a checagem ao adapter com o `worker_id` assumido; falhas de heartbeat passavam despercebidas até a tentativa final de conclusão.

## 4. Lacunas Acionáveis
- **Tratamento de Exceptions de Resolução**: `_require_resolved(task_id_or_spec)` levanta `KeyError` para cards inexistentes, o que difere de falha de claim (`False`). Garantir que os callers diferenciem erro de integridade de lease expirado.
- **Sincronização de Status**: Assegurar que ao receber `False`, o runtime não apenas mate o processo local, mas limpe metadados de execução pendente em `haos_task_runs`.

## 5. Status de Implementação
- **Correção no Código**: Aplicada em `hermes/platform/tasks/kanban_adapter.py` com `ok = bool(result)`.
- **Testes de Regressão**: Implementados em `tests/platform/tasks/test_task_posture.py`:
  - `TestTaskAndPosture.test_basic_lifecycle`: adicionada asserção de que heartbeat por worker estranho (`worker-stranger`) retorna `False`.
  - `TestTaskAndPosture.test_unknown_task_raises`: valida que card inexistente gera `KeyError`.
  - `TestTaskAndPosture.test_heartbeat_unclaimed_or_stranger`: valida que card não claimado (`status="READY"`) retorna `False`, que worker legítimo retorna `True` e que terceiro worker retorna `False`.

## 6. Recomendações
1. Manter a tipagem explícita `bool` nos contratos de adaptadores (`def heartbeat(...) -> bool:`).
2. Adicionar linter rule ou teste de tipagem estática (mypy/pyright) para alertar sobre comparações `x is not None` em funções anotadas com retorno `bool`.
3. Validar telemetria de perda de claim no dispatcher para emissão de alertas quando `HeartbeatLostError` for disparado.
