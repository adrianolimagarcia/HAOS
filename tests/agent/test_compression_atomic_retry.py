"""The compression lock must never fail open or fork a live session."""
from types import SimpleNamespace
from unittest.mock import Mock
from agent.conversation_compression import _retry_lock_acquisition_if_unowned


def _lease(owner):
    db = Mock()
    db.get_compression_lock_holder.return_value = owner
    db.get_active_message_watermark.return_value = 42
    lease = SimpleNamespace(db=db, sid="test-session", holder="candidate", ttl=300, watermark=None)
    return lease


def test_owner_present_never_retries():
    lease = _lease("other-live-compressor")
    claim = Mock(return_value=True)
    assert _retry_lock_acquisition_if_unowned(lease, claim, None) is False
    claim.assert_not_called()


def test_owner_disappeared_retries_atomic_claim_only_once():
    lease = _lease(None)
    claim = Mock(return_value=True)
    assert _retry_lock_acquisition_if_unowned(lease, claim, None) is True
    claim.assert_called_once_with("test-session", "candidate", ttl_seconds=300)
    assert lease.watermark == 42


def test_new_owner_wins_race_stays_closed():
    lease = _lease(None)
    claim = Mock(return_value=False)
    assert _retry_lock_acquisition_if_unowned(lease, claim, None) is False
    claim.assert_called_once()


def test_lookup_failure_stays_closed():
    lease = _lease(None)
    lease.db.get_compression_lock_holder.side_effect = RuntimeError("database busy")
    claim = Mock(return_value=True)
    assert _retry_lock_acquisition_if_unowned(lease, claim, None) is False
    claim.assert_not_called()


def test_missing_lease_holder_stays_closed():
    lease = _lease(None)
    lease.holder = None
    claim = Mock(return_value=True)
    assert _retry_lock_acquisition_if_unowned(lease, claim, None) is False
    claim.assert_not_called()
