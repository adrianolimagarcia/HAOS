"""Wshobson/agents marketplace source adapter for HAOS & Hermes Skills Hub.

Enables consuming the 183+ production-ready engineering skills from
https://github.com/wshobson/agents:
- Zero-latency local index search via bundled wshobson_catalog.json.
- On-demand download and quarantine validation via skills_guard.
- Categorized by domain (Kubernetes, Python, Security, Cloud, AI/ML, etc.).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

from tools.skills_hub_models import (
    SkillBundle,
    SkillMeta,
    SkillSource,
    _matches_query,
)

logger = logging.getLogger("tools.skills_hub.wshobson")

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "hermes" / "platform" / "skills" / "wshobson_catalog.json"


class WshobsonSource(SkillSource):
    """Source adapter for the wshobson/agents marketplace."""

    SOURCE_ID = "wshobson"

    def __init__(self, catalog_path: Optional[Path] = None):
        self._catalog_path = catalog_path or _CATALOG_PATH
        self._catalog: Optional[List[Dict[str, Any]]] = None

    def _load_catalog(self) -> List[Dict[str, Any]]:
        if self._catalog is not None:
            return self._catalog
        if self._catalog_path.exists():
            try:
                self._catalog = json.loads(self._catalog_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Failed to load wshobson catalog from %s: %s", self._catalog_path, e)
                self._catalog = []
        else:
            self._catalog = []
        return self._catalog

    def source_id(self) -> str:
        return self.SOURCE_ID

    def search(self, query: str = "", limit: int = 20) -> List[SkillMeta]:
        """Search the local wshobson catalog by name, description, or tags."""
        catalog = self._load_catalog()
        query_lower = (query or "").lower().strip()
        results: List[SkillMeta] = []

        for item in catalog:
            name = item.get("name", "")
            desc = item.get("description", "")
            tags = item.get("tags", [])
            path = item.get("path", "")
            plugin = item.get("plugin", "")
            category = item.get("category", "")

            if not query_lower or _matches_query(query_lower, name, desc, tags):
                identifier = f"wshobson/agents/{path}"
                meta = SkillMeta(
                    name=name,
                    description=desc,
                    source=self.SOURCE_ID,
                    identifier=identifier,
                    trust_level="trusted",
                    repo="wshobson/agents",
                    path=path,
                    tags=list(tags),
                    extra={"category": category, "plugin": plugin},
                )
                results.append(meta)
                if len(results) >= limit:
                    break

        return results

    def inspect(self, identifier: str) -> Optional[SkillMeta]:
        """Look up metadata from local catalog without network calls."""
        catalog = self._load_catalog()
        for item in catalog:
            path = item.get("path", "")
            if identifier.endswith(path) or identifier.endswith(item.get("name", "")):
                return SkillMeta(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    source=self.SOURCE_ID,
                    identifier=f"wshobson/agents/{path}",
                    trust_level="trusted",
                    repo="wshobson/agents",
                    path=path,
                    tags=list(item.get("tags", [])),
                    extra={"category": item.get("category", ""), "plugin": item.get("plugin", "")},
                )
        return None

    def fetch(self, identifier: str) -> Optional[SkillBundle]:
        """Fetch the skill bundle (SKILL.md) directly from github raw content."""
        meta = self.inspect(identifier)
        if meta is None:
            # Fallback parsing
            path = identifier.replace("wshobson/agents/", "").rstrip("/")
            name = path.split("/")[-1]
        else:
            path = meta.path
            name = meta.name

        raw_url = f"https://raw.githubusercontent.com/wshobson/agents/main/{path}/SKILL.md"
        try:
            req = urllib.request.Request(raw_url, headers={"User-Agent": "HAOS-Agent"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning("Failed to fetch %s (HTTP %d)", raw_url, resp.status)
                    return None
                skill_md = resp.read().decode("utf-8")
        except Exception as e:
            logger.warning("Network error fetching %s: %s", raw_url, e)
            return None

        if meta is None:
            meta = SkillMeta(
                name=name,
                description=f"Skill {name} from wshobson/agents",
                source=self.SOURCE_ID,
                identifier=f"wshobson/agents/{path}",
                trust_level="trusted",
                repo="wshobson/agents",
                path=path,
            )

        return SkillBundle(
            name=name,
            files={"SKILL.md": skill_md},
            source=self.SOURCE_ID,
            identifier=f"wshobson/agents/{path}",
            trust_level="trusted",
            metadata={"category": getattr(meta, "extra", {}).get("category", "") if meta else ""},
        )
