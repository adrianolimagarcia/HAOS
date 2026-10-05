"""Unit and integration tests for the autonomous evolution loop, ring snapshots, and canary guard."""

import time
import pytest

from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    STATUS_DRAFT,
    STATUS_CANARY,
    STATUS_PROMOTED,
)
from hermes.platform.civilization.delegation import (
    RISK_LOW,
    RISK_HIGH,
    create_bot,
)
from hermes.platform.civilization.auto_evolution import (
    AutoEvolutionController,
    DEFAULT_EXPERIENCE_THRESHOLD,
    EVENT_AUTO_EVOLUTION_TRIGGERED,
    EVENT_CANARY_ROLLBACK,
    EVENT_APPROVAL_REQUIRED,
)


@pytest.fixture
def civ_store(tmp_path):
    db_file = tmp_path / "events.db"
    return EventStore(str(db_file))


def test_snapshot_ring_buffer_preserves_three_snapshots(civ_store):
    """Ring buffer returns the last 3 immutable snapshots in reverse chronological order."""
    bot_id = "test-coder"
    create_bot(bot_id, "coding", soul="v1 soul", event_store=civ_store)
    id_mgr = IdentityManager(civ_store)

    active_v1 = id_mgr.get_active_version(bot_id)
    assert active_v1 is not None

    ctrl = AutoEvolutionController(civ_store)
    ring = ctrl.get_snapshot_ring_buffer(bot_id, limit=3)
    assert len(ring) == 1
    assert ring[0]["version"] == active_v1.version

    # Add v2
    v1_bundle = active_v1.bundle
    v2_bundle = id_mgr.create_version(
        bot_id=bot_id,
        bundle=v1_bundle,
        parent_id=active_v1.id,
        activate=True,
    )
    # Add v3
    v3_bundle = id_mgr.create_version(
        bot_id=bot_id,
        bundle=v1_bundle,
        parent_id=v2_bundle.id,
        activate=True,
    )
    # Add v4
    v4_bundle = id_mgr.create_version(
        bot_id=bot_id,
        bundle=v1_bundle,
        parent_id=v3_bundle.id,
        activate=True,
    )

    ring = ctrl.get_snapshot_ring_buffer(bot_id, limit=3)
    assert len(ring) == 3
    # Ordered descending: v4, v3, v2
    assert ring[0]["version"] == 4
    assert ring[1]["version"] == 3
    assert ring[2]["version"] == 2


def test_auto_evolution_triggers_at_threshold(civ_store):
    """Accumulating 20 task experiences triggers an autonomous evolution proposal."""
    bot_id = "test-analyst"
    create_bot(bot_id, "analysis", soul="analyst soul", event_store=civ_store)

    ctrl = AutoEvolutionController(civ_store, experience_threshold=20)
    evo_mgr = BotEvolutionManager(civ_store)

    # 19 experiences -> under threshold
    for i in range(19):
        evo_mgr.record_experience(
            bot_id=bot_id,
            event_type="mission_completed",
            domain="analysis",
            summary=f"Analysis mission {i+1}",
            success=True,
        )

    proposal = ctrl.check_and_trigger_auto_evolution(bot_id)
    assert proposal is None

    # 20th experience -> threshold reached
    evo_mgr.record_experience(
        bot_id=bot_id,
        event_type="mission_completed",
        domain="analysis",
        summary="Analysis mission 20",
        success=True,
    )

    proposal = ctrl.check_and_trigger_auto_evolution(bot_id)
    assert proposal is not None
    assert proposal.bot_id == bot_id
    assert proposal.risk_class == RISK_LOW

    # Second check should return None because proposal is now pending
    assert ctrl.check_and_trigger_auto_evolution(bot_id) is None


def test_deliberate_and_promote_auto_council(civ_store):
    """Autonomous Council approves and promotes low-risk evolution with quorum."""
    bot_id = "test-devops"
    create_bot(bot_id, "devops", soul="devops soul", event_store=civ_store)

    ctrl = AutoEvolutionController(civ_store, experience_threshold=20)
    evo_mgr = BotEvolutionManager(civ_store)

    for i in range(20):
        evo_mgr.record_experience(
            bot_id=bot_id,
            event_type="mission_completed",
            domain="devops",
            summary=f"Devops mission {i}",
            success=True,
        )

    proposal = ctrl.check_and_trigger_auto_evolution(bot_id)
    assert proposal is not None

    promoted = ctrl.deliberate_and_promote_auto(proposal.id)
    assert promoted["status"] == STATUS_CANARY
    assert promoted["canary_tasks_remaining"] == 5

    # Check active version was bumped
    id_mgr = IdentityManager(civ_store)
    active = id_mgr.get_active_version(bot_id)
    assert active.version == 2
    assert active.bundle.metadata["promoted_from_proposal"] == proposal.id


