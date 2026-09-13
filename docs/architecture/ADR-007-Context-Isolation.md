# ADR-007: Context Isolation

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Per-conversation KV prompt caching provides 70–90% cost savings and ultra-low latency. Any dynamic mutation of past system prompts, re-ordering of tools, or unsorted context injection destroys the cache hit rate on modern providers.

## Decision
Enforce strict context isolation and byte-stable prefix caching:
1. The base system prompt and tool definitions compile to a deterministic, byte-stable prefix across all turns of a conversation.
2. Context items are packaged via `ContextPackageBuilder` with deterministic sorting and token budgeting.
3. Mid-turn prompt alterations or tool swaps are prohibited.
4. When tool outputs or context exceed token limits, deterministic elision preserves the prefix and stores large payloads in the `ArtifactStore`, passing back an elided summary and hash pointer.

## Alternatives Considered
- *Alternative A: Dynamic system prompt rewriting on every turn.* Rejected: invalidates prompt cache 100% of the time.
- *Alternative B: Unbounded context concatenation.* Rejected: causes 400 Bad Request / ContextWindowExceeded errors.

## Consequences
- Massive token cost reduction and sub-second response times.
- Reproducible, audit-traceable context packages recorded in `context_packages`.
