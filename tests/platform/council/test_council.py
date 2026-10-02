import time

import pytest

from hermes.platform.council.outbox import CouncilOutbox
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.council.manager import CouncilManager, _LEGAL_TRANSITIONS
from hermes.platform.observability.event_store import EventStore


def _mgr(tmp_path):
    """EventStore file-backed em tmpdir — força persistência real via replay."""
    return CouncilManager(EventStore(str(tmp_path / "events.db")))


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


# ---------------------------------------------------------------------------
# C3: quórum — record_decision exige maioria dos membros com posição
# ---------------------------------------------------------------------------

def test_record_decision_requires_quorum(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.register(CouncilSpec(
        id="q3-council", purpose="Quorum", members=["a", "b", "c"],
    ))
    sess = mgr.start_session("q3-council", "Decide")
    mgr.submit_position(sess.session_id, "a", "yes")

    # 1 posição em 3 membros = abaixo do quórum (ceil(3/2)=2)
    with pytest.raises(ValueError, match="quorum"):
        mgr.record_decision(sess.session_id, "hasty", "approved")

    # sessão NÃO deve ter sido completada pela tentativa rejeitada
    assert mgr.get_session(sess.session_id).phase != "completed"
    assert mgr.list_decisions("q3-council") == []

    # atingido o quórum, decide normalmente
    mgr.submit_position(sess.session_id, "b", "yes")
    dec = mgr.record_decision(sess.session_id, "with quorum", "approved")
    assert dec.decision == "approved"
    assert mgr.get_session(sess.session_id).phase == "completed"


def test_record_decision_quorum_two_member_council(tmp_path):
    # ceil(2/2)=1: council de 2 membros decide com 1 posição (fórmula do spec)
    mgr = _mgr(tmp_path)
    mgr.register(CouncilSpec(id="q2-council", purpose="P", members=["x", "y"]))
    sess = mgr.start_session("q2-council", "Obj")
    mgr.submit_position(sess.session_id, "x", "ok")
    dec = mgr.record_decision(sess.session_id, "s", "approved")
    assert mgr.get_decision(dec.id) is not None


# ---------------------------------------------------------------------------
# A4: pause/abort/fail validam existência e transição legal do FSM
# ---------------------------------------------------------------------------

def test_pause_abort_fail_reject_illegal_transitions(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.register(CouncilSpec(id="fsm-council", purpose="P", members=["m1", "m2"]))
    sess = mgr.start_session("fsm-council", "Obj")
    mgr.submit_position(sess.session_id, "m1", "p1")
    mgr.submit_position(sess.session_id, "m2", "p2")
    mgr.record_decision(sess.session_id, "done", "approved")
    assert mgr.get_session(sess.session_id).phase == "completed"

    # sessão terminal: reescrever terminalidade é ilegal
    with pytest.raises(ValueError, match="Illegal transition"):
        mgr.abort_session(sess.session_id, "too late")
    with pytest.raises(ValueError, match="Illegal transition"):
        mgr.fail_session(sess.session_id, "too late")
    with pytest.raises(ValueError, match="Illegal transition"):
        mgr.pause_session(sess.session_id, "too late")

    # fase terminal preservada no replay
    assert mgr.get_session(sess.session_id).phase == "completed"


def test_pause_abort_fail_unknown_session(tmp_path):
    mgr = _mgr(tmp_path)
    with pytest.raises(KeyError):
        mgr.abort_session("csess-ghost-000", "nope")
    with pytest.raises(KeyError):
        mgr.pause_session("csess-ghost-000", "nope")
    with pytest.raises(KeyError):
        mgr.fail_session("csess-ghost-000", "nope")


def test_pause_then_abort_still_legal(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.register(CouncilSpec(id="ok-council", purpose="P", members=["m1", "m2"]))
    sess = mgr.start_session("ok-council", "Obj")
    paused = mgr.pause_session(sess.session_id, "review")
    assert paused.phase == "paused"
    aborted = mgr.abort_session(sess.session_id, "cancelled")
    assert aborted.phase == "aborted"
    # abortar de novo: aborted é terminal
    with pytest.raises(ValueError, match="Illegal transition"):
        mgr.abort_session(sess.session_id, "again")


# ---------------------------------------------------------------------------
# C4: lease do outbox é persistido no EventStore (CAS lógico por evento)
# ---------------------------------------------------------------------------

def _outbox(tmp_path):
    return CouncilOutbox(EventStore(str(tmp_path / "outbox.db")))


def test_claim_lease_is_persisted_and_exclusive(tmp_path):
    ob = _outbox(tmp_path)
    intent = ob.schedule_intent("c", "s1", "do_thing", {})

    assert ob.claim_lease(intent.intent_id, "worker-A", lease_seconds=60) is True
    # segundo worker NÃO pode claimar com lease ativo
    assert ob.claim_lease(intent.intent_id, "worker-B", lease_seconds=60) is False

    # persistência: outra instância sobre o MESMO store vê o lease
    ob2 = CouncilOutbox(ob.event_store)
    assert ob2.claim_lease(intent.intent_id, "worker-B", lease_seconds=60) is False

    # o próprio worker pode renovar
    assert ob2.claim_lease(intent.intent_id, "worker-A", lease_seconds=60) is True

    # evento de lease existe no log
    names = [e.name for e in ob.event_store.get_all()]
    assert "civ.council.action-lease-claimed" in names


def test_claim_lease_expires_and_reclaimable(tmp_path):
    ob = _outbox(tmp_path)
    intent = ob.schedule_intent("c", "s1", "do_thing", {})
    assert ob.claim_lease(intent.intent_id, "worker-A", lease_seconds=0.05) is True
    time.sleep(0.1)
    # lease expirado: outro worker assume
    assert ob.claim_lease(intent.intent_id, "worker-B", lease_seconds=60) is True
    assert ob.claim_lease(intent.intent_id, "worker-C", lease_seconds=60) is False


def test_dispatch_pending_no_double_execution_across_workers(tmp_path):
    ob = _outbox(tmp_path)
    ob.schedule_intent("c", "s1", "pay", {"x": 1})
    calls = []

    def handler(payload):
        calls.append(payload)
        return {"ok": True}

    res_a = ob.dispatch_pending("worker-A", {"pay": handler})
    res_b = ob.dispatch_pending("worker-B", {"pay": handler})
    assert len(res_a) == 1 and res_a[0]["status"] == "completed"
    assert res_b == []  # B não re-executa: lease/estado já resolvidos no log
    assert len(calls) == 1

    # estado final persistido: intent completed
    intents = ob._get_all_intents()
    assert list(intents.values())[0].status == "completed"


def test_claim_lease_unknown_intent(tmp_path):
    ob = _outbox(tmp_path)
    assert ob.claim_lease("act-nope", "w") is False


# ---------------------------------------------------------------------------
# M6: FSM sem fases mortas — decision_recorded/action_pending removidos
# ---------------------------------------------------------------------------

def test_fsm_has_no_unreachable_phases():
    all_targets = {p for targets in _LEGAL_TRANSITIONS.values() for p in targets}
    all_sources = set(_LEGAL_TRANSITIONS.keys())
    # todo estado citado como destino deve ser um estado do FSM
    assert all_targets <= all_sources
    # fases mortas declaradas no bug report não existem mais
    assert "decision_recorded" not in _LEGAL_TRANSITIONS
    assert "action_pending" not in _LEGAL_TRANSITIONS
    for p in ("decision_recorded", "action_pending"):
        assert all(p not in targets for targets in _LEGAL_TRANSITIONS.values())


def test_record_decision_completes_session_from_synthesis_phase(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.register(CouncilSpec(id="ph-council", purpose="P", members=["m1", "m2"]))
    sess = mgr.start_session("ph-council", "Obj")
    mgr.submit_position(sess.session_id, "m1", "p")
    mgr.advance_phase(sess.session_id, "synthesis")
    mgr.record_decision(sess.session_id, "s", "approved")
    done = mgr.get_session(sess.session_id)
    assert done.phase == "completed"
    # replay por uma instância nova preserva o mesmo resultado
    mgr2 = CouncilManager(mgr.event_store)
    assert mgr2.get_session(sess.session_id).phase == "completed"
