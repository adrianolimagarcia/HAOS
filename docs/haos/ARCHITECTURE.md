# HAOS - Hermes Agent Operating System (v1.2 Architecture Specification)

> **Canonical Architecture Reference:** See [`docs/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md`](../architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md) and [`docs/architecture/HAOS_SYSTEM_SPEC.md`](../architecture/HAOS_SYSTEM_SPEC.md) for the authoritative SOTA multi-agent architecture and system specification.

Este documento especifica a arquitetura formal do **HAOS** (Hermes Agent Operating System), detalhando os princípios fundamentais, contratos determinísticos e garantias operacionais que governam a execução de tarefas agênticas.

---

## 1. Princípio Fundamental: "Task não é Run"

A premissa central do HAOS estabelece a separação ontológica estrita entre a definição do trabalho, o plano dinâmico, as tentativas de execução e os resultados produzidos:

$$\text{TaskSpec} \neq \text{ExecutionPlan} \neq \text{TaskRun} \neq \text{TaskResult}$$

1. **`TaskSpec` (Intenção e Contrato):**
   - Imutável durante a execução.
   - Define o objetivo (`goal`), requisitos de capacidade, critérios de aceitação mecânicos (`acceptance_criteria`), estágios de revisão (`review_stages`) e restrições de orçamento (`max_cost_usd`, `max_runtime_minutes`).
   - Não contém transcripts, CoT ou histórico de chat.
2. **`ExecutionPlan` (Evolução Dinâmica da Execução):**
   - Representa os passos concretos planejados pelo agente.
   - O agente tem autonomia para expandir, refinar e marcar passos como concluídos sem necessidade de aprovação externa ou "cages de prompt".
3. **`TaskRun` (Tentativa Concreta):**
   - Instância temporal de uma tentativa de execução (`run_id`, `worker_id`, `posture_id`, `started_at`, `ended_at`).
   - Associada a um `claim_lock` atômico no SQLite upstream.
4. **`TaskResult` (Artefatos e Vereditos):**
   - Entregáveis finais produzidos (`summary`, `artifacts`, `evidence`, `residual_risk`, `reviewer_verdict`).
   - Persistido de forma auditável e imutável.

---

## 2. Scheduler Determinístico: CPM, PIP e Backpressure

O agendamento de tarefas no HAOS não utiliza heurísticas probabilísticas nem ordenação aleatória:

### A. Critical Path Method (CPM)
- Calculado via ordenação topológica (Algoritmo de Kahn) com fila priorizada lexicograficamente por `task.id`.
- Executa o *Forward Pass* ($ES, EF$) e o *Backward Pass* ($LS, LF$), calculando a folga (*slack*) de cada tarefa:
  $$\text{Slack}(T) = LS(T) - ES(T)$$
- Tarefas com $\text{Slack} = 0$ integram o Caminho Crítico e recebem bônus determinístico de prioridade (`+50.0`).

### B. Priority Inheritance Protocol (PIP)
- Evita o problema clássico de inversão de prioridade em sistemas multiagentes.
- Se uma tarefa preliminar $B$ bloqueia uma tarefa crítica $A$ (com prioridade mais alta), $B$ herda transitoriamente a prioridade de $A$:
  $$\text{Priority}_{\text{eff}}(B) = \max\left(\text{Priority}(B), \max_{A \in \text{dependents}(B)} \text{Priority}_{\text{eff}}(A)\right)$$

### C. ConcurrencyGuard (Admissão e Backpressure)
- Gate atômico avaliado **antes** do `kb.claim_task`.
- Monitora 4 tetos de concorrência:
  1. Concorrência Global (`max_active_workers`).
  2. Quotas por Provider (A6API, OpenAI, Anthropic, fallback).
  3. Quotas por Modelo e Route Key.
  4. Circuito Aberto via `CircuitBreaker`.
- Se a capacidade estiver temporariamente esgotada, a tarefa permanece em `status='ready'` com `claim_lock=NULL`, **sem queimar retries de `consecutive_failures`** nem abrir subprocessos condenados a falhar.

