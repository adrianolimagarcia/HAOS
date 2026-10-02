"""Hardening tests for EvolutionCurator: soul-patch gating, fail-closed canary,
atomic lease acquisition.

Findings covered (bot_curator.py):
- A3 layer 2: proposals with soul patches are never auto-advanced; the curator
  only creates them in draft and any approval requires explicit_soul_change.
- M3: evaluate_canaries promotes only with canary-version-stamped evidence
  (identity_version == proposal target hash); no stamp -> no promotion.
- B5: lease acquisition is atomic via file lock (fcntl.flock).
"""

import threading
import time

import pytest

from hermes.platform.bots.identity import BotIdentityBundle
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.evolution.bot_curator import EvolutionCurator
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    RISK_LOW,
    RISK_MEDIUM,
    STATUS_APPROVED,
    STATUS_CANARY,
    STATUS_DRAFT,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    STATUS_REVIEW,
    STATUS_ROLLED_BACK,
)
from hermes.platform.observability.event_store import EventStore


@pytest.fixture
def curator_env(tmp_path):
    store = EventStore(tmp_path / "events.db")
    id_mgr = IdentityManager(store)
    evo_mgr = BotEvolutionManager(store)

    bundle = BotIdentityBundle(
        bot_id="worker-bot",
        soul="Reliable worker bot.",
        identity="Worker",
        values="Precision and safety.",
    )
    id_mgr.create_version("worker-bot", bundle, activate=True)

    state_dir = tmp_path / "state"
    curator = EvolutionCurator(
        event_store=store,
        id_mgr=id_mgr,
        evo_mgr=evo_mgr,
        state_dir=state_dir,
        profile_id="test-profile",
        curator_id="curator-alpha",
    )
    return {
        "store": store,
        "id_mgr": id_mgr,
        "evo_mgr": evo_mgr,
        "curator": curator,
        "state_dir": state_dir,
    }


def _make_curator(curator_env, curator_id):
    return EvolutionCurator(
        event_store=curator_env["store"],
        id_mgr=curator_env["id_mgr"],
        evo_mgr=curator_env["evo_mgr"],
        state_dir=curator_env["state_dir"],
        profile_id="test-profile",
        curator_id=curator_id,
    )


# ---------------------------------------------------------------------------
# M3 — canary fail-closed
# ---------------------------------------------------------------------------

def test_canary_without_version_stamped_evidence_does_not_promote(curator_env):
    """Evidence recorded against the OLD identity must not promote a canary."""
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]

    active_ver = id_mgr.get_active_version("worker-bot")
    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class=RISK_LOW,
        rationale="Optimize parser speed",
        proposed_values_patch="Fast parser values",
    )
    evo_mgr.update_status(prop.id, STATUS_REVIEW, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_APPROVED, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_CANARY, active_ver.bundle_hash)

    time.sleep(0.01)
    # 3 successful experiences WITHOUT identity_version stamp (old-version runs).
    for i in range(3):
        evo_mgr.record_experience(
            bot_id="worker-bot",
            event_type="task_success",
            domain="parser",
            summary=f"Parser execution {i} succeeded",
            success=True,
        )

    res = curator.evaluate_canaries()
    assert res["promoted"] == []
    assert res["rolled_back"] == []
    updated = [p for p in evo_mgr.get_proposals() if p.id == prop.id][0]
    assert updated.status == STATUS_CANARY  # stays in canary — fail-closed


def test_canary_with_stamped_evidence_promotes(curator_env):
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]

    active_ver = id_mgr.get_active_version("worker-bot")
    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class=RISK_LOW,
        rationale="Optimize parser speed",
        proposed_values_patch="Fast parser values",
    )
    evo_mgr.update_status(prop.id, STATUS_REVIEW, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_APPROVED, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_CANARY, active_ver.bundle_hash)

    time.sleep(0.01)
    # 3 successful experiences stamped with the canary identity version.
    for i in range(3):
        evo_mgr.record_experience(
            bot_id="worker-bot",
            event_type="task_success",
            domain="parser",
            summary=f"Parser execution {i} succeeded",
            success=True,
            identity_version=active_ver.bundle_hash,
        )

    res = curator.evaluate_canaries()
    assert prop.id in res["promoted"]
    updated = [p for p in evo_mgr.get_proposals() if p.id == prop.id][0]
    assert updated.status == STATUS_PROMOTED


