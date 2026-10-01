"""Deep Document Understanding — typed element tree for HAOS retrieval.

Absorbs the *pattern* (not the stack) of RAGFlow's DeepDoc
(https://github.com/infiniflow/ragflow, Apache-2.0): a document is not a flat
string, it is a structure of titles, paragraphs, tables, code, lists. Layout
recognition with vision models is deliberately OUT of scope for the core —
this module is deterministic, stdlib-only, offline-first, and operates on the
markdown/text corpus HAOS actually indexes. A heavy parser, if it ever comes,
comes as an external service behind the same fail-closed seam as
``hermes.platform.memory.graphrag.GraphRAGClient``.

Why this exists: the breadcrumb chunker (``ragflow_engine``) preserves the
heading path but treats a table or a fenced block as opaque text — a chunk can
still be cut *around* an atom in ways that break it, and nothing downstream
knows a chunk was a table. Here every element carries its TYPE and its exact
line span, and tables/code/lists are ATOMS: they are never split, and the
semantic chunker packs whole atoms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ElementKind(str, Enum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    CODE = "code"
    TABLE = "table"
    LIST = "list"
    QUOTE = "quote"
    RULE = "rule"


@dataclass
class DocumentElement:
    """One typed block of a document with its exact line span (1-based)."""

    kind: ElementKind
    content: str
    start_line: int
    end_line: int
    level: Optional[int] = None          # only for TITLE
    parent_title: Optional[str] = None   # nearest enclosing title
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "level": self.level,
            "parent_title": self.parent_title,
            "metadata": dict(self.metadata),
        }


@dataclass
class DocumentTree:
    """Typed elements + heading hierarchy, ready for semantic chunking."""

    doc_path: str
    elements: List[DocumentElement] = field(default_factory=list)

    def titles(self) -> List[DocumentElement]:
        return [e for e in self.elements if e.kind is ElementKind.TITLE]

    def elements_in_section(self, title: DocumentElement) -> List[DocumentElement]:
        """Elements under ``title`` until the next title of same-or-higher level."""
        out: List[DocumentElement] = []
        lvl = title.level or 1
        started = False
        for e in self.elements:
            if e is title:
                started = True
                continue
            if not started:
                continue
            if e.kind is ElementKind.TITLE and (e.level or 1) <= lvl:
                break
            out.append(e)
        return out

    def outline(self) -> List[str]:
        return [f"{'#' * (t.level or 1)} {t.content}" for t in self.titles()]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)\s*(\S*)\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def parse_markdown(text: str, doc_path: str = "document.md") -> DocumentTree:
    """Deterministic markdown → typed DocumentTree.

    Contracts (tested):
    - fenced code and tables are ATOMS: one element, exact span, never split;
    - a TITLE element carries its heading level and closes the previous block;
    - every element keeps 1-based inclusive line numbers — the provenance
      anchor of the whole retrieval stack is derived from these spans.
    """
    tree = DocumentTree(doc_path=doc_path)
    if not text or not text.strip():
        return tree

    lines = text.splitlines()
    title_stack: List[DocumentElement] = []

    def current_parent() -> Optional[str]:
        return title_stack[-1].content if title_stack else None

    def emit(kind: ElementKind, buf: List[str], start: int, end: int,
             level: Optional[int] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        content = "\n".join(buf).strip("\n")
        if not content.strip():
            return
        tree.elements.append(
            DocumentElement(
                kind=kind,
                content=content,
                start_line=start,
                end_line=end,
                level=level,
                parent_title=current_parent(),
                metadata=meta or {},
            )
        )

    i = 0
    n = len(lines)
    para_buf: List[str] = []
    para_start = 0

    def flush_paragraph(end_line: int) -> None:
        nonlocal para_buf, para_start
        if para_buf:
            emit(ElementKind.PARAGRAPH, para_buf, para_start, end_line)
            para_buf = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        m = _HEADING_RE.match(line)
        if m:
            flush_paragraph(i)
            level = len(m.group(1))
            title = DocumentElement(
                kind=ElementKind.TITLE,
                content=m.group(2).strip(),
                start_line=i + 1,
                end_line=i + 1,
                level=level,
                parent_title=None,
            )
            while title_stack and (title_stack[-1].level or 1) >= level:
                title_stack.pop()
            title.parent_title = current_parent() if title_stack else None
            tree.elements.append(title)
            title_stack.append(title)
            i += 1
            continue

        fm = _FENCE_RE.match(line)
        if fm:
            flush_paragraph(i)
            fence, lang = fm.group(1), fm.group(2)
            buf = [line]
            start = i + 1
            i += 1
            while i < n:
                buf.append(lines[i])
                if _FENCE_RE.match(lines[i]) and lines[i].strip().startswith(fence):
                    break
                i += 1
            emit(ElementKind.CODE, buf, start, i + 1, meta={"language": lang or None})
            i += 1
            continue

        if _TABLE_ROW_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_paragraph(i)
            buf = []
            start = i + 1
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            emit(ElementKind.TABLE, buf, start, i,
                 meta={"rows": len(buf), "has_header": True})
            continue

        if _RULE_RE.match(line):
            flush_paragraph(i)
            emit(ElementKind.RULE, [line], i + 1, i + 1)
            i += 1
            continue

        if _QUOTE_RE.match(line):
            flush_paragraph(i)
            buf = []
            start = i + 1
            while i < n and _QUOTE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            emit(ElementKind.QUOTE, buf, start, i)
            continue

        if _LIST_RE.match(line):
            flush_paragraph(i)
            buf = []
            start = i + 1
            while i < n and (_LIST_RE.match(lines[i]) or (lines[i].startswith(("  ", "\t")) and lines[i].strip())):
                buf.append(lines[i])
                i += 1
            emit(ElementKind.LIST, buf, start, i,
                 meta={"items": sum(1 for b in buf if _LIST_RE.match(b))})
            continue

        if not stripped:
            flush_paragraph(i)
            i += 1
            continue

        if not para_buf:
            para_start = i + 1
        para_buf.append(line)
        i += 1

    flush_paragraph(n)
    return tree