---

## 3. As 8 Formas de Execução (Spawn Resolver)

A execução é resolvida puramente em `SpawnResolver` e classificada em 8 formas canônicas:

| Forma | Constante | Descrição |
| :--- | :--- | :--- |
| **Self** | `SHAPE_SELF` | Execução direta no agente atual sem delegação. |
| **Posture Switch** | `SHAPE_POSTURE_SWITCH` | Transição interna de postura (ex.: implementer $\to$ reviewer). |
| **Persistent Specialist** | `SHAPE_PERSISTENT` | Reuso de um worker residente de longa duração. |
| **Ephemeral Worker** | `SHAPE_EPHEMERAL` | Worker temporário descartado ao concluir a tarefa. |
| **Sub-Orchestrator** | `SHAPE_SUB_ORCH` | Agente com autonomia para planejar e delegar sub-tarefas. |
| **Worker Lane** | `SHAPE_WORKER_LANE` | Despacho para uma lane externa específica (ex.: Kilo/Git). |
| **Capability Worker** | `SHAPE_CAPABILITY_WORKER` | Worker especializado em uma ferramenta/capability específica. |
| **External Agent** | `SHAPE_EXTERNAL_AGENT` | Agente remoto integrado via protocolos A2A/ACP. |

---

## 4. Pipeline de Aceitação Mecânica & Anti-Anchoring

Para garantir a qualidade de software sem viés de confirmação (*confirmation bias*):

### Anti-Anchoring Context Builder
- **Isolamento Epistêmico Estrito:** O revisor **nunca** recebe o *chain-of-thought*, transcripts intermediários, conversas de chat ou monólogo interno do implementador.
- **Payload Permitido:** Apenas o `TaskSpec` original, os critérios de aceitação, os diffs/patches gerados, os resultados mecânicos dos testes/LSP e o resumo de riscos residuais declarados.
- Tags `<thought>` e `<thinking>` são sanitizadas via regex e chaves de transcript são expurgadas por denylist normalizada.

### Composite Acceptance Engine
- Avaliação composável via regras lógicas `all_of` e `any_of`.
- Suporte a validadores mecânicos:
  - `structural`: integridade de arquivos esperados.
  - `test`: execução de suite de testes subprocess em sandbox.
  - `lsp`: diagnósticos estáticos de Language Server (zero novos erros).
  - `schema`: validação rigorosa de contratos de I/O de entrada/saída (`validate_contract`).
  - `review_score`: requer pontuação $\ge 0.8$ **e** `approved=True` explícito (veto de revisor reprova imediatamente).
- **Ethos Fail-Closed:** A ausência de evidências requeridas reprova a validação por padrão.

---

## 5. Observabilidade & Loop Ouroboros (Evolution Engine)

1. **EventStore Append-Only:**
   - Todo o ciclo de vida do Kanban (`record_run_event`, `record_task_failure`, `complete_task`, `record_review_verdict`) é projetado de forma fail-safe no `EventStore` com `correlation_id` e `trace_id`.
2. **Classificação Determinística de Falhas (11 Categorias):**
   - Toda falha é mapeada em categorias como `PROVIDER_ERROR`, `TOOL_TRANSIENT`, `WORKER_CRASH`, `REVIEW_REJECTION`, `ACCEPTANCE_FAILURE`, etc., ditando ações de retry não-penalizantes.
3. **Ouroboros Analyzer (Shadow Mode):**
   - Agrupa falhas por modelo e categoria:
     - Falhas de aceitação/revisão frequentes $\to$ proposta automática de elevação de fidelidade do modelo (`high_fidelity`).
     - Falhas de provider transitórias frequentes $\to$ proposta de ajuste de rota de failover (`fallback_route`).
4. **Evolution Ledger:**
   - Propostas recebem ID único determinístico (SHA-256) e são governadas pelo fluxo humano-no-loop: `submit` $\to$ `pending` $\to$ `decide(approved|rejected)`.
