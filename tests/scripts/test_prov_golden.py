"""Contract tests for the versioned PROV-O golden corpus."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "adr_prov"
GOLDEN = FIXTURES / "golden.json"
NEGATIVE = FIXTURES / "negative.json"
GENERATOR = ROOT / "scripts" / "prov_golden.py"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_golden_has_structural_contract_and_all_operations() -> None:
    data = load("golden.json")
    assert data["schema_version"] == 1
    assert set(data["commands"]) == {"why_ADR-015", "desc_ADR-015", "effects_ADR-015", "graph", "check"}
    assert len(data["inventory"]) >= 15
    assert data["commands"]["graph"]["structure"]["nodes"] == sorted(data["inventory"])
    assert data["commands"]["check"]["structure"]["node_count"] == len(data["inventory"])
    assert data["commands"]["check"]["structure"]["ghosts"] == []
    assert data["commands"]["why_ADR-015"]["structure"]["root"] == "ADR-015"
    assert data["commands"]["desc_ADR-015"]["structure"] == data["commands"]["effects_ADR-015"]["structure"]
    for command in data["commands"].values():
        assert command["exit_code"] == 0
        assert len(command["stdout_sha256"]) == 64


def test_negative_corpus_covers_diagnostics_deterministically() -> None:
    cases = {case["case"]: case for case in load("negative.json")["cases"]}
    assert set(cases) == {"empty_vault", "missing_frontmatter", "malformed_frontmatter", "ghost_reference", "causal_cycle"}
    assert cases["empty_vault"]["node_count"] == 0
    assert cases["missing_frontmatter"]["node_count"] == 0
    assert cases["malformed_frontmatter"]["node_count"] == 0
    assert cases["ghost_reference"]["node_count"] == 1
    assert len(cases["ghost_reference"]["ghosts"]) == 2
    assert cases["causal_cycle"]["node_count"] == 2


def test_generator_verifier_regenerates_against_fixture_copy() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "PASS (5 commands, 5 negative cases)" in result.stdout
