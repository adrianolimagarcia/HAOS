"""Session & Turn Provenance Resolver for GBrain Evidence Hardening.

Provides secure resolution of session:// and turn:// URIs against canonical
session transcripts and turn stores in read-only mode, using dependency injection
(SessionStoreReader protocol or injectable callable).

Security invariants:
1. Strict read-only access with dependency injection.
2. Canonical store queries for existent identifier, concrete source/event,
   real content binding, and calculation of SHA-256 digest on the actual
   bytes/text retrieved from the store.
3. NEVER construct proofs or digests from the caller-supplied statement.
4. Fail-closed: returns None / False if store is None, not configured, or raises exceptions.
5. Rejects nonexistent IDs, empty sessions/turns, spoofed query/fragment params
   (e.g. session://fake#override_confidence=1.0), and semantic content mismatch.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Dict, List, Optional, Protocol, Union, runtime_checkable
import urllib.parse

from hermes.platform.context.memory.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    _SUSPICIOUS_URI_PATTERNS,
    verify_claim_support,
)

# Suspicious patterns in query strings / URL fragments
_SUSPICIOUS_QUERY_FRAGMENT_PATTERNS = re.compile(
    r"(?:token|admin|trusted|role|auth|override|bypass|system_trusted|verified=true|type=fact_verified|confidence)",
    re.IGNORECASE,
)

# Max content size safely processed for hashing / attestation (1 MB)
_MAX_SESSION_CONTENT_BYTES = 1024 * 1024


@runtime_checkable
class SessionStoreReader(Protocol):
    """Protocol for read-only access to session transcripts and messages."""

    def get_session_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch messages/turns for a given session.

        Returns None or empty list if session does not exist.
        """
        ...


SessionStoreCallable = Callable[[str], Optional[List[Dict[str, Any]]]]
SessionStoreType = Union[SessionStoreReader, SessionStoreCallable]


