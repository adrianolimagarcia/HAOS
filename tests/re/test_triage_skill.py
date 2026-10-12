"""
Tests for RE Triage Skill (Phase 1).
Validates loading, frontmatter compliance, and BinaryAnalyzer execution.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from agent.skill_utils import iter_skill_index_files, parse_frontmatter


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "reverse-engineering" / "binary-triage"
SCRIPT_PATH = SKILL_DIR / "scripts" / "binary_analyzer.py"


def test_triage_skill_metadata_and_loading():
    """Assert 1: skill file exists, loads cleanly, and frontmatter satisfies schema."""
    skill_md = SKILL_DIR / "SKILL.md"
    assert skill_md.is_file(), f"SKILL.md missing at {skill_md}"

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter.get("name") == "binary-triage"
    assert frontmatter.get("version") == "1.0.0"
    assert frontmatter.get("license") == "MIT"
    assert "linux" in frontmatter.get("platforms", [])
    assert len(frontmatter.get("description", "")) <= 60
    assert frontmatter["description"].endswith(".")
    assert len(body) > 100

    # Ensure iter_skill_index_files discovers it
    skills_found = [
        p for p in iter_skill_index_files(REPO_ROOT / "skills", "SKILL.md")
        if p.parent.name == "binary-triage"
    ]
    assert len(skills_found) == 1
    assert skills_found[0] == skill_md


def test_binary_analyzer_execution_on_test_binary():
    """Assert 2 & 3: analyzer runs without exception and outputs valid ELF triage report."""
    test_binary = "/bin/ls"
    if not os.path.exists(test_binary):
        test_binary = sys.executable

    assert os.path.exists(test_binary), f"Target test binary not found: {test_binary}"
    assert SCRIPT_PATH.is_file(), f"Script missing at {SCRIPT_PATH}"

    # Import the analyzer directly from script path
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    from binary_analyzer import BinaryAnalyzer  # type: ignore

    analyzer = BinaryAnalyzer(test_binary)
    result = analyzer.run(include_strings=True, include_entropy=True)

    # Assert basic report structure
    assert result["file"] == test_binary
    assert result["file_type"] == "ELF"
    assert result["file_size"] > 0
    assert "entropy" in result
    assert result["entropy"]["overall_entropy"] > 0.0
    assert "strings" in result
    assert result["strings"]["total_strings"] > 0

    # Assert ELF specific analysis
    assert "elf_analysis" in result
    elf_info = result["elf_analysis"]
    assert "header" in elf_info
    assert elf_info["header"]["class"] in (32, 64)
    assert "machine" in elf_info["header"]
    assert "sections" in elf_info
    assert isinstance(elf_info["sections"], list)
    assert len(elf_info["sections"]) > 0


def test_binary_analyzer_cli_invocation():
    """Assert CLI invocation via subprocess generates valid JSON output."""
    test_binary = "/bin/ls" if os.path.exists("/bin/ls") else sys.executable
    cmd = [sys.executable, str(SCRIPT_PATH), "--file", test_binary, "--no-strings"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    assert parsed["file"] == test_binary
    assert parsed["file_type"] == "ELF"
    assert "entropy" in parsed
    assert "elf_analysis" in parsed
