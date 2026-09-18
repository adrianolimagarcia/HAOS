"""HAOS scale benchmark — p50/p95/throughput of hot stores and search paths.

WHY this file exists
--------------------
Until this harness there was no measurement of the HAOS data-plane hot
paths at realistic scale:

- ``benchmark_engine.py`` (HAOSBenchmarkSuite) only measures micro-insert
  WAL on a throwaway table and promised RAGFlow measurement it never
  shipped;
- ``evals/codebase_navigability/runtime_bench.py`` measures a ~200-row
  trivial DB.

That leaves p50/p95/throughput of the stores the runtime actually leans on
(state-like message append, FTS5 vs LIKE search, session-resume tail reads,
the WAL EventStore, and the RAGFlow hybrid search) completely unmeasured —
any regression in those paths ships silently. This module closes the gap
with a single runner that:

1. seeds *synthetic, deterministic* data at realistic scale (default 50k
   rows; task guidance 50–100k) into **temp dirs only** — never
   ``~/.hermes``, never real user data, never repo source files;
2. measures per-operation p50/p95 (ms) and ops/s of the hot paths named in
   the audit;
3. prints a human table and returns a structured ``Dict[str, Any]`` for
   callers (CLI ``haos haos benchmark``, ``python -m``, tests).

Measured operations
-------------------
- ``state_messages_append``      — state-like ``messages`` table append
                                  (WAL), one committed INSERT per op.
- ``search_fts5_match``          — FTS5 BM25 ``MATCH`` top-k retrieval.
- ``search_fts5_trigram``        — FTS5 trigram substring ``MATCH`` (only
                                  when the runtime sqlite ships trigram).
- ``search_like_substring``      — the fallback ``LIKE '%term%'`` full scan
                                  on the same seeded text.
- ``session_tail_events_after``  — EventStore ``events_after(0, limit)``
                                  (recent-tail resume window, big store).
- ``session_tail_get_all_limit`` — EventStore ``get_all(limit)`` (same
                                  window shape, second spelling).
- ``event_store_append``         — file-backed EventStore append
                                  (WAL + synchronous=FULL, real fsyncs).
- ``event_store_cursor``         — EventStore ``cursor()`` (SELECT MAX(seq)).
- ``ragflow_hybrid_search``      — RAGFlowStore.hybrid_search (RRF over
                                  FTS5 BM25 + lexical) — fully offline.

Skipped by design
-----------------
- Vault Obsidian scan vs index: there is no FTS5 index module for the vault
  in-tree yet (checked ``hermes/platform/memory/`` — no ``vault_fts``);
  scanning raw notes without an index measures the filesystem, not a store
  hot path. Recorded in ``report["skipped"]``; add the measurement when the
  index exists.
- If the runtime sqlite lacks FTS5 (checked via ``PRAGMA compile_options``
  plus a live ``CREATE VIRTUAL TABLE`` probe), every FTS5/trigram/RAGFlow
  op is skipped with a reason in ``report["skipped"]`` — the LIKE fallback
  still runs, which is exactly the comparison the audit wants.

Determinism: text and query pools come from a fixed-seed ``random.Random``,
so row sets and query sequences are reproducible. Timing itself is not —
latency numbers vary with the runner, which is why consumers must only
assert relations (``p50_ms <= p95_ms``, both ``>= 0``), never absolute
values.

Layout: this is a standalone sibling of ``benchmark_engine.py`` (same
directory, one topic each); it does not append to that module.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Number of seeded rows/text rows for the default run (audit guidance:
# search tables at 50-100k rows). Override via --rows / rows= kwarg.
DEFAULT_ROWS = 50_000
# Fixed RNG seed: same seed -> same synthetic corpus and query pools.
DEFAULT_SEED = 0x5EED_2026
# Recent-tail window size used by session-resume reads (limit n).
DEFAULT_TAIL_LIMIT = 200
# Top-k retrieved per search query.
DEFAULT_SEARCH_LIMIT = 20

# Synthetic vocabulary: operational-log-flavored tokens. Letters only so FTS5
# queries need no escaping; every token is a valid unicode61 word AND carries
# >= 5 chars so a [:6] prefix is a valid substring for LIKE and trigram.
_TOPICS: Tuple[str, ...] = (
    "router", "scheduler", "memory", "vault", "vector", "embedding",
    "queue", "retry", "circuit", "breaker", "model", "fallback",
    "token", "checkpoint", "compaction", "gateway", "plugin", "skill",
    "session", "context", "prompt", "cache", "eviction", "ledger",
)
_VERBS: Tuple[str, ...] = (
    "executed", "scheduled", "routed", "queued", "failed", "retried",
    "compacted", "flushed", "evicted", "resolved", "updated", "deleted",
    "cached", "measured", "spawned", "joined",
)
_STATUS: Tuple[str, ...] = ("ok", "error", "retry", "timeout", "ready", "active", "blocked", "idle")
_EVENT_NAMES: Tuple[str, ...] = (
    "task.completed", "router.retry", "memory.compacted", "cache.evicted",
    "worker.joined", "gateway.heartbeat", "model.fallback", "queue.flushed",
)
_SESSIONS: Tuple[str, ...] = ("s-001", "s-002", "s-003", "s-004", "s-005", "s-006", "s-007", "s-008")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile over a sample (empty -> 0.0)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _time_one(fn: Callable[[], Any]) -> float:
    """Runs one operation and returns its latency in ms.

    Per-op latencies feed p50/p95: they must be computed over the
    individual operations, not over a total (a mean hides tail commits).
    """
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def _stats(ms: List[float], iterations: int) -> Dict[str, Any]:
    """Turns a latency sample into the report shape every op must carry."""
    total_ms = sum(ms)
    return {
        "iterations": iterations,
        "p50_ms": round(_percentile(ms, 0.50), 3),
        "p95_ms": round(_percentile(ms, 0.95), 3),
        "ops_per_sec": round(iterations / (total_ms / 1000.0), 1) if total_ms > 0 else 0.0,
    }


def _synthetic_line(rng: random.Random, topics: Sequence[str]) -> str:
    """One deterministic operational-log-style sentence containing 2 topics."""
    verb = rng.choice(_VERBS)
    a = topics[rng.randrange(len(topics))]
    b = topics[rng.randrange(len(topics))]
    status = _STATUS[rng.randrange(len(_STATUS))]
    return (
        f"{verb} {a} then {b} count={rng.randint(1, 999)} "
        f"status={status} nonce={rng.getrandbits(24):06x}"
    )


def _synthetic_message(rng: random.Random) -> str:
    """A message-body string (~70-140 chars), seeded-deterministic."""
    return " ".join(_synthetic_line(rng, _TOPICS) for _ in range(rng.randint(1, 3)))


def _synthetic_doc(rng: random.Random, idx: int) -> str:
    """A markdown doc (headers + sections) for the RAGFlow chunker."""
    title = _TOPICS[rng.randrange(len(_TOPICS))].capitalize()
    lines = [f"# Doc {idx}: {title} operations", ""]
    for s in range(rng.randint(3, 5)):
        sec = _TOPICS[rng.randrange(len(_TOPICS))]
        lines.append(f"## Section {s} {sec}")
        for _ in range(rng.randint(3, 6)):
            lines.append(_synthetic_line(rng, _TOPICS))
        lines.append("")
    return "\n".join(lines)


def detect_sqlite_features() -> Dict[str, bool]:
    """Probes the runtime sqlite for FTS5 and the trigram tokenizer.

    ``PRAGMA compile_options`` is authoritative for FTS5 but can miss a
    runtime-loaded extension, so each capability is additionally confirmed
    with a live ``CREATE VIRTUAL TABLE`` on an in-memory connection.
    """
    conn = sqlite3.connect(":memory:")
    try:
        options = {row[0] for row in conn.execute("PRAGMA compile_options")}
        fts5 = bool({"ENABLE_FTS5"}.intersection(options))
        trigram = False
        if fts5:
            try:
                conn.execute("CREATE VIRTUAL TABLE _probe_fts USING fts5(x)")
                conn.execute(
                    "CREATE VIRTUAL TABLE _probe_tri USING fts5(x, tokenize='trigram')"
                )
                trigram = True
            except sqlite3.OperationalError:
                pass
        return {"fts5": fts5, "trigram": trigram}
    finally:
        conn.close()


def _connect(db_path: Path, *, wal: bool = True, synchronous: str = "NORMAL") -> sqlite3.Connection:
    """Opens a SQLite connection configured like the HAOS stores (WAL)."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    if wal:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA synchronous={synchronous};")
    return conn


