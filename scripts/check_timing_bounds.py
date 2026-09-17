#!/usr/bin/env python3
"""Advisory ratchet: wall-clock upper bounds in tests must be >= 2s.

Why this exists
---------------
``AGENTS.md`` states the rule — "Timing tests must not assume a quiet runner:
wall-clock bounds >= 2s, event-based sync" — and the suite violates it in dozens
of places. ``scripts/run_tests.sh`` runs 16 files in parallel, so no test ever
sees an idle machine: an absolute wall-clock bound measures the runner's load,
not the code. Two tests broke exactly that way on 2026-09-17 (one failed both
retries at 2.013s against a 1.5s bound).

This is a RATCHET, not a cleanup: ``scripts/ci/timing_bounds_baseline.txt``
grandfathers what exists today, and only NEW tight bounds fail. Fixing a
grandfathered bound (or making the assertion relative) means dropping its line
from the baseline (``--update-baseline``) — a stale baseline is itself a failure,
so the ratchet only ever tightens.

Advisory, not blocking: a tight bound is not automatically a defect. What makes
a test fragile is the MARGIN between its bound and its observed duration, and
only a run can measure that. A bound of 1.0s on a 1ms operation has 1000x of
headroom and is fine despite violating the letter of the rule. Treat a finding
here as "look at this one", and the flake ledger
(``scripts/run_tests_parallel.py --flake-log``) as the evidence that decides.

Usage
-----
    python scripts/check_timing_bounds.py                     # check, exit 1 on new
    python scripts/check_timing_bounds.py --update-baseline   # regenerate ratchet
    python scripts/check_timing_bounds.py --json out.json     # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "ci" / "timing_bounds_baseline.txt"
SCAN_ROOT = ROOT / "tests"

# The rule from AGENTS.md: a wall-clock upper bound below this is too tight to
# survive a 16-way parallel runner.
MIN_BOUND_SECONDS = 2.0

# An expression is a measured elapsed time if it reads a monotonic clock...
_CLOCK = re.compile(r"monotonic\(\)|perf_counter\(\)|process_time\(\)|time\.time\(\)")
# ...or is a bare name that can only mean elapsed time. Anchored on purpose:
# ``total_count`` and ``total_bytes`` are not clocks, and a substring match
# would sweep in every count/byte assertion in the suite.
_ELAPSED_NAME = re.compile(
    r"^(_?elapsed\w*|_?duration\w*|_?took\w*|_?wall\w*|_?latency\w*)$"
)
# Names that look temporal but are data, not elapsed time.
_NOT_TIME = re.compile(
    r"count|bytes|chars|models|cost|tokens|total_|_total|seconds_ago|"
    r"min_duration|max_duration|remaining_seconds|timeout_seconds|_seconds\(\)",
    re.IGNORECASE,
)

_ORDERED = (ast.Lt, ast.LtE)


@dataclass(frozen=True)
class Finding:
    key: str          # stable ratchet key: <repo-relative file>::<test name>
    rel: str
    test: str
    line: int
    source: str
    bound: float

    def describe(self) -> str:
        return f"{self.rel}:{self.line}  {self.source} < {self.bound}  ({self.test})"


def _is_elapsed_time(expr: ast.AST) -> bool:
    src = ast.unparse(expr)
    if _NOT_TIME.search(src):
        return False
    return bool(_CLOCK.search(src)) or bool(_ELAPSED_NAME.match(src.strip()))


def _enclosing_test(tree: ast.AST) -> List[Tuple[str, ast.AST]]:
    """Every function/method node paired with its own name, for key attribution."""
    out: List[Tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
    return out


def _test_name_for(node: ast.AST, funcs: List[Tuple[str, ast.AST]]) -> str:
    """Innermost enclosing function name for a line, or '<module>'."""
    best: Optional[Tuple[int, str]] = None
    for name, fn in funcs:
        if fn.lineno <= node.lineno <= (getattr(fn, "end_lineno", fn.lineno) or fn.lineno):
            span = (getattr(fn, "end_lineno", fn.lineno) or fn.lineno) - fn.lineno
            if best is None or span < best[0]:
                best = (span, name)
    return best[1] if best else "<module>"


def scan_file(path: Path, root: Path = ROOT) -> List[Finding]:
    """Tight wall-clock upper bounds in one test file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    rel = str(path.relative_to(root))
    funcs = _enclosing_test(tree)
    findings: List[Finding] = []
    for node in ast.walk(tree):
        pairs: List[Tuple[ast.AST, float]] = []
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("assertLess", "assertLessEqual") and len(node.args) >= 2:
                right = node.args[1]
                if isinstance(right, ast.Constant) and isinstance(right.value, (int, float)):
                    pairs.append((node.args[0], float(right.value)))
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
            cmp = node.test
            if len(cmp.ops) == 1 and isinstance(cmp.ops[0], _ORDERED):
                right = cmp.comparators[0]
                if isinstance(right, ast.Constant) and isinstance(right.value, (int, float)):
                    pairs.append((cmp.left, float(right.value)))
        for expr, bound in pairs:
            if bound >= MIN_BOUND_SECONDS:
                continue
            if not _is_elapsed_time(expr):
                continue
            test = _test_name_for(node, funcs)
            findings.append(
                Finding(
                    key=f"{rel}::{test}",
                    rel=rel,
                    test=test,
                    line=node.lineno,
                    source=ast.unparse(expr)[:60],
                    bound=bound,
                )
            )
    return findings


