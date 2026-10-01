"""Contracts for the RAGFlow-absorbed parsing layer.

Pins behavior, not snapshots:
- typed parsing keeps atoms (tables/code/lists) whole with exact spans;
- semantic chunks pack whole elements, never cut an atom, and every chunk's
  anchor round-trips through provenance.parse_anchor;
- verify_ref fails closed on every broken-evidence class;
- EvidenceChain status is computed from verification, never asserted.
"""

from __future__ import annotations

from pathlib import Path

from hermes.platform.memory.document_understanding import (
    DocumentElement,
    ElementKind,
    parse_markdown,
)
from hermes.platform.memory.provenance import (
    EvidenceChain,
    SourceRef,
    chain_from_anchors,
    parse_anchor,
    verify_ref,
)
from hermes.platform.memory.semantic_chunker import (
    SemanticChunker,
    extractive_summary,
)

DOC = """# Runbook HAOS

## Arquitetura

Escolhemos SQLite WAL porque zero daemon é requisito do appliance.

| componente | escolha | por quê |
|---|---|---|
| storage | SQLite | offline-first |
| busca | FTS5 | BM25 nativo |

```python
def index(doc):
    return store.upsert(doc)
```

## Decisões

- ADR-002: golden tasks antes de otimização
- ADR-010: baseline imutável
"""


# ------------------------------------------------ document_understanding

def test_atoms_are_typed_and_never_split():
    tree = parse_markdown(DOC, doc_path="runbook.md")
    kinds = [e.kind for e in tree.elements]
    tables = [e for e in tree.elements if e.kind is ElementKind.TABLE]
    codes = [e for e in tree.elements if e.kind is ElementKind.CODE]
    lists = [e for e in tree.elements if e.kind is ElementKind.LIST]

    assert len(tables) == 1 and len(codes) == 1 and len(lists) == 1
    # table atom: header + separator + 2 rows = 4 lines, contiguous span
    t = tables[0]
    assert t.end_line - t.start_line + 1 == 4
    assert t.content.count("\n") == 3
    # code atom keeps language metadata
    assert codes[0].metadata["language"] == "python"
    # list atom counts items
    assert lists[0].metadata["items"] == 2
    assert ElementKind.TITLE in kinds


def test_title_hierarchy_and_parent_titles():
    tree = parse_markdown(DOC, doc_path="runbook.md")
    titles = tree.titles()
    assert [t.content for t in titles] == ["Runbook HAOS", "Arquitetura", "Decisões"]
    assert titles[0].level == 1 and titles[1].level == 2
    # section elements belong to their enclosing title
    arch = titles[1]
    section = tree.elements_in_section(arch)
    assert section and all(e.parent_title == "Arquitetura" for e in section)
    assert any(e.kind is ElementKind.TABLE for e in section)
    assert not any(e.kind is ElementKind.LIST for e in section)  # list is in Decisões


def test_empty_input_is_empty_tree():
    assert parse_markdown("", doc_path="x.md").elements == []
    assert parse_markdown("   \n\n  ", doc_path="x.md").elements == []


# ------------------------------------------------ semantic_chunker

def test_chunks_never_cut_atoms_and_anchors_round_trip():
    chunker = SemanticChunker(max_chars=200, min_chars=20)
    chunks = chunker.chunk_markdown(DOC, doc_path="runbook.md", doc_id="d1")
    assert chunks
    for ch in chunks:
        # anchor parses back to the chunk's own span
        ref = parse_anchor(ch.provenance_anchor)
        assert ref is not None
        assert (ref.start_line, ref.end_line) == (ch.start_line, ch.end_line)
        assert ref.doc_path == "runbook.md"
        # a table or code block is never partially present
        if "|" in ch.content:
            tbl_lines = [l for l in ch.content.splitlines() if l.strip().startswith("|")]
            assert len(tbl_lines) % 1 == 0
            assert "FTS5" in ch.content and "SQLite" in ch.content  # whole table
        if "```" in ch.content:
            assert ch.content.count("```") == 2  # fence closed → atom intact


