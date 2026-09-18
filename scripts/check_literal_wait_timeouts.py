#!/usr/bin/env python3
"""Forbid literal timeouts in test waits, so every budget is a justified name.

Why this exists — and why ``check_timing_bounds.py`` cannot cover it:

That check reads *assertion bounds* (``assert elapsed < 5``). The 2026-09 flake
census over 3 full-suite runs found 7 of 9 distinct flakes were NOT assertion
bounds: they were waits on work another thread, task or subprocess had to finish
(``Event.wait(timeout=5)``, ``asyncio.wait_for(..., timeout=2)``, a
``sleep(0.05)`` poll loop, a lease read against the real clock). The bounds
ratchet sat at "61 grandfathered, 0 new" through every one of them, because it
never looked at a wait call. This is the missing half.

A number written inline at the wait site cannot be reviewed: nothing records
whether it was sized against a contract, a production default, or a guess that
happened to pass on a quiet machine. Naming it forces the justification into the
diff, where a reviewer can see it:

    # 16x the 0.3s configured budget, half the 10s production default.
    _TURN_HOLD_LIVENESS_SECONDS = 5.0
    ...
    await asyncio.wait_for(task, timeout=_TURN_HOLD_LIVENESS_SECONDS)

Flagged call shapes:

  * ``x.wait(N)`` / ``wait(timeout=N)``      — threading.Event, Condition, asyncio
  * ``t.join(N)``  / ``join(timeout=N)``     — thread/process completion
  * ``f.result(N)`` / ``result(timeout=N)``  — concurrent.futures
  * ``asyncio.wait_for(coro, N)``            — timeout is the SECOND positional
  * ``time.monotonic() + N``                 — deadline arithmetic

NOT flagged, deliberately:

  * ``time.sleep(N)`` — a deliberate delay is not a wait for someone else's
    completion, and flagging it would sweep in ~1200 legitimate pacing calls.
  * Any non-literal timeout (``timeout=_SOME_NAME``) — that is the fix.

Ratchet, not a hard gate: 1942 waits and 378 deadlines predate this check. The
baseline freezes each file's CURRENT COUNT, so existing debt is grandfathered
while any *new* literal in any file — including a second one added to a test
that already had one — fails. Counts rather than ``path::test`` keys on purpose:
at this volume a key set would run to ~1150 lines and would still miss the
second literal, because both share one key. A count that DROPS also fails, so
the ratchet can only tighten.

Usage:
    python scripts/check_literal_wait_timeouts.py                  # gate
    python scripts/check_literal_wait_timeouts.py --all            # list everything
    python scripts/check_literal_wait_timeouts.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "ci" / "literal_wait_timeouts_baseline.txt"
SCAN_ROOT = ROOT / "tests"

# primitive name -> positional index that carries the timeout.
# ``asyncio.wait_for(coro, timeout)`` puts it second; everything else first.
_WAIT_PRIMITIVES: Dict[str, int] = {
    "wait": 0,
    "join": 0,
    "result": 0,
    "wait_for": 1,
}

_CLOCKS = ("monotonic()", "perf_counter()", "process_time()", "time.time()")


@dataclass(frozen=True)
class Finding:
    rel: str
    line: int
    kind: str
    value: float
    source: str

    def describe(self) -> str:
        return f"{self.rel}:{self.line}  {self.kind}({self.value})  {self.source}"


def _call_name(node: ast.Call) -> Optional[str]:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _literal_timeout(node: ast.Call, name: str) -> Optional[float]:
    """The literal timeout on a wait call, or None when it is named/computed."""
    for kw in node.keywords:
        if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, (int, float)) and not isinstance(kw.value.value, bool):
                return float(kw.value.value)
    idx = _WAIT_PRIMITIVES[name]
    if len(node.args) > idx:
        arg = node.args[idx]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
            if not isinstance(arg.value, bool):
                return float(arg.value)
    return None


def _deadline_literal(node: ast.AST) -> Optional[Tuple[float, str]]:
    """``<clock>() + N`` (either operand order) -> (N, source)."""
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
        return None
    for clock_side, num_side in ((node.left, node.right), (node.right, node.left)):
        if not isinstance(num_side, ast.Constant):
            continue
        if not isinstance(num_side.value, (int, float)) or isinstance(num_side.value, bool):
            continue
        rendered = ast.unparse(clock_side)
        if any(c in rendered for c in _CLOCKS):
            return float(num_side.value), ast.unparse(node)
    return None


def scan_file(path: Path, root: Path = ROOT) -> List[Finding]:
    """Literal wait timeouts and literal deadline arithmetic in one test file."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    # `ast.parse` warns about invalid escape sequences in the file it reads. Those
    # belong to the scanned file's author, not to this check — a lint that inspects
    # a file should not print diagnostics about it.
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
            name = _call_name(node)
            if name in _WAIT_PRIMITIVES:
                value = _literal_timeout(node, name)
                if value is not None:
                    findings.append(
                        Finding(rel, node.lineno, name, value, ast.unparse(node)[:70])
                    )
        deadline = _deadline_literal(node)
        if deadline is not None:
            value, src = deadline
            findings.append(Finding(rel, node.lineno, "deadline", value, src[:70]))
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
        "# Tests grandfathered by scripts/check_literal_wait_timeouts.py — literal\n"
        "# timeouts in test waits (Event.wait / Thread.join / Future.result /\n"
        "# asyncio.wait_for) and literal `clock() + N` deadlines, frozen per file so\n"
        "# the ratchet only tightens. Counts, not path::test keys: at this volume a\n"
        "# key set would still miss a second literal added to an already-listed test.\n"
        "# Lowering a count is how you tighten it; a count that no longer matches the\n"
        "# file fails as stale either way.\n"
        "# Regenerate with: python scripts/check_literal_wait_timeouts.py --update-baseline\n"
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
        f"literal wait timeouts: {total} literal(s) "
        f"({len(result['grandfathered'])} grandfathered, {len(result['new'])} new)"
    )

    failed = False
    if result["new"]:
        print(
            f"\n{len(result['new'])} NEW literal wait timeout(s). Give the budget a "
            f"name that says what it is sized against (a configured value, a "
            f"production default, a measured duration) and use that name — then run "
            f"--update-baseline if you deliberately accept the literal.",
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
