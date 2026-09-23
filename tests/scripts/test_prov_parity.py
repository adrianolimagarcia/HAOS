"""Python/Rust provenance parity gate."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "scripts" / "prov_parity.py"


def test_parity_gate_compares_semantic_payloads_and_exit_codes() -> None:
    result = subprocess.run([sys.executable, str(PARITY)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["count"] == 10
    assert {item["case"] for item in payload["cases"]} == {
        "positive", "empty_vault", "missing_frontmatter", "malformed_frontmatter", "ghost_reference", "causal_cycle"
    }


def test_parity_gate_is_not_tautological() -> None:
    source = PARITY.read_text(encoding="utf-8")
    assert "payload" in source
    assert "exit_code" in source
    assert "left != right" in source
