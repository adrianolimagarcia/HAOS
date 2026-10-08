"""Regression tests for terminal snapshot isolation of subagent delegation markers.

When a parent session spawns subagents or when commands run under a delegated
subagent context, the child process environment carries
HERMES_DELEGATED_CHILD_CONTEXT and HERMES_KANBAN_* markers.

Because the local terminal environment captures shell variable snapshots
via `_export_dump_excluding_session_vars` to restore state across commands,
these markers must NEVER be persisted into the shared snapshot file.
Furthermore, if a pre-existing/legacy snapshot file on disk was already
contaminated, loading that snapshot must NOT leak the markers into a parent
command at read time, while legitimate child commands running inside
`delegated_child_context()` must preserve their assigned markers.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from agent.delegation_context import delegated_child_context
from tools.environments.base_session_env import (
    _SNAPSHOT_EXCLUDED_ENV_REGEX,
    _SNAPSHOT_TRANSIENT_ENV_NAMES,
)
from tools.environments.local import LocalEnvironment


def test_regex_matches_transient_delegation_and_kanban_markers():
    rx = re.compile(_SNAPSHOT_EXCLUDED_ENV_REGEX)
    for name in _SNAPSHOT_TRANSIENT_ENV_NAMES:
        line = f'declare -x {name}="test_value"'
        assert rx.search(line), f"{name} should be matched by _SNAPSHOT_EXCLUDED_ENV_REGEX"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash snapshot path")
def test_parent_child_parent_terminal_snapshot_isolation(tmp_path: Path):
    """Verify sequence: Parent command -> Child command -> Parent command.

    Parent must not become contaminated with HERMES_DELEGATED_CHILD_CONTEXT
    or lost Kanban authority after child execution.
    """
    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    env.init_session()
    try:
        # Step 1: Parent runs a command that sets an environment variable
        r1 = env.execute('export PARENT_VAR=parent_val; echo "MARKER=[$HERMES_DELEGATED_CHILD_CONTEXT]"')
        assert r1["returncode"] == 0
        assert "MARKER=[]" in r1["output"]

        # Step 2: Child executes command inside delegated_child_context
        # The child environment receives HERMES_DELEGATED_CHILD_CONTEXT
        with delegated_child_context():
            r2 = env.execute('export CHILD_VAR=child_val; echo "MARKER=[$HERMES_DELEGATED_CHILD_CONTEXT]"')
            assert r2["returncode"] == 0
            assert "MARKER=[]" not in r2["output"]
            assert "MARKER=[" in r2["output"]

        # Step 3: Parent runs a subsequent command.
        # Parent must retain PARENT_VAR, must retain CHILD_VAR (shell state persistence),
        # but HERMES_DELEGATED_CHILD_CONTEXT must NOT leak into the parent environment!
        r3 = env.execute(
            'echo "MARKER=[$HERMES_DELEGATED_CHILD_CONTEXT]"; '
            'echo "PV=[$PARENT_VAR]"; '
            'echo "CV=[$CHILD_VAR]"'
        )
        assert r3["returncode"] == 0
        assert "MARKER=[]" in r3["output"], f"Parent was contaminated with child marker: {r3['output']}"
        assert "PV=[parent_val]" in r3["output"]
        assert "CV=[child_val]" in r3["output"]

        # Step 4: Verify snapshot file does not contain the forbidden markers
        snap = env._snapshot_path
        if os.path.exists(snap):
            with open(snap) as f:
                content = f.read()
                for name in _SNAPSHOT_TRANSIENT_ENV_NAMES:
                    assert name not in content, f"{name} persisted into snapshot file"
    finally:
        env.cleanup()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash snapshot path")
def test_legacy_contaminated_snapshot_sanitized_at_read_time(tmp_path: Path):
    """Verify that a pre-existing contaminated snapshot does not leak markers to parent,
    and is cleansed on the next snapshot dump.
    """
    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    env.init_session()
    try:
        # Seed the snapshot with dirty markers and a legitimate shell variable
        snap = env._snapshot_path
        with open(snap, "w") as f:
            f.write(
                'export HERMES_DELEGATED_CHILD_CONTEXT="/root/.haos/contaminated"\n'
                'export HERMES_KANBAN_TASK="card-contaminated-123"\n'
                'export HERMES_KANBAN_RUN_ID="run-contaminated-456"\n'
                'export HERMES_KANBAN_CLAIM_LOCK="lock-contaminated-789"\n'
                'export USER_LEGIT_VAR="kept_value"\n'
            )

        # 1. Parent command runs: must NOT inherit contaminated markers, but must see USER_LEGIT_VAR
        r_parent = env.execute(
            'echo "M=[$HERMES_DELEGATED_CHILD_CONTEXT]"; '
            'echo "K=[$HERMES_KANBAN_TASK]"; '
            'echo "U=[$USER_LEGIT_VAR]"'
        )
        assert r_parent["returncode"] == 0
        assert "M=[]" in r_parent["output"], f"Contaminated marker leaked to parent: {r_parent['output']}"
        assert "K=[]" in r_parent["output"], f"Contaminated kanban task leaked to parent: {r_parent['output']}"
        assert "U=[kept_value]" in r_parent["output"]

        # 2. Child command runs: must see its legitimate child marker AND USER_LEGIT_VAR
        with delegated_child_context():
            r_child = env.execute(
                'echo "M=[$HERMES_DELEGATED_CHILD_CONTEXT]"; '
                'echo "U=[$USER_LEGIT_VAR]"'
            )
            assert r_child["returncode"] == 0
            assert "M=[]" not in r_child["output"]
            assert "M=[" in r_child["output"]
            assert "U=[kept_value]" in r_child["output"]

        # 3. Snapshot on disk must now be completely clean of contaminated markers
        with open(snap) as f:
            content = f.read()
            for name in _SNAPSHOT_TRANSIENT_ENV_NAMES:
                assert name not in content, f"{name} remains in snapshot after dump"
            assert "USER_LEGIT_VAR" in content
    finally:
        env.cleanup()
