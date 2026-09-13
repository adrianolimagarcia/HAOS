# Auditoria: Agno Typed Contracts — port clean-room para a fronteira TaskSpec→lane

## 1. Escopo
Auditoria read-only (sem importar/executar código Agno) do clone de referência
(`libs/agno/agno/` em TEMP/references/agno) para fundamentar a camada de
contratos tipados de I/O do HAOS (`hermes/platform/execution/contracts.py`):

- O que é "contrato" no Agno: onde vivem os schemas estruturados de
  entrada/saída, como validação/falha é superfície, como guardrails/validators
  se ligam e como times trocam mensagens tipadas entre membros.
- Semântica soft vs hard de guardrails/hooks e o tratamento de cada falha.
- Mapeamento para o que o HAOS já cobre (anti-duplicação) e o design aditivo
  entregue (FieldContract/TaskIOContract/ContractGuard + campo em TaskSpec).

## 2. Achados-chave (contracts tipados)
1. **Contrato = par `input_schema`/`output_schema` na fronteira de cada
   Agent/Team** (pydantic `BaseModel` ou dict JSON do provider): `Agent.input_schema`
   `agent/agent.py:301`, `output_schema` `:304`; `Team.input_schema`
   `team/team.py:294`, `output_schema` `:297`. Knobs de saída: `parser_model`
   `:306`, `output_model` `:310`, `parse_response` `:315`, `structured_outputs`
   `:317`, `use_json_mode` `:319`.
2. **Entrada:** `validate_input()` `utils/agent.py:1349-1400` — sem schema ⇒
   pass-through; falha ⇒ `ValueError` (JSON inválido, classe errada, dict fora do
   schema). Chamado em `agent/_run.py:1336`/`:2847` e `team/_run.py:1958`/`:4339`.
3. **Saída nativa vs fallback:** `get_response_format` `agent/_response.py:856-895`;
   parse nativo em `update_run_response` `:945-975` (content = modelo parseado,
   `content_type` = nome da classe); fallback string→schema em
   `convert_response_to_structured_format` `:903-937`. **Falha de parse não
   levanta** — só `log_warning` (`:920`/`:933`/`:935`) e o conteúdo cru fica na
   resposta (extremo SOFT do Agno).
4. **Carreadores:** `RunOutput` (dataclass) `run/agent.py:617` (content `:632`,
   content_type `:633`, status `:667`); `TeamRunOutput` `run/team.py:745`
   (member_responses `:767`, agregação `add_member_run` `:1080-1097`); mensagem
   tipada = `Message` pydantic `models/message.py:56-127` (role/content/media,
   `extra="allow"` `:127`).
5. **Fronteira de tool:** schema JSON derivado de type hints
   (`tools/function.py:686`; `utils/json_schema.py:99-232`: Literal→enum
   `:106-122`, Optional removido `:213-219`; `strict` ⇒ `additionalProperties:false`
   + tudo required `tools/function.py:688-690`).
6. **Guardrails:** ABC `BaseGuardrail.check/async_check`
   `guardrails/base.py:8-19`, normalizado em pre/post-hooks
   (`utils/hooks.py:76-159`; guardrails sempre síncronos `:63-73`).
   **HARD** = raise `InputCheckError`/`OutputCheckError` (`exceptions.py:347-386`;
   `CheckTrigger` `:335-344`) capturado no loop ⇒ `RunStatus.error` +
   `content=str(e)`, run retornada, **sem retry, sem exceção para fora**
   (`agent/_run.py:667-686` e `:1811-1830`). **SOFT** = mutação in-place e segue
   (máscara PII `guardrails/pii.py:64-72`) ou log_warning deparse. `RetryAgentRun`/
   `StopAgentRun` (`exceptions.py:64-93`) não têm catch sites no tree (vestigiais).
7. **Times:** membros NÃO têm schema por tarefa — `Task` é dataclass de strings
   (`team/task.py:22-71`), estado compartilhado via `TaskList` em session_state
   (`_team_tasks` `:247-260`), líder injeta estado como Message de usuário
   (`team/_run.py:377-383`). ⇒ o payload de delegação por tarefa é o **gap real**
   que o port HAOS preenche com contrato de nível de campo.

## 3. Lacunas acionáveis
1. **Contrato de I/O por tarefa no HAOS:** não existia schema de campo para o
   payload que entra/sai da lane — só dataclasses estruturais (TaskSpec) e
   verificação por ACs. Necessário: `FieldContract` (tipo str/int/float/bool/
   json/path/artifact_ref + enum + required + default), `TaskIOContract`
   (inputs/outputs + política de chaves extras), validador puro com violações em
   string e guard soft/hard — stdlib/dataclass, sem pydantic.
