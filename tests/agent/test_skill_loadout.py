"""Invariant tests for the skill LOADOUT cap (HAOS backlog P3 — docs/haos/RESEARCH_MEDIUM_ABSORPTION.md,
Etapa 1, item B): the literature recommends <= 20 tools per agent, so the always-on skills index is
capped; over-limit skills stay on disk and remain loadable via skill_view/skills_list.

These tests assert CONTRACTS between the config value, the pure selection function and the
rendered prompt — never counts of the real park, never source text.
"""

from __future__ import annotations

import re

import pytest

from agent.skill_utils import (
    ESSENTIAL_SKILLS,
    get_skill_loadout_limit,
    get_skill_loadout_pins,
    select_skill_loadout,
)

_SKILLS_BLOCK_RE = re.compile(r"<available_skills>(.*?)</available_skills>", re.DOTALL)


# ── Pure selection contract ─────────────────────────────────────────────────

def test_loadout_budget_contract():
    """The cap is a budget: limit > 0 selects exactly min(limit, n) entries; 0/None keeps all."""
    entries = [(f"cat{i % 3}", f"skill-{i:02d}", f"desc {i}") for i in range(25)]
    assert len(select_skill_loadout(entries, limit=10)) == 10
    assert len(select_skill_loadout(entries, limit=1)) == 1
    assert len(select_skill_loadout(entries, limit=0)) == 25
    assert len(select_skill_loadout(entries, limit=None)) == 25
    assert len(select_skill_loadout(entries)) == 25  # default: unlimited


def test_loadout_essential_and_pinned_never_dropped():
    """Essential (hermes-agent) and config-pinned names rank first, so a small budget keeps
    them even when alphabetical order would drop them."""
    entries = [
        ("zz", "hermes-agent", "operating manual"),
        ("aa", "alpha", "first"),
        ("bb", "beta", "second"),
        ("cc", "pinned-one", "pinned"),
    ]
    selected = select_skill_loadout(entries, limit=2, prioritized=("pinned-one",))
    names = [name for _, name, _ in selected]
    assert names == ["hermes-agent", "pinned-one"]
    # Without the pin, alphabetical order wins among non-essential.
    selected2 = select_skill_loadout(entries, limit=2)
    assert [name for _, name, _ in selected2] == ["hermes-agent", "alpha"]


def test_loadout_selection_is_deterministic():
    """Same input -> same output (stable order, no randomness)."""
    entries = [(f"cat{i % 3}", f"skill-{i:02d}", f"desc {i}") for i in range(25)]
    first = select_skill_loadout(entries, limit=7, prioritized=("skill-05",))
    second = select_skill_loadout(list(entries), limit=7, prioritized=("skill-05",))
    assert first == second


def test_loadout_keeps_all_when_under_budget():
    """A park smaller than the budget is untouched (the cap never drops anything gratuitously)."""
    entries = [(f"cat{i}", f"skill-{i}", f"desc {i}") for i in range(3)]
    selected = select_skill_loadout(entries, limit=20)
    assert {name for _, name, _ in selected} == {name for _, name, _ in entries}


# ── Config contract ──────────────────────────────────────────────────────────

def test_loadout_limit_config_contract(monkeypatch, tmp_path):
    """``skills.loadout_limit`` drives the selection limit: absent -> 20, set -> honored,
    0 -> unlimited, garbage -> 20. Contract: the value selection uses is the value config exposes."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent import skill_utils
    skill_utils._raw_config_cache_clear()

    assert get_skill_loadout_limit() == 20  # default
    (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: 7\n", encoding="utf-8")
    assert get_skill_loadout_limit() == 7
    (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: 0\n", encoding="utf-8")
    assert get_skill_loadout_limit() == 0
    (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: abc\n", encoding="utf-8")
    assert get_skill_loadout_limit() == 20

    entries = [(f"cat{i}", f"skill-{i}", f"desc {i}") for i in range(25)]
    assert len(select_skill_loadout(entries, limit=get_skill_loadout_limit())) == 20


def test_loadout_pins_config_contract(monkeypatch, tmp_path):
    """``skills.loadout_pin`` (scalar or list) resolves to the prioritized names."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent import skill_utils
    skill_utils._raw_config_cache_clear()

    assert get_skill_loadout_pins() == ()
    (tmp_path / "config.yaml").write_text(
        "skills:\n  loadout_pin: [keep-me, also-me]\n", encoding="utf-8"
    )
    assert get_skill_loadout_pins() == ("also-me", "keep-me")  # sorted, deterministic


