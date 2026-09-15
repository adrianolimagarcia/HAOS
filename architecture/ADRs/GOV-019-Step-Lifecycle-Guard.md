# GOV-019: Step-Level Lifecycle Guard & Deterministic Loop Control (DSH Inspiration)

> **Canonical ID:** `GOV-019` (renamed from `ADR-019`). Registry: [`INDEX.md`](INDEX.md).

- **Status:** Approved
- **Date:** 2026-09-09
- **Governed By:** GOV-001 (Hermes Kernel), GOV-002 (Task Lifecycle), GOV-010 (Worker Lanes), GOV-016 (Anti-Drift Topologies), GOV-018 (Ultrawork)

## Context
Analysis of DeepSeek Harness (`deepseek-ai/deepseek-harness`) highlighted the power of an explicit Step-Level Lifecycle (`before_step` -> `execute_step` -> `after_step`) over loose tool iteration loops. In multi-agent environments, allowing leaf workers to execute multiple subsequent tools after a critical execution failure wastes prompt budget, poisons context with cascade errors, and breaks prompt caching.

## Decision
1. **Step Lifecycle Guard:**
   - Every tool call dispatched in sequential or segmented execution must be guarded by pre-execution validation and post-execution inspection.
   - When a deterministic failure (e.g. non-zero command return code, file access violation, syntax check error) is encountered on a critical step, execution of subsequent steps in the same batch MUST halt immediately to prevent error cascade.
   - A Reflexion loop is triggered immediately, forcing the worker/agent to verbalize the failure root cause before retrying.

2. **Ordered Posture Profiles:**
   - Adopting DSH profile hygiene: Agents are configured with strict, declarative toolset boundaries per posture (`executive/mayor`, `architect/planner`, `implementer/worker`, `reviewer/qa`).
   - Workers never inherit coordination tools (`delegate_task`, `request_operator_form`), preserving narrow context and prompt caching.

3. **Loop Control & Circuit Breakers:**
   - Long-running tool chains have an explicit step-level threshold and timeout to prevent runaway agent execution.

## Consequences
- Prevents cascade failures where step 2 fails and step 3 tries to edit non-existent output from step 2.
- Drastically reduces token consumption on failed steps.
- Guarantees immediate verbalization and recovery in alignment with HAOS engineering discipline.