2. **TaskSpec sem campo aditivo** de contrato; persistência kanban é allowlist
   explícita (`kanban_adapter._spec_dict` `kanban_adapter.py:719-756`) e exigiria
   a chave para o contrato sobreviver ao round-trip via JSON.
3. **Dispatcher sem gate na execução da lane:** `worker.execute(...)→complete_task`
   não validava nem o input do spec nem o dict de resultado — ponto cego entre
   claim e complete (hoje `dispatcher.py`).

## 4. Status de Implementação
(achado → código/teste; verificação de leitura nesta auditoria — suite NÃO
executado por instrução)
- **Camada de contratos — IMPLEMENTADA:** `hermes/platform/execution/contracts.py`
  (292 linhas; módulo folha, sem imports do platform): `FIELD_TYPES` `:23`,
  `ContractViolationKind` `:28`, `FieldContract` `:40`, `TaskIOContract` `:63`
  (`allow_extra_keys=True` `:68`; `to_dict` `:78`/`from_dict` `:86`),
  `empty_contract` `:114`, `validate_contract` `:123` (`max_json_depth=10`),
  checagem json com profundidade limitada `:152`/`:190`, `ContractViolationError`
  `:218`, `ContractGuard` `:227` (`hard_kinds` default exclui `unknown_key` `:237`;
  `check_inputs` `:243`, `check_outputs` `:250`, `gate_inputs` `:262`,
  `gate_outputs` `:269` — hard ⇒ raise/bloqueia, soft ⇒ `residual_risk` +
  `evidence["contract_violations"]`). Docstring espelha a semântica Agno.
- **Campo aditivo em TaskSpec — IMPLEMENTADO:** `task_contract:
  Optional[TaskIOContract] = None` em `hermes/platform/tasks/spec.py:95`
  (único acoplamento tasks→execution, `spec.py:4-7`; posição final preserva
  construções existentes).
- **Persistência — IMPLEMENTADA:** `kanban_adapter._spec_dict` serializa
  `"task_contract": spec.task_contract.to_dict() if ... else None`
  (`kanban_adapter.py:748-749`), round-trippable via `TaskIOContract.from_dict`.
- **Gate no dispatcher — IMPLEMENTADO:** `_contract_guard_for` reconstrói o guard
  do spec JSON (`dispatcher.py:67-72`); `_execute_card` faz `gate_inputs(spec)`
  antes do `worker.execute` e `gate_outputs(out)` depois
  (`dispatcher.py:114-125`); no claim path, `ContractViolationError` vira
  `record_task_failure(outcome="contract_violation")` (`dispatcher.py:279-281`) —
  card não fica preso em `running`.
- **Testes — ESCRITOS:** `tests/platform/execution/test_contracts.py` (193
  linhas, unittest.TestCase; invariantes, não change-detectors): required/missing
  `:23`, type mismatch p/ os 7 tipos `:30`, enum `:56`, política de chaves extras
  `:62`, json aninhado com profundidade limitada `:70`, root não-dict `:87`, hard
  raise + soft gravado `:93`, contrato vazio aceita tudo `:123`, hard_kinds
  default `:133`, aditividade de TaskSpec `:146`/`:153`, e integração com o dict
  da `DeterministicLaneWorker` passando pelo gate `:168`.
- Índice `docs/haos/audits/README.md` já lista o relatório (linha "Agno typed
  contracts"); status permanece "em colheita" até a suite rodar verde.

## 5. Recomendações
1. **Executar a suite** para fechar o ciclo: `scripts/run_tests.sh
   tests/platform/execution/test_contracts.py` (e dependentes do dispatcher:
   `test_dispatcher.py`, `test_task_posture.py`) e marcar a linha do índice como
   "fechada (teste)".
2. **Cobrir o round-trip completo** em teste: salvar TaskSpec com contrato via
   `KanbanAdapter.save_task`, reler `get_task()["spec"]["task_contract"]` e
   reconstruir o guard com `TaskIOContract.from_dict` (persistência
   `kanban_adapter.py:748-749` + reconstrução `dispatcher.py:67-72` já existem;
   falta o teste de ponta a ponta).
3. **Manter a não-duplicação:** contrato de campo não deve virar engine de ACs
   (`tasks/acceptance.py`), gate de review (`execution/team.py` DAG + posture),
   eval (`evals/runner.py`) nem shape de artefato (`capabilities/modality/
   workers.py` — tipo `artifact_ref` é referência); contracts.py permanece folha
   e stdlib-only.
