"""Contract tests for the haos-orchestrator skill and its spec checker."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "autonomous-ai-agents"
    / "haos-orchestrator"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
SPEC_CHECKER = SKILL_DIR / "scripts" / "task_spec_check.py"
TEMPLATES = SKILL_DIR / "templates"

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
    assert _frontmatter_value(skill_text, "name") == "haos-orchestrator"

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


def test_script_and_templates_exist() -> None:
    assert SPEC_CHECKER.exists()
    assert (TEMPLATES / "task_spec.md").exists()
    assert (TEMPLATES / "handoff.md").exists()


def _run_checker(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SPEC_CHECKER), *argv],
        capture_output=True,
        text=True,
    )


def test_valid_spec_passes(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Add customer endpoint\n"
        "## Role\nbackend worker\n"
        "## Constraints\nonly src/customers/ and tests/customers/\n"
        "## Files\nsrc/customers/customer.repository.py\n"
        "## Behavior\nadd create_customer()\n"
        "## Tests\ntest creates customer via repository\n"
        "## Verification\nscripts/run_tests.sh tests/customers/\n",
        encoding="utf-8",
    )
    proc = _run_checker("--spec", str(spec))
    assert proc.returncode == 0
    assert "ready to dispatch" in proc.stdout


def test_oversized_spec_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "big.md"
    spec.write_text(
        "# Big spec\n## Role\nx\n## Constraints\nx\n## Files\nx\n## Behavior\n"
        + ("content " * 2000)
        + "\n## Tests\nx\n## Verification\nx\n",
        encoding="utf-8",
    )
    proc = _run_checker("--spec", str(spec))
    assert proc.returncode == 1
    assert "split into smaller tasks" in proc.stdout


def test_weaken_asserts_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "weak.md"
    spec.write_text(
        "# Spec\n## Role\nx\n## Constraints\nx\n## Files\nx\n## Behavior\nx\n"
        "## Tests\nDo not weaken the asserts in test_x.\n"
        "## Verification\nx\n",
        encoding="utf-8",
    )
    proc = _run_checker("--spec", str(spec))
    assert proc.returncode == 1
    assert "weaken-the-asserts" in proc.stdout


def test_missing_sections_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "partial.md"
    spec.write_text("# Partial\n## Role\nx\n", encoding="utf-8")
    proc = _run_checker("--spec", str(spec))
    assert proc.returncode == 1
    assert "missing required section" in proc.stdout


def test_instruction_restriction_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "restrict.md"
    spec.write_text(
        "# Spec\n## Role\nx\n## Constraints\nx\n## Files\nx\n## Behavior\n"
        "You do not have access to the exchange_rate tool.\n"
        "## Tests\nx\n## Verification\nx\n",
        encoding="utf-8",
    )
    proc = _run_checker("--spec", str(spec))
    assert proc.returncode == 1
    assert "instruction-based tool restriction" in proc.stdout


def test_handoff_validation(tmp_path: Path) -> None:
    good = tmp_path / "HANDOFF.md"
    good.write_text(
        "## Phase\nphase 4\n## Next Action\ndispatch task 4.2 to backend worker\n",
        encoding="utf-8",
    )
    proc = _run_checker("--handoff", str(good))
    assert proc.returncode == 0

    bad = tmp_path / "HANDOFF_bad.md"
    bad.write_text("## Phase\nphase 4\n", encoding="utf-8")
    proc = _run_checker("--handoff", str(bad))
    assert proc.returncode == 1
    assert "missing field" in proc.stdout
