"""Tests for get_hermes_home() profile-mode fallback warning.

Regression test for https://github.com/NousResearch/hermes-agent/issues/18594.

When HERMES_HOME is unset but an active_profile file indicates a non-default
profile is active, get_hermes_home() should:
  1. STILL return ~/.hermes (raising would brick 30+ module-level callers)
  2. Emit a loud one-shot warning to stderr so operators can diagnose
     cross-profile data contamination after the fact.

The warning goes to stderr directly (not through logging) because this
function is called at module-import time from 30+ sites, often before the
logging subsystem has been configured.
"""

from pathlib import Path

import pytest


@pytest.fixture
def fresh_constants(monkeypatch, tmp_path):
    """Import hermes_constants fresh and reset the one-shot warn flag."""
    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Fork HAOS: HAOS_HOME é a variável canônica e vence Path.home() na
    # derivação do home padrão (_get_platform_default_hermes_home lê
    # HAOS_HOME primeiro). Sem este delenv o contrato dependeria do
    # ambiente do host — espelho do delenv de HERMES_HOME abaixo.
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return hermes_constants


def _fallback_home(hermes_constants):
    """Expected fallback home, derived the same way the code under test derives it.

    get_hermes_home()/the warning both resolve the platform default through
    ``_get_platform_default_hermes_home()``; asserting against that (instead of a
    hardcoded ``.haos``) keeps the contract when the fork's default dirname changes.
    """
    return hermes_constants._get_platform_default_hermes_home()


class TestGetHermesHomeProfileWarning:
    def test_classic_mode_no_active_profile_no_warning(
        self, fresh_constants, capsys
    ):
        """Classic mode: no active_profile file → silent, returns the default home."""
        result = fresh_constants.get_hermes_home()
        assert result == _fallback_home(fresh_constants)
        assert "HERMES_HOME fallback" not in capsys.readouterr().err


    def test_named_profile_unset_home_warns_once(
        self, fresh_constants, capsys
    ):
        """active_profile=coder + HERMES_HOME unset → warn loudly, still return fallback."""
        hermes_dir = _fallback_home(fresh_constants)
        hermes_dir.mkdir()
        (hermes_dir / "active_profile").write_text("coder\n")

        result = fresh_constants.get_hermes_home()

        # 1. Still returns the fallback — no import-time crash
        assert result == _fallback_home(fresh_constants)
        # 2. Stderr got the warning exactly once
        err = capsys.readouterr().err
        assert err.count("HERMES_HOME fallback") == 1
        assert "'coder'" in err
        assert "#18594" in err

        # 3. One-shot: second and third calls don't re-warn
        fresh_constants.get_hermes_home()
        fresh_constants.get_hermes_home()
        err2 = capsys.readouterr().err
        assert "HERMES_HOME fallback" not in err2

    def test_hermes_home_set_suppresses_warning(
        self, fresh_constants, capsys, monkeypatch
    ):
        """Even if active_profile is 'coder', setting HERMES_HOME suppresses warning."""
        profile_dir = _fallback_home(fresh_constants) / "profiles" / "coder"
        profile_dir.mkdir(parents=True)
        (_fallback_home(fresh_constants) / "active_profile").write_text("coder\n")
        monkeypatch.setenv("HERMES_HOME", str(profile_dir))

        result = fresh_constants.get_hermes_home()

        assert result == profile_dir
        assert "HERMES_HOME fallback" not in capsys.readouterr().err

    def test_unreadable_active_profile_no_crash(
        self, fresh_constants, capsys
    ):
        """active_profile that can't be decoded → fall through silently."""
        hermes_dir = _fallback_home(fresh_constants)
        hermes_dir.mkdir()
        # Write bytes that aren't valid utf-8
        (hermes_dir / "active_profile").write_bytes(b"\xff\xfe\x00\x00")

        result = fresh_constants.get_hermes_home()

        assert result == _fallback_home(fresh_constants)
        # Shouldn't crash; shouldn't warn either (can't tell what profile was intended)
        assert "HERMES_HOME fallback" not in capsys.readouterr().err

