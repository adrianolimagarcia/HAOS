import pytest
from hermes.platform.bots.identity import BotIdentityBundle
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.observability.event_store import EventStore


def test_identity_manager_create_and_get():
    store = EventStore()
    mgr = IdentityManager(store)

    bundle_v1 = BotIdentityBundle(
        bot_id="bot-arch",
        soul="Soul v1",
        identity="Identity v1",
        values="Values v1",
    )
    v1 = mgr.create_version("bot-arch", bundle_v1)
    assert v1.version == 1
    assert v1.bot_id == "bot-arch"

    active = mgr.get_active_version("bot-arch")
    assert active is not None
    assert active.id == v1.id
    assert active.bundle.soul == "Soul v1"

    # Create v2
    bundle_v2 = BotIdentityBundle(
        bot_id="bot-arch",
        soul="Soul v2 updated",
        identity="Identity v1",
        values="Values v1",
    )
    v2 = mgr.create_version("bot-arch", bundle_v2)
    assert v2.version == 2
    assert v2.parent_id == v1.id

    active_v2 = mgr.get_active_version("bot-arch")
    assert active_v2.id == v2.id
    assert active_v2.bundle.soul == "Soul v2 updated"

    # List versions
    versions = mgr.list_versions("bot-arch")
    assert len(versions) == 2
    assert [v.version for v in versions] == [1, 2]


def test_identity_manager_projection_rebuild():
    store = EventStore()
    mgr1 = IdentityManager(store)

    bundle = BotIdentityBundle(bot_id="bot-1", soul="Persistent Soul")
    v1 = mgr1.create_version("bot-1", bundle)

    # Re-instantiate IdentityManager using the same EventStore
    mgr2 = IdentityManager(store)
    recovered = mgr2.get_active_version("bot-1")
    assert recovered is not None
    assert recovered.id == v1.id
    assert recovered.bundle.soul == "Persistent Soul"


def test_identity_manager_rollback_compensating():
    store = EventStore()
    mgr = IdentityManager(store)

    b1 = BotIdentityBundle(bot_id="bot-1", soul="Original Good Soul")
    v1 = mgr.create_version("bot-1", b1)

    b2 = BotIdentityBundle(bot_id="bot-1", soul="Bad Drift Soul")
    v2 = mgr.create_version("bot-1", b2)
    assert mgr.get_active_version("bot-1").id == v2.id

    # Rollback to v1 creates v3 with v1 content
    v3 = mgr.rollback("bot-1", v1.id, reason="revert regression")
    assert v3.version == 3
    assert v3.parent_id == v1.id
    assert v3.bundle.soul == "Original Good Soul"
    assert v3.bundle.metadata.get("rollback_from") == v1.id

    # Active is now v3
    assert mgr.get_active_version("bot-1").id == v3.id

    # History contains all 3 versions
    versions = mgr.list_versions("bot-1")
    assert [v.version for v in versions] == [1, 2, 3]


def test_record_drift():
    store = EventStore()
    mgr = IdentityManager(store)
    mgr.record_drift("bot-1", "v-1", "new-hash-xyz")

    events = store.get_all(name="civ.bot.identity-drift-detected")
    assert len(events) == 1
    assert events[0].payload["detected_bundle_hash"] == "new-hash-xyz"
