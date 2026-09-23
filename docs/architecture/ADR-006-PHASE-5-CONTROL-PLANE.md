# ADR-006: Phase 5 — Comprehensive Control Plane (graph projection retired)

> Historical note: the former Team Graph projection, routes, and UI described below were retired.
> The control plane's taskboard, worker status, interventions, The Eye orchestration, delegation/A2A,
> and GraphRAG/memory contracts are independent and remain active.

- **Status:** Accepted (Canonical Phase 5 Architecture)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & DeepSeek Harness Systems Engineering
- **Phase:** Phase 5 — Control Plane
- **Governed By:** ADR-001 through ADR-005

---

## 1. Contexto e Motivação

Com a consolidação do **Platform Kernel**, **Team Runtime**, **Adaptive Intelligence** e **Universal Protocol Gateway**, a plataforma exige um **Control Plane integral** para observabilidade, governança e controle humano em tempo real.

O Control Plane da Phase 5 unifica a visualização e operação sob uma interface rica e reativa, inspirada nas melhores práticas de cockpits operacionais (AionUI / Hermes Studio).

---

## 2. Visão do control plane operacional (a projeção gráfica foi aposentada)

A superfície atual concentra taskboard, status de workers, métricas e intervenções auditadas.
A antiga árvore Team Graph descrita no histórico abaixo não é mais um contrato de runtime.

O ponto central do Control Plane é a árvore hierárquica viva do time multi-agente:

```text
Hermes Platform Dashboard                                          Total Spent: $0.012

Town Mayor (Executive) [deepseek-v4-flash] ●
│
├── Sub-Orchestrator (Software) ●
│   ├── Polecat #1 (Coder) [deepseek-v4-flash] ✓ ─── Task: Implement TokenBucket
│   ├── Polecat #2 (Coder) [deepseek-v4-flash] ✓ ─── Task: Pytest Suite
│   └── Witness (Reviewer) [deepseek-v4-flash] ○ ─── Task: Architecture Review
│
└── Sub-Orchestrator (Research) ✓
    └── Specialist #1 (Researcher) [deepseek-v4-flash] ✓
```

### Inspeção Detalhada por Worker
Ao clicar em qualquer nó do grafo, o painel lateral exibe:
- **Task ID & Posture:** (`task-token-bucket-impl` | `coder`)
- **Model & Provider:** `deepseek-v4-flash` via `a6api`
- **Worktree:** `.worktrees/task-token-bucket`
- **Telemetry:** Tokens consumidos, custo em USD, latência e status do circuito.
- **Artifacts:** Arquivos gerados e veredito do review.
- **Controls:** Botões de intervenção em tempo real (`Steer`, `Interrupt`, `Pause`).

---

## 3. Endpoints Canônicos da API do Control Plane

Integrados no servidor standalone (`hermes/platform/webui/standalone.py`):
1. `GET /api/controlplane/overview`: Sumário executivo (número de workers, pools ativos, custo total, taxa de sucesso).
2. `GET /api/controlplane/team_graph`: **retired; no longer served**.
3. `GET /api/controlplane/routes`: Recomendações ativas do `AdaptiveRoutingOptimizer`.
4. `GET /api/controlplane/federation`: Diretório de `AgentCards`, agentes remotos e pontuações de reputação.
5. `POST /api/controlplane/intervene`: Ações de controle (`steer`, `pause`, `resume`, `abort`).
