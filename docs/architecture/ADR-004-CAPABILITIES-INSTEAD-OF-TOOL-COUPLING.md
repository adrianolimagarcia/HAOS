# ADR-004: Capabilities Instead of Tool Coupling

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Binding agents directly to hardcoded tool names bloats context tokens, breaks prompt caching across conversations, and introduces vendor lock-in.

## Decision
Abstract all tool invocations through the `UniversalCapabilityRegistry`:
1. Capabilities are registered with semantic metadata, category (`mcp`, `lsp`, `kilo`, `plugin`, `multimodal`), and failure policy (`FAIL_CLOSED` or `FAIL_OPEN`).
2. Agents request capabilities based on their active posture (e.g. `posture: coder` receives file + git + lsp; `posture: reviewer` receives AST + diff inspection).
3. Tools are dynamically resolved, filtered, and sandboxed without polluting the core prompt schema.

## Consequences
- High modularity: tools can be replaced by MCP servers or native plugins seamlessly.
- Strict security boundaries enforced per posture.
