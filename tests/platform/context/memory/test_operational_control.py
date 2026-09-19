"""The fabric's operational surface: journal counters, and cutover control from outside.

Merged from origin's `f2d4c751e1` (read-only operational metrics) and `4429ddc4b0` (explicit
cutover flags). Both were dead on their own branch — nothing imported them — but each carried one
idea the in-tree implementation lacked: the store counters a cutover decision reads, and a way to
move the cutover without editing code. The flag *names* and the module layout are this tree's, not
origin's, because these are the ones `federated_fabric` actually branches on.

Contracts pinned here:

**The counters describe the journal, not the process.** A migration that wrote nothing and a
healthy quiet fabric look identical from in-process counters; these come from SQL over the store.
**The cutover is a prefix.** A stage cannot be on while an earlier one is off, and saying so is an
error rather than something the cascade silently resolves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.flags import CUTOVER_ORDER, FlagError, MemoryFeatureFlags

DECISION = "DECISION: the canonical journal is the only source of truth for memory."


def _store(tmp_path: Path) -> CanonicalMemoryStore:
    return CanonicalMemoryStore(tmp_path / "fabric.db")


# --------------------------------------------------------------------------
# Journal counters (origin's f2d4c751e1, folded into the store that owns the SQL)
# --------------------------------------------------------------------------


def test_operational_counters_follow_the_journal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert store.operational_counters() == {
            "records_active": 0,
            "records_superseded": 0,
            "outbox_events": 0,
            "outbox_retries": 0,
            "expired_leases": 0,
        }

        first = store.append(content=DECISION, scope="project")
        store.append(content="Retention window is 30 days.", scope="project")
        counters = store.operational_counters()
        assert counters["records_active"] == 2
        assert counters["records_superseded"] == 0
        assert counters["outbox_events"] >= 2, "a write that reaches the journal emits an outbox event"

        # The status filter is the point: a superseded record must leave the active count and
        # appear in the superseded one, or the numbers cannot answer "did the cutover land?".
        store.append(content="Deploys use Nomad.", scope="project", supersedes=[first.record_id])
        counters = store.operational_counters()
        assert counters["records_superseded"] == 1
        assert counters["records_active"] == 2
    finally:
        store.close()


def test_fabric_metrics_carries_the_journal_beside_the_outbox_backlog(tmp_path: Path) -> None:
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "vault")
    try:
        access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)

        snapshot = coordinator.fabric_metrics()
        assert "outbox_backlog" in snapshot, "the per-projection backlog keeps its existing key"
        assert snapshot["journal"]["records_active"] >= 1
    finally:
        coordinator.canonical_store.close()


# --------------------------------------------------------------------------
# Cutover control (origin's 4429ddc4b0, folded into the ordered flag set)
# --------------------------------------------------------------------------


def test_a_disabled_stage_turns_off_everything_after_it() -> None:
    flags = MemoryFeatureFlags.from_config(
        {"canonical_writes": True, "projections_via_outbox": True, "canonical_fts": False}
    )
    assert flags.active() == ["canonical_writes", "projections_via_outbox"]
    assert flags.position() == 2


def test_a_stage_on_while_an_earlier_one_is_off_is_refused() -> None:
    """The cascade must not quietly resolve this: the operator asked for something incoherent."""
    with pytest.raises(FlagError, match="cutover stages are a prefix"):
        MemoryFeatureFlags.from_config({"canonical_writes": False, "vector_rrf": True})


def test_an_unset_config_leaves_every_stage_on() -> None:
    assert MemoryFeatureFlags.from_config({}).active() == list(CUTOVER_ORDER)


def test_env_overrides_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The origin branch's spelling for this stage keeps working."""
    monkeypatch.setenv("HAOS_MEMORY_CANONICAL_READS", "no")
    flags = MemoryFeatureFlags.from_config({"canonical_fts": True})
    assert not flags.enabled("canonical_fts")


def test_an_env_name_with_no_stage_is_reported_but_not_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """origin's shadow flag has no stage here; saying so beats ignoring it silently."""
    monkeypatch.setenv("HAOS_MEMORY_SHADOW_RETRIEVAL", "true")
    with caplog.at_level("WARNING"):
        flags = MemoryFeatureFlags.from_config({})
    assert flags.active() == list(CUTOVER_ORDER), "an unrelated name must not move the cutover"
    assert "HAOS_MEMORY_SHADOW_RETRIEVAL" in caplog.text


def test_an_unparseable_env_value_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAOS_MEMORY_CANONICAL_WRITES", "maybe")
    with pytest.raises(FlagError, match="is not a boolean"):
        MemoryFeatureFlags.from_config({})
