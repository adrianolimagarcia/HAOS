"""Tests for Canonical Event Stream and Cryptographic Receipts."""

import json
import pytest
from starlette.testclient import TestClient

from hermes.platform.events.canonical_event import (
    CanonicalEvent,
    verify_event_chain,
    sign_event,
    verify_signature,
)
from hermes.platform.events.event_stream import EventStreamManager
from hermes_cli.web_server import app


class TestCanonicalEventStream:
    def test_hash_chain_integrity_and_tamper_detection(self):
        manager = EventStreamManager()
        ev1 = manager.emit("sess_1", "turn.start", {"user": "alice"})
        ev2 = manager.emit("sess_1", "tool.call", {"tool": "terminal", "cmd": "ls"})
        ev3 = manager.emit("sess_1", "turn.finish", {"status": "ok"})

        chain = [ev1, ev2, ev3]
        valid, idx, msg = verify_event_chain(chain)
        assert valid is True
        assert idx is None

        # Tamper ev2 payload
        ev2_tampered = CanonicalEvent(
            session_id=ev2.session_id,
            event_id=ev2.event_id,
            event_type=ev2.type,
            payload={"tool": "terminal", "cmd": "rm -rf /"},  # altered!
            prev_hash=ev2.prev_hash,
            timestamp=ev2.timestamp,
            state_hash=ev2.state_hash,  # keeping old hash -> mismatch
        )
        tampered_chain = [ev1, ev2_tampered, ev3]
        valid, idx, msg = verify_event_chain(tampered_chain)
        assert valid is False
        assert idx == 1
        assert "State hash mismatch" in msg

    def test_circular_buffer_bounds(self):
        manager = EventStreamManager(max_buffer_size=5)
        for i in range(10):
            manager.emit("sess_circ", "step", {"step": i})

        events = manager.get_events_after(last_event_id=0, session_id="sess_circ")
        # Buffer holds only last 5
        assert len(events) == 5
        assert [e.payload["step"] for e in events] == [5, 6, 7, 8, 9]

    def test_reconnection_last_event_id(self):
        manager = EventStreamManager()
        for i in range(1, 6):
            manager.emit("sess_recon", "step", {"step": i})

        # Ask events strictly after id 3
        missed = manager.get_events_after(last_event_id=3, session_id="sess_recon")
        assert len(missed) == 2
        assert [e.event_id for e in missed] == [4, 5]

    def test_cryptographic_receipts_signing(self):
        key = "secret-hmac-key-2026"
        manager = EventStreamManager()
        ev = manager.emit("sess_crypto", "action", {"authorized": True}, signing_key=key)

        assert ev.signature is not None
        assert ev.signature.startswith("hmac-sha256:")
        assert verify_signature(ev.state_hash, ev.signature, key) is True
        assert verify_signature(ev.state_hash, ev.signature, "wrong-key") is False

        # Verify chain with key
        valid, idx, msg = verify_event_chain([ev], signing_key=key)
        assert valid is True

    def test_sse_endpoint_delivery(self):
        from hermes.platform.events.event_stream import get_event_stream_manager
        stream_mgr = get_event_stream_manager()
        stream_mgr.clear()

        # Emit 3 events
        ev1 = stream_mgr.emit("sess_web", "event_1", {"v": 1})
        ev2 = stream_mgr.emit("sess_web", "event_2", {"v": 2})
        ev3 = stream_mgr.emit("sess_web", "event_3", {"v": 3})

        client = TestClient(app)
        # Fetch with Last-Event-ID = 1
        resp = client.get("/stream/events", headers={"Last-Event-ID": "1"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        text = resp.text
        assert "event: event_2" in text
        assert "event: event_3" in text
        assert "event: event_1" not in text
