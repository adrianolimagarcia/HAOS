"""Unit tests for background EvolutionCurator: leases, experience cursor, and proposal engine."""

import tempfile
import time
from pathlib import Path

import pytest

from hermes.platform.bots.identity import BotIdentityBundle
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.evolution.bot_curator import EvolutionCurator
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    STATUS_CANARY,
    STATUS_PROMOTED,
    STATUS_ROLLED_BACK,
)
from hermes.platform.observability.event_store import EventStore


@pytest.fixture
def curator_env():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = EventStore(root / "events.db")
        id_mgr = IdentityManager(store)
        evo_mgr = BotEvolutionManager(store)

        # Create active bot identity
        bundle = BotIdentityBundle(
            bot_id="worker-bot",
            soul="Reliable worker bot.",
            identity="Worker",
            values="Precision and safety.",
        )
        id_mgr.create_version("worker-bot", bundle, activate=True)

        state_dir = root / "state"

        curator = EvolutionCurator(
            event_store=store,
            id_mgr=id_mgr,
            evo_mgr=evo_mgr,
            state_dir=state_dir,
            profile_id="test-profile",
            curator_id="curator-alpha",
        )

        yield {
            "root": root,
            "store": store,
            "id_mgr": id_mgr,
            "evo_mgr": evo_mgr,
            "curator": curator,
            "state_dir": state_dir,
        }


def test_curator_lease_acquisition_and_conflict(curator_env):
    curator_1 = curator_env["curator"]
    store = curator_env["store"]
    id_mgr = curator_env["id_mgr"]
    evo_mgr = curator_env["evo_mgr"]
    state_dir = curator_env["state_dir"]

    # First curator acquires lease
    assert curator_1.acquire_lease(ttl_seconds=30.0) is True

    # Second curator on same profile cannot acquire lease
    curator_2 = EvolutionCurator(
        event_store=store,
        id_mgr=id_mgr,
        evo_mgr=evo_mgr,
        state_dir=state_dir,
        profile_id="test-profile",
        curator_id="curator-beta",
    )
    assert curator_2.acquire_lease(ttl_seconds=30.0) is False

    # Status reflects active lease
    status = curator_2.get_status()
    assert status["lease_active"] is True
    assert status["lease_owner"] == "curator-alpha"

    # First curator releases lease
    curator_1.release_lease()

    # Now second curator can acquire
    assert curator_2.acquire_lease(ttl_seconds=30.0) is True
    curator_2.release_lease()


def test_curator_experience_cursor_and_proposal_formulation(curator_env):
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]

    # Record 2 failures in the same domain for worker-bot
    evo_mgr.record_experience(
        bot_id="worker-bot",
        event_type="task_failure",
        domain="database_migration",
        summary="Lock timeout on table users",
        success=False,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot",
        event_type="task_failure",
        domain="database_migration",
        summary="Deadlock detected during foreign key alter",
        success=False,
    )

    # First curator run should ingest both experiences and formulate a proposal
    res1 = curator.run_cycle(dry_run=False)
    assert res1["status"] == "completed"
    assert res1["experiences_ingested"] == 2
    assert res1["proposals_generated"] == 1
    assert len(res1["created_proposal_ids"]) == 1

    prop_id = res1["created_proposal_ids"][0]
    proposals = evo_mgr.get_proposals("worker-bot")
    assert any(p.id == prop_id for p in proposals)
    target_prop = [p for p in proposals if p.id == prop_id][0]
    assert "database_migration" in target_prop.rationale
    assert target_prop.proposed_soul_patch is not None

    # Second run immediately without new events should process 0 experiences (cursor advanced)
    res2 = curator.run_cycle(dry_run=False)
    assert res2["status"] == "completed"
    assert res2["experiences_ingested"] == 0
    assert res2["proposals_generated"] == 0


def test_curator_canary_evaluation_promotes_healthy(curator_env):
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]

    active_ver = id_mgr.get_active_version("worker-bot")

    # Create a proposal directly and set to canary
    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class="low",
        rationale="Optimize parser speed",
        proposed_values_patch="Fast parser values",
    )
    evo_mgr.update_status(prop.id, STATUS_CANARY, active_ver.bundle_hash)

    # Add 3 successful post-canary experiences
    time.sleep(0.01)
    for i in range(3):
        evo_mgr.record_experience(
            bot_id="worker-bot",
            event_type="task_success",
            domain="parser",
            summary=f"Parser execution {i} succeeded",
            success=True,
        )

    # Curator evaluates canaries
    res = curator.run_cycle()
    assert res["status"] == "completed"
    assert prop.id in res["canary_evaluation"]["promoted"]

    updated_prop = [p for p in evo_mgr.get_proposals() if p.id == prop.id][0]
    assert updated_prop.status == STATUS_PROMOTED


def test_curator_canary_evaluation_rolls_back_failing(curator_env):
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]

    active_ver = id_mgr.get_active_version("worker-bot")

    # Create a proposal directly and set to canary
    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class="medium",
        rationale="Experimental async indexing",
        proposed_soul_patch="Async indexing soul",
    )
    evo_mgr.update_status(prop.id, STATUS_CANARY, active_ver.bundle_hash)

    # Add 1 success and 2 failures post-canary
    time.sleep(0.01)
    evo_mgr.record_experience(
        bot_id="worker-bot",
        event_type="task_success",
        domain="indexing",
        summary="Indexing step 1 ok",
        success=True,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot",
        event_type="task_failure",
        domain="indexing",
        summary="Segmentation fault on buffer read",
        success=False,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot",
        event_type="task_failure",
        domain="indexing",
        summary="OutOfMemory exception during index flush",
        success=False,
    )

    # Curator evaluates canaries and rolls back
    res = curator.run_cycle()
    assert res["status"] == "completed"
    assert prop.id in res["canary_evaluation"]["rolled_back"]

    updated_prop = [p for p in evo_mgr.get_proposals() if p.id == prop.id][0]
    assert updated_prop.status == STATUS_ROLLED_BACK
