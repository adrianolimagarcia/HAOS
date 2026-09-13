# Auditoria de Arquitetura: Scheduler, Critical Path e Priority Inheritance (Frente 3)

## 1. Escopo
Análise da engine de agendamento de tarefas do HAOS (`hermes/platform/execution/scheduler.py`), interfaces com `TaskSpec`/`DependencyEdge` (`hermes/platform/tasks/spec.py`), e integração de políticas de execução, concorrência e anti-starvation.

## 2. Achados-chave
- **Cálculo Atual de Prioridade:** Em `HAOSScheduler.calculate_priority_score`, o score combina `task.priority` (base 50.0), `+50.0` se `task.id in critical_path_ids`, e `+10.0` se `risk_level == "high"`.
- **Cálculo Externo de Critical Path:** O scheduler consome `critical_path_ids` pré-computado externamente; não há cálculo intrínseco de Caminho Crítico sobre o DAG de tarefas.
- **Inversão de Prioridade (Priority Inversion):** Se uma tarefa $A$ (alta prioridade) requer $B$ (baixa prioridade), $B$ não herda a prioridade de $A$. Tarefas independentes de média prioridade preempem $B$, bloqueando indiretamente $A$.
- **Fairness e Starvation:** `pick_with_fairness` rotaciona apenas empates no topo de pontuação. Tarefas com prioridade menor enfrentam inanição permanente na presença de afluxo contínuo de tarefas de maior prioridade.

## 3. Lacunas Acionáveis
1. **Critical Path Determinístico (CPM):** Falta algoritmo determinístico (Kahn com desempate lexicográfico estável + forward/backward passes) para identificar automaticamente o slack zero a partir de `requires_tasks` e `typed_dependencies` (`DependencyEdge(kind="requires")`).
2. **Priority Inheritance Protocol (PIP):** Propagação transitiva de prioridade efetiva: $\text{eff}(u) = \max(\text{prio}(u), \max_{v \in \text{succ}(u)} \text{eff}(v))$, com proteção contra ciclos.
3. **Mecanismo de Envelhecimento (Age Bonus):** Acúmulo de bônus por ciclo de espera ($\Delta \text{ticks} \times \text{rate}$ limitado por teto) para tarefas prontas não agendadas, zerado no agendamento.
4. **Resolução Dinâmica do Grafo Completo:** `schedule_next` recebe apenas `ready_tasks`; para CPM e PIP globais precisos, necessita opcionalmente do grafo completo (`all_tasks`).

## 4. Status de Implementação
- **Código Existente:**
  - Scheduler com backpressure e rotação de topo: `hermes/platform/execution/scheduler.py` (`HAOSScheduler`, `pick_with_fairness`).
  - Modelo de dependências tipadas: `hermes/platform/tasks/spec.py` (`TaskSpec`, `DependencyEdge`).
  - Testes unitários do scheduler atual: `tests/platform/execution/test_scheduler_and_shape.py` (11 testes verdes).
- **Proposta Técnica Elaborada:**
  - Funções propostas: `extract_requires_dependencies`, `compute_deterministic_critical_path` (CPM) e `compute_inherited_priorities` (PIP recursivo com memoização).
  - Assinaturas de `calculate_priority_score` e `schedule_next` mantidas 100% retrocompatíveis com parâmetros default opcionais (`inherited_priority`, `wait_ticks`, `all_tasks`).
  - Alias `TaskScheduler = HAOSScheduler` para compatibilidade com especificações legadas/paralelas.

## 5. Recomendações
1. **Enriquecimento Não-Disruptivo:** Implementar os algoritmos de CPM e PIP diretamente em `hermes/platform/execution/scheduler.py` ou módulo irmão `scheduler_dag.py`, preservando as assinaturas públicas.
2. **Garantia de Determinismo:** Manter ordenação lexicográfica por `task.id` nos desempates de ordenação topológica e no algoritmo de Kahn.
3. **Cobertura de Testes Dedicada:** Criar suite `tests/platform/execution/test_scheduler_cpm_pip.py` validando especificamente: inversão evitada por PIP, caminho crítico CPM em grafo diamante, e superação de starvation via age bonus.
