# ADR-001: Hermes Kernel

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Hermes possesses a robust cognitive kernel, agent loop, tool orchestration, and plugin system. As HAOS expands to multi-agent teams and distributed workloads, there is pressure to replace or wrap the agent core with heavyweight external orchestration frameworks.

## Decision
Hermes remains the central, uncompromised cognitive kernel:
1. AIAgent, Kanban lifecycle, PluginManager, and tool dispatch remain canonical upstream surfaces.
2. Extensions, adapters, and plugins are always preferred over core modifications.
3. Per-conversation prompt caching is sacred: prefix tokens remain byte-stable across conversational turns.

## Alternatives Considered
- *Alternative A: Reimplement a new custom agent loop from scratch.* Rejected: creates high maintenance drag and splits the community ecosystem.
- *Alternative B: Wrap Hermes in an external heavy container.* Rejected: destroys low latency and breaks native tool integration.

## Consequences
- Clean rebasing against upstream `NousResearch/hermes-agent:main`.
- Upstream patches are strictly a last resort and must be documented in `architecture/core-patches.md`.
