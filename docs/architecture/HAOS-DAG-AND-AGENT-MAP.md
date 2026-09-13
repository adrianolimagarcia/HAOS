# HAOS Dependency DAG, Agent Roster & Operational Strategy

## 1. O DAG Real de Dependências (M0 a M20 / TASK-0001 a TASK-0021)

```
[M0: TASK-0001 Architecture Freeze]
       │
       ├─────────────────────────────────────────┐
       ▼                                         ▼
[M1: TASK-0002 Registries]             [M2: TASK-0003 EventStore]
       │                                         │
       └────────────────────┬────────────────────┘
                            ▼
               [M3: TASK-0004 Task Engine]
                            │
                            ▼
               [M4: TASK-0005 Assignment Engine]
                            │
       ┌────────────────────┼────────────────────┬────────────────────┐
       ▼                    ▼                    ▼                    ▼
[M5: TASK-0006]      [M6: TASK-0007]      [M7: TASK-0008]      [M8: TASK-0009]  [M9: TASK-0010]
 Model/Router          Capabilities         Context Cache        Memory Fabric     Generic Lane
       │                    │                    │                    │                 │
       └────────────────────┼────────────────────┴────────────────────┴─────────────────┘
                            ▼
               [M10: TASK-0011 Vertical Slice v0.1]
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
 [M11: TASK-0012 Golden Tasks]     [M12: TASK-0013 Chaos & Hardening]
            │                               │
            └───────────────┬───────────────┘
                            ▼
               [GATE 6: Deterministic Pass > 95%]
                            │
               [M13: TASK-0014 Team Foundation]
                            │
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
[M14: TASK-0015]     [M15: TASK-0016]     [M16: TASK-0017]
 Worker Pools        Sub-Orchestrators        Mailbox
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
             [M17: TASK-0018 Review / Judge / Integrator]
                            │
             [M18: TASK-0019 Multimodal Delegation]
                            │
             [M19: TASK-0020 Team Evals & Benchmarks]
                            │
             [M20: TASK-0021 Adaptive Intelligence (Shadow)]
```

### 1.1 Classificação Estrutural do DAG

1. **Tarefas Serializadas (Strict Sequential Foundations):**
   - `TASK-0001` (Freeze) $\to$ `TASK-0003` (Events) $\to$ `TASK-0004` (Task/Run) $\to$ `TASK-0005` (Assignment).
   - Nenhuma baia ou modelo pode ser executado sem que `Task` e `Assignment` estejam imutavelmente congelados.

2. **Tarefas Paralelizáveis (Controlled Concurrency Slices):**
   - **M5 a M9 (Bloco de Infraestrutura Especializada):**
     - `TASK-0006` (Model Router / Circuit Breaker)
     - `TASK-0007` (MCP / LSP Capability Fabric)
     - `TASK-0008` (Context Package & Prompt Cache)
     - `TASK-0009` (Obsidian & GraphRAG Memory Fabric)
     - `TASK-0010` (Generic Worker Lane & Worktrees)
     Essas 5 tarefas possuem apenas dependência de `TASK-0005` e de seus respectivos sub-registries, operando em arquivos totalmente ortogonais sem risco de conflito semântico.
   - **M14 a M16 (Bloco de Expansão de Equipe):**
     - `TASK-0015` (Worker Pools)
     - `TASK-0016` (Sub-Orchestrators)
     - `TASK-0017` (Mailbox Bus)

3. **Blockers & Merge Gates:**
   - **Gate 1 (M0):** `TASK-0001` - Nenhuma linha de código antes dos schemas compilarem e os 12 ADRs estarem aceitos.
   - **Gate 2 (M4):** `TASK-0005` - Nenhuma baia spawnada antes do `AssignmentSpec` reproduzível estar ativo.
   - **Gate 3 (M10 - Vertical Slice):** O primeiro pipeline ponta-a-ponta deve passar 10 vezes seguidas com failover do provedor mantendo o modelo exato.
   - **Gate 4 (M12 - Chaos Gate):** Nenhuma expansão para multi-agent (Phase 2) antes do sistema resistir a 95%+ de injeções de caos (429, 503, SIGKILL, DB lock, worktrees zumbis).
   - **Gate 5 (M20 - Ouroboros Boundary):** Ouroboros confinado estritamente a modo Shadow (observação e hipótese em sandbox), com modificação autônoma de produção proibida.

