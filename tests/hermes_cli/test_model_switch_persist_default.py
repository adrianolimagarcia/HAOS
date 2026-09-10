"""Tests for model-switch persistence.

Covers:
- ``parse_model_flags`` recognises ``--session`` (and keeps ``--global``).
- ``resolve_persist_behavior`` applies the config-gated default and the
  ``--session`` / ``--global`` overrides.
- HAOS persists the pick (the last model used is remembered): ``DEFAULT_CONFIG``
  ships ``model.persist_switch_by_default: true`` and an absent key defaults to
  True, so only an explicit ``--once`` / ``--session`` — or a config that sets
  the key to false — is session-only. This diverges from upstream, where the
  plain ``/model <name>`` default was session-only.
"""

from unittest.mock import patch

from hermes_cli.model_switch import parse_model_flags, resolve_persist_behavior


# ---------------------------------------------------------------------------
# parse_model_flags
# ---------------------------------------------------------------------------


class TestParseModelFlagsSession:
    def test_no_flags(self):
        assert parse_model_flags("sonnet") == ("sonnet", "", False, False, False)


    def test_unicode_dash_session_normalized(self):
        # Telegram/iOS auto-converts -- to en/em dashes.
        assert parse_model_flags("sonnet \u2013session") == (
            "sonnet",
            "",
            False,
            False,
            True,
        )


# ---------------------------------------------------------------------------
# resolve_persist_behavior
# ---------------------------------------------------------------------------


class TestResolvePersistBehavior:
    def test_session_flag_always_session_only(self):
        # --session opts out even if the config default is True.
        with _config({"model": {"persist_switch_by_default": True}}):
            assert resolve_persist_behavior(False, True) is False


    def test_no_provider_uses_config_default(self):
        # No --provider → respects config default (True).
        with _config({"model": {"persist_switch_by_default": True}}):
            assert resolve_persist_behavior(False, False, explicit_provider="") is True

    def test_first_pick_persists_then_every_pick_keeps_persisting(self):
        # #90235 / #86414: the ONE policy every surface (CLI, gateway, Desktop
        # picker) defers to. With no default ever configured, the first pick
        # persists (even with --provider, which is how the Desktop picker
        # always sends it) so resolve_provider never falls through to a stray
        # env key on restart. HAOS keeps persisting from then on: an absent
        # ``persist_switch_by_default`` defaults to True
        # (hermes_cli/model_switch.py) and DEFAULT_CONFIG ships it true, so the
        # model the user last picked is the model they come back to. Only an
        # explicit opt-out — ``--once`` / ``--session``, or the key set to
        # false — is session-only.
        with _config({"model": {}}):
            assert resolve_persist_behavior(False, False, explicit_provider="anthropic") is True
        with _config({"model": ""}):
            assert resolve_persist_behavior(False, False) is True
        with _config({"model": {"default": "gpt-5.6", "provider": "openai-codex"}}):
            # --provider is not an opt-out: the Desktop picker sends it on every
            # pick, so treating it as exploratory would make those picks vanish.
            assert resolve_persist_behavior(False, False, explicit_provider="openai-api") is True
            assert resolve_persist_behavior(False, False) is True
            assert resolve_persist_behavior(True, False, explicit_provider="openai-api") is True
            # Explicit opt-outs still win over the default.
            assert resolve_persist_behavior(False, True) is False
            assert resolve_persist_behavior(False, False, is_once=True) is False
        with _config({"model": {"default": "gpt-5.6", "persist_switch_by_default": False}}):
            assert resolve_persist_behavior(False, False) is False
        # Legacy scalar ``model:`` form: a configured default, and (upstream
        # behavior, unchanged) its pick stays session-only.
        with _config({"model": "gpt-5.6"}):
            assert resolve_persist_behavior(False, False) is False


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


class _config:
    """Context manager that patches ``load_config`` to return a fixed dict."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def __enter__(self):
        self._patch = patch(
            "hermes_cli.config.load_config",
            return_value=self.cfg,
        )
        # resolve_persist_behavior imports load_config lazily inside the
        # function, so patching the source module is sufficient.
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