def _derive_plan(rows: int) -> Dict[str, int]:
    """Derives per-operation iteration counts from the seeded scale.

    Iterations grow with rows so small test runs stay fast while the
    default run exercises the paths enough for stable p50/p95; caps keep a
    full-scale run (50k rows) bounded in wall time.
    """
    return {
        "rows": rows,
        "message_append_ops": max(50, min(rows, 2_000)),
        "search_ops": max(30, min(rows, 300)),
        "tail_ops": max(20, min(rows // 2, 200)),
        "event_append_ops": max(20, min(rows, 400)),
        "cursor_ops": max(20, min(rows, 200)),
        "ragflow_docs": max(10, min(max(rows // 100, 1), 400)),
        "ragflow_search_ops": max(20, min(rows, 200)),
    }


# --------------------------------------------------------------------------- #
# per-op benchmarks (each one runs entirely on its own seeded temp DB)
# --------------------------------------------------------------------------- #
def _bench_messages_append(db_path: Path, plan: Dict[str, int], rng: random.Random) -> Dict[str, Any]:
    """State-like messages table: seed ``rows`` (bulk), then measure appends.

    WHY: the append hot path is one committed INSERT on a table with real
    indexes at real size — seeding first makes the measured commit pay for
    index maintenance against a populated table.
    """
    conn = _connect(db_path, synchronous="NORMAL")
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            msg_id TEXT NOT NULL UNIQUE
        );
    """)
    conn.commit()
    rows = plan["rows"]
    now = time.time()
    batch = [
        (
            _SESSIONS[i % len(_SESSIONS)],
            "assistant" if i % 3 else "user",
            _synthetic_message(rng),
            now + i * 0.001,
            f"m-{i:07d}",
        )
        for i in range(rows)
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, created_at, msg_id) VALUES (?, ?, ?, ?, ?)",
        batch,
    )
    conn.commit()
    # Indexes after the bulk load: seeding stays fast, appends pay the real cost.
    conn.execute("CREATE INDEX idx_messages_session ON messages(session_id, id);")
    conn.execute("CREATE INDEX idx_messages_created ON messages(created_at);")
    conn.commit()

    def _append(i: int) -> None:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, msg_id) VALUES (?, ?, ?, ?, ?)",
            (
                _SESSIONS[i % len(_SESSIONS)],
                "assistant",
                _synthetic_message(rng),
                time.time(),
                f"m-append-{i:07d}",
            ),
        )
        conn.commit()

    ops = plan["message_append_ops"]
    latencies = [_time_one(lambda i=i: _append(i)) for i in range(ops)]
    total_rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    stats = _stats(latencies, ops)
    stats["table_rows_at_measure"] = total_rows
    return stats


def _bench_search(
    db_path: Path,
    plan: Dict[str, int],
    rng: random.Random,
    features: Dict[str, bool],
) -> Dict[str, Dict[str, Any]]:
    """FTS5 BM25 / trigram vs LIKE over one seeded text corpus.

    The same deterministic query pool is replayed for every spelling so the
    comparison is apples-to-apples: BM25 queries are whole topic words,
    trigram/LIKE queries are the same words' [:6] prefixes (substring
    semantics). All queries are drawn from the seeded vocabulary, so every
    MATCH/LIKE returns hits — the measured cost is retrieval, not miss.
    """
    conn = _connect(db_path, synchronous="NORMAL")
    conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, content TEXT NOT NULL);")
    conn.commit()
    rows = plan["rows"]
    # docs.id (INTEGER PRIMARY KEY) == rowid, mirrored 1:1 by the FTS rowid.
    batch = [(i + 1, _synthetic_message(rng)) for i in range(rows)]
    conn.executemany("INSERT INTO docs (id, content) VALUES (?, ?)", batch)
    conn.commit()
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(content);")
    conn.execute("INSERT INTO docs_fts(rowid, content) SELECT id, content FROM docs;")
    conn.commit()

    trigram_ok = bool(features.get("trigram"))
    if trigram_ok:
        conn.execute("CREATE VIRTUAL TABLE docs_trigram USING fts5(content, tokenize='trigram');")
        conn.execute("INSERT INTO docs_trigram(rowid, content) SELECT id, content FROM docs;")
        conn.commit()

    ops = plan["search_ops"]
    n_topics = len(_TOPICS)
    bm25_queries = [f'"{_TOPICS[i % n_topics]}"' for i in range(ops)]
    frag_queries = [_TOPICS[i % n_topics][:6] for i in range(ops)]
    like_queries = [f"%{frag}%" for frag in frag_queries]
    limit = DEFAULT_SEARCH_LIMIT

    results: Dict[str, Dict[str, Any]] = {}

    like_ms = []
    for query in like_queries:
        like_ms.append(_time_one(lambda q=query: conn.execute(
            "SELECT rowid FROM docs WHERE content LIKE ? LIMIT ?",
            (q, limit),
        ).fetchall()))
    results["search_like_substring"] = _stats(like_ms, ops)

    bm25_ms = []
    for query in bm25_queries:
        bm25_ms.append(_time_one(lambda q=query: conn.execute(
            "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, limit),
        ).fetchall()))
    results["search_fts5_match"] = _stats(bm25_ms, ops)

    if trigram_ok:
        tri_ms = []
        for query in frag_queries:
            tri_ms.append(_time_one(lambda q=query: conn.execute(
                "SELECT rowid FROM docs_trigram WHERE docs_trigram MATCH ? LIMIT ?",
                (q, limit),
            ).fetchall()))
        results["search_fts5_trigram"] = _stats(tri_ms, ops)

    total_rows = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    conn.close()
    for stats in results.values():
        stats["table_rows_at_measure"] = total_rows
    return results


def _bench_event_store(
    db_path: Path,
    plan: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    """File-backed EventStore: big seeded store, then append + cursor + tails.

    The store is bulk-seeded to ``rows`` through its own schema (opened once
    via EventStore so schema/PRAGMAs match production, then filled with raw
    inserts in one transaction — seeding must not pay per-op fsync). Reads
    (tail windows) and writes (append, synchronous=FULL) are then measured
    on the populated file, which is the real hot path.
    """
    from hermes.platform.observability.event_store import EventStore

    # 1. create schema exactly like the runtime would.
    seed_store = EventStore(str(db_path))
    seed_store.close()

    rows = plan["rows"]
    conn = _connect(db_path, synchronous="NORMAL")
    now = time.time()
    events = []
    for i in range(1, rows + 1):
        events.append(
            (
                f"bench-{i:07d}",
                i,
                _EVENT_NAMES[i % len(_EVENT_NAMES)],
                f"trace-{i % 64:03d}",
                None,
                None,
                "internal",
                1,
                now + i * 0.001,
                '{"synthetic": true}',
            )
        )
    conn.executemany(
        """
        INSERT INTO events (
            event_id, seq, name, trace_id, correlation_id, causation_id,
            trust_level, schema_version, timestamp, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        events,
    )
    conn.commit()
    conn.close()

    store = EventStore(str(db_path))

    # tail reads first (measure against the seeded scale, no extra writes).
    tail_ops = plan["tail_ops"]
    tail_limit = DEFAULT_TAIL_LIMIT
    after_ms = [_time_one(lambda: store.events_after(0, limit=tail_limit)) for _ in range(tail_ops)]
    getall_ms = [_time_one(lambda: store.get_all(limit=tail_limit)) for _ in range(tail_ops)]

    # append: one committed Event per op (WAL + synchronous=FULL -> real fsync).
    append_ops = plan["event_append_ops"]

    def _make_event(i: int):
        from hermes.platform.observability.events import Event
        return Event(
            event_id=f"bench-append-{i:06d}",
            name=_EVENT_NAMES[i % len(_EVENT_NAMES)],
            trace_id=f"trace-append-{i % 64:03d}",
            payload={"synthetic": True, "seq_index": i},
            trust_level="internal",
            schema_version=1,
            timestamp=time.time(),
        )

    append_ms = []
    for i in range(append_ops):
        ev = _make_event(i)
        append_ms.append(_time_one(lambda e=ev: store.append(e)))

    cursor_ms = [_time_one(lambda: store.cursor()) for _ in range(plan["cursor_ops"])]

    final_cursor = store.cursor()
    store.close()

    stats: Dict[str, Dict[str, Any]] = {
        "session_tail_events_after": _stats(after_ms, tail_ops),
        "session_tail_get_all_limit": _stats(getall_ms, tail_ops),
        "event_store_append": _stats(append_ms, append_ops),
        "event_store_cursor": _stats(cursor_ms, plan["cursor_ops"]),
    }
    stats["session_tail_events_after"]["store_rows_at_measure"] = rows
    stats["session_tail_get_all_limit"]["store_rows_at_measure"] = rows
    stats["event_store_append"]["store_rows_at_measure"] = rows
    stats["event_store_cursor"]["store_rows_at_measure"] = final_cursor
    return stats


def _bench_ragflow(db_path: Path, plan: Dict[str, int], rng: random.Random) -> Optional[Dict[str, Any]]:
    """RAGFlow hybrid search (RRF: FTS5 BM25 + lexical) on a seeded vault.

    Fully offline: RAGFlowStore is SQLite+FTS5 only (no model, no
    embeddings, no network), so this path is measurable without any
    external service. Seeding goes through the engine's own
    ``index_document`` (one connection per doc, its real contract).
    """
    try:
        from hermes.platform.memory.ragflow_engine import RAGFlowStore
    except ImportError as exc:  # pragma: no cover - contract drift guard
        return None

    store = RAGFlowStore(db_path=db_path)
    docs = plan["ragflow_docs"]
    for i in range(1, docs + 1):
        doc = _synthetic_doc(rng, i)
        store.index_document(doc_path=f"vault/bench/{i:04d}.md", text=doc)

    ops = plan["ragflow_search_ops"]
    n_topics = len(_TOPICS)
    queries = [_TOPICS[i % n_topics] for i in range(ops)]

    def _search(i: int) -> None:
        store.hybrid_search(queries[i], limit=DEFAULT_SEARCH_LIMIT)

    ms = [_time_one(lambda i=i: _search(i)) for i in range(ops)]
    store = None  # release
    stats = _stats(ms, ops)
    stats["seeded_docs"] = docs
    return stats


# --------------------------------------------------------------------------- #
# runner / report / CLI
# --------------------------------------------------------------------------- #
def run_scale_benchmark(
    *,
    rows: Optional[int] = None,
    seed: Optional[int] = None,
    workdir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Runs the full HAOS scale benchmark and returns the structured report.

    ``workdir``: where seeded temp DBs live. Pass a path to keep artifacts
    (tests do this); when None a TemporaryDirectory is created and removed.
    Every other store path defaults under ``~/.hermes`` only when the store
    is constructed without a db_path — this runner always passes one, so
    nothing touches ``HERMES_HOME``.
    """
    rows = DEFAULT_ROWS if rows is None else int(rows)
    seed = int(seed) if seed is not None else DEFAULT_SEED
    plan = _derive_plan(rows)
    rng = random.Random(seed)
    t0 = time.perf_counter()
    features = detect_sqlite_features()
    skipped: List[Dict[str, str]] = [
        {
            "op": "vault_obsidian_scan_vs_index",
            "reason": (
                "no FTS5 index module for the Obsidian vault exists in-tree "
                "(hermes/platform/memory has no vault_fts/vault index); raw "
                "note scans without an index measure the filesystem, not a "
                "store hot path — add when the vault index ships"
            ),
        }
    ]
    if not features["fts5"]:
        skipped.append({"op": "search_fts5_match", "reason": "runtime sqlite has no FTS5 (PRAGMA compile_options)"})
        skipped.append({"op": "search_fts5_trigram", "reason": "runtime sqlite has no FTS5/trigram"})
        skipped.append({"op": "ragflow_hybrid_search", "reason": "RAGFlowStore requires FTS5 (CREATE VIRTUAL TABLE)"})
    if features["fts5"] and not features["trigram"]:
        skipped.append({"op": "search_fts5_trigram", "reason": "trigram tokenizer unavailable"})

    owns_workdir = workdir is None
    scratch = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="haos-scale-bench-"))
    scratch.mkdir(parents=True, exist_ok=True)

    operations: Dict[str, Dict[str, Any]] = {}

    try:
        # 1. state-like messages append.
        operations["state_messages_append"] = _bench_messages_append(
            scratch / "messages.db", plan, rng
        )

        # 2. search: FTS5 (BM25/trigram) vs LIKE on the same corpus.
        search = _bench_search(scratch / "search.db", plan, rng, features)
        operations.update(search)

        # 3 + 4. EventStore (file-backed, WAL): tail windows + append + cursor.
        operations.update(_bench_event_store(scratch / "events.db", plan))

        # 6. RAGFlow hybrid (offline SQLite+FTS5).
        if features["fts5"]:
            ragflow = _bench_ragflow(scratch / "ragflow.db", plan, rng)
            if ragflow is not None:
                operations["ragflow_hybrid_search"] = ragflow
            else:  # pragma: no cover - import drift guard
                skipped.append({"op": "ragflow_hybrid_search", "reason": "ragflow_engine import failed"})
        # 5. vault measurement: skipped (see top of list).

        report: Dict[str, Any] = {
            "harness": "haos-scale-bench",
            "timestamp": time.time(),
            "total_duration_seconds": round(time.perf_counter() - t0, 3),
            "config": {
                "seed": seed,
                "rows": plan["rows"],
                "tail_limit": DEFAULT_TAIL_LIMIT,
                "search_limit": DEFAULT_SEARCH_LIMIT,
                "op_iterations": {k: v for k, v in plan.items() if k != "rows"},
                "sqlite_version": sqlite3.sqlite_version,
            },
            "features": features,
            "skipped": skipped,
            "operations": operations,
        }
        if not owns_workdir:
            report["scratch_dir"] = str(scratch)
        return report
    finally:
        if owns_workdir:
            shutil.rmtree(scratch, ignore_errors=True)


def format_report_table(report: Dict[str, Any]) -> str:
    """Renders the human table (op | p50 ms | p95 ms | ops/s | iterations)."""
    ops = report.get("operations", {})
    width = max([len(name) for name in ops] + [len("operation")])
    lines = [
        "=" * (width + 46),
        f"HAOS scale benchmark — rows={report['config']['rows']} seed={report['config']['seed']} "
        f"sqlite={report['config']['sqlite_version']}",
        f"features: fts5={'yes' if report['features']['fts5'] else 'no'} "
        f"trigram={'yes' if report['features']['trigram'] else 'no'}",
        "-" * (width + 46),
        f"{'operation':<{width}}{'p50 ms':>10}{'p95 ms':>10}{'ops/s':>12}{'iterations':>12}",
        "-" * (width + 46),
    ]
    for name, stats in ops.items():
        lines.append(
            f"{name:<{width}}{stats['p50_ms']:>10.3f}{stats['p95_ms']:>10.3f}"
            f"{stats['ops_per_sec']:>12.1f}{stats['iterations']:>12}"
        )
    lines.append("=" * (width + 46))
    for skip in report.get("skipped", []):
        lines.append(f"skipped: {skip['op']} — {skip['reason']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m hermes.platform.benchmarks.haos_scale_bench`` entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m hermes.platform.benchmarks.haos_scale_bench",
        description="HAOS scale benchmark: p50/p95/throughput of hot stores and search paths.",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS,
                        help=f"seeded rows/text rows (default {DEFAULT_ROWS})")
    parser.add_argument("--out", type=str, default=None,
                        help="write the full JSON report to this path")
    parser.add_argument("--json", action="store_true",
                        help="print the full JSON report instead of the table")
    args = parser.parse_args(argv)

    report = run_scale_benchmark(rows=args.rows)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report_table(report))
        if args.out:
            print(f"JSON report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
