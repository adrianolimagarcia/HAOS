# ADR-003: Phase 2 — Multi-Agent Team Runtime Architecture

- **Status:** Accepted (Canonical Phase 2 Architecture)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & DeepSeek Harness Systems Engineering
- **Phase:** Phase 2 — Team Runtime
- **Supersedes:** Modelos ad-hoc de sub-agentes sem isolamento epistêmico e sem pool de workers
- **Governed By:** ADR-001 (SOTA Multi-Agent) & ADR-002 (Architecture Freeze v0.1)

---

## 1. Contexto e Motivação

Com a conclusão da **Phase 1 — Hermes Platform Kernel**, a plataforma possui:
1. Contratos formais e imutáveis (`TaskSpec`, `AssignmentSpec`, `PostureSpec`, `ContextPackage`, etc.).
2. Um pipeline comprovado de ponta a ponta (Vertical Slice E2E).
3. Adapters reais operacionais (`A6API`, `OpenRouter`, `LSP`, `Obsidian`, `GraphRAG`, `Kilo`, `MCP`).

A **Phase 2 — Team Runtime** eleva o sistema para a **execução coordenada de times multi-agentes especializados em escala**, respondendo a quatro desafios fundamentais:
1. **Paralelismo Seguro:** Executar múltiplas tarefas simultaneamente com limites de concorrência por provedor e por nó de computação.
2. **Worker Pools & Specialist Reuse:** Reutilizar contextos de agentes especialistas aquecidos sem pagar latência de boot nem quebrar o isolamento de worktrees efêmeros.
3. **Sub-Orquestradores por Domínio:** Decomposição recursiva de metas complexas em sub-grafos de tarefas direcionadas (software, pesquisa, infra).
4. **Isolamento Epistêmico (Anti-Anchoring):** Cooperação sem compartilhamento de monólogos brutos ou contaminação de viés cognitivo entre agentes.

---

## 2. A Topologia Canônica do Team Runtime

Inspirada no modelo de papéis GasTown e estendida para arquitetura corporativa:

```
                               ┌───────────────────────────────────┐
                               │           TOWN MAYOR              │
                               │  (Executive Lead Orchestrator)    │
                               └─────────────────┬─────────────────┘
                                                 │ Decomposição em Sub-Goals
                         ┌───────────────────────┴───────────────────────┐
                         ▼                                               ▼
          ┌─────────────────────────────┐                 ┌─────────────────────────────┐
          │      SUB-ORCHESTRATOR       │                 │      SUB-ORCHESTRATOR       │
          │      (Domain: Software)     │                 │      (Domain: Research)     │
          └──────────────┬──────────────┘                 └──────────────┬──────────────┘
                         │                                               │
         ┌───────────────┴───────────────┐               ┌───────────────┴───────────────┐
         ▼                               ▼               ▼                               ▼
  ┌──────────────┐                ┌──────────────┐┌──────────────┐                ┌──────────────┐
  │ POLECAT #1   │                │ POLECAT #2   ││ RESEARCHER #1│                │ RESEARCHER #2│
  │ (Lane Worker)│                │ (Lane Worker)││(Web/Analysis)│                │(Doc/Extract) │
  └──────┬───────┘                └──────┬───────┘└──────┬───────┘                └──────┬───────┘
         │ Artefatos (Diffs)             │               │                               │
         └───────────────┬───────────────┘               └───────────────┬───────────────┘
                         ▼                                               ▼
          ┌─────────────────────────────┐                 ┌─────────────────────────────┐
          │           WITNESS           │                 │           DEACON            │
          │   (Independent Reviewer)    │                 │   (Knowledge Synthesizer)   │
          └──────────────┬──────────────┘                 └──────────────┬──────────────┘
                         │ Veredito Aprovado                             │ Fatos Validados
                         ▼                                               ▼
          ┌─────────────────────────────┐                 ┌─────────────────────────────┐
          │          REFINERY           │                 │      FEDERATED MEMORY       │
          │  (AutoMerge / Integration)  │                 │    (Obsidian + GraphRAG)    │
          └─────────────────────────────┘                 └─────────────────────────────┘
```

### Papéis Formais e Especializações
1. **Town Mayor (Executive Lead):** Decompõe o objetivo global em metas de domínio e orquestra o orçamento total de tempo/tokens.
2. **Sub-Orchestrator:** Especialista em um domínio (engenharia de software, pesquisa ou operações). Gera DAGs de `TaskSpec` com dependências tipadas.
3. **Polecat (Worker):** Trabalhador efêmero que executa na postura `coder` em worktree Kilo isolado, produzindo artefatos tipados (`git_patch`).
4. **Witness (Independent Reviewer):** Avalia os artefatos com postura `reviewer` e contexto fresco (`fresh_context`). Executa AST LSP e calcula o blast radius.
5. **Refinery (Integrator):** Gerencia a `MergeQueue` serializando rebases atômicos e executando a suíte afetada de testes.
6. **Deacon (Knowledge Steward):** Garante a saúde da memória coletiva, consolidando candidatos no Obsidian e indexando o GraphRAG.

---

## 3. Worker Pools & Specialist Reuse

Em vez de destruir e recriar o ambiente a cada micro-tarefa:
- O `SpecialistPool` gerencia instâncias de agentes prontas para uso organizadas por postura (`coder`, `reviewer`, `researcher`).
- Um worker é alocado para um lease (`AssignmentSpec`), executa em um worktree efêmero e, ao concluir, passa por um **procedimento formal de higienização** (limpeza de variáveis transitórias de sessão, preservação do prefix-cache) antes de retornar ao pool.

---

## 4. Sub-Orquestradores e Decomposição Hierárquica

Tarefas complexas não devem poluir um scheduler central único:
- O `DomainSubOrchestrator` recebe uma meta de alto nível (ex.: "Migrar camada de autenticação para JWT").
- Constrói um subgrafo DAG de tarefas locais:
  - `task-auth-model` (Coder)
  - `task-auth-endpoints` (Coder) $\to$ requer `task-auth-model`
  - `task-auth-tests` (Coder) $\to$ requer `task-auth-endpoints`
  - `task-auth-review` (Reviewer) $\to$ review de todas as anteriores
- O sub-orquestrador monitora os batimentos cardíacos dos seus workers e reporta apenas o sumário consolidado de progresso ao Town Mayor.

---

## 5. Eventos e Observabilidade de Time

A telemetria da Phase 2 expande o `EventStore` com eventos estruturados de coordenação:
- `team.formed`: Time e papéis inicializados.
- `subgoal.delegated`: Meta decomposta e entregue a um sub-orquestrador.
- `worker.acquired`: Worker alocado do pool.
- `worker.released`: Worker higienizado e devolvido ao pool.
- `review.verdict`: Parecer formal do Witness (Approved / Changes Requested).
- `refinery.integrated`: Patch mesclado com sucesso na branch alvo.
