#!/usr/bin/env python3
"""Forbid tests that read source text, so assertions stay about behaviour.

The root ``AGENTS.md`` bans this outright ("Never read source code in tests"): a
test that reads a module's text asserts the *shape of the source*, not what the
code does. It passes while the implementation is subtly broken — the regex still
matches a mis-wired call site — and fails on correct refactors, blocks structural
cleanup, and cannot run against bundled or minified artifacts. The prescribed fix
is to extract the logic into a pure/DI-testable function and call it.

There is no equivalent of the extract-a-function fix that a lint can perform, so
this is a ratchet rather than a hard gate: the baseline freezes each test file's
current count, existing debt is grandfathered, and any NEW read — including a
second one added to a file that already has some — fails. A count that DROPS also
fails, so the ratchet can only tighten.

Flagged:

  * ``inspect.getsource(...)``       — the module/function/class text
  * ``inspect.getsourcelines(...)``  — same, as lines
  * ``inspect.getsourcefile(...)``   — the path, then read by the caller

Deliberately NOT flagged: ``Path(...).read_text()`` on a ``.py`` literal. It is
the same defect when the path points into the repo, but it is not statically
separable from a legitimate read of a file the test itself wrote — e.g.
``assert (live / "run_agent.py").read_text() == "new"`` in the updater tests
checks a temp-dir fixture, not project source. Flagging it would trade a real
class of misses for false positives on correct tests, so that read stays a
review responsibility.

Usage:
    python scripts/ci/check_test_source_reads.py                  # gate
    python scripts/ci/check_test_source_reads.py --all            # list everything
    python scripts/ci/check_test_source_reads.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "scripts" / "ci" / "test_source_reads_baseline.txt"
SCAN_ROOT = ROOT / "tests"

# ``inspect``'s source-text readers. Any of these in a test is the banned shape.
_SOURCE_READERS = ("getsource", "getsourcelines", "getsourcefile")


@dataclass(frozen=True)
class Finding:
    rel: str
    line: int
    kind: str
    source: str

    def describe(self) -> str:
        return f"{self.rel}:{self.line}  {self.kind}  {self.source}"


def _reader_name(node: ast.Call) -> Optional[str]:
    """The source-reader name for a call, or None when it is not one."""
    fn = node.func
    name: Optional[str] = None
    if isinstance(fn, ast.Attribute):
        name = fn.attr
    elif isinstance(fn, ast.Name):
        name = fn.id
    return name if name in _SOURCE_READERS else None


def scan_file(path: Path, root: Path = ROOT) -> List[Finding]:
    """Source-text reads in one test file."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    # `ast.parse` warns about invalid escape sequences in the file it reads. Those
    # belong to the scanned file's author, not to this check.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)

    findings: List[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _reader_name(node)
            if name is not None:
                findings.append(
                    Finding(rel, node.lineno, name, ast.unparse(node)[:70])
                )
    return findings


def load_baseline(path: Optional[Path] = None) -> Dict[str, int]:
    path = BASELINE if path is None else path
    if not path.exists():
        return {}
    counts: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        try:
            counts[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return counts


def write_baseline(counts: Dict[str, int], path: Optional[Path] = None) -> None:
    path = BASELINE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{name} {count}" for name, count in sorted(counts.items()))
    path.write_text(
        "# Tests grandfathered by scripts/ci/check_test_source_reads.py — tests that\n"
        "# read source text (inspect.getsource / getsourcelines / getsourcefile),\n"
        "# frozen per file so the ratchet only tightens. The root AGENTS.md bans the\n"
        "# shape outright; the fix is to extract the logic into a pure/DI-testable\n"
        "# function and assert its behaviour. Counts, not path::test keys: a key set\n"
        "# would still miss a second read added to an already-listed test.\n"
        "# Lowering a count is how you tighten it; a count that no longer matches the\n"
        "# file fails as stale either way.\n"
        "# Regenerate with: python scripts/ci/check_test_source_reads.py --update-baseline\n"
        f"{body}\n",
        encoding="utf-8",
    )


def evaluate(files: Iterable[Path], baseline: Dict[str, int], root: Path = ROOT) -> dict:
    """Bucket findings by file. Keys: grandfathered, new, stale_baseline."""
    result: dict = {"grandfathered": [], "new": [], "stale_baseline": []}
    per_file: Dict[str, List[Finding]] = {}
    for path in sorted(files):
        for finding in scan_file(path, root):
            per_file.setdefault(finding.rel, []).append(finding)

    for rel, found in sorted(per_file.items()):
        allowed = baseline.get(rel, 0)
        # Sort so a file over its budget reports its most recent additions first.
        for finding in sorted(found, key=lambda f: f.line)[allowed:]:
            result["new"].append(finding)
        for finding in sorted(found, key=lambda f: f.line)[:allowed]:
            result["grandfathered"].append(finding)

    for rel, allowed in sorted(baseline.items()):
        actual = len(per_file.get(rel, ()))
        if actual != allowed:
            result["stale_baseline"].append(f"{rel} (baseline {allowed}, now {actual})")
    return result


def _discover(root: Path = SCAN_ROOT) -> List[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite the ratchet file from the current findings")
    ap.add_argument("--baseline", default=None, help="override the baseline path")
    ap.add_argument("--files", nargs="*", default=[], help="scan these files only")
    ap.add_argument("--json", default=None, help="also write findings as JSON here")
    ap.add_argument("--all", action="store_true",
                    help="list grandfathered findings too, not just new ones")
    args = ap.parse_args(argv)

    baseline_path = Path(args.baseline) if args.baseline else BASELINE
    files = [Path(f) for f in args.files] if args.files else _discover()
    baseline = load_baseline(baseline_path)

    if args.update_baseline:
        counts: Dict[str, int] = {}
        for path in files:
            for finding in scan_file(path):
                counts[finding.rel] = counts.get(finding.rel, 0) + 1
        write_baseline(counts, baseline_path)
        print(f"baseline updated: {len(counts)} file(s) -> {baseline_path}")
        return 0

    result = evaluate(files, baseline)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {k: [f.describe() for f in v] if k != "stale_baseline" else v
                 for k, v in result.items()},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    if args.all:
        for finding in result["grandfathered"]:
            print(f"  grandfathered  {finding.describe()}")
    for finding in result["new"]:
        print(f"  NEW            {finding.describe()}")

    total = len(result["grandfathered"]) + len(result["new"])
    print(
        f"test source reads: {total} read(s) "
        f"({len(result['grandfathered'])} grandfathered, {len(result['new'])} new)"
    )

    failed = False
    if result["new"]:
        print(
            f"\n{len(result['new'])} NEW source read(s). A test that reads source text "
            f"asserts the shape of the source, not behaviour — extract the logic into "
            f"a pure/DI-testable function and assert that instead (root AGENTS.md, "
            f"'Never read source code in tests').",
            file=sys.stderr,
        )
        failed = True
    if result["stale_baseline"]:
        print(
            f"\n{len(result['stale_baseline'])} baseline entr(y/ies) no longer match "
            f"— the ratchet must tighten: run --update-baseline.",
            file=sys.stderr,
        )
        for entry in result["stale_baseline"]:
            print(f"  stale          {entry}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
