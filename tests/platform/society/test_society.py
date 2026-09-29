import pytest
from hermes.platform.observability.event_store import EventStore
from hermes.platform.society.manager import (
    SelfEndorsementError,
    SocietyManager,
)
from hermes.platform.society.models import DebatePhase


def test_relationship_establishment_and_projection(tmp_path):
    db_path = tmp_path / "events.db"
    store = EventStore(str(db_path))
    mgr = SocietyManager(store)

    edge = mgr.establish_relationship(
        from_bot="bot_architect",
        to_bot="bot_reviewer",
        relation_type="collaborates_with",
        weight=0.95,
        evidence_refs=["session-2026-09-29"],
    )

    assert edge.from_bot == "bot_architect"
    assert edge.to_bot == "bot_reviewer"

    # Query relations
    rels_arch = mgr.get_relationships("bot_architect")
    assert len(rels_arch) == 1
    assert rels_arch[0].relation_type == "collaborates_with"

    rels_rev = mgr.get_relationships("bot_reviewer")
    assert len(rels_rev) == 1
    assert rels_rev[0].id == edge.id


def test_reputation_anti_self_endorsement(tmp_path):
    db_path = tmp_path / "events.db"
    store = EventStore(str(db_path))
    mgr = SocietyManager(store)

    # Self endorsement must fail closed
    with pytest.raises(SelfEndorsementError):
        mgr.record_reputation(
            subject_bot="bot_rogue",
            domain="security",
            evidence_ref="self_declaration",
            outcome="success",
            delta_hint=0.5,
            actor_bot="bot_rogue",
        )

    # Peer endorsement succeeds
    ev = mgr.record_reputation(
        subject_bot="bot_rogue",
        domain="security",
        evidence_ref="peer_audit_report",
        outcome="success",
        delta_hint=0.3,
        actor_bot="bot_auditor",
    )
    assert ev.subject_bot == "bot_rogue"


def test_reputation_vector_and_specialist_selection(tmp_path):
    db_path = tmp_path / "events.db"
    store = EventStore(str(db_path))
    mgr = SocietyManager(store)

    # bot_sec gets high security reviews
    for i in range(3):
        mgr.record_reputation(
            subject_bot="bot_sec",
            domain="security",
            evidence_ref=f"audit-{i}",
            outcome="success",
            delta_hint=0.2,
            actor_bot="bot_peer",
        )

    # bot_code gets high code quality reviews
    for i in range(3):
        mgr.record_reputation(
            subject_bot="bot_code",
            domain="code_quality",
            evidence_ref=f"pr-{i}",
            outcome="success",
            delta_hint=0.2,
            actor_bot="bot_peer",
        )

    vec_sec = mgr.get_reputation_vector("bot_sec")
    assert "security" in vec_sec.domains
    assert vec_sec.domains["security"].score > 0.8
    assert vec_sec.domains["security"].confidence > 0.5

    candidates = ["bot_sec", "bot_code", "bot_novice"]

    # Select specialist for security
    top_sec = mgr.select_specialists(candidates, "security", count=1)
    assert top_sec[0][0] == "bot_sec"

    # Select specialist for code_quality
    top_cq = mgr.select_specialists(candidates, "code_quality", count=1)
    assert top_cq[0][0] == "bot_code"


def test_role_assignment_and_collaboration(tmp_path):
    db_path = tmp_path / "events.db"
    store = EventStore(str(db_path))
    mgr = SocietyManager(store)

    role = mgr.assign_role(
        session_id="csess-123",
        bot_id="bot_sec",
        role="skeptic",
        rationale="Specialized in edge cases and vulnerabilities",
    )
    assert role.role == "skeptic"
    assert role.bot_id == "bot_sec"

    collab = mgr.record_collaboration(
        participants=["bot_sec", "bot_code"],
        task_ref="refactor_event_store",
        outcome_ref="commit-d689488",
        reviewer_refs=["bot_reviewer"],
        score=0.96,
    )
    assert collab.score == 0.96
    assert "bot_sec" in collab.participants


def test_replay_produces_identical_state(tmp_path):
    db_path = tmp_path / "events.db"
    store1 = EventStore(str(db_path))
    mgr1 = SocietyManager(store1)

    mgr1.establish_relationship("b1", "b2", "trusts", 0.9)
    mgr1.record_reputation("b1", "architecture", "ev-1", "success", 0.3, "b2")

    vec1 = mgr1.get_reputation_vector("b1")

    # Replay on fresh manager instance
    store2 = EventStore(str(db_path))
    mgr2 = SocietyManager(store2)

    vec2 = mgr2.get_reputation_vector("b1")
    assert vec1.overall_score == vec2.overall_score
    assert vec1.domains["architecture"].score == vec2.domains["architecture"].score
    assert len(mgr2.get_relationships("b1")) == 1