def test_deliberate_and_promote_blocks_high_risk(civ_store):
    """High or critical risk proposals are not auto-promoted and require operator approval."""
    bot_id = "test-security"
    create_bot(bot_id, "security", soul="security soul", event_store=civ_store)

    ctrl = AutoEvolutionController(civ_store)
    evo_mgr = BotEvolutionManager(civ_store)
    id_mgr = IdentityManager(civ_store)
    active = id_mgr.get_active_version(bot_id)

    # Manually create high-risk proposal
    prop = evo_mgr.create_proposal(
        bot_id=bot_id,
        base_version_hash=active.bundle_hash,
        risk_class=RISK_HIGH,
        rationale="Needs elevated privilege",
    )

    res = ctrl.deliberate_and_promote_auto(prop.id)
    assert res["status"] == "requires_approval"
    assert res["risk_class"] == RISK_HIGH

    # Active version should remain unchanged
    assert id_mgr.get_active_version(bot_id).version == 1


def test_canary_guard_rollback_on_regression(civ_store):
    """Canary guard detects >= 40% failure rate in window and triggers auto-rollback."""
    bot_id = "test-worker"
    create_bot(bot_id, "general", soul="stable v1 soul", event_store=civ_store)

    ctrl = AutoEvolutionController(civ_store, experience_threshold=20, canary_window=5, max_failure_rate=0.4)
    evo_mgr = BotEvolutionManager(civ_store)
    id_mgr = IdentityManager(civ_store)

    # Trigger evolution to v2
    for i in range(20):
        evo_mgr.record_experience(
            bot_id=bot_id,
            event_type="mission_completed",
            domain="general",
            summary=f"Worker mission {i}",
            success=True,
        )

    proposal = ctrl.check_and_trigger_auto_evolution(bot_id)
    promoted = ctrl.deliberate_and_promote_auto(proposal.id)
    assert promoted["status"] == STATUS_CANARY

    active_v2 = id_mgr.get_active_version(bot_id)
    assert active_v2.version == 2

    # Now under canary, execute 5 tasks with 2 failures (2/5 = 40% >= 0.40)
    evo_mgr.record_experience(bot_id=bot_id, event_type="mission_completed", domain="general", summary="task 1", success=True)
    evo_mgr.record_experience(bot_id=bot_id, event_type="mission_failed", domain="general", summary="task 2 error", success=False)
    evo_mgr.record_experience(bot_id=bot_id, event_type="mission_completed", domain="general", summary="task 3", success=True)
    evo_mgr.record_experience(bot_id=bot_id, event_type="mission_failed", domain="general", summary="task 4 error", success=False)
    evo_mgr.record_experience(bot_id=bot_id, event_type="mission_completed", domain="general", summary="task 5", success=True)

    canary_result = ctrl.evaluate_canary_guard(bot_id)
    assert canary_result["status"] == "rolled_back"
    assert canary_result["failure_rate"] == 0.4

    # Active version should now be compensatory v3 with content of v1
    active_now = id_mgr.get_active_version(bot_id)
    assert active_now.version == 3
    v1_version = id_mgr.list_versions(bot_id)[0]
    assert active_now.bundle.metadata["rollback_from"] == v1_version.id


def test_canary_guard_healthy_no_rollback(civ_store):
    """Canary guard keeps active version if failure rate is below threshold."""
    bot_id = "test-rocksolid"
    create_bot(bot_id, "general", soul="rocksolid soul", event_store=civ_store)

    ctrl = AutoEvolutionController(civ_store, experience_threshold=20, canary_window=5, max_failure_rate=0.4)
    evo_mgr = BotEvolutionManager(civ_store)
    id_mgr = IdentityManager(civ_store)

    for i in range(20):
        evo_mgr.record_experience(
            bot_id=bot_id,
            event_type="mission_completed",
            domain="general",
            summary=f"Mission {i}",
            success=True,
        )

    proposal = ctrl.check_and_trigger_auto_evolution(bot_id)
    ctrl.deliberate_and_promote_auto(proposal.id)

    # All 5 canary tasks succeed
    for i in range(5):
        evo_mgr.record_experience(bot_id=bot_id, event_type="mission_completed", domain="general", summary=f"Canary {i}", success=True)

    canary_result = ctrl.evaluate_canary_guard(bot_id)
    assert canary_result["status"] == "healthy"
    assert canary_result["failure_rate"] == 0.0

    # No rollback was triggered
    assert id_mgr.get_active_version(bot_id).version == 2
