# ADR-009: Artifact Communication

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Forwarding entire conversation logs and internal reasoning traces between agents exhausts token limits and introduces cognitive bias (anchoring reviewers to the author's flawed assumptions).

## Decision
Inter-agent collaboration is strictly **artifact-driven**:
1. Workers emit structured `ArtifactSpec` records (code patches, test results, AST summaries, diagrams).
2. Dependent agents (Reviewers, Judges, Consumers) receive only the `TaskSpec` and the produced `Artifacts`, never the raw conversational transcript of the producer.
3. Large artifacts are stored out-of-band in the filesystem/artifact store with cryptographic SHA-256 hashes.

## Alternatives Considered
- *Alternative A: Piping full transcripts between agents.* Rejected: causes token budget explosions and review bias.
- *Alternative B: Pure in-memory dictionary passing.* Rejected: untraceable and non-reproducible.

## Consequences
- 80%+ token savings during task handoffs.
- Completely objective, independent reviews without cognitive anchoring.
