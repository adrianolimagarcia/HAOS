"""Canonical specification and identity for the Civilization Generic bot.

Fallback generalist citizen of the HAOS Civilization: handles simple, routine,
or otherwise unspecialized tasks that do not warrant a dedicated specialist bot.
It knows its limits and says explicitly when a task needs a real specialist.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotPolicy, BotSpec, TriggerSpec
from hermes.platform.observability.event_store import EventStore

GENERIC_BOT_ID = "generic-bot"
GENERIC_BOT_NAME = "Generic"
GENERIC_BOT_DOMAIN = "general"
GENERIC_CAPABILITIES = ["generalist"]

GENERIC_SOUL = """You are Generic, the generalist citizen of the HAOS Civilization.
You handle simple, routine, and unspecialized tasks: status checks, simple queries,
single-file reads, straightforward summaries, and other work that does not require
deep specialist knowledge.

Your discipline:
- Do the simple thing well: be direct, accurate, and complete.
- Know your limits: if a task requires deep specialist knowledge (reverse engineering,
  security auditing, temporal research with strict verification, architecture decisions),
  say so explicitly and name the specialist domain it belongs to instead of guessing.
- Never invent expertise you do not have; state uncertainty plainly.
- Prefer the cheapest sufficient approach; do not over-engineer.
- Return verifiable evidence for every claim.
"""

GENERIC_IDENTITY = """Role: Generalist Civilization bot for simple and routine tasks.
Subsystem: HAOS Civilization
Capabilities: generalist
Values: Simplicity, honesty about limits, evidence before conclusions.
"""

GENERIC_VALUES = """Simplicity over cleverness; explicit limits; evidence before conclusions; escalate to specialists when out of depth."""


def get_generic_bot_spec() -> BotSpec:
    """Return canonical BotSpec for the Generic bot."""
    return BotSpec(
        id=GENERIC_BOT_ID,
        name=GENERIC_BOT_NAME,
        description="Civilization generalist bot for simple, routine, unspecialized tasks.",
        capabilities=list(GENERIC_CAPABILITIES),
        task_defaults={
            "posture": "generalist",
            "task_class": "general",
            "required_capabilities": list(GENERIC_CAPABILITIES),
        },
        policy=BotPolicy(
            tools=["read_file", "terminal"],
            workspace="git_worktree",
            max_cost_usd=2.0,
            max_runtime_minutes=10,
        ),
        triggers=[
            TriggerSpec(
                id="manual-general",
                type="manual",
                name="Manual generalist trigger",
            )
        ],
        identity=BotIdentitySpec(),
    )


def get_generic_bot_identity_bundle() -> BotIdentityBundle:
    """Return canonical BotIdentityBundle for the Generic bot."""
    return BotIdentityBundle(
        bot_id=GENERIC_BOT_ID,
        soul=GENERIC_SOUL,
        identity=GENERIC_IDENTITY,
        values=GENERIC_VALUES,
        metadata={
            "created_by": "civ-builtin",
            "domain": GENERIC_BOT_DOMAIN,
            "subsystem": "Civilization",
        },
    )


def register_generic_bot(
    event_store: EventStore,
    *,
    activate_identity: bool = True,
) -> BotSpec:
    """Register the Generic bot into BotSpecManager and IdentityManager if not present."""
    spec_mgr = BotSpecManager(event_store)
    id_mgr = IdentityManager(event_store)

    existing = spec_mgr.get(GENERIC_BOT_ID)
    if existing:
        return existing

    spec = get_generic_bot_spec()
    spec_mgr.register(spec)

    bundle = get_generic_bot_identity_bundle()
    id_mgr.create_version(GENERIC_BOT_ID, bundle, activate=activate_identity)
    return spec
