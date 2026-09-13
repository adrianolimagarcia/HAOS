#!/usr/bin/env python3
"""Deterministic skill-park auditor (HAOS backlog P3, docs/haos/RESEARCH_MEDIUM_ABSORPTION.md Etapa 1).

A standalone CI check in the style of ``scripts/check-case-collisions.py``: walks the park,
prints the real skill count and every rule violation, and exits non-zero when anything fails.
No external measurement, no network — the audit is reproducible on a checkout.

What counts as a skill
----------------------
A directory containing ``SKILL.md``, found by the RUNTIME LOADER's own iterator
(``agent.skill_utils.iter_skill_index_files``), so the audit can never diverge from what the
agent would actually load. README.md / DESCRIPTION.md / category index files and everything
under ``references/ templates/ assets/ scripts/`` of a skill root are support content, not
skills; VCS/venv/cache dirs and ``.archive/`` are excluded (same EXCLUDED_SKILL_DIRS the
loader prunes). Measured today: skills/ = 68 SKILL.md, optional-skills/ = 137 (205 in-repo
skill packages; the "274" figure from the research doc counts every ``*.md``, including
support files).

Rules (each with its documented basis — nothing invented)
---------------------------------------------------------
- ``frontmatter``: SKILL.md must open with a ``---`` fence, close it, and hold a YAML mapping
  (skills/AGENTS.md "SKILL.md frontmatter").
- ``required_fields``: name, description, version, author, license, platforms and tags
  (metadata.hermes.tags or top-level) — the exact set enforced by
  tests/skills/test_authoring_standards.py::test_required_frontmatter_fields.
- ``name_dir_match``: frontmatter ``name`` must equal the containing directory name
  (test_authoring_standards.py::test_name_matches_directory; the loader keys skills by name
  AND dir name — prompt_size.py::_skill_md_paths_by_name maps both).
- ``unique_names``: no two skills may share ``name:`` across the audited park — a duplicate
  silently shadows one of them in the index (loader first-wins precedence).
- ``oversize``: SKILL.md > ``--max-md-bytes`` bytes is a "giant skill". Limit derived from the
  measured distribution (bytes of SKILL.md, both surfaces — 205 skills, Sep 2026): min 1,537 ·
  median 8,960 · p90 16,401 · p95 20,542 · p99 34,281 · max 72,162 → default 80 KiB (81,920 B),
  ~13.5% above the largest in-park skill. The legacy 100k-char guard in
  test_authoring_standards.py stays as the outer safety net; this limit is the earlier warning.
- ``description``: skills/AGENTS.md HARDLINE rule 1 — ``description`` <= 60 chars, one
  sentence ending with a period, no marketing words (the exact check the doc states; word
  list from test_authoring_standards.py).

Usage
-----
    python scripts/audit_skills.py                 # repo skills/ + optional-skills/
    python scripts/audit_skills.py <root> [...]    # arbitrary roots (tests, other checkouts)
    python scripts/audit_skills.py --max-md-bytes 40960 <root>

Exit status: 0 = audited, no violations · 1 = at least one violation · 2 = a root is missing.
Violations print as GitHub Actions ``::error file=...::rule: detail`` lines.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The runtime loader is the single source of "what is a skill": reusing its iterator and
# frontmatter parser (import-light by design) guarantees the audit matches what loads.
from agent.skill_utils import iter_skill_index_files, parse_frontmatter, yaml_load  # noqa: E402

# Required frontmatter fields — the exact set tests/skills/test_authoring_standards.py enforces.
REQUIRED_FIELDS = ("name", "description", "version", "author", "license", "platforms")

# skills/AGENTS.md HARDLINE rule 1 — description budget and marketing-word blocklist.
DESCRIPTION_HARDLINE_CHARS = 60
MARKETING = re.compile(
    r"\b(powerful|comprehensive|seamless|revolutionary|cutting-edge|state-of-the-art)\b", re.I,
)

# Giant-skill boundary, derived from the measured park distribution (see module docstring).
DEFAULT_MAX_MD_BYTES = 80 * 1024  # 80 KiB — ~13.5% above the largest in-park SKILL.md (72,162 B)


def _parse_frontmatter_strict(content: str) -> "tuple[Dict[str, Any] | None, Optional[str]]":
    """Frontmatter dict, or ``(None, reason)`` when the fence/mapping is invalid.

    Mirrors ``agent.skill_utils.parse_frontmatter`` (BOM strip, ``---`` fences, YAML with a
    key:value fallback) and additionally rejects a well-formed fence that is not a YAML
    mapping — that is a malformed frontmatter, not an empty one.
    """
    content = content.removeprefix("\ufeff")
    if not content.startswith("---"):
        return None, "SKILL.md does not start with a --- frontmatter fence"
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return None, "unclosed --- frontmatter fence"
    yaml_content = content[3 : end_match.start() + 3]
    try:
        parsed = yaml_load(yaml_content)
    except Exception:
        parsed = None
    if parsed is not None and not isinstance(parsed, dict):
        return None, "frontmatter must be a YAML mapping"
    frontmatter, _ = parse_frontmatter(content)
    return (frontmatter if isinstance(frontmatter, dict) else {}), None


def _description_violations(frontmatter: Dict[str, Any], rel: str) -> List[Dict[str, str]]:
    """HARDLINE rule 1: <= 60 chars, one sentence ending with a period, no marketing words."""
    desc = str(frontmatter.get("description") or "").strip().strip("\"'")
    out: List[Dict[str, str]] = []
    if len(desc) > DESCRIPTION_HARDLINE_CHARS:
        out.append({"rule": "description", "path": rel,
                    "detail": f"description {len(desc)} chars > {DESCRIPTION_HARDLINE_CHARS}"})
    if desc and not desc.rstrip().endswith("."):
        out.append({"rule": "description", "path": rel, "detail": "description must end with a period"})
    match = MARKETING.search(desc)
    if match:
        out.append({"rule": "description", "path": rel,
                    "detail": f"marketing word {match.group(0)!r} in description"})
    return out


def audit_skill_file(skill_md: Path, root: Path, *, max_md_bytes: int) -> Dict[str, Any]:
    """Audit one SKILL.md package -> {"rel", "name", "size", "violations": [...]}."""
    rel = str(skill_md.relative_to(root))
    size = skill_md.stat().st_size
    content = skill_md.read_text(encoding="utf-8")
    frontmatter, fence_error = _parse_frontmatter_strict(content)
    violations: List[Dict[str, str]] = []
    if fence_error is not None:
        violations.append({"rule": "frontmatter", "path": rel, "detail": fence_error})
    name = skill_md.parent.name
    if frontmatter is not None:
        name = str(frontmatter.get("name") or name)
        missing = [f for f in REQUIRED_FIELDS if f not in frontmatter]
        hermes = (frontmatter.get("metadata") or {}).get("hermes") or {}
        if not (hermes.get("tags") or frontmatter.get("tags")):
            missing.append("tags")
        if missing:
            violations.append({"rule": "required_fields", "path": rel,
                               "detail": "missing: " + ", ".join(missing)})
        if frontmatter.get("name") is not None and str(frontmatter.get("name")) != skill_md.parent.name:
            violations.append({"rule": "name_dir_match", "path": rel,
                               "detail": f"name {frontmatter.get('name')!r} != dir {skill_md.parent.name!r}"})
        violations.extend(_description_violations(frontmatter, rel))
    if size > max_md_bytes:
        violations.append({"rule": "oversize", "path": rel,
                           "detail": f"{size} bytes > {max_md_bytes} (max-md-bytes limit)"})
    return {"rel": rel, "name": name, "size": size, "violations": violations}


def audit_root(root: Path, *, max_md_bytes: int = DEFAULT_MAX_MD_BYTES) -> Dict[str, Any]:
    """Audit one skills root -> {"root", "skills": [...]}. Unreadable packages yield a
    ``read_error`` violation instead of crashing the check."""
    root = Path(root)
    skills: List[Dict[str, Any]] = []
    for skill_md in iter_skill_index_files(root, "SKILL.md"):
        try:
            skills.append(audit_skill_file(skill_md, root, max_md_bytes=max_md_bytes))
        except OSError as e:
            skills.append({"rel": str(skill_md.relative_to(root)), "name": skill_md.parent.name,
                           "size": 0, "violations": [{"rule": "read_error", "path": str(skill_md),
                                                      "detail": str(e)}]})
    return {"root": str(root), "skills": skills}


def flag_duplicate_names(results: List[Dict[str, Any]]) -> int:
    """Flag every skill whose ``name:`` is shared elsewhere in the audited park. Returns the
    number of violations added (one per occurrence)."""
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        for skill in result["skills"]:
            by_name.setdefault(skill["name"], []).append(skill)
    added = 0
    for name, group in by_name.items():
        if len(group) > 1:
            others = ", ".join(g["rel"] for g in group)
            for skill in group:
                skill["violations"].append({"rule": "unique_names", "path": skill["rel"],
                                            "detail": f"duplicate name {name!r} (also at: {others})"})
                added += 1
    return added


def main(argv: "Optional[List[str]]" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help="skills roots (default: repo skills/ + optional-skills/)")
    parser.add_argument("--max-md-bytes", type=int, default=DEFAULT_MAX_MD_BYTES,
                        help="giant-skill boundary in bytes (default: %(default)s, see docstring)")
    args = parser.parse_args(argv)
    roots = [Path(p) for p in args.roots] or [REPO_ROOT / "skills", REPO_ROOT / "optional-skills"]

    missing = [r for r in roots if not r.is_dir()]
    if missing:
        for r in missing:
            print(f"::error::audit_skills: root does not exist: {r}", file=sys.stderr)
        return 2

    results = []
    try:
        results = [audit_root(r, max_md_bytes=args.max_md_bytes) for r in roots]
    except ImportError as e:
        print(f"::error::audit_skills environment: {e}", file=sys.stderr)
        return 2
    flag_duplicate_names(results)
    total_skills = sum(len(r["skills"]) for r in results)
    violations = [v for r in results for s in r["skills"] for v in s["violations"]]

    for r in results:
        print(f"{r['root']}: {len(r['skills'])} skill(s)")
    print(f"total: {total_skills} skill(s), {len(violations)} violation(s)")
    for v in sorted(violations, key=lambda v: (v["path"], v["rule"])):
        print(f"::error file={v['path']}::{v['rule']}: {v['detail']}")
    if violations:
        print(f"FAIL: {len(violations)} violation(s) across the park")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
