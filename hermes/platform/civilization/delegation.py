"""Civilization-first routing and lifecycle execution for delegated Hermes tasks.

This module unifies the 4 tiers of HAOS Civilization:
- V1 Foundation: Persistent Bot identity, version freeze, Leaf derivation without mutating base SOUL.
- V2 Society: Evidence-based reputation updates, cross-bot attribution, specialist capability matching.
- V3 Evolution: Automatic post-task experience recording, governed proposal promotion/rollback.
- V4 Civilization: Hard-deny and advisory constitutional policy evaluation before execution,
  plus institutional memory retrieval.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.leaf_protocol import create_leaf_identity_snapshot
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotSpec
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    POLICY_RESULT_ALLOW,
    POLICY_RESULT_DENY,
    POLICY_RESULT_REQUIRE_APPROVAL,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.promotion_executor import (
    PromotionDeliberationError,
    real_deliberation_enabled,
    run_promotion_deliberation,
)
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    EvolutionError,
    EvolutionProposal,
    RISK_HIGH,
    RISK_IDENTITY_CRITICAL,
    RISK_LOW,
    STATUS_APPROVED,
    STATUS_CANARY,
    STATUS_PROMOTED,
    STATUS_REVIEW,
    STATUS_ROLLED_BACK,
)
from hermes.platform.evolution.promotion_holdout_gate import (
    HoldoutPromotionGate,
    default_holdout_gate,
)
from hermes.platform.observability.event_store import EventStore, default_event_store_path
from hermes.platform.observability.events import Event
from hermes.platform.society.manager import SocietyManager

logger = logging.getLogger("hermes.platform.civilization.delegation")

_CREATED = "civ.leaf.created"
_COMPLETED = "civ.leaf.completed"
_FAILED = "civ.leaf.failed"
_ROUTED = "civ.delegate.routed"
_RECOVERY = "civ.delegate.recovery"


# ---------------------------------------------------------------------------
# ADR-021 — Failure-recovery taxonomy
# ---------------------------------------------------------------------------

class RecoveryStrategy(str, Enum):
    """Named recovery vocabulary (absorbed concept from CAMEL FailureHandlingConfig).

    The router SUGGESTS a strategy; the parent/agent decides whether to act on
    it. Every suggestion is an auditable ``civ.delegate.recovery`` event.
    ``HALT`` is the fail-closed ceiling: past ``max_retries`` the parent must
    give up and alert instead of re-delegating in a loop.
    """

    RETRY = "retry"
    REPLAN = "replan"
    REASSIGN = "reassign"
    DECOMPOSE = "decompose"
    CREATE_WORKER = "create_worker"
    HALT = "halt"


@dataclass(frozen=True)
class RecoveryPolicy:
    """Rollout knobs for the recovery taxonomy.

    ``enabled_strategies=None`` means every strategy is available; a frozenset
    restricts the escalation ladder (strategies outside it are skipped).
    ``halt_on_max_retries=False`` keeps escalating past the ceiling — only for
    experiments; the default is fail-closed.
    """

    max_retries: int = 3
    enabled_strategies: Optional[frozenset] = None
    halt_on_max_retries: bool = True


_DEFAULT_RECOVERY_POLICY = RecoveryPolicy()

# Escalation ladder for hard failures: try again, then move to a better-reputed
# specialist (only when one can actually be named), then rethink the plan.
_FAILURE_LADDER = (
    RecoveryStrategy.RETRY,
    RecoveryStrategy.REASSIGN,
    RecoveryStrategy.REPLAN,
    RecoveryStrategy.DECOMPOSE,
    RecoveryStrategy.CREATE_WORKER,
)
# Light failures (a DONE that revalidates as insufficient) climb a gentler ladder.
_INSUFFICIENT_LADDER = (
    RecoveryStrategy.RETRY,
    RecoveryStrategy.REPLAN,
)
_INSUFFICIENT_MARKER = "[insufficient"
# A completed summary shorter than this, with no registered tool evidence, is
# treated as an unverified DONE (CAMEL's is_task_result_insufficient analogue).
_MIN_SUFFICIENT_SUMMARY_LEN = 8


def _recovery_enabled() -> bool:
    from .feature_flags import get_feature_flags
    return get_feature_flags().recovery_enabled


def _goal_slug(goal: Any) -> str:
    return _slug(str(goal or "general"))


def _is_insufficient_result(result: Dict[str, Any]) -> bool:
    """Revalidate a declared success: empty/whitespace summary, an explicit
    '[insufficient' marker, or a stub summary with no tool evidence."""
    summary = str(result.get("summary") or "")
    if not summary.strip():
        return True
    if summary.lstrip().lower().startswith(_INSUFFICIENT_MARKER):
        return True
    if len(summary.strip()) < _MIN_SUFFICIENT_SUMMARY_LEN:
        evidence = result.get("tool_trace") or result.get("tool_calls") or []
        try:
            has_evidence = bool(len(evidence))
        except TypeError:
            has_evidence = False
        if not has_evidence:
            return True
    return False


def failure_profile(
    store: EventStore,
    bot_id: str,
    goal_slug: str,
) -> Dict[str, Any]:
    """Aggregate the failure history for (bot_id, goal_slug) by replaying the
    append-only event stream — pure function of the log, no new state.

    Counts terminal leaf events whose payload carries this bot and whose goal
    replays to the same slug (``civ.leaf.failed`` always; ``civ.leaf.completed``
    only when revalidated ``status='insufficient'`` — ADR-021 treats an
    insufficient DONE as a light failure). ``last_strategy`` comes from the most
    recent ``civ.delegate.recovery`` suggestion for the same pair.
    """
    count = 0
    last_status: Optional[str] = None
    last_strategy: Optional[str] = None
    for event in store.get_all():
        payload = event.payload or {}
        name = event.name
        if name == _FAILED:
            if payload.get("bot_id") != bot_id:
                continue
            if _goal_slug(payload.get("goal") or payload.get("leaf_id", "")) != goal_slug:
                # failed payloads may lack the goal (timeout path); fall back to
                # the routed event's goal via correlation id.
                if not _correlation_matches_goal(store, payload, goal_slug):
                    continue
            count += 1
            last_status = "failed"
        elif name == _COMPLETED:
            if payload.get("bot_id") != bot_id or payload.get("status") != "insufficient":
                continue
            if _goal_slug(payload.get("goal") or payload.get("leaf_id", "")) != goal_slug:
                continue
            count += 1
            last_status = "insufficient"
        elif name == _RECOVERY:
            if payload.get("bot_id") != bot_id:
                continue
            if _goal_slug(payload.get("goal", "")) != goal_slug:
                continue
            last_strategy = str(payload.get("strategy", last_strategy or "")) or last_strategy
    return {"count": count, "last_status": last_status, "last_strategy": last_strategy}


def _correlation_matches_goal(store: EventStore, payload: Dict[str, Any], goal_slug: str) -> bool:
    """Resolve a goal-less failed event's slug from its routed/created twin."""
    leaf_id = payload.get("leaf_id")
    if not leaf_id:
        return False
    for event in store.get_all(_ROUTED):
        ev_payload = event.payload or {}
        if ev_payload.get("leaf_id") == leaf_id:
            return _goal_slug(ev_payload.get("goal", "")) == goal_slug
    return False


