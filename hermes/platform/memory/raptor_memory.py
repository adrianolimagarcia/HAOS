"""RAPTOR-style hierarchical memory — a knowledge tree over document chunks.

The gap this closes (OBSERVED: zero `raptor` in the repo before this): flat
retrieval answers "where is the passage that contains the words" but never
"what is the overall picture". A 10k-chunk corpus queried for a *decision*
returns ten fragments and no synthesis. RAGFlow's RAPTOR layer (and its 2026
"Tree" knowledge-compilation mode) fixes this with a recursive
cluster→summarize tree; retrieval can then land at ANY level of abstraction
and expand downward to the evidence.

HAOS-native constraints honored here:

- Deterministic clustering by default: greedy agglomerative over lexical
  Jaccard similarity of unigrams, stable tie-break by id — same corpus, same
  tree, on any host, forever (the HashingEmbedder philosophy). A semantic
  ``similarity_fn``/``embedder`` plugs into the seam when a deployment has
  one; nothing calls an LLM implicitly.
- Summarization is a seam (``summarizer``): default is extractive
  concatenation of child summaries; the auxiliary LLM plugs in there.
- Storage is SQLite WAL (no daemon), and every node keeps the provenance
  anchors of its leaves — a summary node is an *index into evidence*, never a
  free-floating claim.
- Governance hook: ``as_memory_candidates()`` feeds node summaries to the
  ``MemoryReconciler`` (ADD/UPDATE/SUPERSEDE), so a re-ingested document
  supersedes facts instead of duplicating them.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from hermes.platform.memory.semantic_chunker import KnowledgeChunk

SummarizerFn = Callable[[List[str]], str]
SimilarityFn = Callable[[str, str], float]

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall((text or "").lower()))


# FTS5's unicode61 tokenizer splits on '_' (connector punctuation), but our
# _TOKEN_RE keeps it inside tokens — "foo_bar" would index as two terms and a
# query for it could miss. Mapping '_' to a character unicode61 treats as
# alphanumeric (µ, category Ll) and _TOKEN_RE can never emit makes the FTS
# term set an EXACT image of our token set: same splits, no misses. The
# space-joined pre-tokenized blob keeps one term per token regardless.
_FTS_SAFE = "µ"


def _fts_form(tokens: Iterable[str]) -> str:
    """Token list -> FTS5-safe document (underscore folded, space-joined)."""
    return " ".join(t.replace("_", _FTS_SAFE) for t in tokens)


def _fts_query(q: set) -> str:
    """Token set -> MATCH expression OR'ing every (quoted) term."""
    return " OR ".join('"{}"'.format(t.replace("_", _FTS_SAFE)) for t in sorted(q))


def _jaccard_sets(ta: set, tb: set) -> float:
    """Jaccard over pre-computed token sets.

    Split out of ``jaccard_similarity`` so callers that compare one text
    against many can tokenize each side once instead of per pair (the
    clustering hot loop did exactly that).
    """
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def jaccard_similarity(a: str, b: str) -> float:
    return _jaccard_sets(_tokens(a), _tokens(b))


@dataclass
class RaptorNode:
    node_id: str
    level: int                 # 0 = leaf (chunk), higher = more abstract
    text: str
    summary: str
    leaf_ids: List[str] = field(default_factory=list)
    provenance_anchors: List[str] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)
    doc_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "level": self.level,
            "text": self.text,
            "summary": self.summary,
            "leaf_ids": list(self.leaf_ids),
            "provenance_anchors": list(self.provenance_anchors),
            "child_ids": list(self.child_ids),
            "doc_path": self.doc_path,
        }


def extractive_cluster_summary(child_summaries: List[str]) -> str:
    """Deterministic default: join child summaries, capped. No model, no drift."""
    joined = " ".join(s.strip() for s in child_summaries if s.strip())
    return joined[:600]


