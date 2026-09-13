# ADR-009: Exact-Model Provider Failover & Circuit Breakers

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Transient network glitches, HTTP 429 rate limits, and 503 service outages are common in cloud inference. Naive retry loops hammer empty rate buckets or cause mission stalls.

## Decision
The `ExactModelClient` couples circuit breakers with exact-model multi-provider routing:
1. Each provider endpoint has an independent `CircuitBreaker` tracking consecutive failures and cooldown backoffs.
2. When a provider trips (e.g. 503 or confirmed 429), traffic automatically shifts to the next prioritized route hosting the **exact same model**.
3. Fallback to `reasoning_content` is supported when `thinking: {"type": "disabled"}` or reasoning tokens exhaust output buffers.

## Consequences
- 99.9%+ effective availability for agent inference.
- Zero silent model downgrade or semantic drift during outages.
