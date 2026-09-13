# HAOS Implementation Master Plan: Phases 1 & 2 Execution Blueprint

- **Document Version:** 1.0.0 (Execution Ready)
- **Target Audience:** Autonomous Hermes Multi-Agent Swarms & Human Engineering Leads
- **Architectural Freeze:** ADR-002 (Architecture Freeze v0.1) & ADR-003 (Team Runtime)
- **Ethos:** Fail-closed, narrow-waist, strictly byte-stable prompt caching, zero silent degradation.

---

## 1. Executive Summary & Architecture Freeze v0.1

Este documento é o **plano operacional canônico e acionável** para execução paralela e autônoma das **Phases 1 e 2** no fork `haos-fork`. Cada tarefa está especificada de forma que um agente especialista (ou humano) possa executá-la isoladamente em um worktree git efêmero com garantias formais de aceitação.

### 1.1. Os 10 Contratos Congelados (ADR-002 Invariants)
Nenhum agente ou PR pode alterar os campos desses contratos sem revisão formal do Lead Architect:
1. `TaskSpec`: Identidade, grafo de dependências, postura e critérios de aceite (`AcceptanceCriterion`).
2. `TeamSpec`: Topologia GasTown (`mayor`, `sub_orchestrator`, `polecat`, `witness`, `refinery`, `deacon`).
3. `AssignmentSpec`: Contrato de lease de worker, workspace URI, lane e postura.
4. `PostureSpec`: Restrições de sandboxing, capabilities permitidas e prompt prefix byte-estável.
5. `ModelProfile`: Axioma `Model != Provider`, tupla de identidade do modelo e lista prioritária de rotas.
6. `CapabilityMetadata`: Categorias canônicas (`plugin`, `mcp`, `lsp`, `kilo`, `multimodal`), failure policies.
7. `ContextPackage`: Pacote imutável de contexto (system prompt, memórias, ferramentas, referências).
8. `KnowledgeItem`: Unidade atômica da Memory Fabric com escopo (`private`, `team`, `project`, `global`).
9. `SkillSpec`: Especificação procedural com SemVer estrito, checksum SHA-256 e lifecycle.
10. `PluginManifest`: Manifesto de extensibilidade para descoberta dinâmica sem colisão de namespace.

---

## 2. Dependency Graph Canônico de Implementação

```text
               [0. Schemas & Contracts Freeze (ADR-002)]
                                  │
                                  ▼
                     [1. Core Registries & Stores]
                      (Capability & Memory Store)
                                  │
         ┌────────────────────────┴────────────────────────┐
         ▼                                                 ▼
[2. Model & Provider Router]                      [3. Task Engine & Graph]
 (ExactModelClient / Failover)                   (Scheduler CPM / DAG Resolver)
         │                                                 │
         └────────────────────────┬────────────────────────┘
                                  ▼
                   [4. Worker Lanes & Workspaces]
                      (GitWorktree / Kilo Host)
                                  │
                                  ▼
                [5. Vertical Slice v0.1 (Phase 1 E2E)]
                 (User -> Team -> Worker -> Review)
                                  │
                                  ▼
             [6. Team Runtime & Specialist Pools (Phase 2)]
            (SpecialistPool / Sub-Orchestrators / Witness)
                                  │
                                  ▼
               [7. Chaos Harness & Integration Refinery]
                   (Fault Injection & MergeQueue)
```

---

## 3. Catálogo Detalhado de Tarefas Executáveis (Task Matrix)

---

### 🔹 TASK-01: Freeze Validation & Core Schemas (P1.1)
- **ID da Tarefa:** `TASK-01-SCHEMAS-FREEZE`
- **Objetivo:** Garantir a invariância estrita dos 10 contratos centrais contra regressões ou mutações de tipo.
- **Arquivos/Módulos:**
  - `hermes/platform/tasks/spec.py`
  - `hermes/platform/execution/assignment.py`
  - `hermes/platform/models/profiles.py`
  - `hermes/platform/capabilities/universal_registry.py`
  - `hermes/platform/context/memory/federated_fabric.py`
