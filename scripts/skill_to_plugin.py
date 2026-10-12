#!/usr/bin/env python3
"""Suggest a plugin-catalog entry for an existing SKILL.md (suggestion only).

Reads a SKILL.md frontmatter and prints a suggested ``plugin-catalog/*.yaml``
entry that would distribute it as a plugin. NEVER writes the catalog file
itself — the operator reviews, pins a real sha, and decides.

This is the "skill → plugin" direction of the 1 plugin ↔ N skills model:
a plugin entry whose ``capabilities.provides_skills`` lists this skill.

Usage:
    python3 scripts/skill_to_plugin.py skills/reverse-engineering/binary-triage/SKILL.md
    python3 scripts/skill_to_plugin.py skills/web/my-skill/SKILL.md --repo https://github.com/org/repo --maintainer handle

Exit codes: 0 = suggestion printed, 1 = usage/parse error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


def read_frontmatter(skill_md: Path) -> dict | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {skill_md}: {exc}", file=sys.stderr)
        return None
    if not text.startswith("---"):
        print(f"ERROR: {skill_md} has no YAML frontmatter", file=sys.stderr)
        return None
    end = text.find("\n---", 3)
    if end == -1:
        print(f"ERROR: {skill_md} has unterminated frontmatter", file=sys.stderr)
        return None
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError as exc:
        print(f"ERROR: invalid frontmatter YAML: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Suggest a plugin-catalog entry for an existing SKILL.md (prints only).")
    ap.add_argument("skill_md", help="path to the SKILL.md file")
    ap.add_argument("--repo", default="https://github.com/REPLACE/with-plugin-repo",
                    help="plugin repo URL (default: placeholder)")
    ap.add_argument("--maintainer", default="TODO", help="maintainer handle")
    ap.add_argument("--tier", default="community", choices=("official", "community"))
    ap.add_argument("--category", default=None,
                    help="catalog category (default: from skill metadata.hermes.category)")
    args = ap.parse_args()

    fm = read_frontmatter(Path(args.skill_md))
    if fm is None:
        return 1
    name = fm.get("name")
    if not name:
        print("ERROR: frontmatter has no 'name'", file=sys.stderr)
        return 1
    description = str(fm.get("description", "TODO: description")).strip()
    hermes = fm.get("metadata", {}).get("hermes", {}) if isinstance(fm.get("metadata"), dict) else {}
    category = args.category or hermes.get("category", "general")

    entry = f"""# Suggested plugin-catalog entry for skill {name!r}
# REVIEW, pin a real sha, then save as plugin-catalog/<name>.yaml
# (or run: python3 scripts/scaffold_plugin_entry.py --repo {args.repo} --name <plugin-name> --provides-skills {name})
name: {name}-plugin
repo: {args.repo}
sha: {'0' * 40}  # TODO: pin with: git ls-remote {args.repo} HEAD
description: "Plugin distributing the {name} skill: {description}"
maintainer: {args.maintainer}
tier: {args.tier}
category: {category}
docs_url: {args.repo}#readme
capabilities:
  provides_tools: []
  provides_hooks: []
  provides_middleware: []
  requires_env: []
  provides_skills:
    - {name}
"""
    print(entry)
    print("# To link back from the skill, add to its SKILL.md frontmatter:")
    print("#   metadata:")
    print("#     hermes:")
    print(f"#       plugin: {name}-plugin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
