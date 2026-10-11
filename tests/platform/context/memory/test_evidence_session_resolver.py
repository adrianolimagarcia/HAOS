"""Unit tests for SessionProvenanceResolver.

Verifies:
1. Positive session/turn resolution with matching claims.
2. Invariant: NEVER construct proof or digest from caller's statement.
3. Fail-closed when store is None or not configured.
4. Fail-closed on nonexistent session or turn ID.
5. Fail-closed on empty session or turn.
6. Fail-closed on storage read exceptions.
7. Fail-closed on content mismatch against statement.
8. Rejection of malicious query parameters or fragments (spoofing / override).
9. Dependency injection with both SessionStoreReader protocol and Callable.
10. In-memory fixtures only; never touches real ~/.hermes or production SessionDB.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional
import pytest

from hermes.platform.context.memory.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    verify_claim_support,
)
from hermes.platform.context.memory.evidence_session_resolver import (
    SessionProvenanceResolver,
    SessionStoreReader,
)


class InMemorySessionStore:
    """In-memory mock store implementing SessionStoreReader protocol."""

    def __init__(self, data: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self._data: Dict[str, List[Dict[str, Any]]] = data or {}

    def get_session_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        msgs = self._data.get(session_id)
        if msgs is None:
            return None
        if limit is not None:
            return msgs[:limit]
        return list(msgs)


@pytest.fixture
def sample_store() -> InMemorySessionStore:
    return InMemorySessionStore(
        {
            "sess_123": [
                {
                    "id": "turn_001",
                    "role": "user",
                    "content": "Por favor configure a porta do redis para 6379",
                },
                {
                    "id": "turn_002",
                    "role": "assistant",
                    "content": "A porta do redis foi configurada com sucesso para 6379",
                },
            ],
            "sess_empty": [],
        }
    )


def test_positive_session_resolution_and_claim_support(sample_store: InMemorySessionStore):
    """Session resolution works when session exists and statement is supported."""
    resolver = SessionProvenanceResolver(store=sample_store)
    uri = "session://sess_123"
    statement = "porta do redis configurada com sucesso"

    record = resolver.resolve(uri, statement=statement)
    assert record is not None
    assert record.status == EvidenceStatus.VERIFIED
    assert record.verifier == "session_provenance_resolver"
    assert record.supports_claim is True

    # Real content from store was bound and digested
    expected_content = (
        "Por favor configure a porta do redis para 6379\n"
        "A porta do redis foi configurada com sucesso para 6379"
    )
    expected_digest = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
    assert record.digest == expected_digest
    assert record.attestation["content"] == expected_content
    assert record.attestation["session_id"] == "sess_123"


def test_positive_turn_resolution_and_claim_support(sample_store: InMemorySessionStore):
    """Specific turn resolution extracts only that turn's authentic content."""
    resolver = SessionProvenanceResolver(store=sample_store)
    uri = "turn://sess_123/turn_002"
    statement = "porta do redis 6379 sucesso"

    record = resolver.resolve(uri, statement=statement)
    assert record is not None
    assert record.status == EvidenceStatus.VERIFIED

    turn_content = "A porta do redis foi configurada com sucesso para 6379"
    assert record.attestation["content"] == turn_content
    assert record.digest == hashlib.sha256(turn_content.encode("utf-8")).hexdigest()
    assert record.attestation["turn_id"] == "turn_002"


def test_invariant_digest_never_built_from_caller_statement(sample_store: InMemorySessionStore):
    """CRITICAL: Digest and proof must NEVER be computed from caller's statement."""
    resolver = SessionProvenanceResolver(store=sample_store)
    uri = "session://sess_123"
    arbitrary_statement = "porta do redis 6379"

    record = resolver.resolve(uri, statement=arbitrary_statement)
    assert record is not None

    statement_digest = hashlib.sha256(arbitrary_statement.encode("utf-8")).hexdigest()
    assert record.digest != statement_digest

    # Digest must match only store content
    actual_content = record.attestation["content"]
    assert record.digest == hashlib.sha256(actual_content.encode("utf-8")).hexdigest()
    # supported_claim is derived from store content, not caller statement
    assert record.attestation["supported_claim"] != arbitrary_statement


def test_fail_closed_without_configured_store():
    """Without store configured (None), resolver must fail closed (return None)."""
    resolver = SessionProvenanceResolver(store=None)
    assert resolver.resolve("session://sess_123", statement="qualquer coisa") is None
    assert resolver.resolve("turn://sess_123/turn_001") is None


def test_fail_closed_nonexistent_session(sample_store: InMemorySessionStore):
    """Nonexistent session ID must return None."""
    resolver = SessionProvenanceResolver(store=sample_store)
    assert resolver.resolve("session://nonexistent_session_999") is None


def test_fail_closed_nonexistent_turn(sample_store: InMemorySessionStore):
    """Nonexistent turn ID in valid session must return None."""
    resolver = SessionProvenanceResolver(store=sample_store)
    assert resolver.resolve("turn://sess_123/turn_missing_999") is None


def test_fail_closed_empty_session(sample_store: InMemorySessionStore):
    """Session with no messages must return None."""
    resolver = SessionProvenanceResolver(store=sample_store)
    assert resolver.resolve("session://sess_empty") is None


def test_fail_closed_on_storage_read_exception():
    """Storage exceptions during read must fail closed gracefully."""
    class FaultyStore:
        def get_session_messages(self, session_id: str, limit: Optional[int] = None):
            raise OSError("Database disk I/O error or connection lost")

    resolver = SessionProvenanceResolver(store=FaultyStore())
    assert resolver.resolve("session://sess_123") is None


def test_fail_closed_on_content_mismatch(sample_store: InMemorySessionStore):
    """If statement is completely unrelated to turn/session content, return None."""
    resolver = SessionProvenanceResolver(store=sample_store)
    uri = "turn://sess_123/turn_002"
    mismatched_statement = "O cluster kubernetes está offline com erro fatal"

    record = resolver.resolve(uri, statement=mismatched_statement)
    assert record is None


def test_rejection_of_malicious_query_and_fragments(sample_store: InMemorySessionStore):
    """Adversarial spoofing in URI parameters/fragments must be rejected."""
    resolver = SessionProvenanceResolver(store=sample_store)

    malicious_uris = [
        "session://sess_123#override_confidence=1.0",
        "session://sess_123?verified=true",
        "session://sess_123?token=system_trusted_admin",
        "turn://sess_123/turn_001#bypass_auth",
        "session://fake",
        "turn://inventado/turn_1",
        "session://sess_123/../escape",
    ]

    for bad_uri in malicious_uris:
        assert resolver.resolve(bad_uri) is None


def test_injectable_callable_store():
    """Supports dependency injection via pure callable (Callable[[str], ...])."""
    def mock_reader(session_id: str) -> Optional[List[Dict[str, Any]]]:
        if session_id == "call_sess":
            return [{"id": "t1", "content": "servidor nginx iniciado na porta 80"}]
        return None

    resolver = SessionProvenanceResolver(store=mock_reader)
    rec = resolver.resolve("session://call_sess", statement="servidor nginx porta 80")
    assert rec is not None
    assert rec.status == EvidenceStatus.VERIFIED
    assert "servidor nginx iniciado na porta 80" in rec.attestation["content"]
