# ADR-007: Context Isolation & Byte-Stable Prompt Caching

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Modern LLMs rely heavily on KV prompt caching for cost reduction and ultra-low latency. Any dynamic mutation of past system instructions, dynamic tool swapping mid-turn, or un-ordered prompt injection invalidates the cache on every round.

## Decision
Enforce strict byte-stable prefix caching and context isolation:
1. The base system prompt and tool definitions compile to an exact byte-stable prefix that remains identical turn-over-turn.
2. Context items (memories, file contents, conversation history) are appended chronologically at the tail with deterministic ordering.
3. Mid-turn toolset swapping or prompt mutations are forbidden; any configuration change is deferred to the next session unless explicitly forced.

## Consequences
- 70–90% reduction in inference costs via provider prompt-cache hits.
- Sub-second first-token latency on long-running multi-turn sessions.
