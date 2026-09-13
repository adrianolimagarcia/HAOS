# Auditoria: Review Pipeline & Anti-Anchoring Context (HAOS)

## 1. Escopo
Auditoria e desenho técnico da pipeline de revisão e aceitação no HAOS, cobrindo:
- `hermes/platform/tasks/spec.py` (`TaskSpec`, `ReviewStage`, `AcceptanceCriterion`)
- `hermes/platform/tasks/acceptance.py` (`AcceptanceEngine`)
- `hermes/platform/tasks/kanban_adapter.py` (ciclo de vida, `record_review_verdict`, `request_review`)
- `hermes/platform/memory/context_engine/guard.py` (`build_reviewer_package`, isolamento anti-anchoring)

---

## 2. Achados-Chave

### 2.1 Conceito e Isolamento de Anti-Anchoring Context
- **Viés de Ancoragem:** O reviewer ancorado no raciocínio do worker tende a convalidar premissas erradas e ignorar casos de borda.
- **Barreira Epistêmica Estrita:** O reviewer deve receber apenas `TaskSpec`, ADRs/decisões arquiteturais, diffs/patches, evidências mecânicas (testes/LSP/schema) e summary/riscos residuais declarados.
- **Invariante:** Transcrição interna (*scratchpad*, *chain-of-thought*, tool loops) do implementador é rigorosamente expurgada do envelope do reviewer.

### 2.2 Validação Mecânica Composta em `AcceptanceEngine`
- Validações mecânicas determinísticas (testes pytest, verificação estrutural de arquivos, diagnósticos LSP e conformidade de schemas de I/O) devem preceder qualquer avaliação cognitiva por LLM.
- Suporte a operadores de composição booleana (`all_of`, `any_of`, `none_of`) para compor critérios complexos com falha rápida (*fail-fast*).

---

## 3. Lacunas Acionáveis
1. **Ausência de Módulo de Orquestração Dedicado (`review_pipeline.py`):** A execução dos `ReviewStage`s declarados na `TaskSpec` ainda dependia de chamadas manuais no `KanbanAdapter`.
2. **AcceptanceEngine Monolítico:** Validação original em `acceptance.py` não possuía suporte a operadores compostos (`all_of`/`any_of`), timeout configurável ou integração direta com `validate_contract`.
3. **Persistência de Múltiplos Estágios de Review:** `KanbanAdapter.record_review_verdict` grava o veredito final, mas necessita registrar o histórico de cada `ReviewStage` no campo `evidence` do `TaskResult`.

---

## 4. Status de Implementação

| Componente | Arquivo / Caminho | Status |
|---|---|---|
| Contratos de Review e Aceite | `hermes/platform/tasks/spec.py` | Implementado (`ReviewStage`, `AcceptanceCriterion`) |
| Motor de Aceitação Preliminar | `hermes/platform/tasks/acceptance.py` | Implementado; proposta de expansão composta |
| Anti-Anchoring Context Guard | `hermes/platform/memory/context_engine/guard.py` | Implementado (`build_reviewer_package`) |
| Ciclo de Vida e Vereditos | `hermes/platform/tasks/kanban_adapter.py` | Implementado (`record_review_verdict`, `request_review`) |
| Pipeline & Builder Desacoplado | `hermes/platform/tasks/review_pipeline.py` | Proposto e documentado na auditoria |
| Testes Unitários de Aceitação | `tests/platform/tasks/test_task_posture.py` | Verde (11 testes, `test_acceptance_and_failure`) |

---

## 5. Recomendações
1. **Materializar `hermes/platform/tasks/review_pipeline.py`:** Implementar `AntiAnchoringContextBuilder` e `ReviewPipeline` para desacoplar a preparação do envelope do reviewer e o despacho dos `ReviewStage`s.
2. **Expandir `AcceptanceEngine`:** Adicionar a avaliação recursiva de `all_of`/`any_of` e validação de `TaskIOContract` via `hermes.platform.execution.contracts`.
3. **Fail-Fast Econômico:** Garantir que falhas na validação mecânica (`AcceptanceEngine`) abortem a tarefa imediatamente antes de invocar o modelo do reviewer, economizando tokens e tempo.
