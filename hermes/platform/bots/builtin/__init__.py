"""Builtin canonical bot specifications."""

from hermes.platform.bots.builtin.generic_bot import (
    GENERIC_BOT_ID,
    GENERIC_BOT_NAME,
    GENERIC_BOT_DOMAIN,
    GENERIC_CAPABILITIES,
    get_generic_bot_spec,
    get_generic_bot_identity_bundle,
    register_generic_bot,
)
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
    "GENERIC_BOT_ID",
    "GENERIC_BOT_NAME",
    "GENERIC_BOT_DOMAIN",
    "GENERIC_CAPABILITIES",
    "get_generic_bot_spec",
    "get_generic_bot_identity_bundle",
    "register_generic_bot",
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
    "ensure_builtin_bots",
]


def ensure_builtin_bots(event_store) -> list:
    """Idempotently register all builtin bots (research, reverse, generic).

    Safe to call on every routing pass: each register_*_bot() checks for
    existence first and returns the existing spec without writing events.
    """
    return [
        register_research_bot(event_store),
        register_reverse_bot(event_store),
        register_generic_bot(event_store),
    ]
