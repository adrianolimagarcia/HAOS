# ADR-012: Plugin-first

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Forks of open-source projects frequently suffer merge hell when developers rewrite core files to add specialized features, preventing the fork from receiving security patches and upstream advancements.

## Decision
All new features, backends, and tool integrations adhere to the **Plugin-First** doctrine:
1. Always prefer: Extension $\to$ Adapter $\to$ Plugin $\to$ Hook over modifying upstream Hermes core.
2. The PEP-420 namespace structure (`hermes/platform/` with zero `__init__.py`) ensures zero namespace collisions with upstream modules.
3. If an upstream extension point is insufficient, widen the generic plugin interface itself rather than special-casing features in core.
4. Any unavoidable upstream patch must be logged in `architecture/core-patches.md` with justification and exit plan.

## Alternatives Considered
- *Alternative A: In-place modification of upstream Hermes classes.* Rejected: causes catastrophic merge conflicts on future upstream rebases.
- *Alternative B: Complete detachment and total hard-fork rename.* Rejected: loses the entire upstream ecosystem, tool updates, and community support.

## Consequences
- Frictionless rebase against `NousResearch/hermes-agent:main` at any time.
- Clean separation between kernel and enterprise platform layers.
