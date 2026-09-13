# ADR-011: SQLite-first

- **Status:** Accepted
- **Version:** 1.0.0
- **Date:** 2026-09-08
- **Authors:** Architecture Agent & Hermes Core Team

## Context
Deploying multi-agent systems often stumbles on heavyweight infrastructure prerequisites (e.g. Postgres clusters, Redis brokers, ClickHouse) that prevent fast local startup and edge operation.

## Decision
Adopt a strict **SQLite-First** operational store philosophy:
1. All core persistence (`kanban.db`, `events.db`, `knowledge_graph.db`) is powered by SQLite.
2. Production configuration mandates Write-Ahead Logging (`WAL`), `foreign_keys=ON`, `busy_timeout=30000`, and transaction isolation.
3. Pluggable export adapters allow optional mirroring to enterprise data warehouses without compromising zero-config local operation.

## Alternatives Considered
- *Alternative A: Mandatory Postgres backend.* Rejected: complicates local CLI workflows and requires running containers.
- *Alternative B: Pure JSON file storage on disk.* Rejected: lacks ACID transactions, causes race conditions, and has poor indexing performance.

## Consequences
- Zero-dependency, single-binary / single-command instant startup.
- Complete operational resilience with trivial single-file backup and portability.
