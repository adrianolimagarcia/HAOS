"""Gateway <-> Civilization advisory bridge (Phase 1: shadow mode).

The "missing link": the gateway's inbound message flow never consults
Civilization delegation. This module is the consultation point.

PHASE 1 (this module) -- ADVISORY / SHADOW ONLY
------------------------------------------------
``suggest_specialist()`` returns a bot id (e.g. ``"reverse-bot"``) or
``None``. It is pure and side-effect-free:

- never creates bots (unlike ``route_task``'s auto-create path),
- never writes to the EventStore,
- never evaluates constitutional policy,
- never raises -- every failure degrades to ``None`` (fallback).

The gateway attaches the suggestion as event metadata
(``event._civ_suggested_bot``, same pattern as
``event._heartbeat_execution_started``) and logs it. The normal agent
flow is UNCHANGED -- this is the "shadow" stage of the rollout ladder
(shadow -> opt-in -> canary -> default-on -> full enforcement).

PHASE 2 (future, NOT implemented here): active delegation -- actually
routing the turn to the suggested specialist (e.g. switching the
turn's bot identity). That requires product decisions about UX,
authorization and rollback, and must NOT be smuggled into Phase 1.

Feature flag
------------
``HAOS_CIV_GATEWAY_ROUTING``: "1"/"0" (also accepts true/false/yes/on),
default ``"1"`` (ON).

Default ON is safe *because Phase 1 never alters behavior* -- it only
annotates turn metadata and emits a log line. The master kill-switch
``HAOS_CIV_ENABLED=0`` disables the bridge as well.

Rollback: set ``HAOS_CIV_GATEWAY_ROUTING=0`` (or ``HAOS_CIV_ENABLED=0``)
and restart the gateway. No code changes, no migrations.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger("hermes.platform.civilization.gateway_bridge")

# Env var for this bridge's own flag. Kept separate from the civilization
# master flags so the bridge can be rolled back independently.
_GATEWAY_ROUTING_ENV_VAR = "HAOS_CIV_GATEWAY_ROUTING"


def gateway_routing_enabled() -> bool:
    """Return True when the gateway bridge may consult Civilization.

    Requires BOTH the bridge flag (default ON) and the civilization master
    switch (default ON). Never raises.
    """
    try:
        raw = os.environ.get(_GATEWAY_ROUTING_ENV_VAR)
        bridge_on = True if raw is None else raw.strip().lower() in (
            "1", "true", "yes", "on",
        )
        if not bridge_on:
            return False
        from hermes.platform.civilization.feature_flags import get_feature_flags
        return bool(get_feature_flags().civ_enabled)
    except Exception:
        # Fail closed: if we cannot even read the flags, do not consult.
        return False


def gateway_active_routing_enabled(session: Optional[dict] = None) -> bool:
    """Return True when active routing of inbound gateway turns to specialists is enabled (Phase 2D).

    Requires:
    1. HAOS_CIV_GATEWAY_ROUTING != 0 (instant rollback if set to 0)
    2. Master civ_enabled is True
    3. Delegation opt-in flag HAOS_CIV_DELEGATE is True OR session['civ_delegate'] is True.
    """
    try:
        if not gateway_routing_enabled():
            return False
        from hermes.platform.civilization.delegation import civ_delegation_enabled
        return civ_delegation_enabled(session)
    except Exception:
        return False


def route_turn_to_specialist(
    agent: Any,
    bot_id: str,
    *,
    store: Optional[Any] = None,
) -> bool:
    """Route an agent instance to the specified civilization specialist bot (Phase 2D).

    Applies the bot_id and its active identity bundle to the agent.
    Returns True if successfully applied, False otherwise (fail-safe).
    """
    if agent is None or not bot_id:
        return False
    try:
        from hermes.platform.bots.identity_manager import IdentityManager
        from hermes.platform.civilization.delegation import _store

        st = store or _store()
        identities = IdentityManager(st)
        version = identities.get_active_version(bot_id)
        bundle = version.bundle if version else None

        agent.bot_id = bot_id
        if bundle:
            agent.identity_bundle = bundle

        logger.info(
            "[CIV_GATEWAY] Routed turn agent %s to specialist bot=%s (version=%s)",
            id(agent), bot_id, getattr(version, "version_id", "unknown")
        )
        return True
    except Exception as exc:
        logger.warning(
            "civ gateway active routing failed for bot %s; falling back to normal flow: %s",
            bot_id, exc, exc_info=True
        )
        return False


def _domain_table() -> Dict[str, Iterable[str]]:
    """Return the civilization domain keyword table (single source of truth).

    Imported lazily from delegation.py so this module stays import-light;
    any import failure degrades to an empty table (=> no suggestion).
    """
    try:
        from hermes.platform.civilization.delegation import _DOMAINS
        return dict(_DOMAINS)
    except Exception:
        logger.debug("gateway bridge: could not load _DOMAINS; no suggestion", exc_info=True)
        return {}


def suggest_specialist(
    message_text: Optional[str],
    *,
    available_bots: Optional[Set[str]] = None,
) -> Optional[str]:
    """Suggest a Civilization specialist bot for an inbound message.

    Pure advisory match of the message text against the civilization
    domain keyword table. Returns the matched ``bot_id`` (e.g.
    ``"reverse-bot"``) or ``None`` when nothing matches / the bridge is
    disabled / anything goes wrong.

    ``available_bots`` optionally restricts suggestions to a set of
    registered bot ids (callers with a BotSpecManager can pass the
    registry; default None means "all known domains").

    This function NEVER raises and NEVER has side effects.
    """
    try:
        if not gateway_routing_enabled():
            return None
        if not message_text or not isinstance(message_text, str):
            return None
        text = message_text.lower()
        domains = _domain_table()
        if not domains:
            return None
        for bot_id, keywords in domains.items():
            if available_bots is not None and bot_id not in available_bots:
                continue
            try:
                if any(kw in text for kw in keywords):
                    return bot_id
            except Exception:
                continue
        return None
    except Exception:
        # Absolute last resort: the bridge must never break the caller.
        return None
