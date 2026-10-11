"""Canonical specification and identity for Civilization Research bot."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotPolicy, BotSpec, TriggerSpec
from hermes.platform.observability.event_store import EventStore
from hermes.platform.research.temporal_engine import (
    TemporalResearchResult,
    execute_temporal_research,
)

RESEARCH_BOT_ID = "research-bot"
RESEARCH_BOT_NAME = "Research"
RESEARCH_BOT_DOMAIN = "temporal-research"
RESEARCH_CAPABILITIES = ["temporal-research", "web-search"]

RESEARCH_SOUL = """You are Research, a bounded HAOS Civilization specialist bot for temporal research and verification.
You operate within the Civilization subsystem and consume the shared temporal research engine.
You strictly enforce the HAOS Temporal Research Contract:
- Always distinguish in-window findings from historical context.
- Never fabricate dates; unanchored sources remain unverified.
- Provide explicit UTC window boundaries and cutoff timestamps.
- Highlight conflicts, evidence gaps, and channel coverage status.
"""

RESEARCH_IDENTITY = """Role: Specialist Civilization bot for temporal research and recent event synthesis.
Subsystem: HAOS Civilization
Capabilities: temporal-research, web-search
Consumes: hermes.platform.research.temporal_engine and skill temporal-research
Values: Grounded citations, uncompromised temporal honesty, fail-closed verification.
"""

RESEARCH_VALUES = """Evidence before confidence; verifiable citations over assertions; explicit limitations; no fabricated dates."""


def get_research_bot_spec() -> BotSpec:
    """Return canonical BotSpec for the Research bot."""
    return BotSpec(
        id=RESEARCH_BOT_ID,
        name=RESEARCH_BOT_NAME,
        description="Civilization specialist bot for temporal research and recent event synthesis.",
        capabilities=list(RESEARCH_CAPABILITIES),
        task_defaults={
            "posture": "researcher",
            "task_class": "research",
            "required_capabilities": list(RESEARCH_CAPABILITIES),
        },
        policy=BotPolicy(
            tools=["web_search", "web_extract", "browse"],
            workspace="git_worktree",
            max_cost_usd=5.0,
            max_runtime_minutes=15,
        ),
        routines={
            "monthly-sweep": {
                "version": 1,
                "task_defaults": {
                    "goal": "Execute temporal research sweep over the past 30 days",
                    "window_spec": "last30days",
                    "priority": 70,
                },
                "prompt": "Run monthly temporal research sweep using temporal_engine",
            },
            "weekly-sweep": {
                "version": 1,
                "task_defaults": {
                    "goal": "Execute temporal research sweep over the past 7 days",
                    "window_spec": "last7days",
                    "priority": 75,
                },
                "prompt": "Run weekly temporal research sweep using temporal_engine",
            },
        },
        triggers=[
            TriggerSpec(
                id="manual-sweep",
                type="manual",
                name="Manual temporal research trigger",
            )
        ],
        identity=BotIdentitySpec(),
    )


def get_research_bot_identity_bundle() -> BotIdentityBundle:
    """Return canonical BotIdentityBundle for the Research bot."""
    return BotIdentityBundle(
        bot_id=RESEARCH_BOT_ID,
        soul=RESEARCH_SOUL,
        identity=RESEARCH_IDENTITY,
        values=RESEARCH_VALUES,
        metadata={
            "created_by": "civ-builtin",
            "domain": RESEARCH_BOT_DOMAIN,
            "subsystem": "Civilization",
        },
    )


def register_research_bot(
    event_store: EventStore,
    *,
    activate_identity: bool = True,
) -> BotSpec:
    """Register the Research bot into BotSpecManager and IdentityManager if not present."""
    spec_mgr = BotSpecManager(event_store)
    id_mgr = IdentityManager(event_store)

    existing = spec_mgr.get(RESEARCH_BOT_ID)
    if existing:
        return existing

    spec = get_research_bot_spec()
    spec_mgr.register(spec)

    bundle = get_research_bot_identity_bundle()
    id_mgr.create_version(RESEARCH_BOT_ID, bundle, activate=activate_identity)
    return spec


def run_bot_research(
    goal: str,
    *,
    window_spec: str = "last30days",
    channels: Optional[List[str]] = None,
) -> TemporalResearchResult:
    """Invoke the shared temporal research engine on behalf of Research bot."""
    return execute_temporal_research(
        goal=goal,
        window_spec=window_spec,
        channels=channels or ["web-search"],
    )
