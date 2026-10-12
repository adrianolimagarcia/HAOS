"""Tests for Reverse-Skill Cherry-Pick & Tool Routing (Phase 4).
Validates router skill frontmatter, no functional duplication, and deterministic routing.
"""

from pathlib import Path
import pytest

from agent.skill_utils import iter_skill_index_files, parse_frontmatter
from hermes.platform.re.router import RERouter, RETier


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROUTER_SKILL_DIR = REPO_ROOT / "skills" / "reverse-engineering" / "re-router"


def test_re_router_skill_metadata_and_loading():
    """Assert router skill exists and complies with HAOS Hardline standards."""
    skill_md = ROUTER_SKILL_DIR / "SKILL.md"
    assert skill_md.is_file(), f"SKILL.md missing at {skill_md}"

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter.get("name") == "re-router"
    assert frontmatter.get("version") == "1.0.0"
    assert frontmatter.get("license") == "MIT"
    assert "linux" in frontmatter.get("platforms", [])
    assert len(frontmatter.get("description", "")) <= 60
    assert frontmatter["description"].endswith(".")
    assert len(body) > 100

    # Ensure iter_skill_index_files discovers it
    skills_found = [
        p for p in iter_skill_index_files(REPO_ROOT / "skills", "SKILL.md")
        if p.parent.name == "re-router"
    ]
    assert len(skills_found) == 1
    assert skills_found[0] == skill_md


def test_re_router_deterministic_tier_selection():
    """Assert router directs tasks to the correct toolchain layer."""
    # Decompilation -> Deep (Ghidra)
    assert RERouter.select_tier("Decompile main function into C pseudocode") == RETier.DEEP
    assert RERouter.select_tier("Recover struct layout from target binary") == RETier.DEEP

    # Disassembly & Callgraph -> Backbone (radare2)
    assert RERouter.select_tier("Disassemble the authentication routine instructions") == RETier.BACKBONE
    assert RERouter.select_tier("List function xrefs and control flow blocks") == RETier.BACKBONE

    # Headers & Initial inspection -> Triage
    assert RERouter.select_tier("Inspect ELF headers and security mitigations") == RETier.TRIAGE
    assert RERouter.select_tier("Check Shannon entropy of sections", {"entropy": {"is_likely_packed": True}}) == RETier.TRIAGE


def test_no_functional_duplication_with_prior_phases():
    """Assert router references rather than duplicates triage or backbone tools."""
    content = (ROUTER_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    # Must reference the 3 tiers
    assert "binary-triage" in content
    assert "radare2" in content
    assert "Ghidra" in content
    # Excludes unsupported niche architectures
    assert "Mach-O" not in content or "Linux" in content
