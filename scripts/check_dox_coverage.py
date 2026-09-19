#!/usr/bin/env python3
"""Fail when a NEW module in a governed scope ships without its contract document.

The area ``AGENTS.md`` files carry the contract for a *subtree*, and most modules already open with
a docstring. Neither answers the question a reader actually has at 3am: what does THIS module own,
what may I rely on it doing, what does it write outside itself, and how do I prove it still works?
With 2,253 first-party modules the routing table alone cannot answer that per file.

A ``<name>.py.dox.md`` sibling answers it. The contract (all four sections required, each with
body text):

    ## Purpose       what this module owns; the boundary it must not cross
    ## Contract      public surface callers depend on, and the invariants that must hold
    ## Side effects  filesystem / network / process / env / global-state writes; "None." if pure
    ## Verification  the command or test that proves a change here still works

Retrofitting this onto 483 existing modules at once would be a wall of stub files, so the check is
a RATCHET: ``scripts/ci/dox_coverage_baseline.txt`` grandfathers what exists today, and only files
that are NOT grandfathered are required to ship docs. A grandfathered file that gains its doc must
be dropped from the baseline (``--update-baseline``) — a stale baseline is itself a failure, so the
ratchet can only tighten.

``--report`` prints coverage per scope and always exits 0, so the backlog is visible without
blocking anything.

Usage:
    python scripts/check_dox_coverage.py                      # enforce (exit 1 on a new gap)
    python scripts/check_dox_coverage.py --report             # coverage stats, never fails
    python scripts/check_dox_coverage.py --files a.py b.py    # only these files (PR-diff mode)
    python scripts/check_dox_coverage.py --update-baseline    # regenerate the ratchet file
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "ci" / "dox_coverage_baseline.txt"

# Scopes are repo-relative. Start with the two trees where a per-file contract pays for itself:
# the tool implementations and the HAOS platform layer. Widen deliberately, never by default.
DEFAULT_SCOPE = ("tools", "hermes/platform")

REQUIRED_SECTIONS = ("purpose", "contract", "side effects", "verification")

# A package marker has no contract of its own; a generated/compiled tree is not ours to document.
SKIP_NAMES = {"__init__.py"}
SKIP_DIR_PARTS = {"__pycache__", ".venv", "venv", "node_modules", "build", "dist", ".worktrees"}

_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


def dox_path(py: Path) -> Path:
    """``tools/foo.py`` -> ``tools/foo.py.dox.md`` (appended, so the pairing is unambiguous)."""
    return py.with_name(py.name + ".dox.md")


def parse_sections(text: str) -> dict[str, str]:
    """Map normalized heading text -> body. Only headings at level 2+ count as sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = match.group(2).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(body) for name, body in sections.items()}


def missing_sections(dox_text: str) -> list[str]:
    """Required sections absent, or present with no body text under them.

    A heading-only section is the failure mode this exists to prevent: it satisfies a grep for
    "## Contract" while telling the reader nothing.
    """
    sections = parse_sections(dox_text)
    missing = []
    for name in REQUIRED_SECTIONS:
        body = sections.get(name)
        if body is None or not any(line.strip() for line in body.splitlines()):
            missing.append(name)
    return missing


def iter_scope_files(scope: Iterable[str], root: Path | None = None) -> Iterator[Path]:
    """Every first-party ``*.py`` in *scope*, sorted for deterministic output."""
    root = ROOT if root is None else root
    for entry in scope:
        base = root / entry
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in candidates:
            if path.name in SKIP_NAMES:
                continue
            if SKIP_DIR_PARTS & set(path.relative_to(root).parts):
                continue
            yield path


def rel(path: Path, root: Path | None = None) -> str:
    return path.relative_to(ROOT if root is None else root).as_posix()


def load_baseline(path: Path | None = None) -> set[str]:
    path = BASELINE if path is None else path
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(line)
    return entries


