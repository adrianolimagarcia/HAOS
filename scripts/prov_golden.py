#!/usr/bin/env python3
"""Generate and verify deterministic PROV-O golden fixtures.

The checked-in vault is the default input.  ``--update --source-vault`` may be
used to refresh it from a read-only source vault; all writes stay under this
repository.  Every oracle invocation is the current ``tools/adr_prov.py``
CLI, executed against a temporary copy of the selected fixture vault.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "adr_prov"
VAULT_ROOT = FIXTURE_ROOT / "vault"
GOLDEN_PATH = FIXTURE_ROOT / "golden.json"
NEGATIVE_PATH = FIXTURE_ROOT / "negative.json"
ORACLE = ROOT / "tools" / "adr_prov.py"
SCHEMA_VERSION = 1


def _oracle_module():
    spec = importlib.util.spec_from_file_location("adr_prov_oracle", ORACLE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load oracle: {ORACLE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json(value: Any) -> Any:
    """Convert the oracle's dataclasses into JSON-safe stable values."""
    if isinstance(value, Path):
        return value.name
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def node_record(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "titulo": node.titulo,
        "status": node.status,
        "file": node.file_path.name,
        "causado_by": list(node.causado_by),
        "evidence": list(node.evidence),
        "affects": list(node.affects),
        "supersedes": list(node.supersedes),
        "superseded_by": node.superseded_by,
        "prov": {
            "wasGeneratedBy": node.was_generated_by,
            "wasAssociatedWith": node.was_associated_with,
            "wasDerivedFrom": list(node.was_derived_from),
            "causado_by": node.prov_causado_by,
            "affects": list(node.prov_affects),
            "supersedes": list(node.prov_supersedes),
            "superseded_by": node.prov_superseded_by,
        },
    }


def inventory(nodes: dict[str, Any]) -> dict[str, Any]:
    return {key: node_record(nodes[key]) for key in sorted(nodes)}


def edges(nodes: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for adr_id in sorted(nodes):
        node = nodes[adr_id]
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                result.append({"source": adr_id, "relation": "causado_by", "target": normalize(node, item)})
        for item in node.was_derived_from:
            result.append({
                "source": adr_id,
                "relation": "wasDerivedFrom",
                "target": normalize(node, item) if item.lower().startswith("adr:") else item,
            })
        for item in node.supersedes:
            result.append({"source": adr_id, "relation": "supersedes", "target": normalize(node, item)})
        if node.superseded_by:
            result.append({"source": adr_id, "relation": "superseded_by", "target": normalize(node, node.superseded_by)})
        if node.was_generated_by:
            result.append({"source": adr_id, "relation": "wasGeneratedBy", "target": node.was_generated_by})
    return result


def normalize(_node: Any, value: str) -> str:
    # Kept as a tiny adapter so all normalization is delegated to the oracle.
    return _oracle_module().normalize_adr_id(value)


def why_structure(nodes: dict[str, Any], target: str, oracle: Any) -> dict[str, Any]:
    root = oracle.normalize_adr_id(target)
    if root not in nodes:
        return {"root": root, "error": "not_found"}
    visited: set[str] = set()
    trace: list[dict[str, Any]] = []

    def visit(curr: str, depth: int, via: str) -> None:
        if curr not in nodes:
            trace.append({"event": "unresolved", "id": curr, "depth": depth, "via": via})
            return
        node = nodes[curr]
        trace.append({"event": "node", "id": curr, "depth": depth, "via": via})
        if curr in visited:
            trace.append({"event": "cycle", "id": curr, "depth": depth})
            return
        visited.add(curr)
        parents: list[tuple[str, str]] = []
        for item in node.causado_by:
            if item.lower().startswith("adr:"):
                pair = (oracle.normalize_adr_id(item), "causado_by")
                if pair not in parents:
                    parents.append(pair)
        for item in node.was_derived_from:
            if item.lower().startswith("adr:"):
                pair = (oracle.normalize_adr_id(item), "wasDerivedFrom")
                if pair not in parents:
                    parents.append(pair)
        for parent, relation in parents:
            visit(parent, depth + 1, relation)

    visit(root, 0, "root")
    return {
        "root": root,
        "trace": trace,
        "cycle_ids": sorted({x["id"] for x in trace if x["event"] == "cycle"}),
        "unresolved_ids": sorted({x["id"] for x in trace if x["event"] == "unresolved"}),
    }


def desc_structure(nodes: dict[str, Any], target: str, oracle: Any) -> dict[str, Any]:
    root = oracle.normalize_adr_id(target)
    if root not in nodes:
        return {"root": root, "error": "not_found"}
    node = nodes[root]
    children = {"causado_by": set(), "wasDerivedFrom": set(), "supersedes": set()}
    for other_id, other in sorted(nodes.items()):
        for item in other.causado_by:
            if item.lower().startswith("adr:") and oracle.normalize_adr_id(item) == root:
                children["causado_by"].add(other_id)
        for item in other.was_derived_from:
            if item.lower().startswith("adr:") and oracle.normalize_adr_id(item) == root:
                children["wasDerivedFrom"].add(other_id)
        for item in other.supersedes:
            if oracle.normalize_adr_id(item) == root:
                children["supersedes"].add(other_id)
    return {
        "root": root,
        "superseded_by": node.superseded_by,
        "supersedes": list(node.supersedes),
        "children": {key: sorted(value) for key, value in children.items()},
        "affects": list(node.affects),
    }


def run_cli(vault: Path, args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(ORACLE), "--vault", str(vault), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "argv": args,
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "stdout_bytes": len(completed.stdout.encode()),
        "stderr_bytes": len(completed.stderr.encode()),
    }


def build_snapshot(vault: Path) -> dict[str, Any]:
    oracle = _oracle_module()
    nodes, warnings = oracle.load_adr_vault(vault)
    commands: dict[str, Any] = {}
    for name, args, structure in (
        ("why_ADR-015", ["why", "ADR-015"], why_structure(nodes, "ADR-015", oracle)),
        ("desc_ADR-015", ["desc", "ADR-015"], desc_structure(nodes, "ADR-015", oracle)),
        ("effects_ADR-015", ["effects", "ADR-015"], desc_structure(nodes, "ADR-015", oracle)),
        ("graph", ["graph"], {"nodes": sorted(nodes), "edges": edges(nodes)}),
        (
            "check",
            ["check"],
            {
                "warnings": list(warnings),
                "ghosts": oracle.check_ghost_entities(nodes),
                "nodes": sorted(nodes),
                "node_count": len(nodes),
            },
        ),
    ):
        command = run_cli(vault, args)
        command["structure"] = structure
        commands[name] = command
    return {
        "schema_version": SCHEMA_VERSION,
        "oracle": "tools/adr_prov.py",
        "vault": "tests/fixtures/adr_prov/vault",
        "inventory": inventory(nodes),
        "commands": commands,
    }


def copy_vault(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"vault source is not a directory: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.md")):
        shutil.copyfile(path, destination / path.name)


