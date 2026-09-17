# ADR-013: Canonical Memory Fabric

## Status
Accepted for the migration path.

## Decision
CanonicalMemoryStore (SQLite/WAL) is the only durable writer of a memory record. It owns immutable revisions, temporal validity, provenance, supersession, content-hash idempotency, full-text search and a transactional outbox. A successful write atomically inserts the record and memory.changed:<record_id>; no projection may write canonical state.

| Component | Role after migration |
|---|---|
| CanonicalMemoryStore | canonical journal, outbox, FTS, scope/temporal policy |
| FederatedMemoryCoordinator | command facade, authorization and projection recovery |
| ObsidianAdapter | human-auditable Markdown projection |
| DecisionStore | decision projection, never a second truth |
| GraphRAGStore / GraphRAGAdapter | rebuildable graph projection |
| HermesFabricMemoryProvider | provider facade and retrieval entrypoint |

## Event protocol
1. Validate caller scope and provenance.
2. Append a deterministic record in one SQLite transaction with its outbox event. Retried commands use the same idempotency key.
3. A projection worker claims an event with a lease.
4. The projection performs an idempotent upsert keyed by record_id.
5. It acknowledges only after the upsert completes. A crash leaves the event unacknowledged and recover_projections() replays it.

The legacy EventBus is not durable and must not be the source of recovery. A synchronous subscriber must publish with enqueue=False, otherwise it receives the same event again when the queue drains.

## Record and scopes
A record carries record_id, logical_id, revision, kind, content, confidence, structured provenance, valid_from, valid_until, supersession edges and metadata. Scope is mandatory:

- private: only the owning principal;
- team: members of the named team;
- project: project members;
- global: explicitly curated shared knowledge.

Scope filtering happens before every retrieval phase and is retained in every projection payload. It is never inferred from a pathname.

## Retrieval target
Candidate generation is parallel FTS, vector and graph traversal, all scope-filtered. Fuse ranked lists with weighted RRF, then cross-encoder or LLM rerank the top K. Deduplicate by logical_id, prefer active/latest revisions, preserve citations/provenance, and pack a token-budgeted context using diversity-aware selection. GraphRAG contains no unique facts: it can be dropped and rebuilt by replaying canonical active records in deterministic record order.

## Migration and invariants
Backfill Obsidian and DecisionStore documents into records first, with original URI and timestamps as provenance. Build GraphRAG only after canonical backfill, then switch reads to hybrid retrieval and disable legacy writers.

Required invariants:
- one canonical write per idempotency key;
- every canonical record has exactly one outbox event;
- projections are replaceable and idempotent;
- an acknowledged projection reflects the same record revision;
- superseded records never enter default retrieval;
- no cross-scope candidate reaches ranking or prompt packing.
