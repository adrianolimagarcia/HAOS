# Auditoria: ExecutionPlan e DependencyEdge Validation

## 1. Escopo
Auditoria e consolidação técnica dos artefatos do subsistema de tarefas (`hermes/platform/tasks`):
- `DependencyEdge.kind`, `TaskSpec.strategy` e `TaskSpec.reuse` contra tipagem fraca e typos silenciosos.
- Ciclo de vida e persistência de `ExecutionPlan` e `DelegationPlan` no KanbanAdapter (`haos_task_meta`).

## 2. Achados-chave
- **Tipagem Fraca / Sem Fail-Fast em Tasks:** `DependencyEdge.kind`, `TaskSpec.strategy` e `TaskSpec.reuse` eram aceitos como `str` livres sem validação em instanciação (`__post_init__`). Valores incorretos podiam propagar em runtime até quebras tardias em `classify_execution`.
- **ExecutionPlan e DelegationPlan Órfãos:** As classes estavam definidas em `execution_plan.py`, porém sem persistência no banco SQLite upstream (`kanban_adapter.py`) e sem pontos de leitura (`get_plan` / `store_plan`), impedindo a rastreabilidade do raciocínio arquitetural e passos de execução.

## 3. Lacunas Acionáveis
1. **Validação Eager:** Garantir fail-fast imediato no construtor de `DependencyEdge` e `TaskSpec`, alinhando `reuse` com `hermes.platform.execution.classify.InvalidReuseError`.
2. **Esquema de Banco e Métodos de Acesso:** Adicionar coluna `plan_json` na tabela `haos_task_meta` (com migração idempotente `ALTER TABLE`) e métodos públicos `store_execution_plan` e `load_execution_plan` no `KanbanAdapter`.
3. **Visibilidade do Plano:** Integrar `plan` ao dicionário retornado por `KanbanAdapter.get_task`.

## 4. Status de Implementação
- **Validação Eager de Enums/Literals:** Implementado em `hermes/platform/tasks/spec.py`. Valida `DependencyEdge.kind` contra `("requires", "informs", "produces_for", "review_of", "invalidates")`, `TaskSpec.strategy` contra `("auto", "direct", "single_worker", "orchestrated", "parallel", "ensemble", "goal")` e `TaskSpec.reuse` contra `REUSE_VALUES` (reutilizando `InvalidReuseError`).
- **Persistência de ExecutionPlan no KanbanAdapter:** Implementado em `hermes/platform/tasks/kanban_adapter.py`. Tabela `haos_task_meta` atualizada com `plan_json TEXT`, e métodos `store_execution_plan(task_id, plan)`, `load_execution_plan(task_id)` e `_load_plan(task_id)` adicionados. `KanbanAdapter.get_task` agora inclui chave `"plan"`.
- **Testes Unitários:** Adicionados testes `test_spec_validation_fail_fast` e `test_execution_plan_persistence` em `tests/platform/tasks/test_task_posture.py` cobrindo validação e persistência round-trip.

## 5. Recomendações
- **TaskEngine / Dispatcher Hook:** Conectar a geração do plano pelo `architect` antes da fase `in_progress`, armazenando-o via `adapter.store_execution_plan` para permitir acompanhamento de `advance_step` pelo executor de lane.
- **Enums Estritos:** Avaliar migração futura para `Enum` ou `StrEnum` (Python 3.11+) mantendo compatibilidade de serialização JSON.
