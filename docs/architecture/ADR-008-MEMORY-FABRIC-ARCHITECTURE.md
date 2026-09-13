# ADR-008: Memory Fabric Architecture (Obsidian & GraphRAG)

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Agents need long-term memory that is both human-auditable (transparent) and semantically queryable across complex entity relationships, without opaque vector-only black boxes.

## Decision
The HAOS Memory Fabric combines two complementary storage models:
1. **Obsidian Markdown Vault:** Human-readable notes partitioned into 4 distinct visibility scopes (`private`, `team`, `project`, `global`).
2. **GraphRAG SQLite Store:** Relational knowledge graph storing extracted entities, typed edges, and temporal supersession timestamps.
3. Deduplication and supersession ensure obsolete facts are superseded rather than duplicated.

## Consequences
- Operators can inspect and edit agent knowledge directly in Obsidian.
- High retrieval precision with provenance linking each memory item to its source task and run.