class RaptorTreeBuilder:
    """Builds the multi-level tree from leaf chunks (pure, no storage)."""

    def __init__(
        self,
        max_level: int = 3,
        cluster_threshold: float = 0.12,
        max_cluster_size: int = 8,
        similarity_fn: Optional[SimilarityFn] = None,
        summarizer: Optional[SummarizerFn] = None,
    ):
        if max_level < 1:
            raise ValueError("max_level must be >= 1")
        if not 0.0 < cluster_threshold <= 1.0:
            raise ValueError("cluster_threshold must be in (0, 1]")
        self.max_level = max_level
        self.cluster_threshold = cluster_threshold
        self.max_cluster_size = max_cluster_size
        self.similarity = similarity_fn or jaccard_similarity
        self.summarizer = summarizer or extractive_cluster_summary

    def build(self, chunks: Sequence[KnowledgeChunk]) -> List[RaptorNode]:
        """Return every node (leaves + abstracts). Level 0 mirrors the chunks."""
        nodes: List[RaptorNode] = []
        current: List[RaptorNode] = []
        for ch in chunks:
            current.append(
                RaptorNode(
                    node_id=ch.chunk_id,
                    level=0,
                    text=ch.content,
                    summary=ch.summary,
                    leaf_ids=[ch.chunk_id],
                    provenance_anchors=[ch.provenance_anchor],
                    doc_path=ch.doc_path,
                )
            )
        nodes.extend(current)

        level = 1
        while current and level < self.max_level:
            clusters = self._cluster(current)
            parents: List[RaptorNode] = []
            for members in clusters:
                if len(members) < 2:
                    # A singleton parent just duplicates its child — it is not
                    # an abstraction. Skip; if nothing clusters, the tree stops
                    # growing here (honest: no shared structure was found).
                    continue
                node_id = f"raptor-L{level}-{uuid.uuid4().hex[:10]}"
                text = "\n\n".join(m.text for m in members)
                summary = self.summarizer([m.summary for m in members])
                anchors = [a for m in members for a in m.provenance_anchors]
                parents.append(
                    RaptorNode(
                        node_id=node_id,
                        level=level,
                        text=text,
                        summary=summary,
                        leaf_ids=[l for m in members for l in m.leaf_ids],
                        provenance_anchors=anchors,
                        child_ids=[m.node_id for m in members],
                        doc_path=members[0].doc_path,
                    )
                )
            if not parents:
                break
            nodes.extend(parents)
            current = parents
            level += 1
        return nodes

    def _cluster(self, nodes: List[RaptorNode]) -> List[List[RaptorNode]]:
        """Greedy agglomerative clustering, deterministic.

        Seed order = input order (stable); a node joins the first cluster whose
        representative is similar enough and not full; otherwise it seeds a new
        cluster. Representative = first member (its own summary).

        With the default lexical similarity the pass is pruned through an
        inverted index over the cluster representatives' tokens: a pair
        sharing no token has Jaccard exactly 0.0, and 0.0 can never win the
        strict ``sim > best_sim`` comparison nor clear the (positive)
        threshold — so skipping those pairs changes no assignment, only the
        cost. A custom ``similarity_fn`` may score token-disjoint pairs above
        0 (embeddings do), so it keeps the exact previous full-scan loop.
        """
        if self.similarity is jaccard_similarity:
            return self._cluster_pruned(nodes)
        clusters: List[List[RaptorNode]] = []
        for node in nodes:
            best = -1
            best_sim = 0.0
            for idx, members in enumerate(clusters):
                sim = self.similarity(node.summary, members[0].summary)
                if sim > best_sim:
                    best_sim, best = sim, idx
            if best >= 0 and best_sim >= self.cluster_threshold \
                    and len(clusters[best]) < self.max_cluster_size:
                clusters[best].append(node)
            else:
                clusters.append([node])
        return clusters

    def _cluster_pruned(self, nodes: List[RaptorNode]) -> List[List[RaptorNode]]:
        """Inverted-index pruned clone of ``_cluster`` for the default lexical
        similarity: same assignment, same tie-break, same output."""
        tokens_by_id = {n.node_id: _tokens(n.summary) for n in nodes}
        clusters: List[List[RaptorNode]] = []
        rep_tokens: List[set] = []           # parallel to clusters
        postings: Dict[str, List[int]] = {}  # token -> cluster indices, seed order
        for node in nodes:
            tn = tokens_by_id[node.node_id]
            candidates: set = set()
            for tok in tn:
                lst = postings.get(tok)
                if lst:
                    candidates.update(lst)
            best = -1
            best_sim = 0.0
            for ci in sorted(candidates):
                sim = _jaccard_sets(tn, rep_tokens[ci])
                if sim > best_sim:
                    best_sim, best = sim, ci
            if best >= 0 and best_sim >= self.cluster_threshold \
                    and len(clusters[best]) < self.max_cluster_size:
                clusters[best].append(node)
            else:
                clusters.append([node])
                rep_tokens.append(tn)
                for tok in tn:
                    postings.setdefault(tok, []).append(len(clusters) - 1)
        return clusters


