"""Real (LLM-backed) council deliberation for governed identity promotions.

Scope (operator decision, audit Council-fachada follow-up): promotions of
RISK_HIGH / RISK_IDENTITY_CRITICAL proposals through the human route
(``hermes.platform.civilization.delegation.deliberate_and_promote_proposal``)
are decided by an ACTUAL deliberation of the council members instead of the
scripted facade. Low/medium risk keeps the declared facade (cost decision —
rare promotions do not pay N x rounds x tokens).

Provenance contract (never fabricate):
- ``deliberation="real"`` is stamped ONLY here, and only after every member
  position came from the injected ``LeafExecutor`` (the same ``(prompt,
  context) -> dict`` contract as ``hermes.platform.council.member_runner``).
- If the executor raises, returns an invalid vote, or blows the budget, this
  module FAILS CLOSED: the session is marked failed and
  ``PromotionDeliberationError`` is raised. The caller must refuse the
  promotion — degrading to the facade is explicitly forbidden.
- ``HAOS_CIV_REAL_DELIBERATION=0`` disables the real path (facade as before,
  with an INFO log); default is enabled for high/identity-critical risk.

Voting semantics: each member returns ``{"vote": "APPROVE"|"REJECT", ...}``.
The binding verdict is a strict majority of APPROVE over submitted votes
(``approve*2 > n``); anything else (majority REJECT, tie, no majority) is
REJECTED / DIVIDED and blocks the promotion. ``SynthesisBot`` majority mode
alone is NOT used for the verdict because its tally approves whichever vote
wins — including REJECT — so the verdict is computed here from the explicit
votes and recorded verbatim in the DecisionRecord.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.leaf_protocol import create_leaf_identity_snapshot
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.council.budget import BudgetExhaustedError, CouncilBudget
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.member_runner import LeafExecutor
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.council.synthesis import SynthesisBot
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

logger = logging.getLogger("hermes.platform.council.promotion_executor")

#: Env gate. Default ON for high/identity-critical promotions; "0"/"false"/
#: "off" restores the declared facade (with an INFO log, never silently).
REAL_DELIBERATION_ENV = "HAOS_CIV_REAL_DELIBERATION"

#: Implicit council used when the caller does not name one. Created on demand
#: from the active bots (minimum 2 — quorum ceil(m/2) enforced by the manager).
PROMOTION_COUNCIL_ID = "promotion-council"

#: Conservative budget for a single-round promotion deliberation.
PROMOTION_MAX_ROUNDS = 1
PROMOTION_MAX_TOKENS = 8_000
PROMOTION_MAX_COST_USD = 0.25
PROMOTION_TIMEOUT_SECONDS = 120.0

_REAL_DELIB_EVENT = "civ.promotion.real_deliberation"
_VALID_VOTES = ("APPROVE", "REJECT")


class PromotionDeliberationError(RuntimeError):
    """Real deliberation could not be completed (executor/model/quorum/budget).

    Fail-closed signal: the caller MUST refuse the promotion. Falling back to
    the facade here would recreate the exact audit defect this module closes.
    """


def real_deliberation_enabled() -> bool:
    """True unless HAOS_CIV_REAL_DELIBERATION is explicitly disabled."""
    val = os.environ.get(REAL_DELIBERATION_ENV)
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Real LLM member executor (reuses the existing Hermes provider stack)
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """Parse the first JSON object from a model reply (fences tolerated)."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    m = _JSON_BLOCK_RE.search(cleaned)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def build_model_member_executor(
    llm_call: Optional[Callable[..., Any]] = None,
    *,
    task: str = "council_deliberation",
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> LeafExecutor:
    """Build a real LeafExecutor backed by the configured HAOS model.

    Reuses the existing provider stack — ``agent.auxiliary_client.call_llm``
    with ``task="council_deliberation"`` (the same resolution path the agent
    uses for auxiliary work: explicit ``auxiliary.council_deliberation``
    config wins, otherwise the default/auto provider). No new HTTP client is
    invented here. ``llm_call`` is injectable for integration harnesses;
    tests must NOT use it against a live model (determinism) — inject a fake
    executor into ``deliberate_and_promote_proposal(member_executor=...)``.

    Returned dict follows the MemberRunner LeafExecutor contract plus the
    explicit ``vote`` field required by promotion deliberation. Tokens are
    the REAL usage numbers (they feed CouncilBudget.charge); cost is 0.0 when
    the provider does not report one — never fabricated.
    """
    def executor(prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if llm_call is not None:
            call = llm_call
        else:
            from agent.auxiliary_client import call_llm as call  # lazy: keeps import cheap
        from agent.auxiliary_client import extract_content_or_reasoning

        system = context.get("system_prompt") or (
            "You are a HAOS council member bot deliberating an identity "
            "evolution proposal. Answer strictly from your frozen identity."
        )
        response = call(
            task=task,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            reasoning_config={"enabled": False},
        )
        text = extract_content_or_reasoning(response)
        data = _extract_json_obj(text)
        if data is None or "vote" not in data:
            raise ValueError(
                f"council member executor returned an unparseable verdict "
                f"(bot={context.get('bot_id')!r}): {text[:200]!r}"
            )
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")

        def _u(name: str, default: float = 0):  # noqa: E301 - usage accessor
            if usage is None:
                return default
            if isinstance(usage, dict):
                return usage.get(name, default)
            return getattr(usage, name, default)

        tokens = int(
            _u("total_tokens", 0)
            or (int(_u("input_tokens", 0) or 0) + int(_u("output_tokens", 0) or 0))
            or 0
        )
        cost = float(_u("cost_usd", 0.0) or 0.0)
        model = str(
            getattr(response, "model", "")
            or (response.get("model", "") if isinstance(response, dict) else "")
            or task
        )
        return {
            "position": str(data.get("position", "")),
            "vote": str(data.get("vote", "")),
            "confidence": data.get("confidence"),
            "dissent": data.get("dissent"),
            "tokens_used": tokens,
            "cost_usd": cost,
            "model": model,
        }

    return executor


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _resolve_council(
    council_mgr: CouncilManager, store: EventStore, bot_id: str,
) -> CouncilSpec:
    """Return the named/implicit council, creating 'promotion-council' on demand.

    Implicit membership = active bots (paused bots excluded), minimum 2 —
    quorum ceil(m/2) is enforced later by CouncilManager.record_decision.
    """
    council = council_mgr.get(PROMOTION_COUNCIL_ID)
    if council is not None:
        return council
    spec_mgr = BotSpecManager(store)
    members: List[str] = []
    for spec in spec_mgr.list():
        if spec.id == bot_id or spec_mgr.is_paused(spec.id):
            continue
        members.append(spec.id)
    if len(members) < 2:
        raise PromotionDeliberationError(
            f"cannot form '{PROMOTION_COUNCIL_ID}': only {len(members)} active "
            f"non-subject bot(s) available; real deliberation requires at least 2 "
            f"members (fail-closed, no facade fallback)"
        )
    council = CouncilSpec(
        id=PROMOTION_COUNCIL_ID,
        purpose="Real deliberation of high-risk identity promotions",
        members=members,
        decision_mode="majority",
        budget={
            "max_rounds": PROMOTION_MAX_ROUNDS,
            "max_tokens": PROMOTION_MAX_TOKENS,
            "max_cost_usd": PROMOTION_MAX_COST_USD,
            "timeout_seconds": PROMOTION_TIMEOUT_SECONDS,
        },
        metadata={"created_by": "promotion_executor", "subject_excluded": bot_id},
    )
    council_mgr.register(council)
    logger.info(
        "Created implicit council '%s' with members %s", PROMOTION_COUNCIL_ID, members
    )
    return council


def _member_prompts(
    council: CouncilSpec,
    objective: str,
    bot_id: str,
    member_bot: str,
    proposal: Any,
    identity_bundle: Any,
    other_positions: Dict[str, str],
) -> Tuple[str, str]:
    """Build (system_prompt, user_prompt) for one member's vote turn."""
    if identity_bundle is not None:
        system = (
            f"You are {member_bot}, a specialist bot serving on Council "
            f"'{council.id}'. Vote strictly from your frozen identity below; "
            "do not role-play another agent.\n"
            f"SOUL: {identity_bundle.soul}\n"
            f"IDENTITY: {identity_bundle.identity}\n"
            f"VALUES: {identity_bundle.values}"
        )
    else:
        system = (
            f"You are {member_bot}, a specialist bot serving on Council "
            f"'{council.id}'. Vote conservatively: identity changes are "
            "immutable and hard to roll back."
        )
    parts = [
        f"Objective: {objective}",
        "Evolution proposal under deliberation:",
        f"- proposal_id: {proposal.id}",
        f"- subject bot: {bot_id}",
        f"- risk class: {proposal.risk_class}",
        f"- rationale: {getattr(proposal, 'rationale', '')}",
        f"- proposed values patch: {getattr(proposal, 'proposed_values_patch', '') or '(none)'}",
        f"- proposed identity patch: {getattr(proposal, 'proposed_identity_patch', '') or '(none)'}",
        f"- proposed soul patch: {getattr(proposal, 'proposed_soul_patch', '') or '(none)'}",
        f"- evidence refs: {', '.join(getattr(proposal, 'evidence_refs', []) or []) or '(none)'}",
    ]
    if other_positions:
        parts.append("Positions submitted so far by other council members:")
        for other_bot, pos in other_positions.items():
            if other_bot != member_bot:
                parts.append(f"- {other_bot}: {pos}")
    parts.append(
        'Respond with ONLY a JSON object: {"vote": "APPROVE" or "REJECT", '
        '"position": "<1-3 sentence rationale grounded in your identity>", '
        '"confidence": <float 0-1>, "dissent": "<optional concern, or null>"}'
    )
    return system, "\n".join(parts)


def _validate_member_result(
    raw: Any, member_bot: str,
) -> Dict[str, Any]:
    """Coerce/validate one executor result into a binding ballot.

    Anything malformed fails closed — an unvalidated vote defaulting to
    APPROVE is exactly the facade-in-disguise defect this path replaces.
    """
    if not isinstance(raw, dict):
        raise PromotionDeliberationError(
            f"member executor for {member_bot} returned {type(raw).__name__}, expected dict"
        )
    vote = str(raw.get("vote", "")).strip().upper()
    if vote not in _VALID_VOTES:
        raise PromotionDeliberationError(
            f"member executor for {member_bot} returned invalid vote "
            f"{raw.get('vote')!r} (expected APPROVE or REJECT)"
        )
    position = str(raw.get("position", "")).strip()
    if not position:
        raise PromotionDeliberationError(
            f"member executor for {member_bot} returned an empty position"
        )
    conf_raw = raw.get("confidence")
    if conf_raw is None:
        confidence = 0.5  # neutral: never fabricate model confidence
    else:
        try:
            confidence = min(1.0, max(0.0, float(conf_raw)))
        except (TypeError, ValueError):
            raise PromotionDeliberationError(
                f"member executor for {member_bot} returned non-numeric confidence "
                f"{conf_raw!r}"
            )
    dissent = raw.get("dissent")
    dissent = str(dissent) if dissent is not None and str(dissent).strip() not in ("", "None", "null") else None
    tokens = int(raw.get("tokens_used", raw.get("tokens", 0)) or 0)
    cost = float(raw.get("cost_usd", 0.0) or 0.0)
    model = str(raw.get("model", "") or "unknown")
    return {
        "vote": vote,
        "position": position,
        "confidence": confidence,
        "dissent": dissent,
        "tokens": tokens,
        "cost_usd": cost,
        "model": model,
    }


def run_promotion_deliberation(
    *,
    council_mgr: CouncilManager,
    store: EventStore,
    proposal: Any,
    bot_id: str,
    approver: str,
    council_id: Optional[str] = None,
    decision_summary: str = "",
    member_executor: Optional[LeafExecutor] = None,
    identity_mgr: Optional[IdentityManager] = None,
) -> Dict[str, Any]:
    """Conduct a REAL one-round deliberation over a promotion proposal.

    Returns a verdict dict::

        {approved, decision, decision_id, session_id, council_id,
         votes, tokens_used, cost_usd}

    Raises PromotionDeliberationError (fail-closed) on any executor/model/
    quorum/budget failure — the caller must refuse the promotion, never fall
    back to the facade.
    """
    executor = member_executor or build_model_member_executor()
    identity_mgr = identity_mgr or IdentityManager(store)

    # 1. Council resolution: named council must exist (never silently swapped);
    #    unnamed uses the implicit 'promotion-council' built from active bots.
    if council_id:
        council = council_mgr.get(council_id)
        if council is None:
            raise PromotionDeliberationError(
                f"council '{council_id}' is not registered; refusing to deliberate "
                f"against a different council (fail-closed)"
            )
    else:
        council = _resolve_council(council_mgr, store, bot_id)
    if len(council.members) < 2:
        raise PromotionDeliberationError(
            f"council '{council.id}' has {len(council.members)} member(s); real "
            f"deliberation requires at least 2 (fail-closed)"
        )

    objective = (
        f"Deliberate evolution proposal {proposal.id} for bot {bot_id} "
        f"(risk {proposal.risk_class}): {getattr(proposal, 'rationale', '')}"
    )

    # 2. Session + conservative budget (single round, hard token/cost caps).
    budget = CouncilBudget(
        max_rounds=PROMOTION_MAX_ROUNDS,
        max_turns=len(council.members) + 1,
        max_tokens=PROMOTION_MAX_TOKENS,
        max_cost_usd=PROMOTION_MAX_COST_USD,
        timeout_seconds=PROMOTION_TIMEOUT_SECONDS,
    )
    try:
        session = council_mgr.start_session(
            council_id=council.id,
            objective=objective,
            budget=budget.to_dict(),
        )
    except (KeyError, ValueError) as exc:
        raise PromotionDeliberationError(f"cannot start deliberation session: {exc}") from exc
    session_id = session.session_id

    votes: Dict[str, Dict[str, Any]] = {}
    positions_text: Dict[str, str] = {}
    dissent_map: Dict[str, str] = {}
    total_tokens = 0
    total_cost = 0.0

    # 3. One independent ballot per member, frozen in its Leaf snapshot.
    try:
        for member_bot in council.members:
            budget.reserve(estimated_tokens=500, estimated_cost=0.005)

            identity_ver = identity_mgr.get_active_version(member_bot)
            leaf_id = f"leaf-{member_bot}-{uuid.uuid4().hex[:8]}"
            snapshot = None
            bundle = identity_ver.bundle if identity_ver else None
            if identity_ver is not None:
                snapshot = create_leaf_identity_snapshot(
                    leaf_id=leaf_id,
                    parent_bot_id=member_bot,
                    identity_version=identity_ver,
                    task_description=f"Promotion deliberation: {objective}",
                    council_id=council.id,
                    council_session_id=session_id,
                    model="promotion-deliberation",
                    correlation_id=session_id,
                )
            system_prompt, user_prompt = _member_prompts(
                council, objective, bot_id, member_bot, proposal, bundle, positions_text,
            )
            context = {
                "bot_id": member_bot,
                "council_id": council.id,
                "session_id": session_id,
                "objective": objective,
                "round": 0,
                "snapshot_id": leaf_id,
                "assigned_model": "promotion-deliberation",
                "system_prompt": system_prompt,
            }
            raw = executor(user_prompt, context)
            ballot = _validate_member_result(raw, member_bot)

            budget.charge(tokens=ballot["tokens"], cost_usd=ballot["cost_usd"], turns=1)
            total_tokens += ballot["tokens"]
            total_cost += ballot["cost_usd"]
            votes[member_bot] = ballot
            positions_text[member_bot] = ballot["position"]
            if ballot["dissent"]:
                dissent_map[member_bot] = ballot["dissent"]

            council_mgr.submit_position(
                session_id=session_id,
                bot_id=member_bot,
                position={
                    "vote": ballot["vote"],
                    "position": ballot["position"],
                    "confidence": ballot["confidence"],
                    "model": ballot["model"],
                    "proposal_id": proposal.id,
                    # Deliberated by the injected executor, not scripted.
                    "ratification": "deliberated",
                    "approver": approver,
                },
                dissent=ballot["dissent"],
                leaf_id=leaf_id,
                leaf_snapshot=snapshot.to_dict() if snapshot is not None else None,
                cost_usd=ballot["cost_usd"],
                tokens=ballot["tokens"],
                correlation_id=session_id,
            )
    except PromotionDeliberationError as exc:
        _fail_quietly(council_mgr, session_id, str(exc))
        raise
    except BudgetExhaustedError as exc:
        msg = f"deliberation budget exhausted: {exc}"
        _fail_quietly(council_mgr, session_id, msg)
        raise PromotionDeliberationError(msg) from exc
    except Exception as exc:  # model down / executor crash: fail closed, never facade
        msg = f"real deliberation unavailable; refusing to fall back to facade (member executor failed: {exc})"
        _fail_quietly(council_mgr, session_id, msg)
        raise PromotionDeliberationError(msg) from exc

    # 4. Binding verdict: strict majority of APPROVE over submitted ballots.
    n = len(votes)
    approve = sum(1 for v in votes.values() if v["vote"] == "APPROVE")
    reject = n - approve
    approved = approve * 2 > n
    if approved:
        decision_text = decision_summary or (
            f"APPROVED by council majority ({approve}/{n} votes): {objective}"
        )
        if not decision_text.startswith("APPROVED"):
            decision_text = f"APPROVED by council majority ({approve}/{n} votes): {decision_text}"
    else:
        decision_text = (
            f"REJECTED / DIVIDED ({approve} APPROVE, {reject} REJECT of {n} ballots): {objective}"
        )
    confidences = [v["confidence"] for v in votes.values()]
    confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Synthesis text from the standard engine (audit-consistent format); the
    # verdict itself comes from the explicit ballot tally above.
    try:
        synth = SynthesisBot().synthesize(
            council_id=council.id,
            session_id=session_id,
            objective=objective,
            positions=positions_text,
            dissent_map=dissent_map,
            debate_turns=None,
            decision_mode="majority",
        )
        synthesis_text = synth.synthesis
    except Exception:
        synthesis_text = "\n".join(f"{b}: {v['vote']} — {v['position']}" for b, v in votes.items())

    # 5. Record the binding DecisionRecord with honest provenance.
    try:
        record = council_mgr.record_decision(
            session_id=session_id,
            synthesis=synthesis_text,
            decision=decision_text,
            confidence=confidence,
            action_refs=[proposal.id],
            correlation_id=session_id,
            deliberation="real",
        )
    except (KeyError, ValueError) as exc:
        _fail_quietly(council_mgr, session_id, f"cannot record deliberation decision: {exc}")
        raise PromotionDeliberationError(f"cannot record deliberation decision: {exc}") from exc

    # 6. Observability: tokens/cost of the real deliberation in the event log.
    store.append(Event(
        name=_REAL_DELIB_EVENT,
        payload={
            "proposal_id": proposal.id,
            "bot_id": bot_id,
            "approver": approver,
            "council_id": council.id,
            "session_id": session_id,
            "decision_id": record.id,
            "approved": approved,
            "decision": decision_text,
            "votes": {b: v["vote"] for b, v in votes.items()},
            "ballots": votes,
            "tokens_used": total_tokens,
            "cost_usd": total_cost,
            "timestamp": time.time(),
        },
        correlation_id=session_id,
    ))

    logger.info(
        "Real promotion deliberation %s: council=%s decision=%s approved=%s "
        "tokens=%d cost_usd=%.4f",
        record.id, council.id, decision_text[:80], approved, total_tokens, total_cost,
    )
    return {
        "approved": approved,
        "decision": decision_text,
        "decision_id": record.id,
        "session_id": session_id,
        "council_id": council.id,
        "votes": {b: v["vote"] for b, v in votes.items()},
        "tokens_used": total_tokens,
        "cost_usd": total_cost,
    }


def _fail_quietly(council_mgr: CouncilManager, session_id: str, reason: str) -> None:
    """Mark the session failed without masking the original fail-closed error."""
    try:
        council_mgr.fail_session(session_id, reason)
    except Exception:  # pragma: no cover - best-effort audit trail
        logger.warning("could not fail deliberation session %s: %s", session_id, reason)


__all__ = [
    "PromotionDeliberationError",
    "PROMOTION_COUNCIL_ID",
    "REAL_DELIBERATION_ENV",
    "build_model_member_executor",
    "real_deliberation_enabled",
    "run_promotion_deliberation",
]
