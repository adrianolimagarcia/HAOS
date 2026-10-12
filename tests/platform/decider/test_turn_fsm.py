"""Tests for Turn State Machine (FSM) and Pre-turn Decider."""

import pytest

from hermes.platform.decider.fsm import (
    TurnFSM,
    TurnState,
    InvalidStateTransitionError,
    PreTurnDecision,
    classify_pre_turn,
    decide_and_advance_turn,
)


class TestTurnFSM:
    def test_valid_transitions_lifecycle(self):
        fsm = TurnFSM(TurnState.BOOTING)
        assert fsm.state == TurnState.BOOTING

        fsm.transition_to(TurnState.IDLE, reason="ready")
        assert fsm.state == TurnState.IDLE

        fsm.transition_to(TurnState.COLLECTING, reason="user input")
        assert fsm.state == TurnState.COLLECTING

        fsm.transition_to(TurnState.PLANNING, reason="mission received")
        assert fsm.state == TurnState.PLANNING

        fsm.transition_to(TurnState.EXECUTING, reason="plan approved")
        assert fsm.state == TurnState.EXECUTING

        fsm.transition_to(TurnState.AWAITING_APPROVAL, reason="gate check")
        assert fsm.state == TurnState.AWAITING_APPROVAL

        fsm.transition_to(TurnState.EXECUTING, reason="user approved")
        assert fsm.state == TurnState.EXECUTING

        fsm.transition_to(TurnState.VERIFYING, reason="run test checks")
        assert fsm.state == TurnState.VERIFYING

        fsm.transition_to(TurnState.COMPLETED, reason="tests green")
        assert fsm.state == TurnState.COMPLETED

        fsm.transition_to(TurnState.IDLE, reason="turn done")
        assert fsm.state == TurnState.IDLE

    def test_invalid_transition_fails_strict(self):
        fsm = TurnFSM(TurnState.IDLE)
        # IDLE to COMPLETED directly is invalid
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            fsm.transition_to(TurnState.COMPLETED, reason="jump")
        assert "Invalid state transition from IDLE to COMPLETED" in str(exc_info.value)
        assert fsm.state == TurnState.IDLE

    def test_awaiting_approval_timeout_policy(self):
        # Policy: abort
        fsm_abort = TurnFSM(TurnState.EXECUTING)
        fsm_abort.transition_to(TurnState.AWAITING_APPROVAL, reason="ask human")
        result_state = fsm_abort.handle_approval_timeout(timeout_policy="abort")
        assert result_state == TurnState.FAILED
        assert fsm_abort.state == TurnState.FAILED

        # Policy: proceed_read_only
        fsm_ro = TurnFSM(TurnState.EXECUTING)
        fsm_ro.transition_to(TurnState.AWAITING_APPROVAL, reason="ask human")
        result_state_ro = fsm_ro.handle_approval_timeout(timeout_policy="proceed_read_only")
        assert result_state_ro == TurnState.EXECUTING
        assert fsm_ro.state == TurnState.EXECUTING


class TestPreTurnDeciderHeuristics:
    def test_empty_message_is_noop(self):
        assert classify_pre_turn("") == PreTurnDecision.NOOP
        assert classify_pre_turn("   \n\t  ") == PreTurnDecision.NOOP

    def test_short_simple_message_is_direct_execution(self):
        assert classify_pre_turn("Que horas são?") == PreTurnDecision.DIRECT_EXECUTION
        assert classify_pre_turn("Mostre o arquivo README.md") == PreTurnDecision.DIRECT_EXECUTION
        assert classify_pre_turn("Qual a versão do Python?") == PreTurnDecision.DIRECT_EXECUTION

    def test_mission_and_phases_requires_planning(self):
        msg1 = "MISSÃO: aplicar o plano de adoção OpenMausBot no HAOS"
        assert classify_pre_turn(msg1) == PreTurnDecision.REQUIRES_PLANNING

        msg2 = "Siga as seguintes fases: 1. Setup, 2. Execução, 3. Deploy"
        assert classify_pre_turn(msg2) == PreTurnDecision.REQUIRES_PLANNING

        msg3 = "Execute os passos definidos no roadmap da Sprint 2"
        assert classify_pre_turn(msg3) == PreTurnDecision.REQUIRES_PLANNING

    def test_decide_and_advance_turn_flow(self):
        fsm = TurnFSM(TurnState.IDLE)
        decision = decide_and_advance_turn(fsm, "Olá, liste os arquivos.")
        assert decision == PreTurnDecision.DIRECT_EXECUTION
        assert fsm.state == TurnState.EXECUTING

        # Complete and reset
        fsm.transition_to(TurnState.COMPLETED, reason="done")
        fsm.transition_to(TurnState.IDLE, reason="ready")

        # Complex mission
        mission_decision = decide_and_advance_turn(fsm, "MISSÃO: refatorar módulo em 4 fases")
        assert mission_decision == PreTurnDecision.REQUIRES_PLANNING
        assert fsm.state == TurnState.PLANNING
