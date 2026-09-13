# ADR-003: Task vs Run

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
When an execution fails or requires rework after review, overwriting the task record destroys execution history, prevents root-cause diagnosis, and breaks retry budgets.

## Decision
Enforce strict physical and logical separation between `TaskSpec` and `TaskRun`:
1. `TaskSpec`: What is requested (objective, dependencies, acceptance criteria, budget). Persistent and immutable per revision.
2. `TaskRun`: A single bounded execution attempt by a specific worker, with its own start time, heartbeat, token metrics, errors, and output artifacts.
3. A single Task can have multiple runs:
   - Run 1: `FAILED` (provider timeout)
   - Run 2: `REVIEW_REJECTED` (failed linter/tests)
   - Run 3: `ACCEPTED` (review approved)

## Alternatives Considered
- *Alternative A: Single table combining task and execution details.* Rejected: retries wipe out crash diagnostics.
- *Alternative B: Separate logs without structured run entity.* Rejected: unqueryable for statistical analysis and auto-tuning.

## Consequences
- Complete operational traceability.
- Rework and failure classification engines can accurately calculate rework rates and MTTR.
