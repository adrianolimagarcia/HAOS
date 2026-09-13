"""OKF (Open Knowledge Format) Local Parser and Knowledge Store.

Parses curated Markdown files with YAML frontmatter from a local directory or Git checkout.
Operates 100% offline without remote network dependencies.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


class OKFDocument:
    """Represents a single deterministic OKF knowledge document."""

    def __init__(
        self,
        filepath: Path,
        relative_path: str,
        metadata: Dict[str, Any],
        body: str,
    ):
        self.filepath = filepath
        self.relative_path = relative_path
        self.metadata = metadata
        self.body = body

    @property
    def title(self) -> str:
        return str(self.metadata.get("title") or self.filepath.stem)

    @property
    def doc_type(self) -> str:
        return str(self.metadata.get("type") or "concept")

    @property
    def tags(self) -> List[str]:
        raw_tags = self.metadata.get("tags") or []
        if isinstance(raw_tags, list):
            return [str(t).lower() for t in raw_tags]
        return [str(raw_tags).lower()]

    @property
    def owner(self) -> str:
        return str(self.metadata.get("owner") or "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "type": self.doc_type,
            "path": self.relative_path,
            "tags": self.tags,
            "owner": self.owner,
            "metadata": self.metadata,
            "content": self.body,
        }


class OKFStore:
    """Local, offline file-backed knowledge store for OKF bundles."""

    def __init__(self, bundle_dir: Path):
        self.bundle_dir = bundle_dir.resolve()
        self._cache: Dict[str, OKFDocument] = {}
        self._loaded = False

    def load(self, force_reload: bool = False) -> None:
        """Scan and load all markdown files in the local OKF directory."""
        if self._loaded and not force_reload:
            return

        self._cache.clear()
        if not self.bundle_dir.exists() or not self.bundle_dir.is_dir():
            self._loaded = True
            return

        for p in self.bundle_dir.rglob("*.md"):
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                meta: Dict[str, Any] = {}
                body = content

                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            parsed_meta = yaml.safe_load(parts[1])
                            if isinstance(parsed_meta, dict):
                                meta = parsed_meta
                            body = parts[2].strip()
                        except Exception:
                            body = content

                rel_path = str(p.relative_to(self.bundle_dir)).replace(os.sep, "/")
                doc = OKFDocument(
                    filepath=p,
                    relative_path=rel_path,
                    metadata=meta,
                    body=body,
                )
                self._cache[rel_path] = doc
            except Exception:
                continue

        self._loaded = True

    def find_deterministic(self, query: str) -> Optional[OKFDocument]:
        """Deterministic exact/keyword lookup against titles, tags and filenames.
        
        Zero embeddings or vector math; 100% offline & reproducible.
        """
        self.load()
        q_clean = query.strip().lower()

        # 1. Exact match by relative path or stem
        for rel_path, doc in self._cache.items():
            if q_clean == rel_path.lower() or q_clean == doc.filepath.stem.lower():
                return doc

        # 2. Exact match by title
        for doc in self._cache.values():
            if q_clean == doc.title.lower():
                return doc

        # 3. Substring match in title or tag match
        for doc in self._cache.values():
            if doc.title.lower() in q_clean or q_clean in doc.title.lower():
                return doc
            if any(q_clean == t or t in q_clean for t in doc.tags):
                return doc

        return None

    def search_all(self, query: str) -> List[OKFDocument]:
        """Search local OKF documents matching query in title, tags, or content."""
        self.load()
        q_clean = query.strip().lower()
        matches: List[OKFDocument] = []
        for doc in self._cache.values():
            if (
                q_clean in doc.title.lower()
                or any(q_clean in t for t in doc.tags)
                or q_clean in doc.body.lower()
            ):
                matches.append(doc)
        return matches

    def save_document(
        self,
        title: str,
        content: str,
        doc_type: str = "concept",
        tags: Optional[List[str]] = None,
        owner: str = "",
        folder: str = "",
    ) -> OKFDocument:
        """Create or update an OKF markdown document locally."""
        target_dir = self.bundle_dir / folder if folder else self.bundle_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", title.lower()).strip("-")
        filename = f"{slug}.md"
        filepath = target_dir / filename

        metadata = {
            "title": title,
            "type": doc_type,
            "tags": tags or [],
            "owner": owner,
        }
        yaml_frontmatter = yaml.safe_dump(metadata, sort_keys=False).strip()
        full_content = f"---\n{yaml_frontmatter}\n---\n\n{content.strip()}\n"

        filepath.write_text(full_content, encoding="utf-8")
        rel_path = str(filepath.relative_to(self.bundle_dir)).replace(os.sep, "/")

        doc = OKFDocument(
            filepath=filepath,
            relative_path=rel_path,
            metadata=metadata,
            body=content.strip(),
        )
        self._cache[rel_path] = doc
        return doc
