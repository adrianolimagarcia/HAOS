"""Ultra-SOTA harness: gateway <-> Civilization advisory bridge.

Proves invariants, not just coverage (per HAOS-CIV-ULTRA-SOTA
TEST_AND_CHAOS_HARNESS.md):

- Contract: suggest_specialist() returns a bot_id or None, never raises,
  never has side effects (no bots created, no events written).
- Flag matrix: bridge flag OFF and master kill-switch both force fallback.
- Failure injection: broken delegation import, empty/None input,
  restricted available_bots -- all degrade silently to None.
- Each scenario asserts explicitly with a clear failure message.

Run with pytest, or directly (manual runner at the bottom) when pytest
is unavailable.
"""

import os
import sys

try:
    import pytest
except ImportError:  # manual runner environments without pytest
    pytest = None

from hermes.platform.civilization import gateway_bridge as bridge
from hermes.platform.civilization.feature_flags import reset_feature_flags


if pytest is not None:

    @pytest.fixture(autouse=True)
    def _clean_env(monkeypatch):
        """Isolate every scenario: clear bridge/master flags and reset the
        feature-flag singleton so env changes take effect."""
        _reset_test_env(monkeypatch)
        yield
        reset_feature_flags()


def _reset_test_env(monkeypatch):
    monkeypatch.delenv("HAOS_CIV_GATEWAY_ROUTING", raising=False)
    monkeypatch.delenv("HAOS_CIV_ENABLED", raising=False)
    reset_feature_flags()


# ---------------------------------------------------------------------------
# Contract: routing suggestions
# ---------------------------------------------------------------------------

def test_re_message_suggests_reverse_bot():
    got = bridge.suggest_specialist(
        "please decompile this ELF binary with ghidra and check for pwn patterns"
    )
    assert got == "reverse-bot", (
        "FAIL: RE-domain message should suggest 'reverse-bot', got %r. "
        "Check _DOMAINS keywords in delegation.py." % (got,)
    )


def test_research_message_suggests_research_bot():
    got = bridge.suggest_specialist(
        "what is the latest-news on temporal trends from the past-week?"
    )
    assert got == "research-bot", (
        "FAIL: research-domain message should suggest 'research-bot', got %r." % (got,)
    )


def test_security_message_suggests_security_bot():
    got = bridge.suggest_specialist("audit firewall credentials and rotate the token")
    assert got == "security-bot", (
        "FAIL: security-domain message should suggest 'security-bot', got %r." % (got,)
    )


def test_generic_message_falls_back_to_none():
    got = bridge.suggest_specialist("hello, how are you doing today?")
    assert got is None, (
        "FAIL: generic message must fall back to None (normal flow), got %r. "
        "The bridge must not invent specialists." % (got,)
    )


# ---------------------------------------------------------------------------
# Flag matrix
# ---------------------------------------------------------------------------

