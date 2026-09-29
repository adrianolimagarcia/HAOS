"""Unit tests for civilization feature flags and gradual rollout controls."""

import os
import pytest
from hermes.platform.civilization.feature_flags import (
    CivFeatureFlags,
    get_feature_flags,
    reset_feature_flags,
)


def test_default_flags_all_enabled(monkeypatch):
    monkeypatch.delenv("HAOS_CIV_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_COUNCIL_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_SOCIETY_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_EVOLUTION_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_CONSTITUTION_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_CANARY_BOTS", raising=False)
    reset_feature_flags()

    flags = get_feature_flags()
    assert flags.civ_enabled is True
    assert flags.council_enabled is True
    assert flags.society_enabled is True
    assert flags.evolution_enabled is True
    assert flags.constitution_enabled is True
    assert flags.canary_bots == set()

    # All bots and features should be enabled
    assert flags.is_bot_eligible("any-bot") is True
    assert flags.is_feature_enabled("identity", "any-bot") is True
    assert flags.is_feature_enabled("council", "any-bot") is True
    assert flags.is_feature_enabled("society", "any-bot") is True
    assert flags.is_feature_enabled("evolution", "any-bot") is True
    assert flags.is_feature_enabled("constitution", "any-bot") is True


def test_master_kill_switch(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_ENABLED", "0")
    reset_feature_flags()

    flags = get_feature_flags()
    assert flags.civ_enabled is False
    assert flags.is_bot_eligible("any-bot") is False
    assert flags.is_feature_enabled("identity", "any-bot") is False
    assert flags.is_feature_enabled("council", "any-bot") is False


def test_canary_routing(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_ENABLED", "1")
    monkeypatch.setenv("HAOS_CIV_CANARY_BOTS", "architect-bot, security-bot")
    reset_feature_flags()

    flags = get_feature_flags()
    assert flags.canary_bots == {"architect-bot", "security-bot"}

    # Canary bots are allowed
    assert flags.is_bot_eligible("architect-bot") is True
    assert flags.is_bot_eligible("security-bot") is True
    assert flags.is_feature_enabled("council", "architect-bot") is True

    # Non-canary bots are rejected
    assert flags.is_bot_eligible("general-bot") is False
    assert flags.is_bot_eligible(None) is False
    assert flags.is_feature_enabled("council", "general-bot") is False


def test_subsystem_specific_toggles(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_ENABLED", "1")
    monkeypatch.setenv("HAOS_CIV_COUNCIL_ENABLED", "0")
    monkeypatch.setenv("HAOS_CIV_CONSTITUTION_ENABLED", "0")
    monkeypatch.delenv("HAOS_CIV_CANARY_BOTS", raising=False)
    reset_feature_flags()

    flags = get_feature_flags()
    assert flags.is_feature_enabled("identity", "bot-1") is True
    assert flags.is_feature_enabled("society", "bot-1") is True
    assert flags.is_feature_enabled("council", "bot-1") is False
    assert flags.is_feature_enabled("constitution", "bot-1") is False


def test_flags_summary(monkeypatch):
    monkeypatch.setenv("HAOS_CIV_ENABLED", "1")
    monkeypatch.setenv("HAOS_CIV_CANARY_BOTS", "bot-a,bot-b")
    reset_feature_flags()

    flags = get_feature_flags()
    s = flags.summary()
    assert s["civ_enabled"] is True
    assert s["canary_mode"] is True
    assert s["canary_bots"] == ["bot-a", "bot-b"]