4. **Caminho Crítico (Critical Path):**
   $$\text{TASK-0001} \to \text{TASK-0004} \to \text{TASK-0005} \to \text{TASK-0010} \to \text{TASK-0011} \to \text{TASK-0013} \to \text{TASK-0014} \to \text{TASK-0018} \to \text{TASK-0020}$$
   Duração mínima teórica do pipeline governada por esses nós de integração obrigatórios.

---

## 2. Mapa dos 11 Agentes Especializados

| Agente | Postura | Perfil de Modelo Padrão | Responsabilidade Primária | Entregáveis Principais |
|---|---|---|---|---|
| **1. Architecture Agent** | `architect` | `architect-primary` (`deepseek-v4`) | Governança de ADRs, versionamento de schemas, aprovação de ACRs. | `architecture/`, `docs/architecture/ADR-*` |
| **2. Runtime Agent** | `coordinator` | `coding-primary` (`deepseek-v4-flash`) | Núcleo do dispatcher, inicialização do sistema, CLI e endpoints. | `hermes/platform/execution/`, `scripts/` |
| **3. Task Engine Agent** | `coder` | `coding-primary` (`deepseek-v4-flash`) | Ciclo de vida de `TaskSpec` e `TaskRun`, autoridade SQLite `kanban.db`. | `hermes/platform/tasks/`, `hermes/platform/kanban/` |
| **4. Model/Provider Agent** | `coder` | `coding-primary` (`deepseek-v4-flash`) | Roteador exato, circuit breakers, failovers A6API e endpoints. | `hermes/platform/models/` |
| **5. Capability/MCP/LSP Agent** | `coder` | `coding-primary` (`deepseek-v4-flash`) | Integrações de ferramentas, servidores MCP, protocolo LSP e diagnósticos AST. | `hermes/platform/capabilities/` |
| **6. Context/Memory Agent** | `coder` | `coding-primary` (`deepseek-v4-flash`) | Prefix caching byte-estável, Obsidian Vault e Knowledge Graph SQLite. | `hermes/platform/context/`, `hermes/platform/memory/` |
| **7. Worker/Kilo Agent** | `coder` | `coding-primary` (`deepseek-v4-flash`) | Contrato `GenericWorkerLane`, baias efêmeras Kilo e gestão de worktrees git. | `hermes/platform/execution/lane_generic.py`, `workspaces/` |
| **8. Team Runtime Agent** | `coordinator` | `coordinator-primary` (`deepseek-v4`) | GasTown topology, Sub-Orquestradores, pools de especialistas e Mailbox. | `hermes/platform/execution/team*.py`, `communication/` |
| **9. QA/Chaos Agent** | `evaluator` | `eval-primary` (`deepseek-v4-flash`) | Injeção de falhas (429, 503, SIGKILL), testes determinísticos e Golden Tasks. | `hermes/platform/scaling/`, `hermes/platform/evals/` |
| **10. Witness Reviewer** | `witness` | `witness-primary` (`deepseek-v4`) | Auditoria cega e independente de artefatos, diffs e relatórios de testes. | `hermes/platform/execution/review.py` |
| **11. Security Reviewer** | `witness` | `security-primary` (`deepseek-v4`) | Análise de vulnerabilidades, injeção de prompt e sanitização de permissões. | `hermes/platform/scaling/hardening_and_scale.py` |

---

## 3. Estratégia de Repositório, Branches e Worktrees

1. **Remotes Canônicos:**
   - `origin`: `NousResearch/hermes-agent` (Upstream oficial inviolável).
   - `user-fork`: `github.com/adrianolimagarcia/hermes-agent` (Fork de desenvolvimento).

2. **Branch Hierarchy:**
   - `origin/main`: Linha base upstream limpa (0 commits de drift no kernel).
   - `haos-fork` (ou `develop`): Linha de integração estável onde passam todas as 92 suítes de teste.
   - `epic/*`: Branches de longa duração por Epic (`epic/task-engine`, `epic/team-runtime`, `epic/context-memory`).
   - `task/TASK-xxxx-<slug>`: Branches atômicas de trabalho.

3. **Worktree Isolation Matrix:**
   - Cada agente de codificação opera em seu próprio worktree isolado montado sob `/tmp/haos_worktrees/task-XXXX`.
   - Nenhum worker escreve diretamente na árvore de outro worker.
   - Ao término da tarefa e validação pelo Witness Reviewer, o branch é mergeado em `haos-fork` e o worktree efêmero é limpo automaticamente via `cleanup()`.
