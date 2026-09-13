# ADR-003: Model / Provider Separation Axiom

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Traditional multi-agent frameworks conflate the model identity (e.g. `deepseek-v4-flash`) with the hosting provider endpoint (e.g. `a6api`, `openrouter`, `deepseek-direct`). This leads to silent degradation when a primary route fails.

## Decision
Enforce the strict mathematical axiom: `Model != Provider`:
1. `ModelIdentity` is defined as `(family, variant)`.
2. `ModelProfile` holds a prioritized list of `ProviderRoute` configurations pointing to different physical endpoints hosting the exact same model weights/family.
3. Failover switches the transport route, NEVER silently downgrading to an inferior model.
4. If all routes for the requested model are exhausted, the system fails closed with `ModelRouteExhaustedException`.

## Consequences
- Predictable inference quality and benchmark reproducibility.
- Zero silent regressions during provider outages.
