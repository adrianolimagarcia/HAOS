# ADR-013: Runtime vs LLM Responsibilities

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Relying on stochastic LLM reasoning for mechanical distributed systems primitives (such as lease heartbeats, rate limiting, provider health, task queues, and locking) introduces nondeterministic deadlocks, split-brain conditions, and uncontrolled token burn.

## Decision
Enforce strict bifurcation between **Deterministic Runtime** and **Cognitive LLM** duties:
1. **Deterministic Runtime Authority (Zero LLM involvement):**
   - Task leases and heartbeat expiration.
   - Circuit breakers, rate limits, and provider failover decisions.
   - Database transactions, mutex locks, and worktree creation/cleanup.
   - Resource budget enforcement and capacity backpressure.
2. **Cognitive LLM Authority:**
   - Architecture decomposition and task DAG generation.
   - Code writing, debugging, and patch generation.
   - Independent code review and adversarial inspection.
   - Text, audio, and visual perception understanding.

## Alternatives Considered
- *Alternative A: "Agent-as-Scheduler" where LLMs manage cron, leases, and queues via prompt loops.* Rejected: highly fragile, nondeterministic, and wildly expensive.
- *Alternative B: Monolithic non-agentic traditional automation.* Rejected: lacks dynamic problem-solving and flexible reasoning.

## Consequences
- Unbreakable operational stability: the system never crashes due to a model hallucination or loop stall.
- Transparent metrics tracking independent of model behavior.
