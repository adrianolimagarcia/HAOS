"""Contract tests for the haos-codebase-wiki skill (SKILL.md + query helper)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "autonomous-ai-agents"
    / "haos-codebase-wiki"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
HELPER = SKILL_DIR / "scripts" / "haos_wiki_query.py"
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
    assert _frontmatter_value(skill_text, "name") == "haos-codebase-wiki"
    description = _frontmatter_value(skill_text, "description")
    assert len(description) <= 60
    assert description.endswith(".")
    for field in ("version", "author", "license", "platforms"):
        assert _frontmatter_value(skill_text, field)
    assert not _frontmatter_value(skill_text, "author").startswith("Hermes Agent")
    # version is semver
    assert re.match(r"^\d+\.\d+\.\d+$", _frontmatter_value(skill_text, "version"))


def test_body_uses_required_modern_section_order(skill_text: str) -> None:
    indices = [skill_text.find(section) for section in REQUIRED_SECTIONS]
    assert all(index != -1 for index in indices)
    assert indices == sorted(indices)


def test_prose_references_native_hermes_tools(skill_text: str) -> None:
    for tool in ("`terminal`", "`read_file`", "`search_files`"):
        assert tool in skill_text


def test_helper_script_exists_and_compiles() -> None:
    assert HELPER.is_file()
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(HELPER)],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_graph(tmp_path: Path) -> Path:
    """Tiny fixed graph: a imports b; b::run calls c::helper; c herda de b::base."""
    root = tmp_path / "codebase-wiki"
    root.mkdir()
    graph_path = root / "graph.json"
    payload = {
        "root": ".",
        "truncated": False,
        "file_count": 3,
        "nodes": [
            {"id": "a", "kind": "module", "label": "a", "module": "a",
             "source_file": "a.py", "source_location": 1},
            {"id": "b", "kind": "module", "label": "b", "module": "b",
             "source_file": "b.py", "source_location": 1},
            {"id": "b::base", "kind": "class", "label": "base", "module": "b",
             "source_file": "b.py", "source_location": 3},
            {"id": "b::run", "kind": "function", "label": "run", "module": "b",
             "source_file": "b.py", "source_location": 6},
            {"id": "c", "kind": "module", "label": "c", "module": "c",
             "source_file": "c.py", "source_location": 1},
            {"id": "c::helper", "kind": "function", "label": "helper", "module": "c",
             "source_file": "c.py", "source_location": 5},
        ],
        "edges": [
            {"source": "a", "target": "b", "relation": "importa",
             "confidence": "EXTRACTED", "count": 1},
            {"source": "b::run", "target": "c::helper", "relation": "chama",
             "confidence": "EXTRACTED", "count": 1},
            {"source": "c", "target": "b", "relation": "importa",
             "confidence": "EXTRACTED", "count": 1},
        ],
    }
    graph_path.write_text(json.dumps(payload), encoding="utf-8")
    return root


def test_helper_edges_and_gods(tmp_path: Path) -> None:
    root = _write_graph(tmp_path)
    res = subprocess.run(
        [sys.executable, str(HELPER), "--root", str(root), "--edges", "a"],
        capture_output=True, text=True, check=True,
    )
    assert "importa" in res.stdout and "EXTRACTED" in res.stdout
    gods = subprocess.run(
        [sys.executable, str(HELPER), "--root", str(root), "--gods", "--limit", "3"],
        capture_output=True, text=True, check=True,
    )
    assert "grau" in gods.stdout


def test_helper_path_and_missing_graph(tmp_path: Path) -> None:
    root = _write_graph(tmp_path)
    res = subprocess.run(
        [sys.executable, str(HELPER), "--root", str(root), "--path", "a", "c::helper"],
        capture_output=True, text=True, check=True,
    )
    # a -> b (importa), b::run -> c::helper (chama) — undirected BFS connects them
    assert "path" in res.stdout
    missing = subprocess.run(
        [sys.executable, str(HELPER), "--root", str(tmp_path / "nope"), "--gods"],
        capture_output=True, text=True,
    )
    assert missing.returncode != 0
    assert "graph not found" in missing.stderr


def test_helper_defaults_to_hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / "codebase-wiki").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    res = subprocess.run(
        [sys.executable, str(HELPER), "--gods"],
        capture_output=True, text=True,
    )
    assert "graph not found" in res.stderr  # no graph yet in HERMES_HOME
