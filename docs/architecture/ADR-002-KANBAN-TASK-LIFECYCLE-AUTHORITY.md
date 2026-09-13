# ADR-002: Kanban Remains Task Lifecycle Authority

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Worker lanes, sub-orchestrators, and distributed agents execute work in parallel. If worker lanes manage their own lifecycle state, the system risks lost-update anomalies, race conditions, and divergent audit logs.

## Decision
The Kanban database (`kanban.db`) remains the single canonical authority for task lifecycle and auditing:
1. `Task ≠ Run`: The task identity is immutable and durable; execution runs are transient attempts.
2. Status transitions (`todo` -> `ready` -> `in_progress` -> `done` / `failed`) are mastered strictly in SQLite WAL with atomic locking.
3. Worker lanes acquire work via atomic leases (`claim_task`), emit periodic heartbeats, and report results back to Kanban without mutating the task specification directly.

## Consequences
- Single source of truth for all tools, CLI commands, and dashboard web UIs.
- Complete crash resilience: un-heartbeated tasks are recovered by lease reclamation.