def test_canary_rollback_still_works_with_stamped_failures(curator_env):
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]

    active_ver = id_mgr.get_active_version("worker-bot")
    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class=RISK_MEDIUM,
        rationale="Experimental async indexing",
        proposed_values_patch="Async indexing values",
    )
    evo_mgr.update_status(prop.id, STATUS_REVIEW, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_APPROVED, active_ver.bundle_hash)
    evo_mgr.update_status(prop.id, STATUS_CANARY, active_ver.bundle_hash)

    time.sleep(0.01)
    evo_mgr.record_experience(
        bot_id="worker-bot", event_type="task_success", domain="indexing",
        summary="Indexing step 1 ok", success=True,
        identity_version=active_ver.bundle_hash,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot", event_type="task_failure", domain="indexing",
        summary="Segmentation fault", success=False,
        identity_version=active_ver.bundle_hash,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot", event_type="task_failure", domain="indexing",
        summary="OutOfMemory", success=False,
        identity_version=active_ver.bundle_hash,
    )

    res = curator.evaluate_canaries()
    assert prop.id in res["rolled_back"]
    updated = [p for p in evo_mgr.get_proposals() if p.id == prop.id][0]
    assert updated.status == STATUS_ROLLED_BACK


# ---------------------------------------------------------------------------
# A3 layer 2 — soul patches never auto-approved by the curator
# ---------------------------------------------------------------------------

def test_curator_soul_proposals_stay_draft_after_cycle(curator_env):
    """Recurring failures with soul patches must NOT be auto-advanced."""
    curator = curator_env["curator"]
    evo_mgr = curator_env["evo_mgr"]

    evo_mgr.record_experience(
        bot_id="worker-bot", event_type="task_failure", domain="db",
        summary="Lock timeout", success=False,
    )
    evo_mgr.record_experience(
        bot_id="worker-bot", event_type="task_failure", domain="db",
        summary="Deadlock", success=False,
    )

    res = curator.run_cycle(dry_run=False)
    assert res["proposals_generated"] == 1
    prop_id = res["created_proposal_ids"][0]
    prop = [p for p in evo_mgr.get_proposals() if p.id == prop_id][0]
    # Draft only — the curator never advances/approves soul patches itself.
    assert prop.status == STATUS_DRAFT
    assert prop.proposed_soul_patch is not None


def test_soul_proposal_blocked_from_approval_without_flag(curator_env):
    """Even via the curator path, approving a soul patch needs the explicit flag."""
    evo_mgr = curator_env["evo_mgr"]
    id_mgr = curator_env["id_mgr"]
    active_ver = id_mgr.get_active_version("worker-bot")

    prop = evo_mgr.create_proposal(
        bot_id="worker-bot",
        base_version_hash=active_ver.bundle_hash,
        risk_class=RISK_MEDIUM,
        rationale="r",
        proposed_soul_patch="## soul injection",
    )
    evo_mgr.update_status(prop.id, STATUS_REVIEW, active_ver.bundle_hash)
    from hermes.platform.evolution.bot_evolution import (
        PolicyViolationError,
        STATUS_APPROVED,
    )
    with pytest.raises(PolicyViolationError):
        evo_mgr.update_status(prop.id, STATUS_APPROVED, active_ver.bundle_hash)


# ---------------------------------------------------------------------------
# B5 — atomic lease via file lock
# ---------------------------------------------------------------------------

def test_lease_lock_file_used(curator_env):
    curator = curator_env["curator"]
    assert curator.acquire_lease(ttl_seconds=30.0) is True
    lock_path = curator.state_dir / "curator_state_test-profile.lock"
    assert lock_path.exists()
    curator.release_lease()


def test_concurrent_lease_only_one_winner(curator_env):
    """Two curators racing on the same profile: exactly one acquires."""
    results = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def race(curator_id):
        c = _make_curator(curator_env, curator_id)
        barrier.wait()
        got = c.acquire_lease(ttl_seconds=30.0)
        with lock:
            results.append((curator_id, got))

    t1 = threading.Thread(target=race, args=("curator-1",))
    t2 = threading.Thread(target=race, args=("curator-2",))
    t1.start(); t2.start(); t1.join(); t2.join()

    winners = [r for r in results if r[1]]
    assert len(winners) == 1, f"expected exactly one lease winner, got {results}"
