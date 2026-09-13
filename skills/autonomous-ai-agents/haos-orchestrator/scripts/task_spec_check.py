#!/usr/bin/env python3
"""Task-spec quality gate for HAOS multi-worker orchestration.

Validates a worker task specification before dispatch:
- hard size cap (specs over ~4 KB measurably degrade worker success: oversized
  tasks blew 60-min timeouts with code already on disk, see the orchestrator
  field notes);
- required sections present (role, constraints, files, behavior, tests,
  verification);
- forbidden "weaken the asserts" language (a worker must never be asked to
  weaken tests to go green);
- instruction-based tool restriction smell (restricting tools by prompt text
  is prompt-injectable; role scoping must come from the harness/posture, not
  the spec).

Stdlib-only so it can run in any lane sandbox. Exits 0 = ready to dispatch,
1 = reject, 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SPEC_MAX_BYTES = 4096  # empirical ceiling from multi-worker field notes

# Sections every spec must carry (heading text may be nested under any level).
REQUIRED_HEADINGS = [
    "role",
    "constraints",
    "files",
    "behavior",
    "tests",
    "verification",
]

# Wording that would let a worker "satisfy" a failing test by diluting it.
_WEAKEN_PATTERNS = [
    r"weak(?:en|ing)?\s+(?:the\s+)?assert",
    r"relax\s+(?:the\s+)?(?:test|assert|threshold)",
    r"remove\s+(?:the\s+)?failing\s+assert",
    r"make\s+(?:the\s+)?test\s+pass\s+without",
    r"skip\s+(?:the\s+)?failing\s+test",
    r"\b(?:temporarily|just)\s+comment\s+out\s+the\s+(?:test|assert)",
]

# Tool restriction expressed as prose = prompt-injectable; the harness must scope tools.
_INSTRUCTION_RESTRICTION_PATTERNS = [
    r"do\s+not\s+(?:ever\s+)?(?:use|call|invoke)\s+(?:the\s+)?`?[\w.-]+_?(?:tool)?",
    r"never\s+(?:use|call|invoke)\s+(?:the\s+)?tool",
    r"you\s+(?:do\s+not|don'?t)\s+have\s+access\s+to\s+`?[\w.-]+",
]


def _heading_levels(text: str) -> set[str]:
    """Lowercased heading text for every markdown heading in *text*."""
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            found.add(re.sub(r"^#+\s*", "", stripped).strip().lower())
    return found


def validate_spec(spec_path: Path) -> list[str]:
    """Return a list of violations (empty = spec is ready to dispatch)."""
    violations: list[str] = []

    if not spec_path.exists():
        return [f"spec file not found: {spec_path}"]

    try:
        text = spec_path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        return [f"spec unreadable: {exc}"]

    if len(text.encode("utf-8")) > SPEC_MAX_BYTES:
        violations.append(
            f"spec is {len(text.encode('utf-8'))} bytes (cap {SPEC_MAX_BYTES}); "
            "split into smaller tasks — oversized specs measurably fail"
        )

    headings = _heading_levels(text)
    missing = [h for h in REQUIRED_HEADINGS if not any(h in hd for hd in headings)]
    if missing:
        violations.append(f"missing required section(s): {', '.join(missing)}")

    for pattern in _WEAKEN_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            violations.append(
                f"'weaken-the-asserts' language detected near: {match.group(0)!r}"
            )

    for pattern in _INSTRUCTION_RESTRICTION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            violations.append(
                "instruction-based tool restriction detected (prompt-injectable): "
                f"{match.group(0)!r}. Scope tools by role in the harness/posture instead."
            )

    return violations


def validate_handoff(handoff_path: Path) -> list[str]:
    """Return a list of violations for a continuous orchestrator HANDOFF file."""
    violations: list[str] = []
    if not handoff_path.exists():
        return [f"handoff file not found: {handoff_path}"]

    try:
        text = handoff_path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        return [f"handoff unreadable: {exc}"]

    required_fields = ["phase", "next action"]
    lowered = text.lower()
    for field in required_fields:
        if field not in lowered:
            violations.append(f"handoff missing field: {field!r}")

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a HAOS worker task spec and/or orchestrator handoff."
    )
    parser.add_argument("--spec", type=Path, default=None, help="Path to task spec markdown")
    parser.add_argument("--handoff", type=Path, default=None, help="Path to HANDOFF.md")
    args = parser.parse_args()

    if not args.spec and not args.handoff:
        parser.error("provide at least one of --spec or --handoff")

    all_violations: list[str] = []
    if args.spec:
        all_violations.extend(validate_spec(args.spec))
    if args.handoff:
        all_violations.extend(validate_handoff(args.handoff))

    if all_violations:
        print(f"[haos-orchestrator] spec/handoff rejected with {len(all_violations)} violation(s):")
        for v in all_violations:
            print(f"  - {v}")
        return 1

    print("[haos-orchestrator] spec/handoff ready to dispatch (all checks passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
