"""Provenance — verifiable source references and evidence chains.

RAGFlow's core promise is *explainable* RAG: every answer traces to a passage.
HAOS chunks already carry an anchor string ``[ref: path/doc.md#L45-L68]``;
this module makes the anchor a first-class object and — the part nobody had —
makes it CHECKABLE: ``verify_ref`` re-reads the file and confirms the span
exists and is non-empty, so an answer can be graded on whether its evidence
actually resolves, instead of trusting the model to quote honestly.

Contracts:
- ``parse_anchor`` round-trips the exact string format the chunkers emit;
- ``verify_ref`` fails CLOSED: missing file, out-of-range span, empty span →
  ``verified=False`` with a reason (never an exception leaking into a turn);
- ``EvidenceChain.status`` is a computed verdict, not a stored claim:
  ``grounded`` only when every ref verifies; a chain with zero refs is
  ``ungrounded`` — an answer with no evidence is reported as such.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ANCHOR_RE = re.compile(
    r"^\[ref:\s+(?P<path>.+?)#L(?P<start>\d+)-L(?P<end>\d+)\]$"
)


@dataclass(frozen=True)
class SourceRef:
    """A resolvable pointer into a source document."""

    doc_path: str
    start_line: int
    end_line: int
    chunk_id: Optional[str] = None
    quote: Optional[str] = None

    @property
    def anchor(self) -> str:
        return f"[ref: {self.doc_path}#L{self.start_line}-L{self.end_line}]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_path": self.doc_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chunk_id": self.chunk_id,
            "quote": self.quote,
        }

    @staticmethod
    def from_anchor(anchor: str, chunk_id: Optional[str] = None) -> "SourceRef":
        ref = parse_anchor(anchor)
        if ref is None:
            raise ValueError(f"not a HAOS provenance anchor: {anchor!r}")
        if chunk_id:
            return SourceRef(ref.doc_path, ref.start_line, ref.end_line, chunk_id=chunk_id)
        return ref


def parse_anchor(anchor: str) -> Optional[SourceRef]:
    """Parse ``[ref: path#Lx-Ly]`` (the format emitted by both chunkers)."""
    m = _ANCHOR_RE.match((anchor or "").strip())
    if not m:
        return None
    start, end = int(m.group("start")), int(m.group("end"))
    if start < 1 or end < start:
        return None
    return SourceRef(doc_path=m.group("path").strip(), start_line=start, end_line=end)


@dataclass
class Verification:
    verified: bool
    reason: str = ""
    excerpt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"verified": self.verified, "reason": self.reason, "excerpt": self.excerpt}


def verify_ref(ref: SourceRef, root: Optional[Path | str] = None,
                max_excerpt_chars: int = 400) -> Verification:
    """Re-read the source and confirm the span resolves. Fail-closed."""
    path = Path(ref.doc_path)
    if root is not None and not path.is_absolute():
        path = Path(root) / path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Verification(False, f"unreadable: {exc}")
    lines = text.splitlines()
    if not lines:
        return Verification(False, "empty document")
    if ref.start_line > len(lines) or ref.end_line > len(lines):
        return Verification(
            False, f"span L{ref.start_line}-L{ref.end_line} out of range (doc has {len(lines)} lines)"
        )
    excerpt = "\n".join(lines[ref.start_line - 1: ref.end_line]).strip()
    if not excerpt:
        return Verification(False, "span resolves to empty content")
    return Verification(True, "", excerpt[:max_excerpt_chars])


@dataclass
class EvidenceChain:
    """Answer + the refs it claims. Status is computed, never asserted."""

    answer: str
    refs: List[SourceRef] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def evaluate(self, root: Optional[Path | str] = None) -> Dict[str, Any]:
        """Verify every ref and derive the honest verdict.

        status: grounded (all verify) | partial (some) | ungrounded (none or
        no refs at all). Callers must not silence an ungrounded answer — they
        must LABEL it; that is the whole point of the chain.
        """
        results = []
        for ref in self.refs:
            v = verify_ref(ref, root=root)
            results.append({"ref": ref.anchor, **v.to_dict()})
        n = len(results)
        ok = sum(1 for r in results if r["verified"])
        if n == 0:
            status = "ungrounded"
        elif ok == n:
            status = "grounded"
        elif ok == 0:
            status = "ungrounded"
        else:
            status = "partial"
        return {
            "answer": self.answer,
            "status": status,
            "refs_total": n,
            "refs_verified": ok,
            "verification": results,
            "canary": getattr(self, "canary", None),
        }


def chain_from_anchors(answer: str, anchors: List[str]) -> EvidenceChain:
    """Build a chain from raw anchor strings; malformed anchors are DROPPED
    (they would fabricate evidence), never silently coerced."""
    refs = [r for r in (parse_anchor(a) for a in anchors) if r is not None]
    return EvidenceChain(answer=answer, refs=refs)