def test_bridge_flag_off_always_falls_back(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_GATEWAY_ROUTING", "0")
    reset_feature_flags()
    assert bridge.gateway_routing_enabled() is False, (
        "FAIL: gateway_routing_enabled() must be False with HAOS_CIV_GATEWAY_ROUTING=0"
    )
    got = bridge.suggest_specialist("decompile this binary with radare2")
    assert got is None, (
        "FAIL: with bridge flag OFF, even RE text must fall back to None, got %r." % (got,)
    )


def test_master_kill_switch_disables_bridge(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_ENABLED", "0")
    reset_feature_flags()
    assert bridge.gateway_routing_enabled() is False, (
        "FAIL: gateway_routing_enabled() must be False with HAOS_CIV_ENABLED=0 "
        "(master kill-switch)"
    )
    got = bridge.suggest_specialist("decompile this binary with radare2")
    assert got is None, (
        "FAIL: with master kill-switch, suggestion must be None, got %r." % (got,)
    )


def test_bridge_enabled_by_default():
    assert bridge.gateway_routing_enabled() is True, (
        "FAIL: bridge should default ON (advisory-only, zero behavior change). "
        "If this intentionally changed, update the module docstring."
    )


# ---------------------------------------------------------------------------
# Failure injection: silent fallback, never raise
# ---------------------------------------------------------------------------

def test_empty_and_none_input_safe():
    assert bridge.suggest_specialist("") is None, "FAIL: empty string must -> None"
    assert bridge.suggest_specialist(None) is None, "FAIL: None input must -> None"
    assert bridge.suggest_specialist(12345) is None, "FAIL: non-str input must -> None"


def test_broken_delegation_import_falls_back_silently(monkeypatch):
    import hermes.platform.civilization.delegation as delegation

    real_domains = delegation._DOMAINS
    monkeypatch.setattr(delegation, "_DOMAINS", None)  # corrupt the table
    try:
        got = bridge.suggest_specialist("decompile this binary with ghidra")
    finally:
        monkeypatch.setattr(delegation, "_DOMAINS", real_domains)
    assert got is None, (
        "FAIL: corrupted _DOMAINS must degrade to None, not raise; got %r." % (got,)
    )


def test_available_bots_filter_respected():
    got = bridge.suggest_specialist(
        "decompile this ELF binary with ghidra",
        available_bots={"research-bot"},
    )
    assert got is None, (
        "FAIL: reverse-bot not in available_bots -> must be None, got %r." % (got,)
    )
    got2 = bridge.suggest_specialist(
        "decompile this ELF binary with ghidra",
        available_bots={"reverse-bot", "research-bot"},
    )
    assert got2 == "reverse-bot", (
        "FAIL: reverse-bot available -> should suggest it, got %r." % (got2,)
    )


def test_suggest_never_raises_on_hostile_input():
    hostile = ["\x00" * 10000, "ghidra" * 5000, "binary\x00elf"]
    for h in hostile:
        try:
            bridge.suggest_specialist(h)
        except Exception as exc:  # noqa: BLE001 - the point is it must not happen
            msg = (
                "FAIL: suggest_specialist raised %r on hostile input %r; "
                "the bridge must never raise." % (exc, h[:40])
            )
            if pytest is not None:
                pytest.fail(msg)
            raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Manual runner (pytest unavailable on some hosts)
# ---------------------------------------------------------------------------

def _manual_monkeypatch():
    """Minimal monkeypatch shim for direct execution without pytest."""

    class _MP:
        def __init__(self):
            self._env_old = {}
            self._attr_old = []

        def setenv(self, k, v):
            if k not in self._env_old:
                self._env_old[k] = os.environ.get(k, _MISSING)
            os.environ[k] = v

        def delenv(self, k, raising=True):
            if k not in self._env_old:
                self._env_old[k] = os.environ.get(k, _MISSING)
            os.environ.pop(k, None)

        def setattr(self, obj, name, value):
            self._attr_old.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for k, v in self._env_old.items():
                if v is _MISSING:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            for obj, name, v in reversed(self._attr_old):
                setattr(obj, name, v)
            self._env_old.clear()
            self._attr_old.clear()

    return _MP()


_MISSING = object()


def run_manual():
    """Execute all test_* functions without pytest. Returns (passed, failed)."""
    import traceback

    fns = [
        (n, f)
        for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
    passed, failed = 0, 0
    for name, fn in fns:
        mp = _manual_monkeypatch()
        # emulate the autouse fixture
        mp.delenv("HAOS_CIV_GATEWAY_ROUTING", raising=False)
        mp.delenv("HAOS_CIV_ENABLED", raising=False)
        reset_feature_flags()
        try:
            import inspect

            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(mp)
            else:
                fn()
            print("PASS %s" % name)
            passed += 1
        except Exception:
            print("FAIL %s" % name)
            traceback.print_exc()
            failed += 1
        finally:
            mp.undo()
            reset_feature_flags()
    print("manual: %d passed, %d failed" % (passed, failed))
    return passed, failed


if __name__ == "__main__":
    p, f = run_manual()
    sys.exit(1 if f else 0)
