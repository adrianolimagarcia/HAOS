# ADR-011: Plugins Extend Upstream, Not Replace It

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Overwriting core upstream files to add custom capabilities creates unmaintainable forks that cannot keep up with upstream security fixes and community features.

## Decision
All HAOS extensions operate through the standard `PluginManager` and extension hooks:
1. Capabilities, memory providers, tools, and UI panels are packaged as plugins or modular adapters.
2. Changes to upstream Hermes core are forbidden unless they expand the generic plugin interface itself.
3. PEP-420 namespace packages under `hermes/platform/` guarantee zero collision with standard upstream modules.

## Consequences
- Frictionless upstream rebases and merge parity.
- Clean separation between core Hermes agent and the HAOS multi-agent team runtime.
