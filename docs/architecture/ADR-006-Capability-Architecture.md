# ADR-006: Capability Architecture

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Exposing every tool to every agent floods LLM context windows, degrades tool-calling accuracy, multiplies token costs, and violates the principle of least privilege.

## Decision
Abstract concrete tools behind semantic **Capabilities**:
1. Tasks and Postures request semantic capabilities (e.g. `code-intelligence`, `terminal-execution`, `web-search`) rather than concrete tool names (e.g. `lsp_find_references`).
2. The `CapabilityResolver` maps capabilities to underlying providers: native tools, plugins, MCP servers, or LSP instances.
3. Capability Packs (`software`, `research`, `review`, `multimodal`) group related tools into cohesive, reusable bundles.
4. Dynamic capability expansion (`request_capability`) allows temporary acquisition during a run without permanent posture mutation.

## Alternatives Considered
- *Alternative A: Static global toolsets for all agents.* Rejected: causes token bloat and hallucinated tool calls.
- *Alternative B: Ad-hoc tool injection in prompt strings.* Rejected: fragile and unvalidated at runtime.

## Consequences
- Clean abstraction: underlying tool backends (e.g. switching from local grep to an LSP server or MCP) happen transparently without rewriting prompts.
- Strict security boundaries enforced dynamically per task.
