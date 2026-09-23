"""Acceptance gates for the blocked Rust writer contract v2.

These tests deliberately fail where the current ``haos-edge`` has not implemented the
contract. They specify observable requirements without starting a server, opening a real
state.db, or changing the writer cutover.
"""

from __future__ import annotations

import pytest

CONTRACT_VERSION = "haos-edge.rust-writer.v2"
SCHEMA_VERSION = 2
WRITER_ROUTE = "/internal/rust-writer/v2/operations"

OPERATIONS = {
    "create_session",
    "append_messages",
    "update_session",
    "set_session_archived",
    "set_session_hidden",
    "set_session_pinned",
    "set_session_read",
    "set_session_title",
    "update_session_cwd",
    "update_session_meta",
    "set_message_reaction",
    "delete_session",
    "archive_and_compact",
}
FORBIDDEN_OPERATIONS = {"execute_sql", "query", "statement", "mutation"}
ERROR_CODES = {
    "invalid_request",
    "unsupported_contract_version",
    "unsupported_schema_version",
    "unknown_operation",
    "missing_idempotency_key",
    "invalid_idempotency_key",
    "invalid_timeout",
    "unauthenticated",
    "forbidden",
    "profile_mismatch",
    "data_dir_mismatch",
    "idempotency_conflict",
    "not_found",
    "timeout",
    "conflict",
    "busy",
    "internal",
    "storage_unavailable",
    "schema_mismatch",
    "read_only",
}


def test_contract_identity_and_closed_operation_registry():
    assert CONTRACT_VERSION == "haos-edge.rust-writer.v2"
    assert SCHEMA_VERSION == 2
    assert WRITER_ROUTE.startswith("/internal/")
    assert OPERATIONS
    assert not (OPERATIONS & FORBIDDEN_OPERATIONS)


def test_writer_route_is_present_and_versioned():
    """The current crate has no v2 typed-writer route."""
    pytest.fail(
        "BLOCKED: current haos-edge exposes no POST "
        f"{WRITER_ROUTE}; v1 read/SSE routes must not gain implicit writes"
    )


def test_startup_requires_explicit_profile_and_data_dir_binding():
    """A missing binding must fail closed before any database is opened."""
    pytest.fail(
        "BLOCKED: current run_server resolves HAOS_DATA_DIR and falls back to "
        "/tmp/haos_shared_data instead of requiring immutable --profile and --data-dir"
    )


def test_internal_auth_is_checked_before_database_access():
    """WebUI cookies and unset auth must not authorize the internal writer."""
    pytest.fail(
        "BLOCKED: no v2 internal Authorization boundary exists; current auth can allow "
        "access when no password is configured and is not bound to a writer route"
    )


def test_typed_operations_reject_raw_sql_and_unknown_fields():
    """The wire contract must have no SQL execution escape hatch."""
    pytest.fail(
        "BLOCKED: current crate has no v2 request validator/typed operation dispatcher "
        "that can reject raw SQL and unknown operation fields"
    )


def test_idempotency_is_atomic_and_detects_fingerprint_conflicts():
    """Retrying one request must not duplicate its write or overwrite its result."""
    pytest.fail(
        "BLOCKED: existing idempotency storage is not transactionally coupled to typed "
        "state.db writes and no v2 route enforces same-key fingerprint conflicts"
    )


def test_timeout_is_a_transactional_deadline_with_rollback():
    """A deadline must prevent a committed partial write and return timeout."""
    pytest.fail(
        "BLOCKED: current writer surfaces do not expose v2 timeout_ms validation, a total "
        "monotonic deadline, or rollback/readback acceptance coverage"
    )


def test_error_codes_are_stable_and_machine_readable():
    assert "idempotency_conflict" in ERROR_CODES
    assert "profile_mismatch" in ERROR_CODES
    assert "timeout" in ERROR_CODES
    assert "internal" in ERROR_CODES
    assert "storage_unavailable" in ERROR_CODES
    pytest.fail(
        "BLOCKED: current endpoints return ad-hoc error payloads and no v2 error envelope "
        "maps the required codes to HTTP statuses"
    )


def test_writer_is_the_only_state_db_mutation_authority_before_cutover():
    """The v2 gate must stay red while other Rust SQLite writers remain active."""
    pytest.fail(
        "BLOCKED: current haos-edge still starts EventHub's background SQLite writer and "
        "other writable surfaces; the writer cutover is intentionally not implemented here"
    )
