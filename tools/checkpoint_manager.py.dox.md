# tools/checkpoint_manager.py — contract

## Purpose

Owns the `/rollback` checkpoint feature end to end: the shadow-git store, the per-turn snapshot
decision, the diff/restore surface, and the retention/prune housekeeping that keeps the store from
growing without bound.

It is **not** a model tool. Nothing here is exposed to the agent as a callable; the agent's writes
are checkpointed as a side effect of tool execution, and the user drives restore through
`/rollback` and `hermes checkpoints`. The module owns storage and policy only — deciding *when* a
snapshot is warranted belongs to the caller (`agent/agent_init.py` constructs the manager; the tool
executor calls `ensure_checkpoint`).

One store serves every project. A single bare repo under `~/.hermes/checkpoints/store/` holds
per-project refs (`refs/hermes/<hash16>`), so git dedupes blobs across projects. The pre-v2 layout
was one repo per working directory and re-stored ~40 MB each; `_migrate_legacy_store` still absorbs
those into `legacy-<ts>/` archives rather than deleting them.

## Contract

`CheckpointManager` — the per-session object:

| Member | Contract |
|---|---|
| `__init__(enabled, max_snapshots, max_total_size_mb, max_file_size_mb)` | Disabled by default. `max_snapshots` is floored at 1; the two size caps are floored at 0 (0 = unlimited). `git` availability is probed lazily, not at construction. |
| `new_turn()` | Resets the per-turn dedup set. Must be called at the start of each agent iteration; a missed call means at most one extra snapshot, never a lost one. |
| `ensure_checkpoint(working_dir, reason)` | Idempotent **within a turn** — the same directory is snapshotted at most once per turn. Returns whether a snapshot exists afterwards. |
| `record_agent_write(file_path)` | Records an agent-originated write in the shared ledger, distinguishing agent edits from user edits during a later restore. |
| `list_checkpoints` / `list_all_checkpoints` | Per-directory and store-wide listings. |
| `diff(working_dir, commit_hash)` / `session_diff(working_dir)` | Change sets against one checkpoint / against the whole session. |
| `safe_restore_plan(working_dir, commit_hash)` | **Preview** of what a restore would do, without doing it. This is the binding input for a restore that deletes: the plan's orphan set is what a later call is allowed to act on. |
| `restore(working_dir, commit_hash, file_path=None, ...)` | Restores the directory, or a single file when `file_path` is given. Takes a pre-rollback snapshot first, so an unwanted restore is itself revertible. |
| `get_working_dir_for_path(file_path)` | Maps a file back to the directory that owns its checkpoints. |

Module-level entry points: `prune_checkpoints`, `maybe_auto_prune_checkpoints`,
`auto_prune_from_config`, `store_status`, `checkpoint_footprint_notice`, `format_checkpoint_list`,
`clear_all`, `clear_legacy`.

Invariants a caller may rely on:

- **Never raises.** `prune_checkpoints`, `maybe_auto_prune_checkpoints`, `auto_prune_from_config`
  and `checkpoint_footprint_notice` are documented never to raise — they are called from startup
  and housekeeping paths where a failure must not take down the process. They return an error
  field instead.
- **Orphan deletion is bound to a preview.** `prune_checkpoints` accepts `orphan_allowlist`; passing
  the set from a `store_status()` preview means a project orphaned *after* the preview is skipped.
  `None` means "delete every current orphan" and is the explicit `--force` path only.
- **`auto_prune_from_config` never honours `delete_orphans` unattended.** A missing working
  directory is ambiguous (deleted vs. unmounted share), so unattended orphan cleanup is refused by
  design; it happens only through an explicit `hermes checkpoints prune`.
- **The base path is resolved per call, not at import.** `CHECKPOINT_BASE` is a module constant
  captured at import time, so `_resolve_checkpoint_base()` re-reads `get_hermes_home()` whenever
  the constant still equals its import-time value. One process serves several profiles; a cached
  base would write profile A's checkpoints into profile B's store. Do not "simplify" this to a bare
  constant read.
- **Git never touches the user's repo.** Every git invocation runs with `GIT_DIR`, `GIT_WORK_TREE`
  and `GIT_INDEX_FILE` pointed at the shadow store, and `_isolated_git_env()` strips inherited git
  configuration. No ref, index or object is written inside the user's project.
- **A store larger than the cap is expected, not broken.** The cap is a floor of one snapshot per
  project, so `checkpoint_footprint_notice` reports rather than trims.

## Side effects

- **Writes** under `~/.hermes/checkpoints/` (profile-aware): `store/` (bare repo), `indexes/<hash16>`,
  `projects/<hash16>.json`, `ledgers/<hash16>.json`, a shared `store/info/exclude`, the
  `.last_prune` marker written by `maybe_auto_prune_checkpoints`, and `legacy-<ts>/` archives.
- **Spawns `git`** as a subprocess with `stdin=DEVNULL`, a timeout (`_GIT_TIMEOUT`), and
  `windows_hide_flags()` on Windows. A timeout is caught and reported, not propagated.
- **Deletes**: `clear_all` irreversibly removes the entire checkpoint base (store plus legacy);
  `clear_legacy` removes only `legacy-*` archives. `prune_checkpoints` deletes per the retention and
  orphan rules above.
- **Reads** `get_hermes_home()` on every base resolution, and the `checkpoints:` config section
  from `hermes_cli.config.load_config` via `auto_prune_from_config`.
- **No network.** No model calls, no gateway traffic.
- **Does not write inside the user's working directory** — the shadow store is the only destination.

## Verification

```bash
scripts/run_tests.sh tests/tools/test_checkpoint_manager.py \
                     tests/tools/test_checkpoint_footprint_notice.py \
                     tests/tools/test_checkpoint_path_roundtrip.py \
                     tests/hermes_cli/test_checkpoints_prune.py \
                     tests/hermes_cli/test_checkpoints_clear_legacy.py \
                     tests/gateway/test_checkpoint_prune_housekeeping.py
```

The profile-scope invariant is covered by `tests/gateway/test_profile_isolation_runtime.py` (two
homes, A→B→A) — a change to `_resolve_checkpoint_base()` must keep that green. Restore semantics
against real directories live in `tests/tools/test_rollback_all_directories.py`.