- **Interfaces:** `TaskSpec.to_dict()`, `AssignmentSpec`, `ModelProfile`, `CapabilityMetadata`.
- **Dependências:** Nenhuma (Raiz).
- **Modelo / Postura Recomendados:** `claude-3-7-sonnet` / `architect`.
- **Agente Responsável:** `Agent Architect`.
- **Critérios de Aceite:**
  1. Todos os 10 contratos dataclass instanciam com valores padrão sem bibliotecas externas no nível de módulo.
  2. Testes asserem explicitamente a imutabilidade dos campos estruturais.
- **Testes:** `tests/platform/test_adr_002_freeze_contract.py`
- **Risco:** Crítico (Base de todo o sistema).
- **Ordem de Merge:** 1.

---

### 🔹 TASK-02: Universal Capability Registry & Sandboxing (P1.2)
- **ID da Tarefa:** `TASK-02-CAPABILITY-REGISTRY`
- **Objetivo:** Implementar o registro universal de capabilities cobrindo MCP, LSP, Kilo e plugins, com políticas `FAIL_OPEN` e `FAIL_CLOSED`.
- **Arquivos/Módulos:**
  - `hermes/platform/capabilities/universal_registry.py`
  - `hermes/platform/capabilities/posture_sandbox.py`
- **Interfaces:** `UniversalCapabilityRegistry.register()`, `.resolve()`, `PostureCapabilitySandbox.filter_tools()`.
- **Dependências:** `TASK-01-SCHEMAS-FREEZE`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Plugin/Capability`.
- **Critérios de Aceite:**
  1. Bloqueio estrito de ferramentas fora da postura permitida (`FAIL_CLOSED`).
  2. Resolução determinística com TTL cache de checagem de saúde.
- **Testes:** `tests/platform/capabilities/test_sandboxing.py`, `tests/platform/capabilities/test_mcp_dispatcher_integration.py`.
- **Risco:** Alto (Segurança de execução).
- **Ordem de Merge:** 2.

---

### 🔹 TASK-03: ExactModelClient & Circuit-Breaker Failover (P1.3)
- **ID da Tarefa:** `TASK-03-EXACT-MODEL-ROUTING`
- **Objetivo:** Implementar o axioma `Model != Provider` impedindo degradação silenciosa; se a rota primária falha (429/5xx), migra para a secundária mantendo exatamente a mesma identidade de modelo.
- **Arquivos/Módulos:**
  - `hermes/platform/models/client.py`
  - `hermes/platform/models/circuit_breaker.py`
  - `hermes/platform/models/model_resolver.py`
- **Interfaces:** `ExactModelClient.complete(profile, messages) -> ModelResponse`.
- **Dependências:** `TASK-01-SCHEMAS-FREEZE`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Model/Provider`.
- **Critérios de Aceite:**
  1. Chamada HTTPS via stdlib `urllib` para endpoints OpenAI-compatíveis.
  2. Disparo de `ModelRouteExhaustedException` quando todas as rotas falham, sem degradar para modelos inferiores.
  3. Suporte ao `thinking: {type: 'disabled'}` e fallback para `reasoning_content`.
- **Testes:** `tests/platform/models/test_exact_model_client.py`.
- **Risco:** Alto (Disponibilidade e custo de inferência).
- **Ordem de Merge:** 3.

---

### 🔹 TASK-04: Deep Memory Fabric & Scoped Knowledge (P1.4)
- **ID da Tarefa:** `TASK-04-MEMORY-FABRIC`
- **Objetivo:** Implementar o ecossistema de memória híbrida estruturada em escopos (`private`, `team`, `project`, `global`) integrando Obsidian e GraphRAG.
- **Arquivos/Módulos:**
  - `hermes/platform/context/memory/federated_fabric.py`
  - `hermes/platform/context/memory/obsidian.py`
  - `hermes/platform/context/memory/graphrag.py`