class RaptorStore:
    """SQLite WAL persistence + multi-resolution retrieval over the tree."""

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            from hermes_constants import get_hermes_home
            db_path = Path(get_hermes_home()) / "memory" / "raptor.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raptor_nodes (
                    node_id TEXT PRIMARY KEY,
                    level INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    leaf_ids_json TEXT NOT NULL,
                    anchors_json TEXT NOT NULL,
                    child_ids_json TEXT NOT NULL,
                    doc_path TEXT NOT NULL,
                    corpus_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS raptor_level ON raptor_nodes(corpus_id, level)"
            )
            # Exact candidate filter for retrieve(): an inverted index over
            # OUR OWN tokenization (see _fts_form), plus the scorer's length
            # normalization stored next to it. A node scores > 0 only if it
            # shares >= 1 token with the query, so the MATCH output is
            # exactly the set the old full scan scored — no regex runs at
            # query time. Best-effort: on a SQLite without FTS5 the table is
            # absent and retrieve() falls back to the full scan (same
            # results, slower).
            try:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(raptor_nodes_fts)")]
                if cols and "toks" not in cols:
                    conn.execute("DROP TABLE raptor_nodes_fts")
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS raptor_nodes_fts "
                    "USING fts5(node_id UNINDEXED, corpus_id UNINDEXED, "
                    "toks, text_len UNINDEXED)"
                )
            except sqlite3.OperationalError:
                pass

    def put_tree(self, nodes: Iterable[RaptorNode], corpus_id: str) -> int:
        rows = [
            (
                n.node_id, n.level, n.text, n.summary,
                json.dumps(n.leaf_ids), json.dumps(n.provenance_anchors),
                json.dumps(n.child_ids), n.doc_path, corpus_id, time.time(),
            )
            for n in nodes
        ]
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM raptor_nodes WHERE corpus_id = ?", (corpus_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO raptor_nodes VALUES "
                "(?,?,?,?,?,?,?,?,?,?)", rows,
            )
            try:
                conn.execute(
                    "DELETE FROM raptor_nodes_fts WHERE corpus_id = ?", (corpus_id,))
                conn.executemany(
                    "INSERT INTO raptor_nodes_fts VALUES (?,?,?,?)",
                    [(r[0], r[8],
                      _fts_form(sorted(_tokens(r[2] + " " + r[3]))),
                      len(_tokens(r[2])))
                     for r in rows],
                )
            except sqlite3.OperationalError:
                pass  # no FTS5: retrieve() uses the full scan
        return len(rows)

    def _scored_candidates(
        self, corpus_id: str, q: set
    ) -> Optional[List[Tuple[str, set, int]]]:
        """(node_id, query∩node tokens, text_token_count), or None = no index.

        The FTS index stores our own tokenization (see _fts_form), so a MATCH
        over the query terms returns EXACTLY the nodes sharing >= 1 token —
        the same set the old full scan scored (zero-overlap nodes scored 0
        and were dropped). None means "no usable index" (legacy DB, or a
        SQLite built without FTS5) and the caller falls back to the exact
        old scan.
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT node_id, toks, text_len FROM raptor_nodes_fts "
                    "WHERE corpus_id = ? AND raptor_nodes_fts MATCH ?",
                    (corpus_id, _fts_query(q)),
                ).fetchall()
                if not rows:
                    known = conn.execute(
                        "SELECT 1 FROM raptor_nodes_fts WHERE corpus_id = ? LIMIT 1",
                        (corpus_id,),
                    ).fetchone()
                    if not known:
                        return None  # corpus written before the index existed
                    return []
                qs = {t.replace("_", _FTS_SAFE) for t in q}
                return [(r["node_id"], qs & set(r["toks"].split()), r["text_len"])
                        for r in rows]
        except sqlite3.OperationalError:
            return None

    def _scan_candidates(
        self, corpus_id: str, q: set
    ) -> List[Tuple[str, set, int]]:
        """Exact old path: tokenize every node (used when no FTS index)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT node_id, text, summary FROM raptor_nodes "
                "WHERE corpus_id = ?",
                (corpus_id,),
            ).fetchall()
        return [(r["node_id"], q & _tokens(r["text"] + " " + r["summary"]),
                 len(_tokens(r["text"]))) for r in rows]

    def _row_to_node(self, r: sqlite3.Row) -> RaptorNode:
        return RaptorNode(
            node_id=r["node_id"], level=r["level"], text=r["text"],
            summary=r["summary"], leaf_ids=json.loads(r["leaf_ids_json"]),
            provenance_anchors=json.loads(r["anchors_json"]),
            child_ids=json.loads(r["child_ids_json"]), doc_path=r["doc_path"],
        )

    def all_nodes(self, corpus_id: str) -> List[RaptorNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raptor_nodes WHERE corpus_id = ? ORDER BY level, node_id",
                (corpus_id,),
            ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def max_level(self, corpus_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(level) AS m FROM raptor_nodes WHERE corpus_id = ?",
                (corpus_id,),
            ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def retrieve(self, query: str, corpus_id: str, k: int = 6) -> List[Dict[str, Any]]:
        """Multi-resolution retrieval: score EVERY level, return top-k nodes.

        Scoring is lexical overlap (query tokens vs node text+summary), so a
        *why/what-decision* query can hit a level-2 abstract while a *which
        line* query hits a leaf. Each result carries its children (expand
        downward toward evidence) and its provenance anchors (expand sideways
        into the source file). Empty corpus → empty list, no exception.
        """
        q = _tokens(query)
        if not q:
            return []
        cands = self._scored_candidates(corpus_id, q)
        if cands is None:
            cands = self._scan_candidates(corpus_id, q)
        scored: List[Tuple[float, str]] = []
        for node_id, overlap_set, text_len in cands:
            overlap = len(overlap_set)
            if overlap == 0:
                continue
            # length-normalized (log) so a giant abstract doesn't win on bulk
            norm = 1.0 + math.log(1.0 + text_len)
            scored.append((overlap / norm, node_id))
        scored.sort(key=lambda t: (-t[0], t[1]))
        winners = scored[:k]
        if not winners:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raptor_nodes WHERE corpus_id = ? AND node_id IN "
                f"({','.join('?' * len(winners))})",
                [corpus_id, *[nid for _, nid in winners]],
            ).fetchall()
        by_id = {r["node_id"]: r for r in rows}
        out = []
        for score, nid in winners:
            d = self._row_to_node(by_id[nid]).to_dict()
            d["score"] = round(score, 4)
            out.append(d)
        return out

    def as_memory_candidates(self, corpus_id: str, level: Optional[int] = None) -> List[Dict[str, Any]]:
        """Node summaries as reconciler candidates (governance bridge).

        Each candidate carries its anchors in metadata so a SUPERSEDE keeps
        the evidence trail. The caller decides topics/categories; this never
        writes memory by itself.
        """
        nodes = self.all_nodes(corpus_id)
        if level is not None:
            nodes = [n for n in nodes if n.level == level]
        return [
            {
                "content": n.summary,
                "metadata": {
                    "origin": "raptor",
                    "node_id": n.node_id,
                    "level": n.level,
                    "anchors": n.provenance_anchors,
                },
            }
            for n in nodes
        ]
