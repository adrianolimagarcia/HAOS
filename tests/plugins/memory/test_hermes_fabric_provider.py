"""Runtime wiring for the bundled ``hermes_fabric`` memory provider.

Loaded through the real discovery path (``plugins.memory.load_memory_provider``)
against the temp ``HERMES_HOME`` the suite already isolates, so this exercises
the plugin contract end to end instead of instantiating the module directly:
ABC conformance, a canonical journal shared with the coordinator that owns the
write path, decision capture reaching that journal, and a clean shutdown.
"""

from __future__ import annotations

import plugins.memory as memory_plugins
from agent.memory_provider import MemoryProvider


def test_bundled_provider_loads_through_discovery_and_uses_canonical_journal():
    provider = memory_plugins.load_memory_provider("hermes_fabric", register_skills=False)
    assert provider is not None, "hermes_fabric was not discovered as a bundled memory provider"
    assert isinstance(provider, MemoryProvider)
    assert provider.name == "hermes_fabric"
    # The coordinator owns the write path; the registered provider must already
    # point at that same journal or prefetch reads a store nobody writes to.
    assert provider.canonical_store is not None

    provider.initialize(session_id="wiring")
    provider.sync_turn(
        "Choose the durable policy",
        "DECISION: the canonical journal is the only source of truth.",
        session_id="wiring",
    )
    recalled = provider.prefetch("canonical journal", session_id="wiring")
    assert "canonical journal" in recalled

    # Ordinary prose is not memory: no marker, nothing persisted.
    before = provider.canonical_store.list_records()
    provider.sync_turn("what time is it", "It is about noon.", session_id="wiring")
    assert provider.canonical_store.list_records() == before

    provider.shutdown()