def write_baseline(paths: Iterable[str], path: Path | None = None) -> None:
    path = BASELINE if path is None else path
    header = (
        "# Modules grandfathered by scripts/check_dox_coverage.py — they predate the per-file\n"
        "# contract and are exempt until someone documents them. Adding a line here is how you\n"
        "# deliberately skip a module; removing one is how the ratchet tightens.\n"
        "# Regenerate with: python scripts/check_dox_coverage.py --update-baseline\n"
    )
    body = "\n".join(sorted(set(paths)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body + "\n", encoding="utf-8")


def evaluate(files: Iterable[Path], baseline: set[str], root: Path | None = None) -> dict[str, list[str]]:
    """Bucket every file. Keys: documented, grandfathered, gap, malformed, stale_baseline."""
    root = ROOT if root is None else root
    result: dict[str, list[str]] = {
        "documented": [],
        "grandfathered": [],
        "gap": [],
        "malformed": [],
        "stale_baseline": [],
    }
    for py in files:
        name = rel(py, root)
        doc = dox_path(py)
        if not doc.exists():
            (result["grandfathered"] if name in baseline else result["gap"]).append(name)
            continue
        missing = missing_sections(doc.read_text(encoding="utf-8", errors="ignore"))
        if missing:
            result["malformed"].append(f"{name}: missing {', '.join(missing)}")
            continue
        result["documented"].append(name)
        if name in baseline:
            result["stale_baseline"].append(name)
    return result


def print_report(result: dict[str, list[str]], scope: Iterable[str]) -> None:
    total = sum(len(v) for k, v in result.items() if k in {"documented", "grandfathered", "gap"})
    documented = len(result["documented"])
    pct = (100.0 * documented / total) if total else 100.0
    print(f"dox coverage: {documented}/{total} ({pct:.1f}%)  scope: {', '.join(scope)}")
    print(f"  documented     {documented}")
    print(f"  grandfathered  {len(result['grandfathered'])}")
    print(f"  gap (new)      {len(result['gap'])}")
    print(f"  malformed      {len(result['malformed'])}")
    print(f"  stale baseline {len(result['stale_baseline'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scope", nargs="+", default=list(DEFAULT_SCOPE),
                        help=f"repo-relative files or dirs (default: {' '.join(DEFAULT_SCOPE)})")
    parser.add_argument("--files", nargs="+", default=None,
                        help="check only these repo-relative paths (PR-diff mode)")
    parser.add_argument("--report", action="store_true", help="print coverage and always exit 0")
    parser.add_argument("--json", dest="json_out", default=None, help="write the result as JSON")
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the ratchet file from the current tree, then exit")
    args = parser.parse_args(argv)

    if args.files is not None:
        files = [ROOT / f for f in args.files]
        missing_files = [f for f, p in zip(args.files, files) if not p.is_file()]
        if missing_files:
            print(f"not a file: {', '.join(missing_files)}", file=sys.stderr)
            return 2
    else:
        files = list(iter_scope_files(args.scope))

    if args.update_baseline:
        # With an empty baseline every undocumented file lands in "gap" — that is the set to
        # grandfather. A malformed doc is NOT grandfathered: the file is claimed as documented
        # and must be fixed rather than exempted.
        result = evaluate(files, baseline=set())
        write_baseline(result["gap"] + result["grandfathered"])
        print(f"baseline written: {BASELINE.relative_to(ROOT)} "
              f"({len(result['gap'])} grandfathered)")
        if result["malformed"]:
            print(f"{len(result['malformed'])} file(s) have an incomplete doc and are NOT "
                  "grandfathered — fix them:", file=sys.stderr)
            for entry in result["malformed"][:20]:
                print(f"  {entry}", file=sys.stderr)
        return 0

    baseline = load_baseline()
    result = evaluate(files, baseline)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    if args.report:
        print_report(result, args.scope)
        if result["gap"]:
            print("\nundocumented (not yet grandfathered):")
            for name in result["gap"][:20]:
                print(f"  {name}")
            if len(result["gap"]) > 20:
                print(f"  ... and {len(result['gap']) - 20} more")
        return 0

    failed = False

    for entry in result["malformed"]:
        name, _, detail = entry.partition(": ")
        print(f"{name}: {dox_path(ROOT / name).relative_to(ROOT)} exists but is incomplete — {detail}")
        failed = True

    if result["stale_baseline"]:
        print("baseline lists modules that are now documented — run "
              "`python scripts/check_dox_coverage.py --update-baseline` to tighten the ratchet:")
        for name in result["stale_baseline"]:
            print(f"  {name}")
        failed = True

    if result["gap"]:
        print("new modules in a governed scope must ship a contract document "
              f"({', '.join(REQUIRED_SECTIONS)}):")
        for name in result["gap"][:20]:
            print(f"  {name}  ->  {dox_path(ROOT / name).relative_to(ROOT)}")
        if len(result["gap"]) > 20:
            print(f"  ... and {len(result['gap']) - 20} more")
        print("\nSee scripts/check_dox_coverage.py for the section contract. "
              "To grandfather deliberately, add the path to "
              f"{BASELINE.relative_to(ROOT)}.")
        failed = True

    if failed:
        return 1

    print(f"dox coverage ok: {len(result['documented'])} documented, "
          f"{len(result['grandfathered'])} grandfathered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