- **Interfaces:** `FederatedMemoryCoordinator.store_item()`, `.query_relevant()`, `ObsidianAdapter`, `GraphRAGAdapter`.
- **Dependências:** `TASK-01-SCHEMAS-FREEZE`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Context/Memory`.
- **Critérios de Aceite:**
  1. Deduplicação semântica e temporal por timestamp e hash canônico.
  2. Armazenamento transparente em notas Markdown Obsidian e entidades/arestas GraphRAG.
- **Testes:** `tests/platform/context/test_fabric_extensions.py`.
- **Risco:** Médio.
- **Ordem de Merge:** 4.

---

### 🔹 TASK-05: Git Worktree Manager & Ephemeral Workspaces (P1.5)
- **ID da Tarefa:** `TASK-05-GIT-WORKTREES`
- **Objetivo:** Isolar cada tarefa em um worktree git efêmero (`haos/task-<id>`), garantindo que trabalhadores não poluam o repositório principal.
- **Arquivos/Módulos:**
  - `hermes/platform/workspaces/git_worktree.py`
  - `hermes/platform/workspaces/automerge.py`
  - `hermes/platform/workspaces/merge_queue.py`
- **Interfaces:** `GitWorktreeManager.create_worktree()`, `.remove_worktree()`, `AutoMergeGate.evaluate_and_merge()`.
- **Dependências:** `TASK-01-SCHEMAS-FREEZE`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Kilo/Worker Lanes`.
- **Critérios de Aceite:**
  1. Worktrees criados e removidos via `git worktree` atômico.
  2. `AutoMergeGate` executa suite de testes afetada por AST antes de mergear.
- **Testes:** `tests/platform/workspaces/test_git_worktree.py`.
- **Risco:** Alto (Integridade de código do repositório).
- **Ordem de Merge:** 5.

---

### 🔹 TASK-06: Vertical Slice v0.1 — E2E Platform Kernel (P1.6)
- **ID da Tarefa:** `TASK-06-VERTICAL-SLICE-V01`
- **Objetivo:** Cruzar todos os subsistemas da Phase 1 em uma única execução ponta a ponta: do `TaskSpec` ao commit com review.
- **Arquivos/Módulos:**
  - `hermes/platform/execution/dispatcher.py`
  - `hermes/platform/execution/scheduler.py`
  - `tests/platform/execution/test_vertical_slice_phase1.py`
- **Interfaces:** `HAOSDispatcher.dispatch_once()`, `HAOSScheduler.schedule_ready()`.
- **Dependências:** `TASK-01` a `TASK-05`.
- **Modelo / Postura Recomendados:** `claude-3-7-sonnet` / `architect`.
- **Agente Responsável:** `Agent Core Runtime`.
- **Critérios de Aceite:**
  1. Execução completa: TaskSpec $\to$ Worktree Kilo $\to$ Modelo A6API/Failover $\to$ Geração de Código $\to$ Pytest $\to$ Reviewer $\to$ MergeQueue.
- **Testes:** `tests/platform/execution/test_vertical_slice_phase1.py`, `tests/platform/test_seven_real_adapters.py`.
- **Risco:** Crítico (Ponto de validação formal da Fase 1).
- **Ordem de Merge:** 6.

---

### 🔹 TASK-07: Specialist Worker Pools & Hygienic Reuse (P2.1)
- **ID da Tarefa:** `TASK-07-SPECIALIST-POOLS`
- **Objetivo:** Implementar o pool de especialistas aquecidos com reutilização e higienização formal entre tarefas consecutivas.
- **Arquivos/Módulos:**
  - `hermes/platform/execution/team_runtime.py`
- **Interfaces:** `SpecialistPool.acquire(posture, model_id) -> PooledSpecialist`, `.release(worker_id)`.
- **Dependências:** `TASK-06-VERTICAL-SLICE-V01`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Team Runtime`.
- **Critérios de Aceite:**
  1. Workers de mesma postura são reaproveitados sem novo spawn.
  2. Contador de tarefas e timestamp de uso rastreados atipicamente.
- **Testes:** `tests/platform/execution/test_team_runtime.py::test_specialist_pool_reuse_and_sanitization`.
- **Risco:** Médio.
- **Ordem de Merge:** 7.

---

### 🔹 TASK-08: Domain Sub-Orchestrators & Hierarchical DAGs (P2.2)
- **ID da Tarefa:** `TASK-08-SUB-ORCHESTRATORS`
- **Objetivo:** Decomposição recursiva de objetivos de domínio (`software`, `research`) em subgrafos DAGs tipados de tarefas (`impl` $\to$ `test` $\to$ `review`).
- **Arquivos/Módulos:**
  - `hermes/platform/execution/team_runtime.py`
- **Interfaces:** `DomainSubOrchestrator.decompose(sub_goal) -> List[TaskSpec]`.
- **Dependências:** `TASK-07-SPECIALIST-POOLS`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Team Runtime`.
- **Critérios de Aceite:**
  1. Dependências `requires_tasks` e `DependencyEdge` estritamente encadeadas.
  2. Isolamento epistêmico entre executor (Polecat) e revisor (Witness).
