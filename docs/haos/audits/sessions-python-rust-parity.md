# Sessions Python/Rust parity

## Observed differences

- Python `GET /api/sessions` defaults to `limit=20`, `offset=0`, `archived=exclude`, `order=created`; Rust previously defaulted to 30 rows and sorted only by `COALESCE(last_activity_at, started_at)`.
- Python excludes hidden rows, compression/delegate children, and archived rows by default; Rust previously returned all rows.
- Python supports `archived`, `order`, `source`, `sources`, `exclude_sources`, `cwd_prefix`, `min_messages`, `full`, and returns `sessions`, `total`, `limit`, `offset` plus profile/activity flags. Rust previously accepted only `limit` and returned a reduced incompatible row shape.
- Python recent ordering uses the freshest of heartbeat and message timestamps, then started time and id; Rust did not include message timestamps or deterministic ties.
- Python compact list rows omit `system_prompt` and `model_config`; Rust's reduced projection is now explicit and stable.
- Python list rows expose boolean `archived`/`pinned`; Rust now normalizes SQLite integers to booleans.

## Implementation

`packages/haos-edge/src/server.rs` now has a read-only parameterized projection with Python-compatible defaults, archived/hidden/branch filtering, source exclusion, deterministic recent ordering, total count, offset, explicit compact fields, and the existing versioned/profile envelope. The query remains behind the existing authentication middleware.

`tests/haos_edge/fixtures/sessions_parity.json` and `test_sessions_parity.py` provide a deterministic fixture covering archived, hidden, cron exclusion, branch visibility, ephemeral child exclusion, ordering, and compact projection.

## Verification

- `cargo fmt --manifest-path packages/haos-edge/Cargo.toml` — passed.
- `cargo check -p haos-edge` — passed; one pre-existing dead-code warning (`DbHelper::checkpoint_all_dbs`).
- Python parity fixture test was added but not executed in this worktree because the isolated worktree has no local `.venv`; parent checkout venv path is outside the worktree.
