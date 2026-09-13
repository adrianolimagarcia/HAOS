"""ObsidianAdapter — Acesso ao cofre Obsidian (Human-Auditable Canonical Truth).

Implementa acesso ao cofre (Vault) com estratégia Filesystem First + frontmatter parsing.
O Obsidian é a fonte de verdade canônica humana; o GraphRAG é apenas uma projeção derivada.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource


class ObsidianAdapter(ContextSource):
    """Adaptador de leitura e busca em Vault Markdown do Obsidian."""

    def __init__(self, vault_path: Optional[Path | str] = None):
        if isinstance(vault_path, str):
            self.vault_path = Path(vault_path)
        else:
            self.vault_path = vault_path or Path(".hermes/obsidian_vault")
        self._cache: Dict[str, ContextItem] = {}

    @property
    def source_name(self) -> str:
        return "obsidian"

    def set_vault_path(self, path: Path) -> None:
        self.vault_path = path
        self._cache.clear()

    def _parse_frontmatter_and_body(self, content: str) -> tuple[Dict[str, Any], str]:
        """Extrai frontmatter YAML simples e corpo do documento."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                raw_front = parts[1]
                body = parts[2].strip()
                front = {}
                for line in raw_front.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        front[k.strip().lower()] = v.strip()
                return front, body
        return {}, content.strip()

    def read_note(self, relative_path: str) -> Optional[ContextItem]:
        """Lê uma nota Markdown do cofre e constrói o ContextItem com metadados."""
        full_path = self.vault_path / relative_path
        if not full_path.exists() or not full_path.is_file():
            return None

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return None

        front, body = self._parse_frontmatter_and_body(content)
        title = front.get("title", full_path.stem)
        doc_type = front.get("type", "architecture_decision" if "ADR" in relative_path.upper() else "project_doc")
        
        item = ContextItem(
            id=f"obsidian-{full_path.stem}",
            item_type=doc_type,
            source_uri=f"obsidian://{relative_path}",
            content=content,
            title=title,
            summary=body[:300] + "..." if len(body) > 300 else body,
            abstract=title,
            trust=TrustLevel.ARCHITECTURE_DECISIONS if doc_type == "architecture_decision" else TrustLevel.PROJECT_INSTRUCTIONS,
            authority=AuthorityLevel.ARCHITECTURE if doc_type == "architecture_decision" else AuthorityLevel.ADVISORY,
            relevance=1.0,
            metadata=front,
        )
        self._cache[relative_path] = item
        return item

    def write_note(self, relative_path: str, title: str, content: str, doc_type: str = "project_doc", metadata: Optional[Dict[str, str]] = None) -> ContextItem:
        """Escreve nota canônica no cofre com frontmatter auditável."""
        full_path = self.vault_path / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        meta = metadata or {}
        meta["title"] = title
        meta["type"] = doc_type

        front_lines = ["---"]
        for k, v in meta.items():
            front_lines.append(f"{k}: {v}")
        front_lines.append("---\n")
        full_text = "\n".join(front_lines) + content

        full_path.write_text(full_text, encoding="utf-8")
        return self.read_note(relative_path)  # type: ignore

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """Busca notas no vault correspondentes à query ou lista todas se vazia."""
        results: List[ContextItem] = []
        if not self.vault_path.exists():
            return results

        q_lower = query.lower()
        for md_file in self.vault_path.rglob("*.md"):
            rel = str(md_file.relative_to(self.vault_path))
            item = self.read_note(rel)
            if not item:
                continue

            if not query or q_lower in item.title.lower() or q_lower in item.content.lower():
                results.append(item)

        return results