- **Testes:** `tests/platform/execution/test_team_runtime.py::test_domain_sub_orchestrator_decomposition`.
- **Risco:** Médio.
- **Ordem de Merge:** 8.

---

### 🔹 TASK-09: MultiAgentTeamRuntime Full Mission Orchestration (P2.3)
- **ID da Tarefa:** `TASK-09-TEAM-RUNTIME-MISSION`
- **Objetivo:** Execução coordenada de missões completas de time (Town Mayor $\to$ Sub-Orchestrator $\to$ Workers $\to$ Witness $\to$ EventStore).
- **Arquivos/Módulos:**
  - `hermes/platform/execution/team_runtime.py`
- **Interfaces:** `MultiAgentTeamRuntime.execute_team_mission()`.
- **Dependências:** `TASK-07`, `TASK-08`.
- **Modelo / Postura Recomendados:** `claude-3-7-sonnet` / `architect`.
- **Agente Responsável:** `Agent Core Runtime`.
- **Critérios de Aceite:**
  1. Ordenação topológica estrita respeitada na execução das tarefas.
  2. Emissão de todos os eventos de ciclo de vida (`team.formed`, `worker.acquired`, `task.completed`, `worker.released`).
  3. Validação ao vivo em scripts executáveis com A6API real (`scripts/run_live_mission.py`).
- **Testes:** `tests/platform/execution/test_team_runtime.py::test_multi_agent_team_mission_execution`.
- **Risco:** Alto.
- **Ordem de Merge:** 9.

---

### 🔹 TASK-10: Chaos Resilience Harness (Phases 1 & 2 Hardening) (P2.4)
- **ID da Tarefa:** `TASK-10-CHAOS-HARNESS`
- **Objetivo:** Injetar deliberadamente falhas de rede, queda de workers, e respostas 429/503 comprovando a auto-recuperação da plataforma.
- **Arquivos/Módulos:**
  - `hermes/platform/scaling/hardening_and_scale.py`
  - `tests/platform/scaling/test_hardening_and_scale.py`
- **Interfaces:** `ChaosInjector.arm_fault()`, `CrashRecoveryManager.replay_mission_state()`.
- **Dependências:** `TASK-09-TEAM-RUNTIME-MISSION`.
- **Modelo / Postura Recomendados:** `deepseek-v4-flash` / `coder`.
- **Agente Responsável:** `Agent Security Reviewer`.
- **Critérios de Aceite:**
  1. Failover automático acionado sob injeção de 503 sem travar o processo.
  2. Worktrees zumbis identificados e expurgados deterministicamente.
- **Testes:** `tests/platform/scaling/test_hardening_and_scale.py`.
- **Risco:** Médio.
- **Ordem de Merge:** 10.

---

## 4. Playbook de Atribuição e Execução Paralela para Agentes Hermes

Para operar este plano via enxame multi-agente autônomo:

1. **Lead Architect (`architect`):**
   - Supervisiona `TASK-01` e `TASK-06`. Valida conformidade com ADR-002/ADR-003.
2. **Specialist Coder 1 (`coder`):**
   - Alocado para a trilha de modelos e capabilities (`TASK-02`, `TASK-03`).
3. **Specialist Coder 2 (`coder`):**
   - Alocado para a trilha de workspaces e memória (`TASK-04`, `TASK-05`).
4. **Team Runtime Specialist (`coder` / `architect`):**
   - Alocado para os motores de orquestração de times e pools (`TASK-07`, `TASK-08`, `TASK-09`).
5. **Security & Chaos Reviewer (`reviewer`):**
   - Conduz os testes adversários em `TASK-10` e audita o `EventStore`.

---

## 5. Protocolo de Merge & Validação Final
Toda tarefa concluída deve satisfazer os seguintes comandos antes de receber merge na branch principal:

```bash
# 1. Checagem de namespace PEP-420 (Zero __init__.py em hermes/platform/)
[ $(find hermes/platform -name "__init__.py" | wc -l) -eq 0 ] || exit 1

# 2. Execução da suite de testes completa (Hermes Test Runner)
HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python scripts/run_tests.sh tests/platform/

# 3. Verificação de integridade git
git status -s  # Deve estar 100% limpo
```
