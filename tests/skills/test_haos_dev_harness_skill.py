"""Contract tests for the haos-dev-harness skill."""

from __future__ import annotations

import re
from pathlib import Path
import pytest

SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "autonomous-ai-agents"
    / "haos-dev-harness"
    / "SKILL.md"
)
REQUIRED_SECTIONS = [
    "## When to Use",
    "## Prerequisites",
    "## How to Run",
    "## Quick Reference",
    "## Procedure",
    "## Pitfalls",
    "## Verification",
]


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    assert match, f"missing frontmatter field: {key}"
    return match.group(1).strip()


def test_frontmatter_meets_hardline_standard(skill_text: str) -> None:
    assert skill_text.startswith("---\n")
    assert _frontmatter_value(skill_text, "name") == "haos-dev-harness"

    description = _frontmatter_value(skill_text, "description")
    assert len(description) <= 60
    assert description.endswith(".")

    for field in ("version", "author", "license", "platforms"):
        assert _frontmatter_value(skill_text, field)
    assert not _frontmatter_value(skill_text, "author").startswith("Hermes Agent")


def test_body_uses_required_modern_section_order(skill_text: str) -> None:
    indices = [skill_text.find(section) for section in REQUIRED_SECTIONS]
    assert all(index != -1 for index in indices)
    assert indices == sorted(indices)