def load_baseline(path: Optional[Path] = None) -> Set[str]:
    path = BASELINE if path is None else path
    if not path.exists():
        return set()
    keys: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keys.add(line)
    return keys


def write_baseline(keys: Iterable[str], path: Optional[Path] = None) -> None:
    path = BASELINE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sorted(set(keys)))
    path.write_text(
        "# Tests grandfathered by scripts/check_timing_bounds.py — wall-clock upper\n"
        "# bounds below the 2s floor from AGENTS.md, frozen so the ratchet only tightens.\n"
        "# Removing a line here is how you tighten it; leaving a line for a bound that\n"
        "# no longer exists fails as stale.\n"
        "# Regenerate with: python scripts/check_timing_bounds.py --update-baseline\n"
        f"{body}\n",
        encoding="utf-8",
    )


def evaluate(files: Iterable[Path], baseline: Set[str], root: Path = ROOT) -> dict:
    """Bucket findings. Keys: grandfathered, new, stale_baseline."""
    result: dict = {"grandfathered": [], "new": [], "stale_baseline": []}
    seen: Set[str] = set()
    for path in sorted(files):
        for finding in scan_file(path, root):
            seen.add(finding.key)
            bucket = "grandfathered" if finding.key in baseline else "new"
            result[bucket].append(finding)
    result["stale_baseline"] = sorted(baseline - seen)
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
        keys = {f.key for path in files for f in scan_file(path)}
        write_baseline(keys, baseline_path)
        print(f"baseline updated: {len(keys)} grandfathered bound(s) -> {baseline_path}")
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
        f"timing bounds: {total} tight bound(s) "
        f"({len(result['grandfathered'])} grandfathered, {len(result['new'])} new)"
    )

    failed = False
    if result["new"]:
        print(
            f"\n{len(result['new'])} NEW wall-clock bound(s) below "
            f"{MIN_BOUND_SECONDS}s. Either widen the bound / the separation it "
            f"relies on, or make the assertion relative — then run "
            f"--update-baseline if you deliberately accept it.",
            file=sys.stderr,
        )
        failed = True
    if result["stale_baseline"]:
        print(
            f"\n{len(result['stale_baseline'])} stale baseline entr(y/ies) — the bound "
            f"is gone, so the ratchet must tighten: run --update-baseline.",
            file=sys.stderr,
        )
        for key in result["stale_baseline"]:
            print(f"  stale          {key}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
