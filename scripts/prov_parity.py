#!/usr/bin/env python3
"""Run Python/Rust PROV parity checks against controlled vault fixtures."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "tools" / "adr_prov.py"
FIXTURES = ROOT / "tests" / "fixtures" / "adr_prov"
DEFAULT_BINARY = ROOT / "target" / "debug" / "haos-prov"
CASES = {
    "positive": (FIXTURES / "vault", [("graph",), ("check",), ("why", "ADR-015"), ("desc", "ADR-015")]),
    "empty_vault": (FIXTURES / "negative_vaults" / "empty", [("check",)]),
    "missing_frontmatter": (FIXTURES / "negative_vaults" / "missing", [("check",)]),
    "malformed_frontmatter": (FIXTURES / "negative_vaults" / "malformed", [("check",)]),
    "ghost_reference": (FIXTURES / "negative_vaults" / "ghost", [("check",)]),
    "causal_cycle": (FIXTURES / "negative_vaults" / "cycle", [("check",), ("why", "ADR-001")]),
}


def run(command: list[str], vault: Path) -> dict[str, Any]:
    env = dict(os.environ)
    if command and command[0] == sys.executable:
        insertion = 2
    else:
        insertion = 1
    completed = subprocess.run(
        [*command[:insertion], "--vault", str(vault), "--json", *command[insertion:]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload: Any = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError as exc:
        payload = {"invalid_json": str(exc), "stdout": completed.stdout}
    return {"exit_code": completed.returncode, "payload": payload, "stderr": completed.stderr}


def normalize(result: dict[str, Any]) -> dict[str, Any]:
    """Discard only presentation text; retain semantic payload and exit status."""
    payload = result["payload"]
    if isinstance(payload, dict):
        payload = _normalize_payload(payload)
    return {"exit_code": result["exit_code"], "payload": payload}


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "GhostReference" in str(payload):
        ghosts = []
        for item in payload.get("ghosts", []):
            detail = item.get("GhostReference", {})
            ghosts.append({
                "source": detail.get("source"),
                "relation": _relation(detail.get("relation")),
                "target": detail.get("target"),
            })
        payload = {**payload, "ghosts": ghosts}
    if "edges" in payload:
        payload = {**payload, "edges": [
            {**edge, "relation": _relation(edge.get("relation"))} for edge in payload["edges"]
        ]}
    return payload


def _relation(value: Any) -> Any:
    return {"causadoBy": "causado_by", "wasDerivedFrom": "wasDerivedFrom", "supersedes": "supersedes", "supersededBy": "superseded_by"}.get(value, value)


def compare_case(name: str, vault: Path, args: tuple[str, ...], binary: Path) -> dict[str, Any]:
    py = run([sys.executable, str(ORACLE), *args], vault)
    rust = run([str(binary), *args], vault)
    left, right = normalize(py), normalize(rust)
    if left != right:
        raise SystemExit(json.dumps({"case": name, "args": args, "python": left, "rust": right}, ensure_ascii=False, indent=2))
    return {"case": name, "args": list(args), "exit_code": left["exit_code"], "payload": left["payload"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    if not args.binary.is_file():
        raise SystemExit(f"Rust binary not found: {args.binary}; run cargo build -p haos-prov")
    selected = CASES if args.case == "all" else {args.case: CASES[args.case]}
    results = []
    for name, (vault, commands) in selected.items():
        for command in commands:
            results.append(compare_case(name, vault, command, args.binary.resolve()))
    print(json.dumps({"cases": results, "count": len(results)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
