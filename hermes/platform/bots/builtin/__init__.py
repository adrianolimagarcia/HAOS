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

__all__ = [
    "RESEARCH_BOT_ID",
    "RESEARCH_BOT_NAME",
    "RESEARCH_BOT_DOMAIN",
    "RESEARCH_CAPABILITIES",
    "get_research_bot_spec",
    "get_research_bot_identity_bundle",
    "register_research_bot",
    "run_bot_research",
]
