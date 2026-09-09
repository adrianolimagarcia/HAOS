"""Tests for the HAOS scale benchmark harness (haos_scale_bench.py).

Contract under test (behavior, not values — no change-detector, no
absolute-latency asserts; a busy runner shifts every timing):
- ``run_scale_benchmark`` returns a structured dict; every measured
  operation carries ``p50_ms``/``p95_ms``/``ops_per_sec``/``iterations``.
- Latency relations hold per sample: ``0 <= p50_ms <= p95_ms``.
- The run only ever writes to tmp dirs / an explicit ``workdir`` — never
  ``~/.hermes`` (HERMES_HOME stays empty after a run).
- Same seed -> same config and same seeded scale (determinism of what is
  seeded; latency itself is intentionally not frozen).

Small rows keep the suite fast (these tests run on the canonical runner,
whose per-file wall clock has no tight upper bound).
"""

from __future__ import annotations

import json

from hermes.platform.benchmarks.haos_scale_bench import (
    DEFAULT_SEED,
    format_report_table,
    run_scale_benchmark,
)

# Small scale for a fast suite; iterations are derived from rows and stay
# bounded (see _derive_plan), so this stays ~seconds even on a slow disk.
_SMALL_ROWS = 1200

_OP_STAT_KEYS = {"p50_ms", "p95_ms", "ops_per_sec", "iterations"}
# Ops that must exist on every run regardless of sqlite capabilities.
_ALWAYS_PRESENT_OPS = {
    "state_messages_append",
    "search_like_substring",
    "session_tail_events_after",
    "session_tail_get_all_limit",
    "event_store_append",
    "event_store_cursor",
}


def _assert_stats_relations(stats) -> None:
    """Behaviour contract of every measured op (relations, never fixed values)."""
    assert _OP_STAT_KEYS.issubset(stats.keys()), f"missing keys in {sorted(stats)}"
    assert stats["iterations"] >= 1
    assert 0.0 <= stats["p50_ms"] <= stats["p95_ms"], stats
    assert stats["ops_per_sec"] > 0.0


def test_report_shape_and_stat_relations(tmp_path):
    report = run_scale_benchmark(rows=_SMALL_ROWS, workdir=tmp_path / "bench")

    assert report["harness"] == "haos-scale-bench"
    assert report["total_duration_seconds"] >= 0.0
    assert report["config"]["rows"] == _SMALL_ROWS
    assert set(report["features"]) == {"fts5", "trigram"}
    assert isinstance(report["skipped"], list)

    ops = report["operations"]
    assert _ALWAYS_PRESENT_OPS.issubset(ops.keys())
    for stats in ops.values():
        _assert_stats_relations(stats)

    # FTS-backed ops only exist where the runtime sqlite actually has FTS5;
    # when it does not, they are documented as skipped (never silently absent
    # or silently present).
    fts5 = report["features"]["fts5"]
    assert ("search_fts5_match" in ops) is fts5
    assert ("search_fts5_trigram" in ops) is bool(report["features"]["trigram"])
    assert ("ragflow_hybrid_search" in ops) is fts5
    skipped_ops = {s["op"] for s in report["skipped"]}
    if not fts5:
        assert "search_fts5_match" in skipped_ops
        assert "ragflow_hybrid_search" in skipped_ops


def test_runs_in_tmp_dirs_never_hermes_home(tmp_path, monkeypatch):
    # Point HERMES_HOME at a fresh dir: the harness must not create a single
    # artifact there — all seeded DBs land in the caller-provided workdir.
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    bench_dir = tmp_path / "bench"
    report = run_scale_benchmark(rows=300, workdir=bench_dir)

    assert report["scratch_dir"] == str(bench_dir)
    dbs = sorted(p.name for p in bench_dir.glob("*.db"))
    assert len(dbs) >= 3, dbs  # messages.db / search.db / events.db / ragflow.db
    assert list(home.iterdir()) == [], "benchmark wrote into HERMES_HOME"


def test_same_seed_is_deterministic_in_config_and_seeded_scale(tmp_path):
    # Timing varies run to run, but the seeded corpus scale and the op plan
    # must be reproducible for a fixed seed — that is what makes p50/p95
    # comparisons across builds meaningful.
    a = run_scale_benchmark(rows=250, workdir=tmp_path / "a")
    b = run_scale_benchmark(rows=250, workdir=tmp_path / "b")

    assert a["config"] == b["config"]
    assert a["config"]["seed"] == DEFAULT_SEED
    for op in a["operations"]:
        assert a["operations"][op]["iterations"] == b["operations"][op]["iterations"]
        for scale_key in ("table_rows_at_measure", "store_rows_at_measure", "seeded_docs"):
            if scale_key in a["operations"][op]:
                assert a["operations"][op][scale_key] == b["operations"][op][scale_key], op


def test_report_is_json_serializable(tmp_path):
    report = run_scale_benchmark(rows=200, workdir=tmp_path / "bench")
    # --out/--json paths serialize the whole report; if this raises, the
    # CLI would too.
    json.dumps(report)


def test_format_report_table_lists_every_measured_operation(tmp_path):
    report = run_scale_benchmark(rows=200, workdir=tmp_path / "bench")
    table = format_report_table(report)
    assert "p50 ms" in table
    assert "ops/s" in table
    for op in report["operations"]:
        assert op in table