# ── Rendered-prompt contract (E2E over the real builder) ────────────────────

class TestLoadoutRenderedIndex:
    @pytest.fixture(autouse=True)
    def _clear_skills_cache(self):
        from agent.prompt_builder import clear_skills_system_prompt_cache
        clear_skills_system_prompt_cache(clear_snapshot=True)
        yield
        clear_skills_system_prompt_cache(clear_snapshot=True)

    @staticmethod
    def _seed_park(home, names):
        for name in names:
            d = home / "skills" / "demo" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Does {name}.\n---\n# {name}\nbody\n",
                encoding="utf-8",
            )

    @staticmethod
    def _index_skill_lines(prompt):
        block = _SKILLS_BLOCK_RE.search(prompt)
        assert block is not None, "no <available_skills> block rendered"
        return [line for line in block.group(1).splitlines() if line.startswith("    - ")]

    def test_cap_limits_index_lines(self, monkeypatch, tmp_path):
        """With loadout_limit: 2 and 5 skills on disk, the always-on index carries exactly 2
        lines — the loadout, not the park. Over-limit skills stay on disk."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: 2\n", encoding="utf-8")
        self._seed_park(tmp_path, ["alpha", "beta", "gamma", "delta", "epsilon"])

        from agent.prompt_builder import build_skills_system_prompt
        prompt = build_skills_system_prompt()
        lines = self._index_skill_lines(prompt)
        assert len(lines) == 2, prompt
        # Deterministic choice: the first two alphabetical names under the budget.
        assert "alpha" in lines[0] and "beta" in lines[1]

    def test_unlimited_shows_all(self, monkeypatch, tmp_path):
        """loadout_limit: 0 disables the cap — every skill is in the loadout."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: 0\n", encoding="utf-8")
        self._seed_park(tmp_path, ["alpha", "beta", "gamma"])
        from agent.prompt_builder import build_skills_system_prompt
        lines = self._index_skill_lines(build_skills_system_prompt())
        assert len(lines) == 3

    def test_default_cap_is_twenty(self, monkeypatch, tmp_path):
        """No config -> the literature default (20) caps the index; essential survives any budget."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        self._seed_park(tmp_path, [f"skill-{i:02d}" for i in range(25)])
        from agent.prompt_builder import build_skills_system_prompt
        lines = self._index_skill_lines(build_skills_system_prompt())
        assert len(lines) == 20

    def test_essential_skill_stays_in_any_budget(self, monkeypatch, tmp_path):
        """hermes-agent (ESSENTIAL_SKILLS) is never dropped by the cap."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("skills:\n  loadout_limit: 1\n", encoding="utf-8")
        self._seed_park(tmp_path, ["hermes-agent", "zzz-last"])
        from agent.prompt_builder import build_skills_system_prompt
        lines = self._index_skill_lines(build_skills_system_prompt())
        assert len(lines) == 1
        assert "hermes-agent" in lines[0]
        assert ESSENTIAL_SKILLS == {"hermes-agent"}  # the contract above depends on this


# ── prompt-size diagnostic exposes the cap ──────────────────────────────────

def test_prompt_size_loadout_metrics_contract():
    """The diagnostic's loadout metrics obey the budget: in_index <= limit when capped, and
    in_index == on_disk when unlimited."""
    from hermes_cli.prompt_size import loadout_metrics

    capped = loadout_metrics(in_index=12, on_disk=68, limit=20)
    assert capped == {"limit": 20, "in_index": 12, "on_disk": 68}
    assert 0 <= capped["in_index"] <= capped["limit"] <= capped["on_disk"]
    unlimited = loadout_metrics(in_index=5, on_disk=5, limit=0)
    assert unlimited["in_index"] == unlimited["on_disk"]
    assert unlimited["limit"] is None


def test_prompt_size_reports_loadout_budget_e2e(tmp_path, monkeypatch):
    """The real diagnostic (offline inspection agent) reports a loadout whose in-index count
    never exceeds its own budget — the cap is observable, not just internal."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.chdir(tmp_path)  # avoid picking up the repo's AGENTS.md

    from hermes_cli.prompt_size import compute_prompt_breakdown
    data = compute_prompt_breakdown("cli")
    loadout = data["skills_loadout"]
    assert set(loadout) == {"limit", "in_index", "on_disk"}
    assert 0 <= loadout["in_index"] <= loadout["on_disk"]
    assert loadout["in_index"] <= (loadout["limit"] or loadout["on_disk"])
