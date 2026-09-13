"""Wshobson/agents marketplace catalog reader and installer for HAOS.

100% offline-first, stdlib only (zero third-party dependencies).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

logger = logging.getLogger("haos.skills.wshobson")

_WSHOBSON_CATALOG_FILE = Path(__file__).resolve().parent / "wshobson_catalog.json"
_ECC_CATALOG_FILE = Path(__file__).resolve().parent / "ecc_catalog.json"


@dataclass
class WshobsonSkillMeta:
    name: str
    description: str
    category: str
    repo: str
    path: str
    plugin: str
    trust_level: str = "trusted"
    tags: List[str] = field(default_factory=list)

    @property
    def identifier(self) -> str:
        return f"{self.repo}/{self.path}"


class WshobsonCatalog:
    """Catalog manager for wshobson/agents and affaan-m/ECC skills."""

    def __init__(self, catalog_path: Optional[Path] = None):
        self._path = catalog_path
        self._entries: Optional[List[WshobsonSkillMeta]] = None

    def _load(self) -> List[WshobsonSkillMeta]:
        if self._entries is not None:
            return self._entries

        entries = []
        catalog_files = [self._path] if self._path else [_WSHOBSON_CATALOG_FILE, _ECC_CATALOG_FILE]

        for cpath in catalog_files:
            if not cpath or not cpath.exists():
                continue
            try:
                raw = json.loads(cpath.read_text(encoding="utf-8"))
                for item in raw:
                    entries.append(
                        WshobsonSkillMeta(
                            name=item["name"],
                            description=item.get("description", ""),
                            category=item.get("category", ""),
                            repo=item.get("repo", "wshobson/agents"),
                            path=item.get("path", ""),
                            plugin=item.get("plugin", ""),
                            trust_level=item.get("trust_level", "trusted"),
                            tags=item.get("tags", []),
                        )
                    )
            except Exception as e:
                logger.warning("Error reading catalog %s: %s", cpath, e)

        self._entries = entries
        return self._entries

    def search(self, query: str = "", limit: int = 20) -> List[WshobsonSkillMeta]:
        entries = self._load()
        q = (query or "").lower().strip()
        if not q:
            return entries[:limit]

        results = []
        for e in entries:
            target = f"{e.name} {e.description} {e.category} {' '.join(e.tags)}".lower()
            if q in target:
                results.append(e)
                if len(results) >= limit:
                    break
        return results

    def get(self, name_or_path: str) -> Optional[WshobsonSkillMeta]:
        entries = self._load()
        for e in entries:
            if e.name == name_or_path or e.path.endswith(name_or_path) or e.identifier == name_or_path:
                return e
        return None

    def fetch_skill_content(self, skill: WshobsonSkillMeta) -> Optional[str]:
        raw_url = f"https://raw.githubusercontent.com/{skill.repo}/main/{skill.path}/SKILL.md"
        try:
            req = urllib.request.Request(raw_url, headers={"User-Agent": "HAOS-Agent"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8")
        except Exception as e:
            logger.warning("Error fetching %s: %s", raw_url, e)
        return None