def _next_strategy(
    attempt: int,
    *,
    insufficient: bool,
    store: EventStore,
    task: Dict[str, Any],
    policy: RecoveryPolicy,
) -> tuple[RecoveryStrategy, str, Optional[str]]:
    """Pick the ladder strategy for ``attempt`` (1-based), honouring the
    enabled-strategy filter and the fail-closed ceiling.

    Returns (strategy, reason, suggested_bot). REASSIGN consults
    SocietyManager.select_specialists when a better-reputed specialist exists
    in the task's domain; CREATE_WORKER relies on the constitution-guarded
    auto-create already in route_task (no duplicated logic).
    """
    if policy.halt_on_max_retries and attempt >= policy.max_retries:
        return RecoveryStrategy.HALT, "max_retries_exceeded", None

    ladder = _INSUFFICIENT_LADDER if insufficient else _FAILURE_LADDER
    enabled = policy.enabled_strategies
    chain = [s for s in ladder if enabled is None or s in enabled]
    if not chain:
        return RecoveryStrategy.HALT, "no_enabled_strategies", None
    start = min(attempt, len(chain)) - 1

    suggested_bot: Optional[str] = None
    reason = "insufficient_result" if insufficient else "delegate_failure"
    for strategy in chain[start:]:
        if strategy is RecoveryStrategy.REASSIGN:
            suggested_bot = _best_specialist(store, task)
            if suggested_bot is None:
                # No concrete target to name — escalate to the next strategy
                # instead of suggesting a reassignment nobody can execute.
                continue
            reason = "reassign_to_higher_reputation"
        return strategy, reason, suggested_bot
    # Every remaining option needed a target we could not name: still name the
    # last enabled strategy (taxonomy is informative), just without a bot.
    return chain[-1], reason, None


def _best_specialist(store: EventStore, task: Dict[str, Any]) -> Optional[str]:
    """Rank active bots for the task domain via SocietyManager.select_specialists.

    Returns a bot other than the failing one when one exists (the reassign
    target); None when the lookup fails or no alternative is available —
    REASSIGN is then still named as the strategy, just without a concrete bot.
    """
    failing = task.get("bot_id")
    try:
        manager = BotSpecManager(store)
        candidates = sorted(_active_bots(manager))
        if not candidates:
            return None
        domain = task.get("domain") or "general"
        ranked = SocietyManager(store).select_specialists(
            candidates, domain, count=len(candidates),
        )
        for bot_id, _score in ranked:
            if bot_id != failing:
                return bot_id
    except Exception as exc:
        logger.debug("REASSIGN specialist lookup failed: %s", exc)
    return None


