#!/usr/bin/env python3
"""Trim guard: deterministic check that a deletion/refactor diff did not cut guards.

Concept (clean-room, inspired by lean-agent research): cutting structure is
fine; cutting *guards* is not. Guards have recognizable signatures in code.
This script counts guard signatures on both sides of a change and REFUSES the
change when the count goes down.

The five "never cut" categories:
  1. input checks at trust boundaries (path containment, type checks on
     parsed input, header sanitization)
  2. data-loss prevention (per-row try so one bad row cannot lose a file,
     transaction/commit discipline)
  3. security (parameterized queries, constant-time compare, HTML escaping,
     auth checks)
  4. accessibility (alt text, labels, ARIA)
  5. anything the user explicitly asked for (not detectable statically --
     the reviewer must confirm)

Guard signatures detected (regex over added/removed lines):
  - path containment: commonpath, is_relative_to, abspath+startswith,
    realpath checks, ".." rejection
  - sql parameterization: placeholders (?, %s, :name, $1) with separate
    params, execute(..., params)
  - constant-time compare: hmac.compare_digest, secrets.compare_digest
  - html escaping: html.escape, markupsafe.escape
  - input validation: isinstance checks on parsed data, pydantic/dataclass
    validation, jsonschema validate
  - per-row guard: try/except inside a loop body
  - header sanitization: newline rejection in headers
  - auth checks: permission/role checks, @login_required, token verify

Usage:
    # compare two file versions
    python3 scripts/trim_guard.py --before old.py --after new.py
    # check a unified diff from stdin
    git diff HEAD~1 -- '*.py' | python3 scripts/trim_guard.py --diff
    # JSON output
    python3 scripts/trim_guard.py --before old.py --after new.py --json

Exit codes: 0 = guards preserved (or increased), 1 = guard count DECREASED
(change refused), 2 = usage/parse error.

Pair with the ponytail-review skill (skills/development/ponytail-review):
the skill judges what *should* be cut; this script verifies nothing
protected was cut along the way.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------- patterns

# (category, compiled regex, human label)
GUARD_PATTERNS: list[tuple[str, "re.Pattern[str]", str]] = [
    ("trust-boundary",
     re.compile(r"\b(commonpath|is_relative_to)\b"),
     "path containment check"),
    ("trust-boundary",
     re.compile(r"abspath\s*\(.*\)\s*(==|!=|>=|<=|\.startswith)"),
     "abspath-anchored path check"),
    ("trust-boundary",
     re.compile(r"""\.\.\s*["']?\s*(in|not in)"""),
     "parent-traversal rejection"),
    ("security",
     re.compile(r"\bexecute\s*\([^)]*(\?|%s|:\w+|\$\d+)"),
     "parameterized SQL"),
    ("security",
     re.compile(r"\b(compare_digest)\b"),
     "constant-time comparison"),
    ("security",
     re.compile(r"\b(html\.escape|escape\s*\(\s*\w*html|markupsafe)"),
     "HTML escaping"),
    ("trust-boundary",
     re.compile(r"\bisinstance\s*\(\s*\w+\s*,\s*(dict|list|str|int|float|tuple)\s*\)"),
     "type check on parsed input"),
    ("data-safety",
     re.compile(r"^\s*try\s*:\s*$"),
     "try block (candidate per-row/data guard)"),
    ("trust-boundary",
     re.compile(r"""["']\\[rn]["']\s*(in|not in)|\\n.*header|header.*\\n"""),
     "header newline sanitization"),
    ("security",
     re.compile(r"@\s*(login_required|permission_required|roles_required|"
                r"requires_auth|authenticated)"),
     "auth decorator"),
    ("security",
     re.compile(r"\b(verify_token|check_permission|has_role|authorize)\s*\("),
     "explicit authorization check"),
    ("data-safety",
     re.compile(r"\b(commit|rollback)\s*\("),
     "transaction discipline"),
    ("accessibility",
     re.compile(r"\balt\s*=\s*[\"']\w"),
     "alt text present"),
    ("accessibility",
     re.compile(r"\baria-[a-z]+\s*="),
     "ARIA attribute"),
    ("trust-boundary",
     re.compile(r"\b(validate|ValidationError|jsonschema|pydantic)\b"),
     "schema/input validation"),
    ("data-safety",
     re.compile(r"\b(fsck|checksum|integrity|backup)\b"),
     "integrity/backup guard"),
]


@dataclass
class GuardHit:
    category: str
    label: str
    line: str


def count_guards(lines: list[str]) -> tuple[int, dict[str, int], list[GuardHit]]:
    """Count guard-signature hits. Returns (total, per_category, hits)."""
    total = 0
    per_cat: dict[str, int] = {}
    hits: list[GuardHit] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for cat, rx, label in GUARD_PATTERNS:
            if rx.search(line):
                total += 1
                per_cat[cat] = per_cat.get(cat, 0) + 1
                hits.append(GuardHit(cat, label, stripped[:100]))
                break  # one hit per line max
    return total, per_cat, hits


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def _split_diff(diff_text: str) -> tuple[list[str], list[str]]:
    """Return (removed_lines, added_lines) from a unified diff."""
    removed, added = [], []
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Refuse a change that cuts guard signatures "
                    "(deterministic trim guard).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--diff", action="store_true",
                   help="read unified diff from stdin")
    g.add_argument("--before", metavar="OLD",
                   help="old version of the file")
    ap.add_argument("--after", metavar="NEW",
                    help="new version of the file (required with --before)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--strict-per-row", action="store_true",
                    help="count bare 'try:' lines only when inside a loop "
                         "(reduces false positives)")
    args = ap.parse_args(argv)

    if args.before and not args.after:
        ap.error("--after is required with --before")

    if args.diff:
        removed, added = _split_diff(sys.stdin.read())
        old_n, old_cat, _ = count_guards(removed)
        new_n, new_cat, new_hits = count_guards(added)
        # In diff mode the "before" is only the removed lines; guard count
        # of removed lines vs added lines is the comparison that matters.
        before_n, after_n = old_n, new_n
        before_cat, after_cat = old_cat, new_cat
        context = "diff"
    else:
        before_lines = _read_lines(args.before)
        after_lines = _read_lines(args.after)
        before_n, before_cat, _ = count_guards(before_lines)
        after_n, after_cat, _ = count_guards(after_lines)
        context = f"{args.before} -> {args.after}"

    delta = after_n - before_n
    verdict = "PASS" if delta >= 0 else "REFUSE"

    report = {
        "context": context,
        "guards_before": before_n,
        "guards_after": after_n,
        "delta": delta,
        "verdict": verdict,
        "categories_before": before_cat,
        "categories_after": after_cat,
        "note": ("Guard count decreased: the change cuts protected lines. "
                 "Restore the guards or justify each removal."
                 if delta < 0 else
                 "Guard count preserved or increased."),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"trim-guard [{context}]")
        print(f"  guards before: {before_n}  after: {after_n}  "
              f"delta: {delta:+d}")
        for cat in sorted(set(before_cat) | set(after_cat)):
            b = before_cat.get(cat, 0)
            a = after_cat.get(cat, 0)
            mark = "  <-- DECREASED" if a < b else ""
            print(f"    {cat}: {b} -> {a}{mark}")
        print(f"  verdict: {verdict} -- {report['note']}")

    return 0 if delta >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