def build_negative(vault: Path, name: str, args: list[str]) -> dict[str, Any]:
    oracle = _oracle_module()
    nodes, warnings = oracle.load_adr_vault(vault)
    result = run_cli(vault, args)
    result.update({
        "case": name,
        "warnings": list(warnings),
        "ghosts": oracle.check_ghost_entities(nodes),
        "nodes": sorted(nodes),
        "node_count": len(nodes),
    })
    return result


def update(source: Path) -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    if source.resolve() != VAULT_ROOT.resolve():
        if VAULT_ROOT.exists():
            shutil.rmtree(VAULT_ROOT)
        copy_vault(source, VAULT_ROOT)
    with tempfile.TemporaryDirectory(prefix="adr-prov-golden-") as temp:
        controlled = Path(temp) / "vault"
        copy_vault(VAULT_ROOT, controlled)
        golden = build_snapshot(controlled)
        cases = [
            ("empty_vault", FIXTURE_ROOT / "negative_vaults" / "empty", ["check"]),
            ("missing_frontmatter", FIXTURE_ROOT / "negative_vaults" / "missing", ["check"]),
            ("malformed_frontmatter", FIXTURE_ROOT / "negative_vaults" / "malformed", ["check"]),
            ("ghost_reference", FIXTURE_ROOT / "negative_vaults" / "ghost", ["check"]),
            ("causal_cycle", FIXTURE_ROOT / "negative_vaults" / "cycle", ["why", "ADR-001"]),
        ]
        negative = {
            "schema_version": SCHEMA_VERSION,
            "oracle": "tools/adr_prov.py",
            "cases": [build_negative(path, name, args) for name, path, args in cases],
        }
    GOLDEN_PATH.write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    NEGATIVE_PATH.write_text(json.dumps(negative, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify() -> None:
    with tempfile.TemporaryDirectory(prefix="adr-prov-verify-") as temp:
        controlled = Path(temp) / "vault"
        copy_vault(VAULT_ROOT, controlled)
        actual = build_snapshot(controlled)
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if actual != expected:
        raise SystemExit("golden mismatch; run scripts/prov_golden.py --update")
    expected_negative = json.loads(NEGATIVE_PATH.read_text(encoding="utf-8"))
    for case in expected_negative["cases"]:
        path = FIXTURE_ROOT / "negative_vaults" / {
            "empty_vault": "empty", "missing_frontmatter": "missing", "malformed_frontmatter": "malformed",
            "ghost_reference": "ghost", "causal_cycle": "cycle",
        }[case["case"]]
        args = case["argv"]
        actual_case = build_negative(path, case["case"], args)
        if actual_case != case:
            raise SystemExit(f"negative fixture mismatch: {case['case']}")
    print("PROV golden fixtures: PASS (5 commands, 5 negative cases)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="regenerate checked-in fixtures")
    parser.add_argument("--source-vault", type=Path, default=VAULT_ROOT)
    args = parser.parse_args()
    if args.update:
        update(args.source_vault.resolve())
        print(f"updated {GOLDEN_PATH} and {NEGATIVE_PATH}")
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
