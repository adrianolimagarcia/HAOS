"""Unified skill ↔ plugin catalog: capability → skill → distributing plugin.

Builds an in-memory index from the two worlds:
  - skills: ``skills/`` + ``optional-skills/`` (SKILL.md frontmatter)
  - plugins: ``plugin-catalog/*.yaml`` (+ ``capabilities.provides_skills`` links)

Works with zero cross-link metadata (name matching only) and gets richer as
``provides_skills`` / ``metadata.hermes.plugin`` fields are adopted.

Public API:
    build_index(repo_root) -> UnifiedIndex
    UnifiedIndex.search(query, limit=25) -> list[UnifiedResult]

CLI:
    python3 -m hermes.platform.catalog.unified search "web search"
    python3 -m hermes.platform.catalog.unified search "web search" --json

Wiring into ``hermes`` CLI (future): add a ``catalog`` subcommand in
``hermes_cli/subcommands/catalog.py`` following ``skills.py``/``plugins.py``,
register via ``build_catalog_parser(subparsers, ...)`` in ``hermes_cli/main.py``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guidance only
    print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


def _repo_root() -> Path:
    # hermes/platform/catalog/unified.py -> repo root (4 levels up)
    return Path(__file__).resolve().parent.parent.parent.parent


def _frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class SkillRecord:
    name: str
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    plugin: str | None = None  # metadata.hermes.plugin
    source: str = ""  # "builtin" | "optional"


@dataclass
class PluginRecord:
    name: str
    description: str = ""
    repo: str = ""
    tier: str = ""
    category: str = ""
    provides_skills: list[str] = field(default_factory=list)


@dataclass
class UnifiedResult:
    kind: str  # "skill" | "plugin"
    name: str
    description: str
    score: float
    plugin_provider: str | None = None  # for skills: which plugin distributes it
    provides_skills: list[str] = field(default_factory=list)  # for plugins
    install_hint: str = ""


@dataclass
class UnifiedIndex:
    skills: dict[str, SkillRecord] = field(default_factory=dict)
    plugins: dict[str, PluginRecord] = field(default_factory=dict)
    # skill name -> plugin entry name (from provides_skills or metadata link)
    skill_to_plugin: dict[str, str] = field(default_factory=dict)

    def search(self, query: str, limit: int = 25) -> list[UnifiedResult]:
        q = query.lower().strip()
        if not q:
            return []
        scored: list[UnifiedResult] = []
        for name, s in self.skills.items():
            hay = f"{name} {s.description} {s.category} {' '.join(s.tags)}".lower()
            score = _score(q, hay, name.lower())
            if score > 0:
                provider = self.skill_to_plugin.get(name)
                hint = (f"plugin '{provider}' distributes it "
                        f"(hermes plugins install {provider})" if provider
                        else "built-in (no install needed)" if s.source == "builtin"
                        else f"install: haos skills install official/<category>/{name}")
                scored.append(UnifiedResult(
                    kind="skill", name=name, description=s.description,
                    score=score, plugin_provider=provider, install_hint=hint))
        for name, p in self.plugins.items():
            hay = f"{name} {p.description} {p.category} {' '.join(p.provides_skills)}".lower()
            score = _score(q, hay, name.lower())
            if score > 0:
                scored.append(UnifiedResult(
                    kind="plugin", name=name, description=p.description,
                    score=score, provides_skills=list(p.provides_skills),
                    install_hint=f"hermes plugins install {name}"))
        scored.sort(key=lambda r: (-r.score, r.kind, r.name))
        return scored[:limit]


def _score(query: str, haystack: str, name: str) -> float:
    if query == name:
        return 100.0
    if query in name:
        return 80.0
    if query in haystack:
        return 50.0
    ratio = difflib.SequenceMatcher(None, query, name).ratio()
    if ratio >= 0.6:
        return ratio * 40.0
    # all query words present?
    words = query.split()
    if words and all(w in haystack for w in words):
        return 30.0
    return 0.0


def build_index(repo_root: Path | None = None) -> UnifiedIndex:
    root = Path(repo_root) if repo_root else _repo_root()
    idx = UnifiedIndex()

    for base, source in (("skills", "builtin"), ("optional-skills", "optional")):
        basedir = root / base
        if not basedir.is_dir():
            continue
        for md in sorted(basedir.rglob("SKILL.md")):
            if ".git" in md.parts:
                continue
            fm = _frontmatter(md)
            name = fm.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            if name in idx.skills:
                continue
            hermes = fm.get("metadata", {}).get("hermes", {}) if isinstance(fm.get("metadata"), dict) else {}
            idx.skills[name] = SkillRecord(
                name=name,
                description=str(fm.get("description", "")),
                category=str(hermes.get("category", "")),
                tags=[str(t) for t in hermes.get("tags", []) if isinstance(t, str)],
                plugin=(str(hermes["plugin"]).strip() or None) if hermes.get("plugin") else None,
                source=source,
            )

    catdir = root / "plugin-catalog"
    if catdir.is_dir():
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
            name = name.strip()
            caps = data.get("capabilities") or {}
            provides = caps.get("provides_skills") if isinstance(caps, dict) else None
            provides = [str(s) for s in provides] if isinstance(provides, list) else []
            idx.plugins[name] = PluginRecord(
                name=name,
                description=str(data.get("description", "")),
                repo=str(data.get("repo", "")),
                tier=str(data.get("tier", "")),
                category=str(data.get("category", "")),
                provides_skills=provides,
            )
            for s in provides:
                idx.skill_to_plugin.setdefault(s, name)

    # back-link: skill metadata.hermes.plugin -> plugin entry
    for sname, srec in idx.skills.items():
        if srec.plugin and srec.plugin in idx.plugins:
            idx.skill_to_plugin.setdefault(sname, srec.plugin)

    return idx


def _print_human(results: list[UnifiedResult]) -> None:
    if not results:
        print("No matches.")
        return
    for r in results:
        tag = "[skill]" if r.kind == "skill" else "[plugin]"
        extra = ""
        if r.kind == "skill" and r.plugin_provider:
            extra = f"  (via plugin: {r.plugin_provider})"
        elif r.kind == "plugin" and r.provides_skills:
            extra = f"  (provides skills: {', '.join(r.provides_skills)})"
        print(f"{tag} {r.name}{extra}")
        if r.description:
            print(f"      {r.description[:100]}")
        print(f"      → {r.install_hint}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Unified skill ↔ plugin catalog search.")
    ap.add_argument("command", choices=["search"], help="command")
    ap.add_argument("query", help="search query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo", default=None, help="repo root (default: auto-detect)")
    args = ap.parse_args(argv)

    idx = build_index(Path(args.repo) if args.repo else None)
    results = idx.search(args.query, limit=args.limit)
    if args.json:
        print(json.dumps([
            {"kind": r.kind, "name": r.name, "description": r.description,
             "score": round(r.score, 1), "plugin_provider": r.plugin_provider,
             "provides_skills": r.provides_skills, "install_hint": r.install_hint}
            for r in results
        ], indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
