"""RAG recall/MRR benchmark — offline, deterministic, no model calls.

Measures the retrieval layer (chunker + store + hybrid search) against a gold
set of (query → expected doc/anchor) pairs. Metrics:

- recall@k: fraction of queries whose gold doc appears in the top-k.
- MRR: mean of 1/rank of the first gold hit per query (0 if absent).

The corpus here is SYNTHETIC and small (deterministic by construction) — it
pins the metric machinery and the end-to-end wiring (index → search → score),
not a quality claim about production docs. Point it at real docs by passing
``corpus_dir``; report the numbers it actually prints, never a remembered
"typical" value.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes.platform.memory.ragflow_engine import RAGFlowStore
from hermes.platform.memory.semantic_chunker import SemanticDocumentChunker

SYNTHETIC_DOCS: Dict[str, str] = {
    "docs/pagination.md": """# Pagination

## Keyset strategy

The sessions table uses keyset pagination to avoid offset scans on large
tables. Keyset cursor encodes last_id and sort direction.

## Cursor format

Pagination cursors are base64 of the JSON {last_id, dir}. Invalid cursors
return 400, never a silent first page.
""",
    "docs/auth.md": """# Auth

## Token rotation

Gateway tokens rotate per session; the token store keeps the previous token
for one grace window to drain in-flight requests.

## Logout invalidation

Logout revokes the token immediately and clears the credentials cache.
""",
    "docs/storage.md": """# Storage

## SQLite WAL

Hot stores use SQLite in WAL mode with synchronous=NORMAL. WAL allows readers
during writers; checkpoint runs on the write path when the log exceeds the
threshold.

## Pruning

Retention pruning deletes rows older than the configured horizon in batches
of 500 to bound transaction size.
""",
}

GOLD_SET: List[Tuple[str, str]] = [
    ("keyset pagination offset", "docs/pagination.md"),
    ("cursor base64 invalid", "docs/pagination.md"),
    ("token rotate grace window", "docs/auth.md"),
    ("logout revokes credentials", "docs/auth.md"),
    ("WAL readers writers checkpoint", "docs/storage.md"),
    ("pruning batches transaction size", "docs/storage.md"),
]


def compute_recall_mrr(
    results_per_query: List[List[str]],
    gold_per_query: List[str],
    k: int,
) -> Dict[str, float]:
    """recall@k and MRR from ranked doc lists (one list per query)."""
    if len(results_per_query) != len(gold_per_query):
        raise ValueError("results and gold must align per query")
    hits = 0
    rr_sum = 0.0
    for ranked, gold in zip(results_per_query, gold_per_query):
        top = ranked[:k]
        if gold in top:
            hits += 1
            rr_sum += 1.0 / (top.index(gold) + 1)
    n = len(gold_per_query)
    return {
        "recall_at_k": round(hits / n, 4) if n else 0.0,
        "mrr": round(rr_sum / n, 4) if n else 0.0,
        "queries": n,
        "k": k,
    }


def run_benchmark(db_path: Path, k: int = 3,
                  corpus_dir: "Path | None" = None) -> Dict[str, float]:
    """Index docs and score retrieval against the matching gold set.

    Default: the SYNTHETIC corpus + its gold pairs. With ``corpus_dir``: real
    .md files + a ``gold.json`` next to them ([["query", "rel/path.md"], ...])
    — no gold file, no run (a mismatched gold set would print a fake score).
    """
    store = RAGFlowStore(db_path=db_path, chunker=SemanticDocumentChunker())
    docs = SYNTHETIC_DOCS
    gold = GOLD_SET
    if corpus_dir is not None:
        root = Path(corpus_dir)
        gold_file = root / "gold.json"
        if not gold_file.exists():
            raise FileNotFoundError(
                f"{gold_file} required: [[query, relative_doc_path], ...] — "
                "refusing to score real docs against the synthetic gold set"
            )
        docs = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*.md"))
        }
        gold = [tuple(pair) for pair in json.loads(gold_file.read_text(encoding="utf-8"))]
    for path, text in docs.items():
        store.index_document(doc_path=path, text=text)

    results = []
    for query, _gold_doc in gold:
        chunks = store.hybrid_search(query, limit=10)
        results.append([c.doc_path for c in chunks])
    metrics = compute_recall_mrr(results, [g for _q, g in gold], k=k)
    return metrics


def main(argv: "List[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="temp sqlite path (default: tmp)")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--corpus-dir", default=None,
                    help="index real .md docs from this dir instead of the synthetic set")
    args = ap.parse_args(argv)

    import tempfile
    db = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "rag_bench.db"
    metrics = run_benchmark(db, k=args.k,
                            corpus_dir=Path(args.corpus_dir) if args.corpus_dir else None)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
