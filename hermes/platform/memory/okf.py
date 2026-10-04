"""OKF (Open Knowledge Format) Local Parser and Knowledge Store.

Parses curated Markdown files with YAML frontmatter from a local directory or Git checkout.
Operates 100% offline without remote network dependencies.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional


def _slugify(title: str) -> str:
    """Deriva o nome de arquivo de um título, transliterando acentos.

    Acentos são DOBRADOS para ASCII (``ç``→``c``, ``ã``→``a``), não trocados por
    hífen. A versão anterior usava ``re.sub(r"[^a-zA-Z0-9_\\-]+", "-", ...)``, que
    tratava cada caractere acentuado como separador: "Medição de recuperação"
    virava ``medi-o-de-recupera-o``, ilegível e sem relação com o título.
    """
    folded = unicodedata.normalize("NFKD", title.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_\-]+", "-", folded).strip("-")


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

        # Fast-Path Rust via haos-edge /api/okf/scan (sub-5ms paralelizado com WalkDir)
        try:
            import urllib.request
            import json
            req_data = json.dumps({"bundle_dir": str(self.bundle_dir)}).encode("utf-8")
            req = urllib.request.Request(
                "http://100.77.31.78:8788/api/okf/scan",
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("ok"):
                        for item in data.get("docs", []):
                            rel_path = item["rel_path"]
                            metadata = dict(item.get("metadata") or {})
                            if "title" not in metadata and item.get("title"):
                                metadata["title"] = item["title"]
                            if "tags" not in metadata and item.get("tags"):
                                metadata["tags"] = item["tags"]
                            doc = OKFDocument(
                                filepath=self.bundle_dir / rel_path,
                                relative_path=rel_path,
                                metadata=metadata,
                                body=item.get("body_preview") or "",
                            )
                            self._cache[rel_path] = doc
                        self._loaded = True
                        return
        except Exception:
            pass

        import yaml  # function-level: o lint A6 de hermes/platform só permite stdlib no topo

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

    _STOPWORDS = {
        "de", "a", "o", "as", "os", "em", "na", "no", "nas", "nos",
        "do", "da", "dos", "das", "por", "para", "com", "sem", "e",
        "ou", "que", "se", "um", "uma", "uns", "umas",
    }

    @staticmethod
    def _fold(s: str) -> str:
        """minúsculas + acentos dobrados para ASCII (mesma dobra de _slugify)."""
        folded = unicodedata.normalize("NFKD", s.lower())
        return "".join(c for c in folded if not unicodedata.combining(c))

    @classmethod
    def _tokens(cls, s: str) -> set:
        """Tokens de palavra inteira (hífen é parte do token: 'static-linking')."""
        return set(re.findall(r"[\w\-]+", cls._fold(s)))

    @classmethod
    def _content_tokens(cls, s: str) -> set:
        """Tokens de conteúdo sem acentos e sem stopwords comuns."""
        return cls._tokens(s) - cls._STOPWORDS

    @classmethod
    def _tag_evidence(cls, tag: str, q_tokens: set) -> bool:
        """Tag casa somente como SEQUÊNCIA de palavras inteiras na query.

        'file' NÃO casa com 'filesystem'; 'static-linking' casa com
        'linkagem static-linking' mas não com 'linking' isolado.
        """
        tt = re.findall(r"[\w\-]+", cls._fold(tag))
        return bool(tt) and all(tok in q_tokens for tok in tt)

    def find_deterministic(self, query: str) -> Optional[OKFDocument]:
        """Deterministic exact/keyword lookup against titles, tags and filenames.

        Zero embeddings or vector math; 100% offline & reproducible.

        Regra de evidência (0.21.83, validado no corpus real de 102 docs):
        - exatos de path/stem/título e título-frase dentro da query: preservados;
        - query == tag exata: preservada (contrato do gate para consultas curtas);
        - corroboração por tags: exige **≥2 tags distintas** que cubram pelo menos
          **50% dos tokens de conteúdo** da query (evita que pares de tags genéricas
          como 'btrfs'+'disco' interceptem queries de 10 palavras sobre swapfile/zram
          quando o RAGFlow tem o documento exato no vault).
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

        # 3. Título como frase (com limites de comprimento para evitar falso-positivo em títulos curtos)
        for doc in self._cache.values():
            t_clean = doc.title.lower()
            if len(t_clean) >= 6 and len(t_clean.split()) >= 2:
                if t_clean in q_clean or q_clean in t_clean:
                    return doc

        # 4. Tag exata (se o query inteiro for exatamente uma tag)
        for doc in self._cache.values():
            if any(q_clean == t for t in doc.tags):
                return doc

        # 5. Tags corroboradas com cobertura mínima: >= 2 tags distintas
        # cobrindo >= 50% dos tokens de conteúdo (não-stopwords) da query
        q_content = self._content_tokens(q_clean)
        if len(q_content) >= 2:
            for doc in self._cache.values():
                matched = [t for t in doc.tags if self._tag_evidence(t, q_content)]
                if len(set(matched)) >= 2:
                    tag_toks = set()
                    for t in matched:
                        tag_toks.update(self._content_tokens(t))
                    if len(tag_toks & q_content) / len(q_content) >= 0.50:
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

    def documents(self) -> List[OKFDocument]:
        """Todos os documentos OKF carregados, sem filtro de consulta."""
        self.load()
        return list(self._cache.values())

    def save_document(
        self,
        title: str,
        content: str,
        doc_type: str = "concept",
        tags: Optional[List[str]] = None,
        owner: str = "",
        folder: str = "",
        filename: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> OKFDocument:
        """Create or update an OKF markdown document locally.

        ``filename`` fixa o nome do arquivo (default: slug derivado do título);
        ``extra_metadata`` acrescenta chaves ao frontmatter (proveniência, destino,
        confiança) sem alterar o contrato dos demais chamadores.
        """
        target_dir = self.bundle_dir / folder if folder else self.bundle_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        slug = _slugify(title)
        filepath = target_dir / (filename or f"{slug}.md")
        import yaml  # function-level: o lint A6 de hermes/platform só permite stdlib no topo

        metadata = {
            "title": title,
            "type": doc_type,
            "tags": tags or [],
            "owner": owner,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
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
