# Auditoria: FailureClassifier Wiring, Categorias e Ordenação Heurística

## 1. Escopo
Auditoria e validação arquitetural do classificador de falhas (`FailureClassifier` em `hermes/platform/tasks/failure_classifier.py`), cobrindo:
1. Mapeamento exato das 11 categorias e políticas de retry em `retry_policy_for`.
2. Detecção e eliminação de hazards de ordenação em `classify()`.
3. Ponto de acoplamento (*wiring*) com o dispatcher de execução (`hermes/platform/execution/dispatcher.py`) e o banco de dados kanban (`hermes_cli/kanban_db_dispatch.py`).

## 2. Achados-Chave

### Categorias e Políticas de Retry (`retry_policy_for`)
- **Problema identificado**: 4 de 11 categorias caíam no fallback genérico `{"action": "escalate", "increments_task_retry": True}`: `PROTOCOL_VIOLATION`, `DEPENDENCY_MISSING`, `CAPABILITY_MISSING` e `HUMAN_INPUT_REQUIRED`.
- **Impacto**: `HUMAN_INPUT_REQUIRED` e `DEPENDENCY_MISSING` queimavam indevidamente o orçamento de retries de tarefa (`consecutive_failures`), acionando o circuit breaker do card antes de intervenção externa.
- **Resolução de contrato**:
  - `PROTOCOL_VIOLATION` -> `{"action": "rework", "increments_task_retry": True}`.
  - `DEPENDENCY_MISSING` -> `{"action": "wait_for_dependency", "increments_task_retry": False}`.
  - `CAPABILITY_MISSING` -> `{"action": "request_capability", "increments_task_retry": False}`.
  - `HUMAN_INPUT_REQUIRED` -> `{"action": "wait_for_human", "increments_task_retry": False}`.

### Ordenação Heurística em `classify()`
Três hazards críticos de precedência textual foram sanados:
1. **OOM vs Socket Reset**: Termos fatais de processo (`oom`, `out of memory`, `sigkill`, `killed`) têm prioridade máxima (`WORKER_CRASH`) antes de checagens de `connection reset` / `broken pipe` (`TOOL_TRANSIENT`).
2. **Tool Timeout vs Provider Error**: Substrings específicas como `tool timeout` e `tool execution timeout` são avaliadas antes da captura genérica de `timeout` / `gateway timeout` (`PROVIDER_ERROR`).
3. **Token Limit vs Auth Error**: Padrões de exaustão de contexto (`token limit`, `max tokens`, `context window exceeded`) são classificados como `BUDGET_EXCEEDED` antes de padrões com a palavra isolada `token` (`AUTH_ERROR`).
4. **Dependência**: Inclusão de ramo explícito para `DEPENDENCY_MISSING` (`dependency missing`, `unmet dependency`, `missing prerequisite`).

### Wiring Real no Dispatcher e Armadilhas de Execução
- Em `hermes/platform/execution/dispatcher.py` (`claim_tick` linhas 210-262), exceções e falhas de lane eram tratadas de modo hardcoded (`outcome="worker_crash"` ou `"contract_violation"`).
- O upstream `_record_task_failure` (`hermes_cli/kanban_db_dispatch.py:1025`) incrementa `consecutive_failures` cegamente (`int(row["consecutive_failures"]) + 1`).
- **Ponto exato de injeção**:
  - No bloco `except Exception as exc:` e na inspeção de `res.get("status") in ("FAILED", "BLOCKED")` em `dispatcher.py`:
  - Invocar `cat = FailureClassifier.classify(str(exc), metadata={"exit_code": ..., "lane": lane})`.
  - Obter `policy = FailureClassifier.retry_policy_for(cat)`.
  - Se `policy["increments_task_retry"] is False`: estacionar o card como `status="blocked"` ou reagendar com cooldown sem invocar `_record_task_failure` destrutivo.
  - Se `policy["increments_task_retry"] is True`: delegar ao `self.adapter.record_task_failure(claimed.id, str(exc), outcome=cat)`.

## 3. Lacunas Acionáveis
1. **Falta de Despacho Condicional no Dispatcher**: `hermes/platform/execution/dispatcher.py` ainda não invoca dinamicamente `FailureClassifier` no tratamento de erros do loop de execução de lanes.
2. **Canal de Propagação de Exit Codes**: `LaneWorker.execute` precisa garantir retorno estruturado com código de saída do processo filho para permitir classificação de `WORKER_CRASH` via código 137 (OOM) ou 143 (SIGTERM).
3. **Mecanismo de Não-Incremento no Kanban Adapter**: O método `KanbanAdapter.record_task_failure` delega diretamente a `kbd._record_task_failure`; deve ser estendido para suportar `increments_task_retry=False` ou expor método específico para bloqueio operacional (`block_task`).

## 4. Status de Implementação
- **`FailureClassifier` Core**:
  - Arquivo: `hermes/platform/tasks/failure_classifier.py`
  - Status: **Implementado**. 11 categorias tratadas em `retry_policy_for`, ordenação corrigida e ramo de dependência adicionado.
- **Cobertura de Testes**:
  - Arquivo: `tests/platform/tasks/test_task_posture.py`
  - Status: **Parcialmente Coberto**. Validações de `PROVIDER_ERROR` e `REVIEW_REJECTION` presentes. Necessário suite abrangente testando os hazards de regex.
- **Wiring no Dispatcher**:
  - Arquivo: `hermes/platform/execution/dispatcher.py`
  - Status: **Pendente de Refatoração**. Permanece utilizando `outcome="worker_crash"` de forma estática.

## 5. Recomendações
1. **Conectar Dispatcher ao Classificador**: Substituir as atribuições estáticas de `outcome` em `dispatcher.py` por chamadas a `FailureClassifier.classify`.
2. **Respeitar `increments_task_retry`**: Quando o classificador indicar que o erro é externo (ex: `AUTH_ERROR`, `HUMAN_INPUT_REQUIRED`, `DEPENDENCY_MISSING`), suspender o card em estado bloqueado ou aguardando dependência sem queimar tentativas do desenvolvedor/tarefa.
3. **Adicionar Testes Parametrizados**: Criar arquivo dedicado `tests/platform/tasks/test_failure_classifier.py` cobrindo todos os casos limítrofes de ordenação de strings.
