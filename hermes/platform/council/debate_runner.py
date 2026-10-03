"""Autonomous multi-turn Council deliberation runner with durable FSM, budgets, and policy gates.

Deliberation provenance — facade vs real (audit hardening)
==========================================================
Every ``DecisionRecord`` written through this runner carries an explicit
``deliberation`` field: ``"real"`` or ``"facade"``.

- ``facade``: member positions were produced by the deterministic mock
  executor inside ``MemberRunner`` (``MemberRunner.uses_mock is True``) —
  scripted text with fabricated confidence (0.85) and cost. The record also
  gets ``metadata.ratification = "automated"`` and a WARNING is logged by
  ``CouncilManager.record_decision``. A facade decision is an audit trail of
  the *pipeline*, never evidence of model judgment.
- ``real``: positions came from an actual model executor injected into
  ``MemberRunner``.

The provenance is derived automatically from ``member_runner.uses_mock`` in
``deliberate()`` — callers cannot mislabel a mock run as real.

Runbook: wiring REAL model deliberation (operator decision pending)
===================================================================
No model execution is implemented here by design (cost/architecture decision
is still open). When that decision lands, real deliberation is a *one-line
composition change* — no FSM, manager, or record format changes needed:

1. Implement a ``LeafExecutor`` callable::

     def model_executor(prompt: str, context: dict) -> dict:
         # context: bot_id, council_id, session_id, objective, round,
         #          snapshot_id, assigned_model
         response = call_my_llm(prompt, identity=context["snapshot_id"])
         return {
             "position": response.text,          # required
             "confidence": response.confidence,  # from the model, not fabricated
             "dissent": response.dissent,        # optional str
             "evidence_refs": [...],             # optional
             "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
             "cost_usd": response.usage.cost,    # feeds CouncilBudget.charge
             "model": response.model_id,         # recorded on the Leaf/audit
         }

   Contract (``hermes.platform.council.member_runner.LeafExecutor``):
   ``(prompt: str, context: dict) -> dict``. The runner freezes each member's
   identity into a Leaf snapshot before the call and passes ``snapshot_id`` in
   context — use it to pin the exact IdentityVersion for reproducibility.
   Returned ``tokens_used``/``cost_usd`` are charged against the council
   budget, so they must be the REAL usage numbers.

2. Inject it when composing the stack::

     member_runner = MemberRunner(identity_provider=id_mgr, executor=model_executor)
     runner = CouncilDebateRunner(council_manager=council_mgr, member_runner=member_runner, ...)

   ``MemberRunner.uses_mock`` becomes False and every DecisionRecord from
   ``runner.deliberate(...)`` is stamped ``deliberation="real"`` automatically.

3. Do NOT fabricate provenance elsewhere: ``CouncilManager.record_decision``
   is fail-closed — omitting ``deliberation`` (or passing anything other than
   the literal ``"real"``) records a facade with a warning. The human
   promotion route in ``hermes/platform/civilization/delegation.py``
   (``deliberate_and_promote_proposal``) deliberately keeps
   ``deliberation="facade"`` with scripted APPROVE votes until the operator
   decides to move it onto this runner.
"""

from __future__ import annotations

