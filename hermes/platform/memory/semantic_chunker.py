"""Semantic chunking over typed DocumentTrees — atoms are never split.

The breadcrumb chunker (``ragflow_engine.HeaderBreadcrumbChunker``) splits by
character budget and can only avoid breaking *inside* a code fence; a table
still gets cut around its edges and nothing records what kind of content a
chunk holds. This module packs WHOLE typed elements (from
``document_understanding.parse_markdown``) into budgeted chunks:

- a chunk never contains a partial atom (table/code/list stay intact);
- a chunk never crosses a heading boundary unless the budget forces it — and
  then it crosses at an element edge, never mid-element;
- every chunk carries the breadcrumb header path AND a provenance anchor
  ``[ref: doc#Lx-Ly]`` spanning exactly its source lines;
- an optional ``summarizer`` seam (default: deterministic extractive first
  sentence) gives each chunk a short abstract usable by RAPTOR-style trees
  and retrieval planners without ever calling an LLM implicitly.

LLM summarization plugs in through ``summarizer=`` (the repo's auxiliary-LLM
pattern); the default keeps ingestion offline, deterministic and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.memory.document_understanding import (
    DocumentElement,
    DocumentTree,
    ElementKind,
    parse_markdown,
)

SummarizerFn = Callable[[str], str]


@dataclass
class KnowledgeChunk:
    chunk_id: str
    doc_path: str
    header_path: str
    breadcrumb: List[str]
    content: str
    summary: str
    start_line: int
    end_line: int
    provenance_anchor: str
    kinds: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_path": self.doc_path,
            "header_path": self.header_path,
            "breadcrumb": list(self.breadcrumb),
            "content": self.content,
            "summary": self.summary,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "provenance_anchor": self.provenance_anchor,
            "kinds": list(self.kinds),
            "metadata": dict(self.metadata),
        }

    def formatted_for_llm(self, include_anchor: bool = True) -> str:
        anchor = f" {self.provenance_anchor}" if include_anchor else ""
        header = f" | {self.header_path}" if self.header_path else ""
        return f"[Doc: {self.doc_path}{header}]{anchor}\n{self.content}"


_SENT_RE = re.compile(r"[^.!?]+[.!?]+")


def extractive_summary(text: str, max_chars: int = 200) -> str:
    """Deterministic default summarizer: first complete sentence(s) up to a cap."""
    body = " ".join(text.split())
    sentences = _SENT_RE.findall(body)
    if not sentences:
        return body[:max_chars]
    out = ""
    for s in sentences:
        if out and len(out) + len(s) > max_chars:
            break
        out += s.strip() + " "
    return (out.strip() or body[:max_chars])[:max_chars]


class SemanticChunker:
    """Budgeted packing of typed elements into KnowledgeChunks."""

    def __init__(
        self,
        max_chars: int = 1600,
        min_chars: int = 80,
        summarizer: Optional[SummarizerFn] = None,
    ):
        if max_chars <= min_chars:
            raise ValueError("max_chars must exceed min_chars")
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.summarizer = summarizer or extractive_summary

    def chunk_tree(self, tree: DocumentTree, doc_id: str = "") -> List[KnowledgeChunk]:
        chunks: List[KnowledgeChunk] = []
        title_stack: List[DocumentElement] = []
        buf: List[DocumentElement] = []
        buf_len = 0

        def breadcrumb() -> List[str]:
            return [t.content for t in title_stack]

        def header_path() -> str:
            return " > ".join(f"{'#' * (t.level or 1)} {t.content}" for t in title_stack)

        def flush() -> None:
            nonlocal buf, buf_len
            if not buf:
                return
            content = "\n\n".join(e.content for e in buf)
            start = buf[0].start_line
            end = buf[-1].end_line
            cid = f"{doc_id or tree.doc_path}-S{len(chunks) + 1:04d}"
            chunks.append(
                KnowledgeChunk(
                    chunk_id=cid,
                    doc_path=tree.doc_path,
                    header_path=header_path(),
                    breadcrumb=breadcrumb(),
                    content=content,
                    summary=self.summarizer(content),
                    start_line=start,
                    end_line=end,
                    provenance_anchor=f"[ref: {tree.doc_path}#L{start}-L{end}]",
                    kinds=sorted({e.kind.value for e in buf}),
                    metadata={"atoms": len(buf)},
                )
            )
            buf = []
            buf_len = 0

        for el in tree.elements:
            if el.kind is ElementKind.TITLE:
                flush()
                level = el.level or 1
                while title_stack and (title_stack[-1].level or 1) >= level:
                    title_stack.pop()
                title_stack.append(el)
                buf.append(el)
                buf_len += len(el.content)
                continue

            # A single element larger than the budget: it is an ATOM — emit it
            # alone rather than cut it. Cutting a table/code block to fit a
            # budget is how retrieval starts returning half a schema.
            el_len = len(el.content)
            if el_len > self.max_chars:
                flush()
                buf.append(el)
                flush()
                continue

            if buf and buf_len + el_len > self.max_chars:
                flush()
            buf.append(el)
            buf_len += el_len

        flush()
        return chunks

    def chunk_markdown(self, text: str, doc_path: str = "document.md",
                       doc_id: str = "") -> List[KnowledgeChunk]:
        return self.chunk_tree(parse_markdown(text, doc_path=doc_path), doc_id=doc_id)