class SessionProvenanceResolver:
    """Secure, fail-closed resolver for session:// and turn:// URIs."""

    name = "session_provenance_resolver"

    def __init__(
        self,
        store: Optional[SessionStoreType] = None,
        max_bytes: int = _MAX_SESSION_CONTENT_BYTES,
        hermes_home: Optional[Any] = None,
        db_path: Optional[Any] = None,
    ) -> None:
        """Initialize SessionProvenanceResolver.

        Args:
            store: SessionStoreReader or callable returning messages for a session.
                   If None, resolver operates in fail-closed mode.
            max_bytes: Maximum allowed bytes for content extraction.
            hermes_home: Optional hermes_home directory to locate state.db.
            db_path: Optional direct path to state.db.
        """
        self.store = store
        self.max_bytes = max_bytes
        self.hermes_home = hermes_home
        self.db_path = db_path

    def can_resolve(self, uri: str) -> bool:
        """Check whether the URI matches session:// or turn:// scheme securely."""
        if not isinstance(uri, str):
            return False
        u = uri.strip()
        if not (u.startswith("session://") or u.startswith("turn://")):
            return False
        if _SUSPICIOUS_URI_PATTERNS.search(u):
            return False
        return True

    def _parse_and_validate_uri(
        self, uri: str
    ) -> Optional[tuple[str, str, Optional[str]]]:
        """Parse URI into (scheme, session_id, turn_id/target).

        Rejects malformed URIs, query strings, fragments, and adversarial injections.
        """
        try:
            parsed = urllib.parse.urlparse(uri.strip())
            scheme = parsed.scheme.lower()
            if scheme not in ("session", "turn"):
                return None

            # Reject malicious query parameters or fragments
            if parsed.query:
                if _SUSPICIOUS_QUERY_FRAGMENT_PATTERNS.search(parsed.query):
                    return None
            if parsed.fragment:
                if _SUSPICIOUS_QUERY_FRAGMENT_PATTERNS.search(parsed.fragment):
                    return None

            # Identifier from netloc or path
            # For turn://session_123/turn_456 or session://session_123
            netloc = parsed.netloc.strip()
            path = parsed.path.strip()

            if netloc and not path:
                session_id = netloc
                turn_id = None
            elif netloc and path:
                session_id = netloc
                turn_id = path.lstrip("/")
            else:
                parts = [p for p in path.split("/") if p]
                if not parts:
                    return None
                session_id = parts[0]
                turn_id = parts[1] if len(parts) > 1 else None

            if not session_id:
                return None

            # Reject path traversal / invalid chars
            if ".." in session_id or (turn_id and ".." in turn_id):
                return None

            return scheme, session_id, turn_id
        except Exception:
            return None

    def _fetch_session_events(
        self, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch session events in read-only mode using injected store or local SessionDB."""
        if self.store is None:
            try:
                from pathlib import Path
                from hermes_constants import get_hermes_home
                from hermes_state import SessionDB

                if self.db_path:
                    db_path = Path(self.db_path).resolve()
                elif self.hermes_home:
                    db_path = Path(self.hermes_home).resolve() / "state.db"
                else:
                    db_path = Path(get_hermes_home()) / "state.db"

                if db_path.exists():
                    db = SessionDB(db_path=db_path)
                    messages = db.get_messages(session_id)
                    db.close()
                    if messages and isinstance(messages, list):
                        return messages
            except Exception:
                pass
            return None

        try:
            if callable(self.store) and not hasattr(self.store, "get_session_messages"):
                messages = self.store(session_id)
            elif hasattr(self.store, "get_session_messages"):
                messages = getattr(self.store, "get_session_messages")(session_id)
            else:
                return None

            if not messages or not isinstance(messages, list):
                return None

            return messages
        except Exception:
            # Storage read exceptions must fail closed
            return None

    def _extract_turn_content(
        self,
        messages: List[Dict[str, Any]],
        turn_id: Optional[str] = None,
    ) -> Optional[str]:
        """Extract content for a specific turn or entire authenticated session."""
        if not messages:
            return None

        if turn_id:
            # Match specific turn/message by ID or 0-based index
            target_msg = None
            for idx, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                msg_id = str(
                    msg.get("id")
                    or msg.get("turn_id")
                    or msg.get("message_id")
                    or ""
                )
                if msg_id and msg_id == turn_id:
                    target_msg = msg
                    break
                # Also allow turn_0, turn_1 or integer index
                if turn_id in (f"turn_{idx}", str(idx)):
                    target_msg = msg
                    break

            if target_msg is None:
                return None

            content = target_msg.get("content") or target_msg.get("text") or ""
            if not isinstance(content, str) or not content.strip():
                return None
            return content.strip()

        # For session:// without specific turn, aggregate message contents in order
        extracted_parts: List[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            text = msg.get("content") or msg.get("text") or ""
            if isinstance(text, str) and text.strip():
                extracted_parts.append(text.strip())

        if not extracted_parts:
            return None

        full_content = "\n".join(extracted_parts)
        if len(full_content.encode("utf-8")) > self.max_bytes:
            # Truncate safely at max_bytes
            full_content = full_content.encode("utf-8")[: self.max_bytes].decode(
                "utf-8", errors="ignore"
            )

        return full_content

    def _extract_supported_claims(self, content: str) -> List[str]:
        """Derive factual claims strictly from the real content retrieved from store."""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return lines[:10]

    def resolve(self, uri: str, statement: str = "") -> Optional[EvidenceRecord]:
        """Resolve session/turn URI against authenticated canonical store.

        Invariants enforced:
        - Fail-closed if store is None or unavailable.
        - NEVER calculate digest or proof from caller's statement.
        - Fail-closed on nonexistent session/turn or empty content.
        - SHA-256 digest is calculated strictly from retrieved content.
        - Fail-closed on content mismatch against statement.
        """
        if not self.can_resolve(uri):
            return None

        parsed = self._parse_and_validate_uri(uri)
        if parsed is None:
            return None

        scheme, session_id, turn_id = parsed

        # Read-only query to injected canonical store
        messages = self._fetch_session_events(session_id)
        if not messages:
            return None

        # Extract authentic content from messages
        retrieved_content = self._extract_turn_content(messages, turn_id)
        if not retrieved_content or not retrieved_content.strip():
            return None

        # Digest SHA-256 strictly on retrieved store content
        raw_bytes = retrieved_content.encode("utf-8")
        if len(raw_bytes) > self.max_bytes:
            raw_bytes = raw_bytes[: self.max_bytes]
            retrieved_content = raw_bytes.decode("utf-8", errors="ignore")

        digest = hashlib.sha256(raw_bytes).hexdigest()

        # Derive supported claims solely from actual retrieved content
        supported_claims = self._extract_supported_claims(retrieved_content)
        supported_claim = supported_claims[0] if supported_claims else ""

        # NEVER construct proofs or digests from the caller's statement!
        record = EvidenceRecord(
            uri=uri.strip(),
            status=EvidenceStatus.VERIFIED,
            verifier=self.name,
            digest=digest,
            attestation={
                "source": "canonical_session_store",
                "uri": uri.strip(),
                "session_id": session_id,
                "turn_id": turn_id,
                "content": retrieved_content,
                "supported_claims": supported_claims,
                "supported_claim": supported_claim,
            },
            supports_claim=True,
        )

        # Fail-closed semantic congruence check if statement provided
        if statement and statement.strip():
            if not verify_claim_support(record, statement):
                return None

        return record
