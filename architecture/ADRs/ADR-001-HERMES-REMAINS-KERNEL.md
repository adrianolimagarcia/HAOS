# ADR-001: Hermes Remains Kernel

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
As the multi-agent operating system (HAOS) scales across diverse capabilities, team topologies, and memory backends, there is a temptation to rebuild a secondary monolithic agent loop that competes with Hermes's narrow-waist core.

## Decision
Hermes remains the central, uncompromised narrow-waist kernel:
1. Every capability, whether MCP, LSP, Kilo workspace, or team coordination, is mediated as an extension or adapter around the Hermes runtime, not a replacement of it.
2. Per-conversation prompt caching is sacred: prefix tokens remain byte-stable across conversational turns.
3. Upstream patches to Hermes core are strictly a last resort; functionality is delivered via plugins, skills, CLI mixins, and service-gated toolsets.

## Consequences
- Clean rebase against upstream `NousResearch/hermes-agent:main` remains zero-drift.
- Eliminates redundant session engines and runtime split-brain risks.
