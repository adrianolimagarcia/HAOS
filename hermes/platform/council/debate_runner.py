"""Autonomous multi-turn Council deliberation runner with durable FSM, budgets, and policy gates."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from hermes.platform.civilization.manager import (
    CivilizationManager,
    POLICY_RESULT_DENY,
    POLICY_RESULT_REQUIRE_APPROVAL,
)
from hermes.platform.council.budget import BudgetExhaustedError, CouncilBudget
from hermes.platform.council.inbox import CommandInbox
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.member_runner import MemberRunner
from hermes.platform.council.outbox import CouncilOutbox
from hermes.platform.council.spec import CouncilSession, DecisionRecord
from hermes.platform.council.synthesis import SynthesisBot, SynthesisResult
from hermes.platform.observability.event_store import EventStore

logger = logging.getLogger("hermes.platform.council.debate_runner")


class CouncilDebateRunner:
    """Orchestrates end-to-end deliberation sessions through an event-sourced FSM."""

    def __init__(
        self,
        council_manager: CouncilManager,
        member_runner: MemberRunner,
        synthesis_bot: Optional[SynthesisBot] = None,
        civ_manager: Optional[CivilizationManager] = None,
        inbox: Optional[CommandInbox] = None,
        outbox: Optional[CouncilOutbox] = None,
    ):
        self.council_manager = council_manager
        self.member_runner = member_runner
        self.synthesis_bot = synthesis_bot or SynthesisBot()
        self.civ_manager = civ_manager
        self.event_store = council_manager.event_store
        self.inbox = inbox or CommandInbox(self.event_store)
        self.outbox = outbox or CouncilOutbox(self.event_store)

    def deliberate(
        self,
        council_id: str,
        objective: str,
        command_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        approved: bool = False,
        dry_run: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute or resume an autonomous multi-turn deliberation toward a binding DecisionRecord."""
        opts = options or {}
        idempotency_key = command_id or correlation_id

        # 1. Inbox deduplication check
        if idempotency_key and self.inbox.is_processed(idempotency_key):
            cached = self.inbox.get_result(idempotency_key)
            if cached:
                logger.info(f"Deliberate: command {idempotency_key} already processed; returning cached result")
                return cached

        # 2. Initialize and configure hard budget
        council = self.council_manager.get(council_id)
        if not council:
            raise KeyError(f"Council '{council_id}' is not registered in CouncilManager")

        budget_cfg = dict(council.budget or {})
        budget_cfg.update({k: v for k, v in opts.items() if k in {
            "max_rounds", "max_turns", "max_tokens", "max_cost_usd", "timeout_seconds", "max_members"
        }})
        budget = CouncilBudget.from_dict(budget_cfg)

        # 3. Create or resume session
        session: Optional[CouncilSession] = None
        if idempotency_key:
            for s in self.council_manager.list_sessions(council_id):
                if s.command_id == idempotency_key and s.phase not in ("completed", "failed", "aborted"):
                    session = s
                    break

        if not session:
            session = self.council_manager.start_session(
                council_id=council_id,
                objective=objective,
                correlation_id=correlation_id,
                command_id=idempotency_key,
                budget=budget.to_dict(),
            )
            if idempotency_key:
                self.inbox.record_received(idempotency_key, "council.deliberate", {"council_id": council_id, "objective": objective})

        session_id = session.session_id

        try:
            # Phase 1: selecting_members & independent analysis
            if session.phase in ("created", "selecting_members", "independent_analysis"):
                members = list(session.members)
                if len(members) < 2:
                    raise ValueError(f"Council '{council_id}' requires at least 2 members for collective deliberation")

                for bot_id in members:
                    if bot_id in session.positions:
                        continue  # Already submitted this round

                    # Pre-flight budget reservation
                    budget.reserve(estimated_tokens=500, estimated_cost=0.005)

                    # Execute member analysis in frozen Leaf
                    result = self.member_runner.execute_member_analysis(
                        bot_id=bot_id,
                        council_id=council_id,
                        session_id=session_id,
                        objective=objective,
                        round_num=0,
                    )

                    # Record actual usage
                    budget.charge(tokens=result.tokens_used, cost_usd=result.cost_usd, turns=1)

                    # Submit position to canonical event store
                    session = self.council_manager.submit_position(
                        session_id=session_id,
                        bot_id=bot_id,
                        position=result.position,
                        dissent=result.dissent,
                        leaf_id=result.leaf_id,
                        leaf_snapshot=result.leaf_snapshot,
                        cost_usd=result.cost_usd,
                        tokens=result.tokens_used,
                        correlation_id=correlation_id,
                    )

                    # Also record in session debate turns transcript
                    self.council_manager.record_debate_turn(
                        session_id=session_id,
                        turn_data={
                            "round": 0,
                            "bot_id": bot_id,
                            "position": result.position,
                            "dissent": result.dissent,
                            "leaf_id": result.leaf_id,
                        },
                        cost_usd=result.cost_usd,
                        tokens=result.tokens_used,
                        correlation_id=correlation_id,
                    )

            # Phase 2: Multi-turn debate rounds if enabled and dissent or conflict exists
            max_rounds = opts.get("max_rounds", budget.max_rounds)
            while budget.rounds_used < max_rounds - 1 and len(session.dissent) > 0:
                try:
                    budget.advance_round()
                except BudgetExhaustedError:
                    logger.warning(f"Session {session_id}: Max debate rounds reached ({budget.rounds_used})")
                    break

                self.council_manager.advance_phase(session_id, "debate_round")
                current_round = budget.rounds_used

                # Build sanitized debate context containing previous positions
                debate_context = {
                    "other_positions": dict(session.positions),
                    "critiques": [f"{bot}: {diss}" for bot, diss in session.dissent.items()],
                    "round": current_round,
                }

                # Each member critiques and optionally updates position
                for bot_id in session.members:
                    budget.reserve(estimated_tokens=400, estimated_cost=0.004)
                    turn_res = self.member_runner.execute_member_analysis(
                        bot_id=bot_id,
                        council_id=council_id,
                        session_id=session_id,
                        objective=objective,
                        round_num=current_round,
                        debate_context=debate_context,
                    )
                    budget.charge(tokens=turn_res.tokens_used, cost_usd=turn_res.cost_usd, turns=1)

                    self.council_manager.record_debate_turn(
                        session_id=session_id,
                        turn_data={
                            "round": current_round,
                            "bot_id": bot_id,
                            "position": turn_res.position,
                            "dissent": turn_res.dissent,
                            "leaf_id": turn_res.leaf_id,
                        },
                        cost_usd=turn_res.cost_usd,
                        tokens=turn_res.tokens_used,
                        correlation_id=correlation_id,
                    )
                session = self.council_manager.get_session(session_id)
                if not session:
                    break

            # Phase 3: Synthesis
            if session.phase in ("independent_analysis", "debate_round", "synthesis"):
                try:
                    self.council_manager.advance_phase(session_id, "synthesis")
                except ValueError:
                    pass

                synthesis_result: SynthesisResult = self.synthesis_bot.synthesize(
                    council_id=council_id,
                    session_id=session_id,
                    objective=objective,
                    positions=session.positions,
                    dissent_map=session.dissent,
                    debate_turns=session.debate_turns,
                    decision_mode=council.decision_mode,
                )
                budget.charge(tokens=synthesis_result.tokens_used, cost_usd=synthesis_result.cost_usd, turns=1)

            # Phase 4: Policy Check on Proposed Actions
            action_gate = "not_requested"
            if synthesis_result.action_plan:
                action_gate = "approved"
                if self.civ_manager:
                    try:
                        self.council_manager.advance_phase(session_id, "policy_check")
                    except ValueError:
                        pass

                    for plan_item in synthesis_result.action_plan:
                        pol_decision = self.civ_manager.evaluate_policy(
                            subject_bot=council_id,
                            action=plan_item.get("action", "execute"),
                            resource=plan_item.get("resource", council_id),
                        )
                        if pol_decision.result == POLICY_RESULT_DENY:
                            action_gate = "denied"
                            raise PermissionError(f"Constitutional Hard Deny on action '{plan_item.get('action')}': {pol_decision.reason}")
                        elif pol_decision.result == POLICY_RESULT_REQUIRE_APPROVAL and not approved:
                            action_gate = "approval_required"

            # Phase 5: Decision Recorded
            decision_record: DecisionRecord = self.council_manager.record_decision(
                session_id=session_id,
                synthesis=synthesis_result.synthesis,
                decision=synthesis_result.decision,
                confidence=synthesis_result.confidence,
                action_refs=[item.get("action", "action") for item in synthesis_result.action_plan],
                evidence_refs=list(session.evidence_refs),
                correlation_id=correlation_id,
            )

            # Phase 6: Action Intent Scheduling (if approved, executable, and not dry run)
            if not dry_run and action_gate == "approved" and synthesis_result.action_plan:
                for plan_item in synthesis_result.action_plan:
                    self.outbox.schedule_intent(
                        council_id=council_id,
                        session_id=session_id,
                        action_type=plan_item.get("action", "execute"),
                        payload=plan_item,
                        correlation_id=correlation_id,
                    )

            output = {
                "session_id": session_id,
                "council_id": council_id,
                "objective": objective,
                "command_id": idempotency_key,
                "status": "completed",
                "decision_id": decision_record.id,
                "decision": decision_record.decision,
                "synthesis": decision_record.synthesis,
                "confidence": decision_record.confidence,
                "participants": decision_record.participants,
                "dissent": decision_record.dissent,
                "budget_usage": budget.to_dict(),
                "action_plan": synthesis_result.action_plan,
                "action_gate": action_gate,
            }

            if idempotency_key:
                self.inbox.record_completed(idempotency_key, output, status="succeeded")

            return output

        except Exception as exc:
            logger.error(f"Deliberation session {session_id} failed: {exc}", exc_info=True)
            self.council_manager.fail_session(session_id, str(exc), correlation_id=correlation_id)
            err_output = {
                "session_id": session_id,
                "council_id": council_id,
                "objective": objective,
                "status": "failed",
                "error": str(exc),
                "budget_usage": budget.to_dict(),
            }
            if idempotency_key:
                self.inbox.record_completed(idempotency_key, err_output, status="failed", error=str(exc))
            raise


__all__ = ["CouncilDebateRunner"]
