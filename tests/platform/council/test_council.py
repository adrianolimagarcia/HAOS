import pytest
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.council.manager import CouncilManager
from hermes.platform.observability.event_store import EventStore


def test_council_registration_and_get():
    store = EventStore()
    mgr = CouncilManager(store)

    spec = CouncilSpec(
        id="arch-council",
        purpose="Review and decide system architecture",
        members=["architect-bot", "security-bot", "engineer-bot"],
        roles={"architect-bot": "lead", "security-bot": "reviewer"},
    )
    registered = mgr.register(spec)
    assert registered.id == "arch-council"

    # Duplication rejection
    with pytest.raises(ValueError, match="already exists"):
        mgr.register(spec)

    fetched = mgr.get("arch-council")
    assert fetched is not None
    assert fetched.purpose == spec.purpose
    assert len(fetched.members) == 3


def test_council_session_validation():
    store = EventStore()
    mgr = CouncilManager(store)

    spec = CouncilSpec(
        id="solo-council",
        purpose="Solo council attempt",
        members=["bot-1"],
    )
    mgr.register(spec)

    # Requires at least 2 members for collective deliberation
    with pytest.raises(ValueError, match="at least 2 member bots"):
        mgr.start_session("solo-council", "Review code")


def test_council_session_flow_with_dissent_and_decision():
    store = EventStore()
    mgr = CouncilManager(store)

    spec = CouncilSpec(
        id="core-council",
        purpose="Decide storage technology",
        members=["architect", "security", "engineer"],
    )
    mgr.register(spec)

    session = mgr.start_session("core-council", "Choose primary event store")
    assert session.phase == "independent_analysis"

    # Member submits position
    s1 = mgr.submit_position(session.session_id, "architect", "Propose SQLite WAL with full durability")
    assert "architect" in s1.positions

    # Non-member rejection
    with pytest.raises(PermissionError, match="not an enrolled member"):
        mgr.submit_position(session.session_id, "unauthorized-bot", "Malicious input")

    # Member submits position with dissent
    s2 = mgr.submit_position(
        session.session_id,
        "security",
        "Prefer isolated files without centralized lock",
        dissent="Contention risk under high concurrent workers",
    )
    assert "security" in s2.positions
    assert "security" in s2.dissent

    # Record synthesized decision
    decision = mgr.record_decision(
        session_id=session.session_id,
        synthesis="Adopt SQLite WAL with busy_timeout=30s and periodic passive checkpointing to mitigate contention.",
        decision="approved",
        confidence=0.95,
        action_refs=["migration:001_event_store"],
    )

    assert decision.decision == "approved"
    assert "architect" in decision.participants
    assert "security" in decision.participants
    assert "security" in decision.dissent
    assert decision.confidence == 0.95

    # Session is marked completed
    sess_done = mgr.get_session(session.session_id)
    assert sess_done.phase == "completed"
    assert sess_done.decision_id == decision.id

    # List decisions
    decisions = mgr.list_decisions("core-council")
    assert len(decisions) == 1
    assert decisions[0].id == decision.id


def test_council_projection_rebuild():
    store = EventStore()
    mgr1 = CouncilManager(store)

    spec = CouncilSpec(id="c1", purpose="Purpose 1", members=["b1", "b2"])
    mgr1.register(spec)
    sess = mgr1.start_session("c1", "Objective 1")
    mgr1.submit_position(sess.session_id, "b1", "Position B1")
    mgr1.submit_position(sess.session_id, "b2", "Position B2")
    dec = mgr1.record_decision(sess.session_id, "Synthesis 1", "approved")

    # Re-instantiate CouncilManager with same EventStore
    mgr2 = CouncilManager(store)
    recovered_council = mgr2.get("c1")
    assert recovered_council is not None
    assert recovered_council.purpose == "Purpose 1"

    recovered_session = mgr2.get_session(sess.session_id)
    assert recovered_session is not None
    assert recovered_session.phase == "completed"
    assert recovered_session.decision_id == dec.id

    recovered_decision = mgr2.get_decision(dec.id)
    assert recovered_decision is not None
    assert recovered_decision.synthesis == "Synthesis 1"
