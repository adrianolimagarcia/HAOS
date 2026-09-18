"""The plaintext DM restart guard must know both CLI spellings.

``coerce_plaintext_gateway_command`` rewrites an exact-match DM admin phrase into ``/restart`` so
it never reaches the LLM/tool path — a self-restart from inside the running agent leaves the
gateway stuck in ``draining``, waiting on that agent. The patterns recognized ``restart hermes
gateway`` but not ``restart haos gateway``, the wording this fork's own CLI exposes, so on the
appliance the phrase fell through to exactly the path the guard exists to keep it out of.
"""

import pytest

from gateway.config import Platform
from gateway.platforms.base import coerce_plaintext_gateway_command
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource


def _event(text: str, chat_type: str = "dm") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type=chat_type),
    )


@pytest.mark.parametrize("text", [
    "restart haos gateway",
    "restart haos",
    "restart the haos gateway",
    "please restart haos gateway",
    "restart hermes gateway",
    "restart hermes",
    "restart gateway",
])
def test_a_restart_phrase_becomes_the_restart_command(text):
    event = _event(text)
    coerce_plaintext_gateway_command(event)
    assert event.text == "/restart"


@pytest.mark.parametrize("text", [
    "restart chaos gateway",
    "restart myhaos",
    "restart haosgateway",
    "restart haos gateway now",
])
def test_a_near_miss_is_left_alone(text):
    """Exact matches only — a longer sentence is a normal message, not an admin command."""
    event = _event(text)
    coerce_plaintext_gateway_command(event)
    assert event.text == text


def test_a_group_message_is_left_alone():
    """DM-only: a group member must not be able to restart the gateway by typing a phrase."""
    event = _event("restart haos gateway", chat_type="group")
    coerce_plaintext_gateway_command(event)
    assert event.text == "restart haos gateway"
