# Rust read-only SSE contract (v1)

**Status:** implementation contract for an authenticated Rust observer. This does not authorize a writer cutover or deployment.

Python remains the sole write authority for `state.db` and `events.db` throughout this slice.

## Contract identifier and envelopes

Contract version: `haos-edge.readonly-sse.v1`.

Every JSON success response and every SSE `data:` envelope contains:

```json
{
  "contract_version": "haos-edge.readonly-sse.v1",
  "profile": "<bound-profile>",
  "schema_version": 1,
  "data": {}
}
```

Stable error shape:

```json
{
  "contract_version": "haos-edge.readonly-sse.v1",
  "profile": "<bound-profile-or-null>",
  "schema_version": 1,
  "error": {"code": "storage_unavailable", "message": "read failed", "request_id": "<id>"}
}
```

Minimum error codes: `invalid_request`, `unsupported_contract_version`, `unauthenticated`, `forbidden`, `profile_mismatch`, `storage_unavailable`, `schema_mismatch`, `replay_unavailable`.

## Profile binding

The process must resolve one immutable profile and its data directory before constructing server state. Existing process-level resolution can supply the already-resolved values, but unset values must fail closed in observer mode; no `/tmp`, default profile, or client-supplied arbitrary path fallback.

Any request profile parameter must match the process binding. It selects no filesystem path. Mismatch is rejected before opening SQLite. A→B→A tests use independent homes and distinct processes; identical IDs/titles across profiles must not leak.

## Authentication

`/health`, login/logout, and static assets remain public under existing policy. Session, state and SSE routes require a valid credential. Authentication must be proven to reject a request before opening/reading the database.

- missing/invalid credential: HTTP 401;
- malformed profile/request: HTTP 400;
- profile mismatch: HTTP 403;
- unavailable/corrupt storage: HTTP 503, without SQL, secret, or filesystem path leakage.

The existing cookie middleware/handler checks are not accepted as proof until covered by black-box tests on the actual route.

## SQLite observer boundary

For every observer read:

- open existing databases using `SQLITE_OPEN_READ_ONLY | SQLITE_OPEN_NO_MUTEX`;
- set `PRAGMA query_only=ON` and bounded `busy_timeout`;
- never create a DB/table/index/WAL, migrate schema, change journal mode, or checkpoint;
- bound query/replay work;
- surface read failures before starting an SSE response.

Observer mode has no event-ingest route, EventHub background writer, or other persistent SQLite writer. Python `SessionDB` and `EventStore` remain the only writers. The black-box proof hashes `state.db`, `events.db`, and their `-wal`/`-shm` sidecars before/after requests; bytes and file-presence must be identical.

## Session response — compatibility rule

Route: `GET /api/sessions/fast`.

The active consumer/reference is `hermes/platform/webui/standalone.py::_sessions_list`:

- it calls `/api/sessions/fast?` with the original query string and accepts a successful payload directly;
- fallback reads `id`, `title`, `started_at`, `last_activity_at` from candidate databases, deduplicates by session ID, resolves a missing title from the first user message (trimmed, max 80 characters, ellipsis when truncated), formats timestamps, sorts by updated timestamp descending, and returns `{sessions: [...]}` with `session_id`, `title`, `started_at`, `updated_at`, `updated_ts`, and `db`.
- fallback default is `limit=30`; it does not demonstrate the proposed filters `include_archived`, `archived_only`, `source`, `include_children`, `min_message_count`, or `order_by_last_active`.

Therefore v1 must preserve the consumer's existing fields and not invent filters absent from the active reference. Add `contract_version`, `profile`, and `schema_version` additively. Preserve any legacy fields consumed by clients during the transition. A differential fixture must settle timestamp formatting, multiple DB selection/deduplication, title fallback, ordering and limit before claiming parity. If a behavior is not represented in the Python reference, mark it out of scope rather than guessing.

## SSE response

Route: `GET /api/events/stream?last_seq=<n>`.

Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

- Authenticate and resolve the immutable profile before opening `events.db`.
- Replay events in ascending `seq`, maximum 100; `id` equals `seq`.
- Snapshot, event and resync payloads all carry the v1 envelope.
- Invalid cursor returns HTTP 400; replay/storage failures return 503 before streaming starts.
- Heartbeat is transport-only and carries no state mutation.

## Routes and writer separation

Observer mode is explicit and read-only. It must not register `/api/events/ingest` or instantiate `EventHub::new`, because the latter starts `spawn_writer_task`. Preserve existing control-plane/writer mode for unrelated callers; do not silently convert the entire current server into read-only if that would break existing behavior. The observer-mode black-box test verifies the writer task is absent and POST ingest is not exposed.

## Acceptance gates

1. Version/profile envelope exists on JSON and SSE responses.
2. Missing credentials receive 401 before DB access.
3. A→B→A profile isolation passes with real temporary homes.
4. `state.db`, `events.db`, WAL and SHM bytes/presence are unchanged after reads.
5. Session payload is differential-tested against the active Python fallback; no unsupported filter is invented.
6. SSE headers, event envelopes, sequence IDs, replay limit and error-before-stream behavior pass.
7. Observer mode exposes no persistent Rust writer/ingest route.

Only after all gates pass may a reversible read-path feature flag direct traffic to the observer. No writer route, writer cutover, or deployment is part of v1.
