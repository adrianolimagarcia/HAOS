# ADR-006: Artifact-Driven Agent Communication

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Passing entire conversational histories and intermediate tool chatter between agents explodes context limits, exhausts token budgets, and causes reasoning distraction (anchoring bias).

## Decision
Inter-agent coordination in HAOS is strictly **artifact-driven**:
1. When Worker A finishes, it emits structured `Artifacts` (e.g. source files, test reports, patch diffs, AST summaries).
2. Worker B (or Reviewer) receives only the `TaskSpec`, the target `Artifacts`, and relevant context package—NEVER the raw, unedited conversation transcript of Worker A.
3. Reviewers inspect outputs independently without epistemic contamination.

## Consequences
- 80%+ reduction in handoff token usage.
- High review independence and zero cognitive anchoring on implementer mistakes.
