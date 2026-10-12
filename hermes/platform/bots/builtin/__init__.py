"""Builtin canonical bot specifications."""
from hermes.platform.bots.builtin.research_bot import (
    RESEARCH_BOT_ID,
    RESEARCH_BOT_NAME,
    RESEARCH_BOT_DOMAIN,
    RESEARCH_CAPABILITIES,
    get_research_bot_spec,
    get_research_bot_identity_bundle,
    register_research_bot,
    run_bot_research,
)
from hermes.platform.bots.builtin.reverse_bot import (
    REVERSE_BOT_ID,
    REVERSE_BOT_NAME,
    REVERSE_BOT_DOMAIN,
    REVERSE_CAPABILITIES,
    get_reverse_bot_spec,
    get_reverse_bot_identity_bundle,
    register_reverse_bot,
    run_bot_triage,
)

__all__ = [
    "RESEARCH_BOT_ID",
    "RESEARCH_BOT_NAME",
    "RESEARCH_BOT_DOMAIN",
    "RESEARCH_CAPABILITIES",
    "get_research_bot_spec",
    "get_research_bot_identity_bundle",
    "register_research_bot",
    "run_bot_research",
    "REVERSE_BOT_ID",
    "REVERSE_BOT_NAME",
    "REVERSE_BOT_DOMAIN",
    "REVERSE_CAPABILITIES",
    "get_reverse_bot_spec",
    "get_reverse_bot_identity_bundle",
    "register_reverse_bot",
    "run_bot_triage",
]
