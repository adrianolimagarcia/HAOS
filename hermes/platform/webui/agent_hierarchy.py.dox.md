## Purpose
Owns persistent HAOS organizational hierarchy, node validation, advisory edges, and bounded council state.

## Contract
Provides atomic snapshots and validated mutations for master, manager, and bot nodes. File writes are atomic and process-safe.

## Side effects
Reads and writes the configured hierarchy JSON and lock file; no network or subprocesses.

## Verification
Run `scripts/run_tests.sh tests/platform/webui/test_agent_hierarchy.py tests/platform/webui/test_standalone_webui.py`.
