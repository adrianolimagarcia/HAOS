# ADR-004: Model/Provider Separation

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Multi-agent systems often hardcode provider endpoints alongside model names, leading to silent degradation (e.g. falling back from `deepseek-v4` to an inferior model when a provider returns 429/503).

## Decision
Model identity and provider endpoints are strictly decoupled:
1. `ModelIdentity`: Represents semantic model weights and family (e.g. `deepseek-v4-flash`).
2. `ProviderRoute`: Represents an endpoint route carrying auth, endpoint URL, and priority (e.g. `a6api`, `direct`, `openrouter`).
3. Provider failover is permitted only across routes hosting the **exact same model**.
4. Silent model substitution is strictly prohibited. If all routes fail, the run fails closed with `ModelRouteExhaustedException`.

## Alternatives Considered
- *Alternative A: Dynamic LLM fallback to cheaper/faster models.* Rejected: causes unpredictable regressions in code generation and instruction adherence.
- *Alternative B: Single hardcoded provider per model.* Rejected: zero fault tolerance during upstream provider outages.

## Consequences
- Deterministic inference quality across retries.
- High availability through prioritized route chains with independent circuit breakers.
