#!/usr/bin/env python3
"""Cross-catalog audit: HAOS skills vs plugin-catalog entries.

Read-only lint that reports the alignment gaps between the two worlds:
  (a) skills with no corresponding plugin entry,
  (b) plugin entries with no corresponding skill,
  (c) near-duplicate names across the two worlds (possible hidden duplication),
  (d) entries missing the new cross-link metadata (provides_skills / metadata.hermes.plugin).

Reads ``skills/`` + ``optional-skills/`` (SKILL.md frontmatter ``name:``) and
``plugin-catalog/*.yaml``. Modifies nothing.

Usage:
    python3 scripts/audit_skill_plugin_catalog.py
    python3 scripts/audit_skill_plugin_catalog.py --json
    python3 scripts/audit_skill_plugin_catalog.py --repo /path/to/checkout

Exit codes: 0 = audit ran (findings are informational, never fail the build;
use --strict to fail when orphans exist).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


def repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    # scripts/ -> repo root
    return Path(__file__).resolve().parent.parent


def parse_frontmatter_name(skill_md: Path) -> str | None:
    """Extract the ``name:`` field from a SKILL.md YAML frontmatter block."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    if isinstance(data, dict):
        name = data.get("name")
        # also check nested metadata.hermes.plugin for (d)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def skill_plugin_link(skill_md: Path) -> str | None:
    """Return metadata.hermes.plugin from a SKILL.md frontmatter, if present."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    try:
        plugin = data["metadata"]["hermes"]["plugin"]
        return str(plugin).strip() or None
    except (KeyError, TypeError, AttributeError):
        return None


def collect_skills(root: Path) -> dict[str, dict]:
    """Map skill name -> {path, plugin_link}."""
    skills: dict[str, dict] = {}
    for base in ("skills", "optional-skills"):
        basedir = root / base
        if not basedir.is_dir():
            continue
        for md in sorted(basedir.rglob("SKILL.md")):
            # skip vendored/node_modules-style noise if any
            if ".git" in md.parts:
                continue
            name = parse_frontmatter_name(md)
            if not name:
                continue
            # first occurrence wins; duplicates are reported separately
            if name not in skills:
                skills[name] = {
                    "path": str(md.relative_to(root)),
                    "plugin": skill_plugin_link(md),
                }
    return skills


def collect_plugins(root: Path) -> dict[str, dict]:
    """Map plugin entry name -> {provides_skills, file}."""
    plugins: dict[str, dict] = {}
    catdir = root / "plugin-catalog"
    if not catdir.is_dir():
        return plugins
    for yf in sorted(catdir.glob("*.yaml")):
        if yf.name == "removed.yaml":
            continue
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        caps = data.get("capabilities") or {}
        provides = caps.get("provides_skills") if isinstance(caps, dict) else None
        plugins[name.strip()] = {
            "file": str(yf.relative_to(root)),
            "provides_skills": [str(s) for s in provides] if isinstance(provides, list) else [],
        }
    return plugins


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def audit(root: Path) -> dict:
    skills = collect_skills(root)
    plugins = collect_plugins(root)

    skill_names = set(skills)
    plugin_names = set(plugins)

    # skills that ARE provided by some plugin (via provides_skills or metadata link)
    provided_skills: set[str] = set()
    for pname, pinfo in plugins.items():
        provided_skills.update(pinfo["provides_skills"])
    for sname, sinfo in skills.items():
        if sinfo["plugin"]:
            provided_skills.add(sname)  # linked skill counts as provided

    orphan_skills = sorted(skill_names - provided_skills - plugin_names)
    orphan_plugins = sorted(plugin_names - skill_names)

    # near-duplicates: normalized name match but not exact
    norm_skills: dict[str, str] = {}
    for s in skill_names:
        norm_skills.setdefault(normalize(s), s)
    near_dupes: list[dict] = []
    for p in sorted(plugin_names):
        n = normalize(p)
        if n in norm_skills and norm_skills[n] != p:
            near_dupes.append({"plugin": p, "skill": norm_skills[n]})

    # fuzzy similar (difflib) for names that don't normalize-match
    fuzzy: list[dict] = []
    for p in sorted(plugin_names):
        matches = difflib.get_close_matches(p, sorted(skill_names), n=2, cutoff=0.75)
        matches = [m for m in matches if m != p and normalize(m) != normalize(p)]
        for m in matches:
            fuzzy.append({"plugin": p, "skill": m,
                          "ratio": round(difflib.SequenceMatcher(None, p, m).ratio(), 2)})

    # metadata coverage
    plugins_with_links = sum(1 for p in plugins.values() if p["provides_skills"])
    skills_with_links = sum(1 for s in skills.values() if s["plugin"])

    return {
        "counts": {
            "skills": len(skills),
            "plugins": len(plugins),
            "orphan_skills": len(orphan_skills),
            "orphan_plugins": len(orphan_plugins),
            "near_duplicates": len(near_dupes),
            "fuzzy_matches": len(fuzzy),
            "plugins_with_provides_skills": plugins_with_links,
            "skills_with_plugin_link": skills_with_links,
        },
        "orphan_skills": orphan_skills,
        "orphan_plugins": orphan_plugins,
        "near_duplicates": near_dupes,
        "fuzzy_matches": fuzzy,
    }


def render_human(report: dict) -> str:
    c = report["counts"]
    lines = [
        "=== skill ↔ plugin catalog audit ===",
        f"skills: {c['skills']}   plugins: {c['plugins']}",
        f"plugins with provides_skills: {c['plugins_with_provides_skills']}",
        f"skills with metadata.hermes.plugin: {c['skills_with_plugin_link']}",
        "",
        f"(a) orphan skills (no plugin provides them): {c['orphan_skills']}",
    ]
    for s in report["orphan_skills"][:20]:
        lines.append(f"    - {s}")
    if c["orphan_skills"] > 20:
        lines.append(f"    ... and {c['orphan_skills'] - 20} more")
    lines.append(f"(b) orphan plugins (no matching skill): {c['orphan_plugins']}")
    for p in report["orphan_plugins"][:20]:
        lines.append(f"    - {p}")
    if c["orphan_plugins"] > 20:
        lines.append(f"    ... and {c['orphan_plugins'] - 20} more")
    lines.append(f"(c) near-duplicate names: {c['near_duplicates']}")
    for d in report["near_duplicates"]:
        lines.append(f"    - plugin {d['plugin']!r} ~ skill {d['skill']!r}")
    lines.append(f"    fuzzy similar: {c['fuzzy_matches']}")
    for d in report["fuzzy_matches"][:10]:
        lines.append(f"    - plugin {d['plugin']!r} ~ skill {d['skill']!r} ({d['ratio']})")
    lines.append("(d) cross-link metadata coverage above ☝️")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit skill ↔ plugin catalog alignment (read-only).")
    ap.add_argument("--repo", default=None, help="Repo checkout root (default: parent of scripts/)")
    ap.add_argument("--json", action="store_true", help="Emit JSON report on stdout")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 if any orphans exist (default: always exit 0)")
    args = ap.parse_args()

    root = repo_root(args.repo)
    report = audit(root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    if args.strict and (report["counts"]["orphan_skills"] or report["counts"]["orphan_plugins"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