def suggest_recovery(
    task: Dict[str, Any],
    result: Dict[str, Any],
    *,
    store: Optional[EventStore] = None,
    policy: Optional[RecoveryPolicy] = None,
) -> Optional[Dict[str, Any]]:
    """Suggest (never auto-execute) a recovery strategy for a failed or
    insufficient delegation and persist it as ``civ.delegate.recovery``.

    Called by delegate_tool AFTER record_task_result on the failure/timeout
    paths; the suggestion is attached to the parent-visible failure entry as
    ``civilization.recovery`` — informational, the parent decides. Returns None
    when recovery is disabled (HAOS_CIV_RECOVERY=0), the task is unrouted, or
    the result is a healthy success. Failures here are swallowed: recovery must
    never break the delegation path.
    """
    try:
        if not _recovery_enabled():
            return None
        if not isinstance(task, dict) or not isinstance(result, dict):
            return None
        bot_id = task.get("bot_id")
        leaf_id = task.get("leaf_id")
        if not bot_id or not leaf_id:
            return None

        status = str(result.get("status", "completed"))
        insufficient = status == "completed" and _is_insufficient_result(result)
        if status == "completed" and not insufficient:
            return None  # healthy success: nothing to recover

        store = store or _store()
        pol = policy or _DEFAULT_RECOVERY_POLICY
        goal_slug = _goal_slug(task.get("goal", ""))
        profile = failure_profile(store, bot_id, goal_slug)
        # suggest_recovery runs AFTER record_task_result, so the current
        # failure is already in the replayed count: attempt == count. The max()
        # guards a direct call made before any terminal event was logged.
        attempt = max(profile["count"], 1)

        strategy, reason, suggested_bot = _next_strategy(
            attempt, insufficient=insufficient, store=store, task=task, policy=pol,
        )
        payload = {
            "leaf_id": leaf_id,
            "bot_id": bot_id,
            "goal": task.get("goal", ""),
            "attempt": attempt,
            "strategy": strategy.value,
            "reason": reason,
            "failure_count": profile["count"],
            "max_retries": pol.max_retries,
            "trigger_status": "insufficient" if insufficient else status,
            "created_at": time.time(),
        }
        if suggested_bot:
            payload["suggested_bot"] = suggested_bot
        store.append(Event(name=_RECOVERY, payload=payload, correlation_id=leaf_id))
        return payload
    except Exception as exc:
        logger.debug("Recovery suggestion failed (ignored): %s", exc)
        return None

# Explicit domain mapping for explainable specialist routing.
_DOMAINS = {
    "security-bot": (
        "security", "permission", "credential", "secret", "firewall",
        "tls", "https", "auth", "vulnerability", "audit", "token", "crl",
    ),
    "architect-bot": (
        "architecture", "architect", "cluster", "topology", "storage",
        "database", "port", "service", "reliability", "deployment",
        "systemd", "api", "network", "mesh", "proxy",
    ),
}


def _store() -> EventStore:
    return EventStore(str(default_event_store_path()))


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:3]
    return "-".join(words) or "general"


def _active_bots(manager: BotSpecManager) -> set[str]:
    return {b.id for b in manager.list() if not manager.is_paused(b.id)}


def _create_bot(manager: BotSpecManager, identity_manager: IdentityManager, domain: str, goal: str) -> str:
    base = f"{_slug(domain)}-bot"
    bot_id = base
    suffix = 2
    while manager.get(bot_id) is not None:
        bot_id = f"{base}-{suffix}"
        suffix += 1
    name = " ".join(x.capitalize() for x in bot_id.split("-"))
    description = f"Autogenerated Civilization specialist for {domain}."
    soul = (
        f"You are {name}, a bounded HAOS Civilization specialist for {domain}.\n"
        "Work only within the assigned mission, state uncertainty, and return verifiable evidence."
    )
    identity = f"Role: {description}\nInitial mission: {goal}"
    values = "Evidence before confidence; least privilege; reversible changes; explicit limitations."
    bundle = BotIdentityBundle(
        bot_id=bot_id,
        soul=soul,
        identity=identity,
        values=values,
        metadata={"created_by": "civ-auto-router", "domain": domain},
    )
    spec = BotSpec(
        id=bot_id,
        name=name,
        description=description,
        capabilities=[domain],
        identity=BotIdentitySpec(),
    )
    manager.register(spec)
    identity_manager.create_version(bot_id, bundle, activate=True)
    return bot_id


