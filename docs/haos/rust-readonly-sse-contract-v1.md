# Rust read-only SSE contract (v1)

Status: **BLOCKED — contract tests intentionally fail against the current `haos-edge` implementation.**

This is the smallest safe slice: Rust is an authenticated observer of the Python-owned
profile store. Python remains the only writer of `state.db` and `events.db`.

## Contract identifier

`haos-edge.readonly-sse.v1`

Every JSON response and every SSE `data:` envelope carries:

```json
{"contract_version":"haos-edge.readonly-sse.v1", "profile":"<explicit-id>"}
```

Unknown future versions must fail closed with an explicit `unsupported_contract_version` error.

## Profile binding

The server must receive one explicit profile binding at startup. The binding resolves to the
profile home before constructing `AppState`; request handlers must use that immutable resolved
home. `HERMES_HOME`/`HAOS_HOME` may provide the already-resolved profile home, but an unset
binding must not silently fall back to `/tmp/haos_shared_data`, `~/.haos`, or another profile.

The A→B→A acceptance case uses two real homes and fresh processes. A response from A must never
contain a session/event from B, including when IDs and titles are equal.

## Auth boundary

`/health`, `/login`, `/api/login`, `/api/logout`, and static assets are public. All state,
sessions, timeline, event ingest/stream, and SSE routes are authenticated. Missing or invalid
credentials return `401` before opening a profile database.

## SQLite observer boundary

For every read connection:

- open an existing database with `SQLITE_OPEN_READ_ONLY | SQLITE_OPEN_NO_MUTEX`;
- set `PRAGMA query_only=ON` and a bounded `busy_timeout` (2 seconds in v1);
- never create a database, table, index, WAL, checkpoint, or schema migration;
- WAL is accepted/read as-is; Rust must not change `journal_mode` or run a checkpoint;
- query work is bounded by the request timeout (2 seconds in v1).

There is no Rust event-ingest writer, background flush task, writable SQLite route, or other
second writer. The Python `EventStore`/`SessionDB` is the sole write authority. The proof is a
black-box test: the Rust surface has no writable ingest route and a read request leaves the
main DB, `-wal`, and `-shm` byte-identical.

## Session response

`GET /api/sessions/fast` accepts the same explicit filters as the Python reference for this
slice: `limit`, `offset`, `include_archived`, `archived_only`, `source`,
`include_children`, `min_message_count`, and `order_by_last_active`.

Each item preserves the reference fields required by the parity fixture, including:
`id`, `display_name`/`title`, `source`, `started_at`, `last_active`, `message_count`,
`archived`, `hidden`, `pinned`, `parent_session_id`, `profile_name`, `schema_version`,
`model`, and `cwd`. The default excludes hidden, archived, and child sessions, matching Python.
Ordering and pagination are deterministic and applied after the same filters.

## SSE response

`GET /api/events/stream?last_seq=<n>` returns one `text/event-stream` response with exactly one
value for each of these headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, and
`Content-Type: text/event-stream`. It emits a versioned snapshot, replayed events with monotonic
`id == seq`, heartbeats, and a terminal `resync` event on replay/consumer lag. The replay query is
read-only and capped at 100 events; the route does not write or checkpoint SQLite.

## Required gates

1. Auth-negative sessions/SSE tests pass.
2. A→B→A profile isolation test passes with fresh processes.
3. SQLite read-only/WAL byte-preservation test passes.
4. Session parity fixture passes for filters, ordering, fields, and `schema_version`.
5. SSE headers/envelope/version test passes.
6. No Rust writer surface/second-writer test passes.
7. Only then may a reversible feature flag route traffic to Rust. No deployment is part of this
   worktree change.

Current observed blockers are recorded by the tests in
`tests/haos_edge/test_readonly_sse_contract.py`.
