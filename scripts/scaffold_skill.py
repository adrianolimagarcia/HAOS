#!/usr/bin/env python3
"""Scaffold a new HAOS skill: generates a valid SKILL.md skeleton.

Follows the authoring standards in skills/AGENTS.md (description <= 60 chars,
frontmatter fields, metadata.hermes.*). The generated SKILL.md is a starting
point — fill in the body sections before submitting.

Usage:
    python3 scripts/scaffold_skill.py --name my-skill --description "Does X." --category web
    python3 scripts/scaffold_skill.py --name my-skill --description "Does X." --category web --optional
    python3 scripts/scaffold_skill.py --name my-skill --description "Does X." --category web --output skills/web/my-skill/

Exit codes: 0 = written, 1 = validation/usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

# built-in categories (skills/) — optional-skills has a wider set
BUILTIN_CATEGORIES = [
    "apple", "autonomous-ai-agents", "creative", "development", "devops",
    "email", "media", "note-taking", "operations", "productivity",
    "research", "reverse-engineering", "social-media", "software-development", "web",
]

SKILL_MD_TEMPLATE = """---
name: {name}
description: "{description}"
version: 0.1.0
author: {author}
license: MIT
platforms: [{platforms}]
metadata:
  hermes:
    tags: [{tags}]
    category: {category}
---

# {title}

{description}

## When to Use

- TODO: describe the trigger situations for this skill.

## Prerequisites

- TODO: list tools, MCP servers, or packages this skill needs.

## Workflow

TODO: describe the step-by-step procedure the agent should follow.

## Examples

TODO: add 1-2 concrete examples.

## Troubleshooting

TODO: common failure modes and fixes.
"""


def validate(name: str, description: str) -> list[str]:
    errors = []
    if not NAME_RE.match(name):
        errors.append(f"name {name!r} must match [a-z0-9_-]{{1,64}}")
    # description: <= 60 chars, one sentence, ends with period (per AGENTS.md)
    desc = description.strip()
    if len(desc) > 60:
        errors.append(f"description is {len(desc)} chars, must be <= 60")
    if not desc.endswith("."):
        errors.append("description must end with a period")
    for banned in ("powerful", "comprehensive", "seamless", "advanced"):
        if banned in desc.lower():
            errors.append(f"description contains marketing word {banned!r} (banned by authoring standards)")
    if name.replace("-", " ") in desc.lower() or name.replace("_", " ") in desc.lower():
        errors.append("description should not repeat the skill name")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new HAOS skill (SKILL.md).")
    ap.add_argument("--name", required=True, help="skill name [a-z0-9_-]{1,64}")
    ap.add_argument("--description", required=True, help="one sentence, <=60 chars, ends with period")
    ap.add_argument("--category", required=True, help="skill category directory")
    ap.add_argument("--author", default="Adriano + HAOS", help="author field")
    ap.add_argument("--platforms", default="linux,macos",
                    help="comma-separated OS list (default: linux,macos)")
    ap.add_argument("--tags", default="", help="comma-separated tags for metadata.hermes.tags")
    ap.add_argument("--optional", action="store_true",
                    help="place under optional-skills/ instead of skills/")
    ap.add_argument("--output", default=None,
                    help="target directory (default: skills/<category>/<name>/ or optional-skills/...)")
    ap.add_argument("--force", action="store_true", help="overwrite existing SKILL.md")
    args = ap.parse_args()

    errors = validate(args.name, args.description)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    base = "optional-skills" if args.optional else "skills"
    target = Path(args.output) if args.output else Path(base) / args.category / args.name
    skill_md = target / "SKILL.md"
    if skill_md.exists() and not args.force:
        print(f"ERROR: {skill_md} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    tags = args.tags or args.category
    content = SKILL_MD_TEMPLATE.format(
        name=args.name,
        description=args.description.strip(),
        author=args.author,
        platforms=args.platforms,
        tags=tags,
        category=args.category,
        title=args.name.replace("-", " ").replace("_", " ").title(),
    )

    target.mkdir(parents=True, exist_ok=True)
    (target / "scripts").mkdir(exist_ok=True)
    skill_md.write_text(content, encoding="utf-8")
    print(f"Wrote {skill_md}")
    print("Next steps:")
    print(f"  1. Fill in the TODO sections of {skill_md}")
    print(f"  2. Add helper scripts under {target / 'scripts'}/ if needed")
    print("  3. Run: python3 scripts/audit_skill_plugin_catalog.py  (to see it as orphan skill)")
    print("  4. Optional: link a distributing plugin via metadata.hermes.plugin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
