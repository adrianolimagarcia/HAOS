"""Tests for Reverse Engineering Permission Broker & Audit (Phase 5).
Validates exact grants, allowlist checking, SHA-256 target hashing, and asymmetric timeouts.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
import pytest

from hermes.platform.re.broker import (
    REPermissionBroker,
    VALID_RE_GRANTS,
    MUTATING_GRANTS,
)


@pytest.fixture
def broker(tmp_path):
    log_file = tmp_path / "test_decision_log.json"
    repo_tests = Path(__file__).resolve().parent
    return REPermissionBroker(
        decision_log_path=log_file,
        allowlisted_dirs=["/bin", "/usr/bin", repo_tests],
    )


def test_exact_grant_enforcement(broker, tmp_path):
    """Assert 1: generic wildcards like 're.*' or unknown grants are rejected."""
    sample_file = tmp_path / "sample.bin"
    sample_file.write_bytes(b"\x7fELFtestbinarycontent")

    allowed, reason = broker.evaluate_request(
        requester="agent_re",
        grant="re.*",
        target=sample_file,
    )
    assert not allowed
    assert "prohibited wildcards" in reason or "invalid" in reason

    allowed, reason = broker.evaluate_request(
        requester="agent_re",
        grant="r2.read_functions",
        target="/bin/ls",
    )
    assert allowed


def test_allowlist_boundaries(broker, tmp_path):
    """Assert 2: binaries in allowlist pass; binaries outside require approval."""
    # /bin/ls is allowlisted
    allowed, _ = broker.evaluate_request(
        requester="agent_re",
        grant="r2.disassemble",
        target="/bin/ls",
    )
    assert allowed

    # Untrusted binary outside allowlist
    external_dir = tmp_path / "untrusted"
    external_dir.mkdir()
    external_bin = external_dir / "target.bin"
    external_bin.write_bytes(b"\x90\x90\x90\x90")

    # Without approval -> DENY
    allowed, reason = broker.evaluate_request(
        requester="agent_re",
        grant="r2.disassemble",
        target=external_bin,
        operator_approved=False,
    )
    assert not allowed
    assert "outside allowlist and lacks operator approval" in reason

    # With explicit approval -> ALLOW
    allowed, reason = broker.evaluate_request(
        requester="agent_re",
        grant="r2.disassemble",
        target=external_bin,
        operator_approved=True,
    )
    assert allowed
    assert "explicit operator approval was provided" in reason


def test_decision_log_records_sha256(broker, tmp_path):
    """Assert 3: every evaluation registers the target SHA-256 in the decision log."""
    sample_bin = tmp_path / "hashed_sample.bin"
    data = b"\x7fELFbinary_hash_check_data_12345"
    sample_bin.write_bytes(data)
    expected_hash = hashlib.sha256(data).hexdigest()

    broker.evaluate_request(
        requester="analyst",
        grant="ghidra.decompile",
        target=sample_bin,
        operator_approved=True,
    )

    log_entries = broker.get_decisions()
    assert len(log_entries) > 0
    latest = log_entries[-1]

    assert latest["target_sha256"] == expected_hash
    assert latest["grant"] == "ghidra.decompile"
    assert latest["requester"] == "analyst"
    assert latest["outcome"] == "allow"
    assert "timestamp" in latest


def test_asymmetric_timeout_policy(broker, tmp_path):
    """Assert 4: mutating timeout triggers fail-closed 'abort'; read timeout triggers 'timeout'."""
    sample_bin = tmp_path / "timeout_target.bin"
    sample_bin.write_bytes(b"\xcc\xcc\xcc\xcc")

    # Mutating operation timeout -> ABORT
    action_mut = broker.handle_timeout(
        requester="worker",
        grant="r2.write",
        target=sample_bin,
        elapsed_seconds=15.0,
    )
    assert action_mut == "abort"

    # Read operation timeout -> TIMEOUT
    action_read = broker.handle_timeout(
        requester="worker",
        grant="ghidra.decompile",
        target=sample_bin,
        elapsed_seconds=120.0,
    )
    assert action_read == "timeout"

    # Verify both were written to decision log
    decisions = broker.get_decisions()
    actions = [d["outcome"] for d in decisions]
    assert "abort" in actions
    assert "timeout" in actions
