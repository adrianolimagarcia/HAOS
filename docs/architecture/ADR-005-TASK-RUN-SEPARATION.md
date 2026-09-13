# ADR-005: Task ≠ Run (Separation of Goal and Execution Attempt)

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
When a single database row represents both what needs to be done (specification) and how it was done (execution run), retries overwrite failure forensics, making post-mortem analysis and crash recovery impossible.

## Decision
Enforce strict separation between `TaskSpec` and `TaskRun`:
1. `TaskSpec` is immutable, defining the objective, dependencies (`requires_tasks`), acceptance criteria, and budget.
2. `TaskRun` represents a single bounded execution attempt by a specific worker, with its own start time, heartbeat timestamp, token consumption, and exit status.
3. A task may have $N$ runs (e.g. run 1 crashed $\to$ run 2 re-attempted and succeeded).

## Consequences
- Complete operational traceability and failure classification.
- Transparent crash recovery and deterministic audit trails.
