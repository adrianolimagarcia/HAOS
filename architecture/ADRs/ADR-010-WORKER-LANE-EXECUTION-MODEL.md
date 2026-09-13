# ADR-010: Worker Lane Execution Model (GenericWorkerLane)

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Workers run in diverse environments: local threads, isolated subprocesses, ephemeral git worktrees (Kilo), or remote cloud instances. Coupling the task engine to a specific execution substrate introduces fragile spaghettification.

## Decision
All execution substrates adhere to the canonical `GenericWorkerLane` lifecycle contract:
1. `prepare(task, workspace)`: Sets up the execution environment, credentials, and worktree.
2. `spawn(assignment)`: Launches the agent process or worker thread.
3. `heartbeat(run_id)`: Emits periodic keep-alive signals to prevent lease reclamation.
4. `collect_result(run_id)`: Extracts artifacts, logs, token usage, and exit codes.
5. `cancel(run_id)`: Gracefully terminates runaway or steered tasks.
6. `cleanup(workspace)`: Purges ephemeral branches and scratch workspaces.

## Consequences
- Concrete lanes (`HermesLane`, `KiloLane`, `RemoteKiloLane`) inherit a uniform interface.
- Complete isolation between workers executing concurrent tasks.
