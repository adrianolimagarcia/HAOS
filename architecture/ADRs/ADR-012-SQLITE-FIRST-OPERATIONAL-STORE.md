# ADR-012: SQLite-First Operational Store (WAL Mode)

- **Status:** Accepted (Foundational Invariant)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & Hermes Core Team

## Context & Problem Statement
Distributed databases like PostgreSQL or ClickHouse introduce substantial deployment friction, operational overhead, and network partition failure modes for personal and single-node agent systems.

## Decision
HAOS adopts a strict **SQLite-First** operational store philosophy:
1. All core persistence (`kanban.db`, `events.db`, `knowledge_graph.db`) runs on SQLite in Write-Ahead Logging (`WAL`) mode.
2. Thread-safe per-thread connections with `busy_timeout=30000` and `check_same_thread=False` ensure zero lock contention between WebUI readers and worker lane writers.
3. Pluggable adapters allow optional forwarding to external data warehouses (e.g. ClickHouse for telemetry, Postgres for multi-host clusters), while SQLite remains the unbreakable local authority.

## Consequences
- Zero-infrastructure zero-config standalone operation.
- Acid compliance, instant startup, and trivial backup via single-file snapshots.
