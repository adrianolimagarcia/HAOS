# ADR-010: Worker Lanes

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Different coding and execution environments (local processes, Docker containers, Kilo worktrees, remote nodes) require a uniform execution contract to prevent spaghettified coupling to specific execution substrates.

## Decision
All execution substrates implement the `GenericWorkerLane` lifecycle interface:
$$\text{prepare()} \longrightarrow \text{spawn()} \longrightarrow \text{heartbeat()} \longrightarrow \text{collect()} \longrightarrow \text{cancel()} \longrightarrow \text{cleanup()}$$
1. **Rule:** 1 writable worker = 1 isolated Git worktree (or container sandbox).
2. Workers never execute in shared writable master workspaces simultaneously.
3. Leases require periodic heartbeats; missing heartbeats trigger automatic lease reclamation.

## Alternatives Considered
- *Alternative A: Shared working directory with file locks.* Rejected: fatal git index conflicts and dirty state.
- *Alternative B: Subprocess execution directly on current repo root.* Rejected: destroys untracked files and blocks parallel workers.

## Consequences
- Total execution isolation across parallel workers.
- Safe, atomic merge procedures via the Integrator role.
