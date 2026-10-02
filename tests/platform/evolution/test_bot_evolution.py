import pytest
from hermes.platform.observability.event_store import EventStore
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    PolicyViolationError,
    StaleBaseVersionError,
    RISK_LOW,
    RISK_IDENTITY_CRITICAL,
    STATUS_DRAFT,
    STATUS_REVIEW,
    STATUS_APPROVED,
    STATUS_CANARY,
    STATUS_PROMOTED,
)


def test_record_experience(tmp_path):
    store = EventStore(tmp_path / "test_events.db")
    evo = BotEvolutionManager(store)

    exp = evo.record_experience(
        bot_id="bot-analyst",
        event_type="reflection",
        domain="finance",
        summary="Detected cyclic arbitrage opportunity",
        success=True,
        payload={"spread_bps": 42},
    )

    assert exp.id.startswith("exp-")
    assert exp.bot_id == "bot-analyst"
    assert exp.success is True


def test_proposal_lifecycle_and_stale_detection(tmp_path):
    store = EventStore(tmp_path / "test_events.db")
    evo = BotEvolutionManager(store)

    prop = evo.create_proposal(
        bot_id="bot-coder",
        base_version_hash="hash-123",
        risk_class=RISK_LOW,
        rationale="Update code review style",
        proposed_soul_patch="## Coding Rules\nPrefer Rust for high-throughput loops.",
    )

    assert prop.status == STATUS_DRAFT
    assert prop.base_version_hash == "hash-123"

    # M2: FSM requires draft -> review -> approved -> canary -> promoted
    evo.update_status(prop.id, STATUS_REVIEW, current_bot_version_hash="hash-123")

    # Stale version detection: current active version is hash-999
    with pytest.raises(StaleBaseVersionError):
        evo.update_status(prop.id, STATUS_APPROVED, current_bot_version_hash="hash-999")

    # A3: a soul patch is never auto-approved — needs human approver + explicit flag
    with pytest.raises(PolicyViolationError):
        evo.update_status(prop.id, STATUS_APPROVED, current_bot_version_hash="hash-123")

    # Correct version matches, with explicit human soul approval
    approved = evo.update_status(
        prop.id, STATUS_APPROVED, current_bot_version_hash="hash-123",
        approver="humano-1", explicit_soul_change=True,
    )
    assert approved.status == STATUS_APPROVED

    canary = evo.update_status(prop.id, STATUS_CANARY, current_bot_version_hash="hash-123")
    assert canary.status == STATUS_CANARY

    promoted = evo.update_status(prop.id, STATUS_PROMOTED, current_bot_version_hash="hash-123")
    assert promoted.status == STATUS_PROMOTED

    proposals = evo.get_proposals(bot_id="bot-coder")
    assert len(proposals) == 1
    assert proposals[0].status == STATUS_PROMOTED
