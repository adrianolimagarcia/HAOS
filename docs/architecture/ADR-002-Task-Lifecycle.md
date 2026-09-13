# ADR-002: Task Lifecycle

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Worker lanes, sub-orchestrators, and distributed agents execute work in parallel. If execution lanes own the task state, workers crashing or stalling leads to state loss, orphaned workflows, and inconsistent taskboards.

## Decision
Task lifecycle is permanent, durable, and mastered exclusively in the persistent operational store (`kanban.db` / `tasks` table):
1. Workers may die; tasks do not.
2. Canonical lifecycle states: `TODO` -> `READY` -> `IN_PROGRESS` -> `REVIEW` -> `DONE` / `FAILED` / `BLOCKED`.
3. Workers acquire temporary leases via heartbeats. If a lease expires, the task reverts to `READY` for automatic rescheduling.

## Alternatives Considered
- *Alternative A: In-memory task management inside worker processes.* Rejected: zero resilience against worker crash or machine reboot.
- *Alternative B: Pure Git branch state as task authority.* Rejected: high race condition probability and slow status queries.

## Consequences
- 100% crash resilience: any crashed worker run is isolated without corrupting the canonical task.
- Kanban remains the canonical audit and visibility plane for operators and dashboards.