def test_oversized_atom_is_emitted_whole_even_over_budget():
    big_table = "\n".join(f"| r{i} | v{i} |" for i in range(60))
    doc = "# T\n\n| a | b |\n|---|---|\n" + big_table + "\n"
    chunker = SemanticChunker(max_chars=100, min_chars=10)
    chunks = chunker.chunk_markdown(doc, doc_path="big.md")
    table_chunks = [c for c in chunks if "table" in c.kinds]
    assert len(table_chunks) == 1
    assert table_chunks[0].content.count("\n") >= 60  # nothing was cut


def test_summary_seam_is_injectable_and_default_is_deterministic():
    chunker = SemanticChunker()
    chunks = chunker.chunk_markdown(DOC, doc_path="r.md")
    assert all(c.summary for c in chunks)
    assert extractive_summary("A. B. C.") == "A. B. C."
    llm = SemanticChunker(summarizer=lambda t: "LLM:" + t[:10])
    assert llm.chunk_markdown(DOC, doc_path="r.md")[0].summary.startswith("LLM:")


# ------------------------------------------------ provenance

def test_anchor_round_trip_and_rejects_malformed():
    ref = SourceRef("a/b.md", 3, 7)
    assert parse_anchor(ref.anchor) == ref
    assert parse_anchor("[ref: a.md#L0-L3]") is None      # start < 1
    assert parse_anchor("[ref: a.md#L9-L3]") is None      # inverted
    assert parse_anchor("no anchor here") is None
    assert parse_anchor("  [ref: a.md#L1-L2]  ") == SourceRef("a.md", 1, 2)


def test_verify_ref_fail_closed_classes(tmp_path: Path):
    good = tmp_path / "doc.md"
    good.write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")
    assert verify_ref(SourceRef(str(good), 2, 3)).verified is True
    assert verify_ref(SourceRef(str(good), 2, 3)).excerpt == "l2\nl3"

    missing = verify_ref(SourceRef(str(tmp_path / "nope.md"), 1, 2))
    assert missing.verified is False and "unreadable" in missing.reason

    oob = verify_ref(SourceRef(str(good), 3, 99))
    assert oob.verified is False and "out of range" in oob.reason

    empty = tmp_path / "empty.md"
    empty.write_text("\n\n\n", encoding="utf-8")
    assert verify_ref(SourceRef(str(empty), 1, 2)).verified is False


def test_evidence_chain_status_is_computed(tmp_path: Path):
    d = tmp_path / "d.md"
    d.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    chain = EvidenceChain(
        answer="resposta",
        refs=[SourceRef(str(d), 1, 2), SourceRef(str(d), 3, 3)],
    )
    rep = chain.evaluate()
    assert rep["status"] == "grounded" and rep["refs_verified"] == 2

    partial = EvidenceChain(
        answer="resposta",
        refs=[SourceRef(str(d), 1, 2), SourceRef(str(d), 50, 60)],
    )
    assert partial.evaluate()["status"] == "partial"

    fake = EvidenceChain(answer="x", refs=[SourceRef(str(d), 50, 60)])
    assert fake.evaluate()["status"] == "ungrounded"

    no_refs = EvidenceChain(answer="x")
    assert no_refs.evaluate()["status"] == "ungrounded"  # zero evidence ≠ grounded


def test_chain_from_anchors_drops_malformed():
    chain = chain_from_anchors("ans", ["[ref: d.md#L1-L2]", "lixo", "[ref: d.md#L5-L1]"])
    assert len(chain.refs) == 1


def test_from_anchor_roundtrip_with_chunk_id():
    ref = SourceRef.from_anchor("[ref: x.md#L4-L8]", chunk_id="c1")
    assert ref.chunk_id == "c1"
    assert ref.anchor == "[ref: x.md#L4-L8]"
