# ADR-008: Memory Fabric

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Multi-agent teams need access to long-term architectural knowledge, project conventions, and runtime lessons without polluting short-term working context or relying on opaque vector black boxes.

## Decision
The Memory Fabric unifies memory access through a single `MemoryFabricProvider` supporting multi-scoped storage:
1. **Scopes:** 4 distinct visibility tiers: `private`, `team`, `project`, `global`.
2. **Obsidian Vault:** Canonical human-readable repository for ADRs, runbooks, architecture notes, and retrospectives.
3. **GraphRAG Store:** Relational knowledge graph for entity extraction, cross-document links, and community detection.
4. **Deduplication & Supersession:** Automatically links newer facts to older ones via `supersedes` / `superseded_by` timestamps.

## Alternatives Considered
- *Alternative A: Pure vector database with opaque embeddings.* Rejected: impossible for human operators to audit, edit, or curate.
- *Alternative B: Unstructured single text file.* Rejected: unscalable across concurrent agents.

## Consequences
- High retrieval precision with human-in-the-loop inspectability.
- Seamless synchronization between Obsidian markdown notes and GraphRAG relational entities.
