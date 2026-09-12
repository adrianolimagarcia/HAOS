"""Invariant tests for ``scripts/audit_skills.py`` — the deterministic skill-park auditor (P3).

The auditor is a standalone CI check (like ``scripts/check-case-collisions.py``): it walks the
park with the RUNTIME LOADER's own iterator (``agent.skill_utils.iter_skill_index_files``), so
"what is a skill" can never diverge from what the agent would load. The tests below assert
CONTRACTS between the auditor's output and independently built fixtures / the loader itself —
never counts of the real park (a change-detector), never source text.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "audit_skills.py"

_spec = importlib.util.spec_from_file_location("audit_skills", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"auditor module not loadable: {SCRIPT}"
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


# ── Fixture helpers ─────────────────────────────────────────────────────────

_VALID = (
    "---\n"
    "name: {name}\n"
    "description: {desc}\n"
    "version: 1.0.0\n"
    "author: Test Author\n"
    "license: MIT\n"
    "platforms: [linux]\n"
    "metadata:\n"
    "  hermes:\n"
    "    tags: [test]\n"
    "---\n"
    "# {name}\n"
    "body\n"
)


def _skill(root: Path, rel: str, frontmatter: str) -> Path:
    """Write a SKILL.md package at ``root / rel`` (rel is a directory path)."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(frontmatter, encoding="utf-8")
    return p


def _valid(name: str, desc: str = "Does something useful.") -> str:
    return _VALID.format(name=name, desc=desc)


def _violations(result, rule: str):
    """Flat list of violations of *rule* across all audited skills of one root."""
    return [v for s in result["skills"] for v in s["violations"] if v["rule"] == rule]


def _all_violations(result):
    """Flat list of every violation across one root."""
    return [v for s in result["skills"] for v in s["violations"]]


def _names(result):
    return {s["name"] for s in result["skills"]}


# ── Counting contract: only real skills count ───────────────────────────────

def test_counts_only_real_skills(tmp_path):
    """README/DESCRIPTION/support files and excluded dirs are not skills; the count is the
    number of SKILL.md packages the runtime loader would see."""
    root = tmp_path / "park"
    _skill(root, "skills/alpha", _valid("alpha"))
    _skill(root, "skills/beta", _valid("beta"))
    (root / "skills" / "README.md").write_text("readme", encoding="utf-8")
    (root / "skills" / "DESCRIPTION.md").write_text("---\ndescription: cat\n---\n", encoding="utf-8")
    (root / "skills" / "alpha" / "references").mkdir()
    (root / "skills" / "alpha" / "references" / "notes.md").write_text("notes", encoding="utf-8")
    (root / "skills" / ".archive" / "gamma").mkdir(parents=True)
    (root / "skills" / ".archive" / "gamma" / "SKILL.md").write_text(_valid("gamma"), encoding="utf-8")

    result = audit.audit_root(root / "skills")
    assert len(result["skills"]) == 2
    assert _names(result) == {"alpha", "beta"}
    assert _all_violations(result) == []


def test_auditor_walk_equals_loader_walk_on_real_park():
    """Contract with the runtime loader: the auditor counts exactly the SKILL.md files the
    agent would load — no more, no fewer. (Not a count snapshot; a set-equality contract.)"""
    from agent.skill_utils import iter_skill_index_files

    for root_name in ("skills", "optional-skills"):
        root = REPO / root_name
        loader_paths = {str(p.relative_to(root)) for p in iter_skill_index_files(root, "SKILL.md")}
        audited = {s["rel"] for s in audit.audit_root(root)["skills"]}
        assert audited == loader_paths, f"auditor and loader disagree under {root_name}/"


# ── Frontmatter rules ───────────────────────────────────────────────────────

def test_missing_required_fields_flagged(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/no-license", _valid("no-license").replace("license: MIT\n", ""))
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "required_fields")
    assert len(hits) == 1
    assert "license" in hits[0]["detail"]


def test_missing_tags_flagged(tmp_path):
    root = tmp_path / "park"
    fm = _valid("no-tags").replace("  hermes:\n    tags: [test]\n", "")
    _skill(root, "skills/no-tags", fm)
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "required_fields")
    assert len(hits) == 1
    assert "tags" in hits[0]["detail"]


def test_unfenced_frontmatter_flagged(tmp_path):
    root = tmp_path / "park"
    (root / "skills" / "bad").mkdir(parents=True)
    (root / "skills" / "bad" / "SKILL.md").write_text(
        "name: bad\ndescription: No fence here.\n", encoding="utf-8"
    )
    result = audit.audit_root(root / "skills")
    assert len(_violations(result, "frontmatter")) == 1


# ── Name rules ──────────────────────────────────────────────────────────────

def test_duplicate_names_flagged_across_categories(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/a/dup", _valid("dup"))
    _skill(root, "skills/b/dup", _valid("dup"))
    result = audit.audit_root(root / "skills")
    audit.flag_duplicate_names([result])  # duplicate detection spans the whole park
    hits = _violations(result, "unique_names")
    assert len(hits) == 2, hits
    assert all("dup" in h["detail"] for h in hits)


def test_name_dir_mismatch_flagged(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/foo", _valid("bar"))
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "name_dir_match")
    assert len(hits) == 1
    assert "bar" in hits[0]["detail"] and "foo" in hits[0]["detail"]


# ── Size rule (distribution-derived limit) ──────────────────────────────────

def test_oversize_flagged_only_above_limit(tmp_path):
    root = tmp_path / "park"
    big = _valid("big") + "x" * 5000  # > 2048-byte limit under test
    _skill(root, "skills/big", big)
    small = _valid("small")
    _skill(root, "skills/small", small)
    result = audit.audit_root(root / "skills", max_md_bytes=2048)
    hits = _violations(result, "oversize")
    assert len(hits) == 1
    assert "big" in hits[0]["path"]
    assert _names(result) == {"big", "small"}  # oversize skills are still counted


# ── Description hardline (skills/AGENTS.md rule 1) ─────────────────────────

def test_description_too_long_flagged(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/long",
           _valid("long", "This description is far too long for the hardline limit of sixty characters."))
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "description")
    assert any("60" in h["detail"] for h in hits)


def test_description_without_period_flagged(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/noperiod", _valid("noperiod", "Does not end with a period"))
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "description")
    assert any("period" in h["detail"] for h in hits)


def test_description_marketing_word_flagged(tmp_path):
    root = tmp_path / "park"
    _skill(root, "skills/marketing", _valid("marketing", "A powerful skill for tests."))
    result = audit.audit_root(root / "skills")
    hits = _violations(result, "description")
    assert any("powerful" in h["detail"] for h in hits)


# ── CLI contract: exit codes ────────────────────────────────────────────────

def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, cwd=REPO,
    )


def test_cli_exit_zero_on_clean_park(tmp_path):
    _skill(tmp_path, "skills/ok", _valid("ok"))
    result = _run(tmp_path / "skills")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skill" in result.stdout.lower()


def test_cli_exit_one_on_violations(tmp_path):
    _skill(tmp_path, "skills/bad", _valid("bad").replace("license: MIT\n", ""))
    result = _run(tmp_path / "skills")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "required_fields" in result.stdout


def test_cli_exit_two_on_missing_root(tmp_path):
    result = _run(tmp_path / "does-not-exist")
    assert result.returncode == 2


def test_real_park_audit_passes():
    """The in-repo park must be green under the auditor's own rules (same rules the
    authoring-standards suite enforces; the auditor restates them standalone)."""
    result = _run(REPO / "skills", REPO / "optional-skills")
    assert result.returncode == 0, result.stdout + result.stderr
