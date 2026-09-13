# ADR-005: Phase 4 — Universal Protocol Gateway & Federation Runtime

- **Status:** Accepted (Canonical Phase 4 Architecture)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & DeepSeek Harness Systems Engineering
- **Phase:** Phase 4 — Protocol & Federation Runtime
- **Governed By:** ADR-001 through ADR-004

---

## 1. Contexto e Motivação

O HAOS opera com múltiplos protocolos especializados:
- **ANP (Agent Network Protocol):** Comunicação peer-to-peer criptográfica e distribuída na Agentic Web com HMAC-SHA256 e nonces anti-replay.
- **A2A (Agent-to-Agent):** Intercâmbio estruturado entre sistemas de agentes heterogêneos.
- **ACP (Agent Client Protocol):** Interface JSON-RPC bidirecional para IDEs, editores (VS Code, Zed) e clientes humanos.
- **MCP (Model Context Protocol):** Consumo seguro de ferramentas e recursos sandboxed.

A **Phase 4** unifica esses protocolos sob o **Universal Protocol Gateway** e implementa o **Federated Capability Resolver**, permitindo que um time integre membros externos de forma transparente e segura.

---

## 2. Topologia do Protocol Gateway

```
                    HERMES EXECUTIVE (Town Mayor)
                               │
                   Universal Protocol Gateway
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
      ANP                     A2A                     ACP
 (Agentic Web)          (Agent Systems)          (IDE / Clients)
       │                       │                       │
 ┌─────┴────────┐        ┌─────┴────────┐        ┌─────┴────────┐
 │ Remote Node  │        │ External Swarm│       │ Human Editor │
 │ (Federated)  │        │ (A2A Client) │        │ (ACP Session)│
 └──────────────┘        └──────────────┘        └──────────────┘
```

---

## 3. Componentes Fundamentais

### 3.1. Agent Cards & Capability Advertisement
- Todo agente (local ou remoto) publica um `AgentCard` tipado contendo:
  - `agent_id`: Identificador globalmente único.
  - `name` e `description`: Intenção e especialidade semântica.
  - `protocol`: Protocolo nativo de escuta (`ANP`, `A2A`, `ACP`).
  - `capabilities`: Lista de capacidades exportadas com schemas e restrições.
  - `trust_tier`: Grau de confiança inicial (`UNTRUSTED`, `SANDBOXED`, `PARTNER`, `LOCAL_SECURE`).

### 3.2. Federated Capability Resolver
Quando uma tarefa requer uma capacidade não presente localmente:
```
Local Capability Registry
          │ (not found)
          ▼
Federated Capability Resolver
          │
          ├── ANP Peer Directory (Busca na malha federada)
          └── A2A External Directory (Busca em swarms parceiros)
          │
          ▼
Resolvido: Aloca Proxy Remoto com Sandboxing e Quarentena de Artefatos
```

### 3.3. Provenance, Scoring & Local Reputation
- Registra cada interação remota:
  - `total_requests`, `successful_completions`, `tampering_attempts`, `avg_latency_ms`.
  - Calcula a pontuação de reputação $R \in [0.0, 1.0]$.
  - Se $R < 0.50$ ou se houver violação de integridade HMAC, o agente remoto entra em quarentena imediata.

### 3.4. External Team Members
O `MultiAgentTeamRuntime` passa a aceitar membros externos:
- Town Mayor (Local)
- Architect (Local)
- Coder (Local)
- **Security Auditor (External via ANP)** com envelope assinado e quarentena de saída.