def create_bot(
    bot_id: str,
    domain: str,
    *,
    description: str = "",
    soul: str = "",
    values: str = "",
    event_store: Optional[EventStore] = None,
) -> str:
    """Register a requested specialist; duplicates and malformed IDs fail closed."""
    if not isinstance(bot_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", bot_id):
        raise ValueError("bot_id must contain 2-63 lowercase letters, digits or hyphens")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain is required")
    store = event_store or _store()
    manager = BotSpecManager(store)
    if manager.get(bot_id):
        raise ValueError(f"Bot already exists: {bot_id}")
    name = " ".join(x.capitalize() for x in bot_id.split("-"))
    mission = description.strip() or f"Specialist for {domain.strip()}."
    bundle = BotIdentityBundle(
        bot_id=bot_id,
        soul=soul.strip() or f"You are {name}. Work within the {domain.strip()} domain.",
        identity=f"Role: {mission}",
        values=values.strip() or "Evidence before confidence; least privilege.",
        metadata={"created_by": "civ-operator", "domain": domain.strip()},
    )
    manager.register(
        BotSpec(
            id=bot_id,
            name=name,
            description=mission,
            capabilities=[domain.strip()],
            identity=BotIdentitySpec(),
        )
    )
    IdentityManager(store).create_version(bot_id, bundle, activate=True)
    return bot_id


def route_task(
    task: Dict[str, Any],
    *,
    event_store: Optional[EventStore] = None,
) -> Dict[str, Any]:
    """Assign a Civilization bot, enforce constitutional policy, build leaf snapshot, and persist routing."""
    # Master kill-switch: with HAOS_CIV_ENABLED=0 the civilization layer is a
    # no-op passthrough (no bots created, no events written, no policy applied).
    from .feature_flags import get_feature_flags
    if not get_feature_flags().civ_enabled:
        return task
    store = event_store or _store()
    manager, identities = BotSpecManager(store), IdentityManager(store)
    civ_mgr = CivilizationManager(store)

    explicit = task.get("bot_id")
    available = _active_bots(manager)
    task_domain = "general"

    # Resolve the target bot WITHOUT creating anything yet, so a constitution
    # denial cannot leave behind an orphan auto-created specialist bot.
    if explicit:
        if explicit not in available:
            raise ValueError(f"Civilization bot is not registered or is paused: {explicit}")
        bot_id, reason = explicit, "explicit"
        spec = manager.get(bot_id)
        if spec and spec.capabilities:
            task_domain = spec.capabilities[0]
    else:
        text = f"{task.get('goal', '')} {task.get('context', '')}".lower()
        candidates = [bid for bid, words in _DOMAINS.items() if bid in available and any(w in text for w in words)]
        if candidates:
            bot_id, reason = candidates[0], "capability-match"
            task_domain = "security" if "security" in bot_id else "architecture"
        else:
            bot_id, reason = None, "auto-created-specialist"
            task_domain = _slug(str(task.get("goal", "general")))

    # 1. Constitution & Policy Evaluation (Tier 4) — BEFORE any bot creation.
    # A HARD_DENY / approval-required task must be blocked before we materialize
    # an auto-created specialist, otherwise the denial leaks a junk bot into the
    # civilization registry.
    action = task.get("action") or "delegate_task"
    resource = task.get("resource") or str(task.get("goal", ""))
    policy_subject = bot_id or f"pending-{task_domain}"
    provisional_leaf_id = task.get("leaf_id") or f"shadow-{policy_subject}-{uuid.uuid4().hex[:10]}"
    decision = civ_mgr.evaluate_policy(subject_bot=policy_subject, action=action, resource=resource)
    if decision.result == POLICY_RESULT_DENY:
        fail_payload = {
            "leaf_id": provisional_leaf_id,
            "bot_id": bot_id,
            "status": "denied",
            "reason": f"denied_by_constitution:{decision.rule_id}",
            "details": decision.reason,
            "timestamp": time.time(),
        }
        store.append(Event(name=_FAILED, payload=fail_payload, correlation_id=provisional_leaf_id))
        raise PermissionError(f"Task blocked by Constitution rule '{decision.rule_id}': {decision.reason}")

    if decision.result == POLICY_RESULT_REQUIRE_APPROVAL and not task.get("approved"):
        fail_payload = {
            "leaf_id": provisional_leaf_id,
            "bot_id": bot_id,
            "status": "requires_approval",
            "reason": f"approval_required_by_constitution:{decision.rule_id}",
            "details": decision.reason,
            "timestamp": time.time(),
        }
        store.append(Event(name=_FAILED, payload=fail_payload, correlation_id=provisional_leaf_id))
        raise PermissionError(f"Task requires explicit approval under rule '{decision.rule_id}': {decision.reason}")

    # Cleared by the constitution: now materialize the auto-created specialist.
    if bot_id is None:
        bot_id = _create_bot(manager, identities, task_domain, str(task.get("goal", "")))

    leaf_id = task.get("leaf_id") or f"shadow-{bot_id}-{uuid.uuid4().hex[:10]}"
    version = identities.get_active_version(bot_id)

    # 2. Institutional Knowledge Query & Enrichment (Tier 4)
    try:
        assertions = civ_mgr.query_knowledge()
        if assertions:
            goal_lower = str(task.get("goal", "")).lower()
            relevant = [
                a for a in assertions
                if a.subject.lower() in goal_lower
                or a.predicate.lower() in goal_lower
                or any(w in a.object.lower() for w in goal_lower.split()[:4] if len(w) > 3)
            ]
            if relevant:
                kn_text = "\n".join(f"- [{a.subject}] {a.predicate}: {a.object}" for a in relevant[:3])
                existing_ctx = task.get("context") or ""
                task["context"] = f"{existing_ctx}\nInstitutional Knowledge:\n{kn_text}".strip()
    except Exception as exc:
        logger.debug("Failed querying institutional knowledge: %s", exc)

    # 3. Leaf Protocol Snapshot & Council Session Integration (Tier 1 & Tier 2)
    leaf_snapshot = None
    if version:
        try:
            council_id = task.get("council_id")
            council_session_id = task.get("council_session_id")
            constraints = task.get("constraints") or []
            if isinstance(constraints, str):
                constraints = [constraints]
            leaf_snapshot = create_leaf_identity_snapshot(
                leaf_id=leaf_id,
                parent_bot_id=bot_id,
                identity_version=version,
                task_description=str(task.get("goal", "")),
                constraints=constraints,
                council_id=council_id,
                council_session_id=council_session_id,
            )
            task["leaf_snapshot"] = leaf_snapshot.to_dict()
        except Exception as exc:
            logger.debug("Failed creating leaf identity snapshot: %s", exc)

    task["bot_id"] = bot_id
    task["leaf_id"] = leaf_id
    task["domain"] = task_domain
    task["identity_version"] = version.version if version else None

    payload = {
        "leaf_id": leaf_id,
        "bot_id": bot_id,
        "domain": task_domain,
        "identity_version": task["identity_version"],
        "goal": task.get("goal", ""),
        "reason": reason,
        "created_at": time.time(),
    }
    if leaf_snapshot is not None:
        payload["snapshot"] = {
            "identity_version_id": leaf_snapshot.identity_version_id,
            "temporary_soul_hash": leaf_snapshot.temporary_soul_hash,
            "council_id": leaf_snapshot.council_id,
            "council_session_id": leaf_snapshot.council_session_id,
        }

    store.append(Event(name=_ROUTED, payload=payload, correlation_id=leaf_id))
    store.append(Event(name=_CREATED, payload=payload, correlation_id=leaf_id))
    return task


def record_task_result(
    task: Dict[str, Any],
    result: Dict[str, Any],
    *,
    event_store: Optional[EventStore] = None,
) -> None:
    """Record execution completion/failure, update society reputation, and capture evolution experience."""
    store = event_store or _store()
    status = str(result.get("status", "completed"))
    success = (status == "completed")
    name = _COMPLETED if success else _FAILED

    leaf_id = task.get("leaf_id")
    bot_id = task.get("bot_id")
    summary = str(result.get("summary") or "")

    # Unrouted tasks (graceful degradation when civ is off/failed) must not
    # pollute the canonical stream with null-id terminal events.
    if not bot_id or not leaf_id:
        return

    # ADR-021 revalidation: a declared DONE whose summary is empty, carries the
    # '[insufficient' marker, or is a stub without tool evidence is recorded as
    # status='insufficient'. Deliberate choice: the leaf event KEEPS its
    # completed name (no downgrade to civ.leaf.failed) so existing e2e consumers
    # of the terminal-event vocabulary do not break; only the payload status and
    # the reputation delta change (neutral 0.0 instead of +0.1). With
    # HAOS_CIV_RECOVERY=0 this whole block is inert — byte-identical pre-ADR.
    insufficient = success and _recovery_enabled() and _is_insufficient_result(result)
    event_status = "insufficient" if insufficient else status

    store.append(
        Event(
            name=name,
            payload={
                "leaf_id": leaf_id,
                "bot_id": bot_id,
                "status": event_status,
                "summary": summary,
                "goal": task.get("goal", ""),
                "completed_at": time.time(),
            },
            correlation_id=leaf_id,
        )
    )

    domain = task.get("domain") or "general"

    # 1. Society Reputation Event (Tier 2)
    try:
        society_mgr = SocietyManager(store)
        parent_bot_id = task.get("parent_bot_id")
        actor = parent_bot_id if (parent_bot_id and parent_bot_id != bot_id) else None
        if insufficient:
            delta = 0.0
            outcome = "neutral"
        else:
            delta = 0.1 if success else -0.15
            outcome = "success" if success else "failure"

        society_mgr.record_reputation(
            subject_bot=bot_id,
            domain=domain,
            evidence_ref=leaf_id,
            outcome=outcome,
            delta_hint=delta,
            actor_bot=actor,
        )
    except Exception as exc:
        logger.debug("Failed recording society reputation: %s", exc)

    # 2. Evolution Experience Ingestion (Tier 3)
    try:
        evo_mgr = BotEvolutionManager(store)
        evo_mgr.record_experience(
            bot_id=bot_id,
            event_type="mission_completed" if success else "mission_failed",
            domain=domain,
            summary=summary or f"Task execution {status}",
            success=success,
            payload={
                "leaf_id": leaf_id,
                "goal": task.get("goal"),
                "status": status,
                "council_id": task.get("council_id"),
            },
        )
    except Exception as exc:
        logger.debug("Failed recording evolution experience: %s", exc)

    # 3. Auto-evolution & Canary Evaluation (Tier 3+)
    try:
        from .auto_evolution import evaluate_canary_health, check_and_trigger_auto_evolution
        evaluate_canary_health(bot_id, event_store=store)
        check_and_trigger_auto_evolution(bot_id, event_store=store)
    except Exception as exc:
        logger.debug("Failed auto-evolution or canary evaluation: %s", exc)


# ---------------------------------------------------------------------------
# Promotion-route hardening (audit B1 / M5 / Council-fachada)
# ---------------------------------------------------------------------------

# M5: identity fields must not grow unboundedly by repeated appends. A patch
# that would push a field past the cap fails closed and demands an explicit
# replacement instead of silent accumulation.
_IDENTITY_FIELD_CAP = 8192
_REPLACE_MARKER = "[[REPLACE]]"

# B1: risk classes that can NEVER be promoted on a self-declared evaluation —
# they must pass through the live HoldoutPromotionGate.
_GATE_REQUIRED_RISKS = (RISK_HIGH, RISK_IDENTITY_CRITICAL)


def _apply_identity_patch(old_text: str, patch: Optional[str], field_name: str) -> str:
    """Apply one proposal patch to a bundle field with a hard size cap (M5).

    Default semantics is append. When the result would exceed the cap, the
    caller must opt into replacement by prefixing the patch with the
    [[REPLACE]] marker — an append that silently overflows is exactly the
    unbounded-growth defect this closes.
    """
    if not patch:
        return old_text
    if patch.lstrip().startswith(_REPLACE_MARKER):
        new_text = patch.lstrip()[len(_REPLACE_MARKER):].strip()
    else:
        new_text = f"{old_text}\n{patch}"
    if len(new_text) > _IDENTITY_FIELD_CAP:
        raise ValueError(
            f"identity field '{field_name}' would exceed the {_IDENTITY_FIELD_CAP}-char cap "
            f"(result: {len(new_text)} chars). Append growth is closed: prefix the patch with "
            f"{_REPLACE_MARKER} to explicitly replace the field instead of appending."
        )
    return new_text


def _normalize_evaluation(evaluation: Any) -> Dict[str, Any]:
    """Coerce a dict/HoldoutVerdict evaluation into a plain metadata dict."""
    if isinstance(evaluation, dict):
        info = {k: evaluation[k] for k in ("accepted", "reason", "evidence") if k in evaluation}
        info["accepted"] = bool(evaluation.get("accepted", False))
    else:
        info = {
            "accepted": bool(getattr(evaluation, "accepted", False)),
            "reason": str(getattr(evaluation, "reason", "")),
        }
    info.setdefault("reason", "")
    return info


def deliberate_and_promote_proposal(
    proposal_id: str,
    *,
    council_id: Optional[str] = None,
    decision_summary: str = "",
    approver: Optional[str] = None,
    evaluation: Any = None,
    holdout_gate: Optional[HoldoutPromotionGate] = None,
    baseline_root: Optional[Path] = None,
    candidate_root: Optional[Path] = None,
    event_store: Optional[EventStore] = None,
    member_executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Promote an evolution proposal to an immutable IdentityVersion — gated.

    Hardening contract (audit B1/M5/Council-fachada):
    1. ``approver`` must be a named human; empty/blank fails with PermissionError.
    2. High / identity-critical risk proposals — or any promotion without an
       ``evaluation`` — must pass the fail-closed HoldoutPromotionGate. A
       rejection (including "nothing was measured") raises PermissionError.
       Low-risk promotions may cite an accepted evaluation verdict (dict or
       HoldoutVerdict) instead of re-running the gate.
    3. Bundle fields are capped (see ``_apply_identity_patch``); overflow
       requires an explicit [[REPLACE]] patch.
    4. Provenance is honest by construction:
       - RISK_HIGH / RISK_IDENTITY_CRITICAL (and no gate-off override): the
         council conducts a REAL deliberation (one round, conservative budget)
         via ``hermes.platform.council.promotion_executor`` using an injected
         ``member_executor`` (LeafExecutor contract) or the configured HAOS
         model. A non-APPROVED verdict raises PermissionError with the
         decision_id and votes; any executor failure FAILS CLOSED — promotion
         is refused, never degraded to the facade. The DecisionRecord is
         stamped ``deliberation="real"``.
       - Low/medium risk (or HAOS_CIV_REAL_DELIBERATION=0): the Council path
         stays a declared facade (members auto-approve, WARNING/INFO logged,
         DecisionRecord stamped ``deliberation="facade"`` with
         ``ratification: automated`` metadata) so the facade can never
         masquerade as real deliberation in the audit trail.
       - Runbook for wiring a real MemberRunner executor: see the docstring
         of ``hermes/platform/council/debate_runner.py``.
    """
    store = event_store or _store()
    evo_mgr = BotEvolutionManager(store)
    id_mgr = IdentityManager(store)
    council_mgr = CouncilManager(store)

    proposals = {p.id: p for p in evo_mgr.get_proposals()}
    if proposal_id not in proposals:
        raise KeyError(f"Proposal {proposal_id} not found")

    proposal = proposals[proposal_id]
    bot_id = proposal.bot_id
    active_version = id_mgr.get_active_version(bot_id)
    if not active_version or not active_version.bundle:
        raise ValueError(f"No active IdentityVersion for bot {bot_id}")

    # --- Gate 1 (B1): named human approver, fail closed ---------------------
    if not isinstance(approver, str) or not approver.strip():
        raise PermissionError(
            "promotion requires named human approver: deliberate_and_promote_proposal "
            "must be invoked with approver=<operator> for every identity promotion."
        )
    approver = approver.strip()

    # --- Gate 2 (B1): holdout evaluation, fail closed -----------------------
    if proposal.risk_class in _GATE_REQUIRED_RISKS or evaluation is None:
        gate = holdout_gate or default_holdout_gate()
        verdict = gate.evaluate(baseline_root, candidate_root)
        gate_info = (
            verdict.to_dict()
            if hasattr(verdict, "to_dict")
            else _normalize_evaluation(verdict)
        )
        gate_info["source"] = "holdout_gate"
        if not gate_info.get("accepted"):
            raise PermissionError(
                f"promotion refused by HoldoutPromotionGate (fail-closed): "
                f"{gate_info.get('reason', 'no reason given')}"
            )
    else:
        gate_info = _normalize_evaluation(evaluation)
        gate_info["source"] = "provided-evaluation"
        if not gate_info["accepted"]:
            raise PermissionError(
                f"promotion refused: evaluation not accepted: {gate_info.get('reason', '')}"
            )

    # --- Gate 3 (M5): build the new bundle BEFORE any state mutation ---------
    old_bundle = active_version.bundle
    new_soul = _apply_identity_patch(old_bundle.soul, proposal.proposed_soul_patch, "soul")
    new_identity = _apply_identity_patch(old_bundle.identity, proposal.proposed_identity_patch, "identity")
    new_values = _apply_identity_patch(old_bundle.values, proposal.proposed_values_patch, "values")

    # --- Gate 4 (Council-fachada follow-up): deliberation provenance ---------
    # High / identity-critical risk on the human route is decided by a REAL
    # council deliberation (unless HAOS_CIV_REAL_DELIBERATION=0). Everything
    # else keeps the declared facade exactly as before.
    decision_id = None
    deliberation = None
    council_session_id = None
    deliberation_cost: Dict[str, Any] = {}
    if proposal.risk_class in _GATE_REQUIRED_RISKS and real_deliberation_enabled():
        try:
            verdict = run_promotion_deliberation(
                council_mgr=council_mgr,
                store=store,
                proposal=proposal,
                bot_id=bot_id,
                approver=approver,
                council_id=council_id,
                decision_summary=decision_summary,
                member_executor=member_executor,
                identity_mgr=id_mgr,
            )
        except PromotionDeliberationError as exc:
            # Fail-closed, inegociável: recusa a promoção, nunca degrada para fachada.
            raise PermissionError(
                f"promotion refused: real deliberation unavailable; refusing to "
                f"fall back to facade: {exc}"
            ) from exc
        decision_id = verdict["decision_id"]
        council_session_id = verdict["session_id"]
        deliberation = "real"
        deliberation_cost = {
            "tokens_used": verdict["tokens_used"],
            "cost_usd": verdict["cost_usd"],
            "council_id": verdict["council_id"],
        }
        if not verdict["approved"]:
            raise PermissionError(
                f"promotion refused by real council deliberation (fail-closed): "
                f"decision_id={decision_id} session_id={council_session_id} "
                f"decision={verdict['decision']!r} votes={verdict['votes']}"
            )
    elif council_id:
        if proposal.risk_class in _GATE_REQUIRED_RISKS:
            logger.info(
                "HAOS_CIV_REAL_DELIBERATION disabled: high-risk proposal %s "
                "promoted via declared facade (council %s), not real deliberation.",
                proposal_id, council_id,
            )
        council = council_mgr.get(council_id)
        if council:
            session = council_mgr.start_session(
                council_id=council_id,
                objective=f"Deliberate evolution proposal {proposal_id} for bot {bot_id}",
            )
            for m in session.members:
                council_mgr.submit_position(
                    session_id=session.session_id,
                    bot_id=m,
                    position={
                        "vote": "APPROVE",
                        "proposal_id": proposal_id,
                        # Council-fachada: this vote is scripted, not deliberated.
                        "ratification": "automated",
                        "approver": approver,
                    },
                )
            decision = council_mgr.record_decision(
                session_id=session.session_id,
                synthesis=decision_summary or f"Council approved evolution proposal {proposal_id}",
                decision="APPROVED",
                confidence=1.0,
                action_refs=[proposal_id],
                # Explicit provenance: no model ever saw this proposal.
                # "facade" is also the fail-closed default in record_decision,
                # but stating it here keeps the audit intent unmissable.
                deliberation="facade",
            )
            decision_id = decision.id
            council_session_id = session.session_id
            deliberation = "facade"

    # Advance proposal status through the governance FSM (audit round-2 seam):
    # draft -> review -> approved -> canary -> promoted. The governed human
    # route walks the full chain atomically so every transition is audited;
    # soul patches require explicit_soul_change=True plus the named approver
    # (never auto-approved), and high/identity-critical risk requires the
    # approver at each gate transition (enforced by BotEvolutionManager).
    evo_mgr.update_status(proposal_id, STATUS_REVIEW, active_version.bundle_hash)
    evo_mgr.update_status(
        proposal_id,
        STATUS_APPROVED,
        active_version.bundle_hash,
        approver=approver,
        explicit_soul_change=bool(proposal.proposed_soul_patch),
    )
    evo_mgr.update_status(proposal_id, STATUS_CANARY, active_version.bundle_hash, approver=approver)
    evo_mgr.update_status(proposal_id, STATUS_PROMOTED, active_version.bundle_hash, approver=approver)

    new_bundle = BotIdentityBundle(
        bot_id=bot_id,
        soul=new_soul,
        identity=new_identity,
        values=new_values,
        metadata={
            "promoted_from_proposal": proposal_id,
            "council_decision_id": decision_id,
            "promoted_at": time.time(),
            "approver": approver,
            "promotion_gate": gate_info,
            "deliberation": deliberation or "none",
            "ratification": (
                "council-deliberated" if deliberation == "real"
                else "automated" if decision_id
                else "direct-human"
            ),
            **({"deliberation_cost": deliberation_cost} if deliberation_cost else {}),
        },
    )

    new_version = id_mgr.create_version(
        bot_id=bot_id,
        bundle=new_bundle,
        parent_id=active_version.id,
        activate=True,
    )

    return {
        "proposal_id": proposal_id,
        "bot_id": bot_id,
        "approver": approver,
        "council_decision_id": decision_id,
        "council_session_id": council_session_id,
        "deliberation": deliberation or "none",
        "new_version_id": new_version.id,
        "new_version_number": new_version.version,
        "bundle_hash": new_version.bundle_hash,
    }


def rollback_bot_identity(
    bot_id: str,
    target_version_id: str,
    *,
    reason: str = "Operator rollback",
    proposal_id: Optional[str] = None,
    event_store: Optional[EventStore] = None,
) -> Dict[str, Any]:
    """Roll back a bot identity to a previous version using a compensating version.

    B1-low: the evolution proposal that produced the rolled-back version is
    marked ``rolled_back`` (explicit ``proposal_id``, or discovered from the
    pre-rollback active version's ``promoted_from_proposal`` metadata).
    """
    store = event_store or _store()
    id_mgr = IdentityManager(store)
    evo_mgr = BotEvolutionManager(store)

    # Discover the originating proposal from the version being abandoned.
    pre_active = id_mgr.get_active_version(bot_id)
    linked_proposal_id = proposal_id or (
        (pre_active.bundle.metadata or {}).get("promoted_from_proposal")
        if pre_active and pre_active.bundle
        else None
    )

    rolled_version = id_mgr.rollback(bot_id, target_version_id=target_version_id, reason=reason)

    rolled_back_proposal_id: Optional[str] = None
    if linked_proposal_id:
        known = {p.id: p for p in evo_mgr.get_proposals()}
        target = known.get(linked_proposal_id)
        if target is not None and target.status != STATUS_ROLLED_BACK:
            try:
                evo_mgr.update_status(
                    linked_proposal_id, STATUS_ROLLED_BACK, rolled_version.bundle_hash
                )
                rolled_back_proposal_id = linked_proposal_id
            except EvolutionError as exc:
                logger.warning(
                    "Rollback of bot %s could not mark proposal %s rolled_back: %s",
                    bot_id, linked_proposal_id, exc,
                )

    return {
        "bot_id": bot_id,
        "restored_from": target_version_id,
        "new_compensatory_version_id": rolled_version.id,
        "version_number": rolled_version.version,
        "rolled_back_proposal_id": rolled_back_proposal_id,
    }


def reconcile_civilization_state(
    *,
    base_repo_dir: Optional[Path] = None,
    shadows_root: Optional[Path] = None,
    event_store: Optional[EventStore] = None,
    max_age_seconds: float = 3600.0,
) -> Dict[str, Any]:
    """Scan and reconcile orphaned leaves, crashed workers, and verify state consistency."""
    from hermes.platform.shadow_leaf import ShadowLeafManager

    store = event_store or _store()
    repo_dir = base_repo_dir or Path.cwd()
    leaf_mgr = ShadowLeafManager(
        base_repo_dir=repo_dir,
        shadows_root=shadows_root,
        event_store=store,
    )
    reconciled_leaves = leaf_mgr.reconcile_orphaned_leaves(max_age_seconds=max_age_seconds)

    bot_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    bots = bot_mgr.list()
    active_versions = {b.id: (id_mgr.get_active_version(b.id).version if id_mgr.get_active_version(b.id) else None) for b in bots}

    return {
        "reconciled_orphaned_leaves": reconciled_leaves,
        "total_active_bots": len(bots),
        "bot_versions": active_versions,
        "timestamp": time.time(),
    }
