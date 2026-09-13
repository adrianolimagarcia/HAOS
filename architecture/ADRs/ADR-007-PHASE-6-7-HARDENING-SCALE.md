# ADR-007: Phase 6 & Phase 7 — Production Hardening, Chaos Resilience & Distributed Scale

- **Status:** Accepted (Canonical Architecture for Hardening & Distributed Scale)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & DeepSeek Harness Systems Engineering
- **Phase:** Phase 6 (Production Hardening) & Phase 7 (Distributed Scale)
- **Governed By:** ADR-001 through ADR-006

---

## 1. Contexto e Objetivos

Com as fundações conceituais e os runtimes das Phases 1 a 5 consolidados, as **Phases 6 e 7** transformam o HAOS em um sistema industrial de alta disponibilidade e escala distribuída:
1. **Production Hardening (Phase 6):** Tolerância extrema a falhas (crash recovery, replay determinístico, expiração de leases, auto-cleanup de worktrees zumbis, injeção de caos de rede e defesas ativas contra prompt injection indireto).
2. **Distributed Scale (Phase 7):** Orquestração multi-host distribuída (`ControlNode` $\to$ `WorkerNode` CPU/GPU/Kilo) e abstrações de storage conectáveis (SQLite WAL local com interfaces para Postgres/ClickHouse).

---

## 2. Pilares da Phase 6 — Production Hardening

### 2.1. Crash Recovery & Replay Determinístico
- `CrashRecoveryManager`:
  - Detecta tarefas marcadas como `in_progress` cujo worker sofreu crash súbito (ausência de heartbeat).
  - Replay de eventos estruturados do `EventStore` para reconstruir o snapshot exato de estado anterior ao crash.
  - Liberação atômica de leases expirados e remoção forçada de worktrees zumbis (`haos/task-*`).

### 2.2. Provider Chaos & Circuit Hardening
- `ChaosInjector`:
  - Simula partições de rede, latência artificial aleatória e respostas HTTP 429/500/503.
  - Testa o `ExactModelClient` sob condições adversas garantindo failover instantâneo sem perda de integridade do prompt cache.

### 2.3. Security Boundaries & Input Sanitization
- `PromptInjectionShield`:
  - Sanitiza payloads recebidos de nós externos e ferramentas federadas contra técnicas de injeção indireta (ex: tentativas de vazamento de credenciais ou fuga de sandbox).
  - Quarentena de artefatos não assinados (`untrusted_quarantine`).

---

## 3. Pilares da Phase 7 — Distributed Scale

### 3.1. Topologia Multi-Host Distribuída
```text
                       ┌─────────────────────────┐
                       │   HERMES CONTROL NODE   │
                       │   (Town Mayor / API)    │
                       └────────────┬────────────┘
                                    │ ANP / Wire Protocol
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
  ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
  │   WORKER NODE A    │ │   WORKER NODE B    │ │ REMOTE KILO NODE   │
  │    (CPU Pools)     │ │   (GPU / Local)    │ │   (Worktree Host)  │
  └────────────────────┘ └────────────────────┘ └────────────────────┘
```

### 3.2. Abstração de Armazenamento Conectável
- `PluggableStorageCoordinator`:
  - Fornece interface polimórfica para persistência de tarefas (`TaskStore`) e eventos (`TelemetryStore`).
  - Implementa fallback transparente para SQLite WAL quando backends distribuídos não estiverem configurados.
