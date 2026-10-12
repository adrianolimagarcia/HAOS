#!/usr/bin/env python3
"""Scaffold a plugin-catalog entry: generates a valid plugin-catalog/*.yaml skeleton.

Takes a repo URL (and optional sha) and produces a YAML entry that passes
``scripts/validate_plugin_catalog.py``. The sha is left as a TODO placeholder
with instructions on how to pin it — never invent a sha.

Usage:
    python3 scripts/scaffold_plugin_entry.py --repo https://github.com/org/plugin --name my-plugin
    python3 scripts/scaffold_plugin_entry.py --repo https://github.com/org/plugin --name my-plugin --sha <40-hex>
    python3 scripts/scaffold_plugin_entry.py --repo https://github.com/org/plugin --name my-plugin --provides-skills skill-a,skill-b

Exit codes: 0 = written, 1 = validation/usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_PLACEHOLDER = "0" * 40
TIERS = ("official", "community")
CATEGORIES = ("desktop", "memory", "platform", "web", "tools", "voice",
              "automation", "models", "general")

YAML_TEMPLATE = """name: {name}
repo: {repo}
sha: {sha}
description: "{description}"
maintainer: {maintainer}
tier: {tier}
category: {category}
docs_url: {docs_url}
capabilities:
  provides_tools: []
  provides_hooks: []
  provides_middleware: []
  requires_env: []
{provides_skills_block}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a plugin-catalog YAML entry.")
    ap.add_argument("--repo", required=True, help="https:// git URL of the plugin repo")
    ap.add_argument("--name", required=True, help="catalog entry name [a-z0-9_-]{1,64}")
    ap.add_argument("--sha", default=None, help="40-hex pinned commit (default: placeholder TODO)")
    ap.add_argument("--description", default="TODO: one-line description of what this plugin provides.",
                    help="entry description")
    ap.add_argument("--maintainer", default="TODO", help="maintainer handle")
    ap.add_argument("--tier", default="community", choices=TIERS)
    ap.add_argument("--category", default="general", choices=CATEGORIES)
    ap.add_argument("--docs-url", default="", help="docs URL (default: <repo>#readme)")
    ap.add_argument("--provides-skills", default="",
                    help="comma-separated SKILL.md names this plugin distributes")
    ap.add_argument("--output", default=None,
                    help="output path (default: plugin-catalog/<name>.yaml)")
    ap.add_argument("--force", action="store_true", help="overwrite existing file")
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="print to stdout instead of writing a file")
    args = ap.parse_args()

    errors = []
    if not args.repo.startswith("https://"):
        errors.append(f"repo {args.repo!r} must be an https:// URL")
    if not NAME_RE.match(args.name):
        errors.append(f"name {args.name!r} must match [a-z0-9_-]{{1,64}}")
    sha = args.sha or SHA_PLACEHOLDER
    if args.sha and not SHA_RE.match(args.sha):
        errors.append("sha must be exactly 40 lowercase hex characters")
    provides_skills = [s.strip() for s in args.provides_skills.split(",") if s.strip()]
    for s in provides_skills:
        if not NAME_RE.match(s):
            errors.append(f"provides_skills entry {s!r} must match [a-z0-9_-]{{1,64}}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if provides_skills:
        skills_block = "  provides_skills:\n" + "".join(f"    - {s}\n" for s in provides_skills)
    else:
        skills_block = "  # provides_skills:  # uncomment and list SKILL.md names this plugin distributes"

    content = YAML_TEMPLATE.format(
        name=args.name,
        repo=args.repo,
        sha=sha,
        description=args.description,
        maintainer=args.maintainer,
        tier=args.tier,
        category=args.category,
        docs_url=args.docs_url or f"{args.repo}#readme",
        provides_skills_block=skills_block,
    )

    if args.print_only:
        print(content)
        return 0

    out = Path(args.output) if args.output else Path("plugin-catalog") / f"{args.name}.yaml"
    if out.exists() and not args.force:
        print(f"ERROR: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content + "\n", encoding="utf-8")
    print(f"Wrote {out}")

    # validate what we generated
    import subprocess
    proc = subprocess.run(
        [sys.executable, "scripts/validate_plugin_catalog.py", str(out)],
        capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        return 1

    if not args.sha:
        print()
        print("NOTE: sha is a placeholder. Pin it with:")
        print(f"  git ls-remote {args.repo} HEAD")
        print("  then replace the sha and re-run the validator.")
    print("Next: python3 scripts/audit_skill_plugin_catalog.py  (to see cross-links)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