import logging
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
from hermes.platform.council.synthesis import SynthesisBot, SynthesisResult, _is_blocked_verdict
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

        # 1. Inbox deduplication check. A 'pending_human_approval' outcome is
        # NOT terminal: the operator must be able to re-drive the same
        # command_id with approved=True after review (C1 resume path).
        if idempotency_key and self.inbox.is_processed(idempotency_key):
            cached = self.inbox.get_result(idempotency_key)
            if cached and cached.get("status") != "pending_human_approval":
                logger.info(f"Deliberate: command {idempotency_key} already processed; returning cached result")
                return cached

        # 2. Resolve council and locate an existing session to resume
        council = self.council_manager.get(council_id)
        if not council:
            raise KeyError(f"Council '{council_id}' is not registered in CouncilManager")

        opts_overrides = {k: v for k, v in opts.items() if k in {
            "max_rounds", "max_turns", "max_tokens", "max_cost_usd", "timeout_seconds", "max_members"
        }}

        session: Optional[CouncilSession] = None
        if idempotency_key:
            for s in self.council_manager.list_sessions(council_id):
                if s.command_id == idempotency_key and s.phase not in ("completed", "failed", "aborted"):
                    session = s
                    break

        # 3. Initialize budget (new session) or restore it from the session (A1:
        # rebuilding from config on resume silently zeroed counters and the
        # timeout clock, letting sessions run past their hard limits forever).
        if session:
            restored_cfg = dict(session.budget or {})
            restored_cfg.update(opts_overrides)
            budget = CouncilBudget.from_dict(restored_cfg)
            # Overlay live counters materialized by event replay.
            budget.tokens_used = max(budget.tokens_used, int(session.tokens_used or 0))
            budget.cost_usd_used = max(budget.cost_usd_used, float(session.cost_usd or 0.0))
            budget.rounds_used = max(budget.rounds_used, int(session.round or 0))
            budget.turns_used = max(budget.turns_used, len(session.debate_turns or []))
        else:
            budget_cfg = dict(council.budget or {})
            budget_cfg.update(opts_overrides)
            budget = CouncilBudget.from_dict(budget_cfg)
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
            if session.phase in ("created", "selecting_members", "independent_analysis", "paused"):
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

                    # Submit position to canonical event store (A2: this is the
                    # single accounting point for round-0 member cost/tokens).
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

                    # Also record in session debate turns transcript.
                    # A2: transcript is a log, not a billing event — charging
                    # here too made event replay double-count every member turn.
                    self.council_manager.record_debate_turn(
                        session_id=session_id,
                        turn_data={
                            "round": 0,
                            "bot_id": bot_id,
                            "position": result.position,
                            "dissent": result.dissent,
                            "leaf_id": result.leaf_id,
                        },
                        correlation_id=correlation_id,
                    )

            # A3: the manager's replay only ADDS dissents (sticky — a later
            # turn with dissent=None never clears the earlier one). Track the
            # live dissent set locally so retracted dissents stop the loop
            # instead of burning budget rounds on members who already converged.
            live_dissent: Dict[str, str] = dict(session.dissent)

            # Phase 2: Multi-turn debate rounds if enabled and dissent or conflict exists
            max_rounds = opts.get("max_rounds", budget.max_rounds)
            # M3: was `rounds_used < max_rounds - 1`, which made max_rounds=1
            # run zero debate rounds. Budget caps rounds_used <= max_rounds.
            while budget.rounds_used < max_rounds and len(live_dissent) > 0:
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
                    "critiques": [f"{bot}: {diss}" for bot, diss in live_dissent.items()],
                    "round": current_round,
                }

                # Each member critiques and optionally updates position
                previous_dissent = dict(live_dissent)
                round_dissent: Dict[str, str] = {}
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

                    # A2: in debate rounds the turn record IS the accounting
                    # point (no submit_position here), so keep the charge.
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
                    if turn_res.dissent:
                        round_dissent[bot_id] = turn_res.dissent

                # A3: every dissenter retracted -> nothing left to debate.
                # Same dissidents repeating identical dissent -> further rounds
                # are futile (starvation guard); stop either way.
                if round_dissent == previous_dissent and round_dissent:
                    logger.info(
                        f"Session {session_id}: dissent unchanged after round {current_round}; stopping debate"
                    )
                    live_dissent = round_dissent
                    session = self.council_manager.get_session(session_id) or session
                    break
                live_dissent = round_dissent
                session = self.council_manager.get_session(session_id)
                if not session:
                    break
                if not live_dissent:
                    break

            if session is None:
                raise ValueError(f"CouncilSession {session_id} vanished during deliberation")

            # Phase 3: Synthesis
            # C5: synthesis_result must exist for every resumable phase.
            # 'paused' sessions previously skipped Phase 3 entirely and Phase 4
            # crashed with UnboundLocalError -> fail_session on a healthy session.
            synthesis_result: Optional[SynthesisResult] = None
            if session.phase in ("independent_analysis", "debate_round", "synthesis", "paused", "policy_check"):
                try:
                    self.council_manager.advance_phase(session_id, "synthesis")
                except ValueError:
                    pass

                synthesis_result = self.synthesis_bot.synthesize(
                    council_id=council_id,
                    session_id=session_id,
                    objective=objective,
                    positions=session.positions,
                    dissent_map=live_dissent,
                    debate_turns=session.debate_turns,
                    decision_mode=council.decision_mode,
                )
                budget.charge(tokens=synthesis_result.tokens_used, cost_usd=synthesis_result.cost_usd, turns=1)

            if synthesis_result is None:
                raise ValueError(
                    f"Cannot resume session {session_id} from phase '{session.phase}': no synthesis result available"
                )

            # C1: Hard human-approval gate. A BLOCKED verdict or a synthesis
            # that flags needs_human_approval must never schedule actions nor
            # silently 'complete' the session; park it as pending approval.
            # approved=True is the human-approval signal on resume.
            blocked_verdict = _is_blocked_verdict(synthesis_result.decision)
            requires_human = bool(synthesis_result.needs_human_approval) and not approved
            if (blocked_verdict or requires_human) and not approved:
                gate_reason = "blocked_verdict" if blocked_verdict else "needs_human_approval"
                self.council_manager.pause_session(
                    session_id,
                    f"pending_human_approval: {gate_reason}",
                    correlation_id=correlation_id,
                )
                pending_output = {
                    "session_id": session_id,
                    "council_id": council_id,
                    "objective": objective,
                    "command_id": idempotency_key,
                    "status": "pending_human_approval",
                    "decision_id": None,
                    "decision": synthesis_result.decision,
                    "synthesis": synthesis_result.synthesis,
                    "confidence": synthesis_result.confidence,
                    "participants": list(session.positions.keys()),
                    "dissent": dict(live_dissent),
                    "budget_usage": budget.to_dict(),
                    "action_plan": synthesis_result.action_plan,
                    "action_gate": "approval_required",
                    "blocked_verdict": blocked_verdict,
                }
                if idempotency_key:
                    self.inbox.record_completed(idempotency_key, pending_output, status="pending_human_approval")
                return pending_output

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
            # Provenance derived from the member runner itself, never from a
            # caller-supplied flag: mock executor => facade, real injected
            # executor => real. Unknown runner shapes fail closed to facade.
            deliberation = (
                "facade"
                if getattr(self.member_runner, "uses_mock", True)
                else "real"
            )
            decision_record: DecisionRecord = self.council_manager.record_decision(
                session_id=session_id,
                synthesis=synthesis_result.synthesis,
                decision=synthesis_result.decision,
                confidence=synthesis_result.confidence,
                action_refs=[item.get("action", "action") for item in synthesis_result.action_plan],
                evidence_refs=list(session.evidence_refs),
                correlation_id=correlation_id,
                deliberation=deliberation,
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
                "deliberation": decision_record.deliberation,
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
