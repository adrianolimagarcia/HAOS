# Golden replay/diff contract (Fase 0B)

`hermes_state_replay.py` is the first implementation of the observation boundary
between the current Python `SessionDB` and a future Rust writer. It is deliberately
read-only with respect to the source database: callers provide a connection and the
operation's explicit affected-table projection.

## Canonical JSON

An observation is an object with:

- `contract`: `haos.rust_writer.golden_replay`;
- `schema_version`: `1`;
- `operation`: typed operation name and request;
- `result`: the operation's observable result;
- `tables`: sorted snapshots of only the declared affected tables.

JSON objects are recursively key-sorted and emitted without insignificant whitespace
(`ensure_ascii=false`, UTF-8). Arrays preserve semantic order. SQLite rows are sorted
by their declared key columns (and then their full row); tables and diff entries are
sorted by name/path. Bytes use `{"$bytes_base64":"..."}`. NaN and infinity are
rejected instead of receiving language-specific spellings.

The Rust implementation should emit this shape, with the same projected columns and
key columns. It must not send arbitrary SQL through the writer API: each operation
owns its typed request and affected-table list.

## Diff semantics

`diff_observations(expected, actual)` reports deterministic entries for:

- operation/request/result changes;
- missing or unexpected affected tables and projection metadata;
- keyed rows missing, unexpected, duplicated, or changed;
- unkeyed-table multiset changes.

`ReplayDiff.raise_if_divergent()` raises `ReplayDivergenceError` with canonical JSON,
which is suitable for a golden artifact or CI output.

## Safe fixture usage

The tests create `state.fixture.db` beneath pytest's `tmp_path`, populate only the
small schema needed by the operation, and close it after each test. They never resolve
or open `HERMES_HOME/state.db`. Production replay is intentionally not implemented in
this phase.

## Limitations

- This is an observation/diff library, not a Rust runner or IPC client.
- It does not discover affected tables from SQL; the operation contract must declare
them explicitly.
- It compares projected rows, not SQLite file bytes, WAL frames, pragmas, triggers,
FTS indexes, or query plans.
- Array order is preserved because order can be observable; callers must define an
operation whose result ordering is stable before recording a golden.
- No committed golden dataset is included yet; current tests exercise the contract
with temporary fixtures and a Python round-trip standing in for future Rust JSON.
