"""ObsidianAdapter — projeção Markdown humana e auditável do Memory Fabric.

Implementa acesso ao cofre (Vault) com estratégia Filesystem First + frontmatter parsing.
O journal SQLite do Memory Fabric é a fonte canônica; este vault é uma projeção auditável e reconstruível.

``retrieve(query)`` usa um índice FTS5 derivado (``VaultFTSIndex``, refresh
incremental por mtime) quando ele é construível; se o índice não puder ser
criado/consultado (ex.: HERMES_HOME sem escrita, SQLite sem FTS5), cai no
comportamento histórico rglob+substring — o índice nunca levanta para o
chamador, e o conjunto de resultados é idêntico nos dois caminhos.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource
from hermes.platform.memory.vault_fts import VaultFTSIndex, parse_obsidian_frontmatter

logger = logging.getLogger("hermes.platform.context.memory.obsidian")


class ObsidianAdapter(ContextSource):
    """Adaptador de leitura e busca em Vault Markdown do Obsidian."""

    def __init__(self, vault_path: Optional[Path | str] = None):
        if isinstance(vault_path, str):
            self.vault_path = Path(vault_path)
        elif vault_path is not None:
            self.vault_path = vault_path
        else:
            # O home canônico, nunca o CWD. Este adapter não só lê: a projeção ``obsidian`` do
            # fabric escreve por ele, e um caminho relativo fazia cada processo gravar num vault
            # diferente conforme de onde rodava — da árvore de desenvolvimento, para dentro do
            # próprio repo (40 notas em ``.hermes/obsidian_vault`` em 19/09/2026), enquanto o
            # vault real ficava com zero. O provider já resolvia assim (``provider.py``); o
            # adapter tinha ficado para trás, e é ele que a projeção usa.
            from hermes_constants import get_hermes_home  # function-level: lint A6

            self.vault_path = Path(get_hermes_home()) / "obsidian_vault"
        self._cache: Dict[str, ContextItem] = {}
        # Lazy FTS index over the vault; None until first built, and rebuilt
        # when set_vault_path() points the adapter at another vault.
        self._fts_idx: Optional[VaultFTSIndex] = None
        self._fts_fallback_warned = False

    @property
    def source_name(self) -> str:
        return "obsidian"

    def set_vault_path(self, path: Path) -> None:
        self.vault_path = path
        self._cache.clear()
        self._fts_idx = None  # index is keyed by vault path; rebuild lazily

    def _parse_frontmatter_and_body(self, content: str) -> tuple[Dict[str, Any], str]:
        """Extrai frontmatter YAML simples e corpo do documento.

        Delegates to the vault_fts parser — the index derives a note's title
        from the same code, so a query that matches via the stem-fallback title
        can never disagree between read_note() and the FTS index.
        """
        return parse_obsidian_frontmatter(content)

    def _fts_index(self) -> Optional[VaultFTSIndex]:
        """Index for the current vault, built on first use.

        Returns None (never raises) when the index cannot be created — read-only
        HERMES_HOME, no FTS5 in this SQLite, a DB that cannot be opened — in
        which case retrieve() keeps the original rglob+substring behaviour. A
        failed attempt is retried on the next call (cheap to fail) but logged
        only once per adapter instance to avoid log spam.
        """
        if self._fts_idx is not None:
            return self._fts_idx
        try:
            self._fts_idx = VaultFTSIndex(self.vault_path)
        except Exception as exc:  # noqa: BLE001 — index is an optimization
            if not self._fts_fallback_warned:
                self._fts_fallback_warned = True
                logger.warning(
                    "obsidian FTS index unavailable for %s; retrieve() falls "
                    "back to the rglob+substring scan: %s", self.vault_path, exc,
                )
            return None
        return self._fts_idx

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

    def _safe_path(self, relative_path: str) -> Path:
        """Resolve ``relative_path`` inside the vault or refuse it.

        A projection must never be able to write outside the configured vault
        (``../`` traversal, absolute path, symlinked escape), so containment is
        checked against the resolved root before any mkdir/write happens.
        """
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("Vault path must be a non-empty relative path")
        root = self.vault_path.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("Vault path escapes configured vault") from None
        return candidate

    def write_note(self, relative_path: str, title: str, content: str, doc_type: str = "project_doc", metadata: Optional[Dict[str, str]] = None) -> ContextItem:
        """Aplica uma nota de projeção no cofre com frontmatter auditável."""
        full_path = self._safe_path(relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        meta = dict(metadata or {})
        meta["title"] = title
        meta["type"] = doc_type

        front_lines = ["---"]
        for k, v in meta.items():
            front_lines.append(f"{k}: {v}")
        front_lines.append("---\n")
        full_text = "\n".join(front_lines) + content

        # Atomic replace + fsync: a crash mid-projection must never leave a
        # truncated note, because Obsidian is the human-auditable projection.
        fd, tmp_name = tempfile.mkstemp(prefix=f".{full_path.name}.", dir=full_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(full_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, full_path)
            dir_fd = os.open(full_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
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

        if query:
            # Index fast path: substring search over the FTS5-derived index
            # (incrementally synced by mtime), then build ContextItems through
            # read_note() exactly like the scan below. Any index failure falls
            # back to the original behaviour — the index never raises here.
            index = self._fts_index()
            if index is not None:
                try:
                    for rel in index.search(query):
                        item = self.read_note(rel)
                        if item:
                            results.append(item)
                    return results
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "obsidian FTS index query failed; falling back to "
                        "rglob+substring for %r", query, exc_info=True,
                    )
                    results = []

        q_lower = query.lower()
        for md_file in self.vault_path.rglob("*.md"):
            rel = str(md_file.relative_to(self.vault_path))
            item = self.read_note(rel)
            if not item:
                continue

            if not query or q_lower in item.title.lower() or q_lower in item.content.lower():
                results.append(item)

        return results
