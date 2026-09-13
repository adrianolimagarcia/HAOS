"""Shared implementation behind ``hermes codebase-wiki`` (CLI + skill paths)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Set

# Reuse the indexer exclusion set; tests/ is added when --include-tests.
from hermes.platform.codebase import indexer
from hermes.platform.codebase.runner import IndexReport, run_index


def default_skip(include_tests: bool, docs: bool = False) -> Set[str]:
    skip = set(indexer.SKIP_DIRS)
    if not include_tests:
        skip.add("tests")
    if docs:
        # Markdown concept indexing wants real docs dirs reachable.
        skip.discard("docs")
        skip.discard("website")
    return skip


def run_cli(
    root: Path,
    out_dir: Path,
    *,
    include_tests: bool = False,
    max_files: Optional[int] = None,
    as_json: bool = False,
    docs: bool = False,
) -> int:
    """Run one full index pass and print a summary (JSON when asked)."""
    root = root.resolve()
    if not root.is_dir():
        print(f"codebase-wiki: not a directory: {root}", file=sys.stderr)
        return 2
    out_dir = out_dir.resolve()
    t0 = time.perf_counter()
    report = run_index(
        root,
        out_dir,
        skip=default_skip(include_tests, docs),
        max_files=max_files,
        markdown=docs,
    )
    elapsed = time.perf_counter() - t0
    if as_json:
        payload = report.to_dict()
        payload["elapsed_seconds"] = round(elapsed, 3)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        fresh = (
            f", {len(report.freshly_parsed)} arquivo(s) re-indexado(s)"
            if report.freshly_parsed
            else ""
        )
        print(
            f"codebase-wiki: {report.file_count} arquivos, {report.node_count} nós, "
            f"{report.edge_count} arestas, {report.community_count} comunidades{fresh} "
            f"em {elapsed:.2f}s"
        )
        print(f"  index  -> {report.written.get('_index')}")
        print(f"  graph  -> {report.written.get('_graph_json')}")
        if report.truncated:
            print("  aviso: corpus truncado (--max-files atingido)", file=sys.stderr)
    return 0


def run_cli_watch(
    root: Path,
    out_dir: Path,
    *,
    include_tests: bool = False,
    max_files: Optional[int] = None,
    docs: bool = False,
    poll_seconds: float = 1.5,
) -> int:
    """Index once, then re-index on every change until interrupted."""
    from hermes.platform.codebase.runner import IndexReport, watch

    root = root.resolve()
    if not root.is_dir():
        print(f"codebase-wiki: not a directory: {root}", file=sys.stderr)
        return 2
    # Index once up front so --watch is useful from a cold wiki, then observe.
    t0 = time.perf_counter()
    report = run_index(
        root,
        out_dir,
        skip=default_skip(include_tests, docs),
        max_files=max_files,
        markdown=docs,
    )
    print(
        f"codebase-wiki: {report.file_count} arquivos, {report.node_count} nós, "
        f"{report.edge_count} arestas em {time.perf_counter() - t0:.2f}s"
    )
    print(f"codebase-wiki: watch {root} -> {out_dir} (Ctrl+C para parar)")

    def on_reindex(report: IndexReport) -> None:
        print(
            f"codebase-wiki: re-indexado — {report.file_count} arquivos, "
            f"{report.node_count} nós, {report.edge_count} arestas"
        )

    try:
        watch(
            root,
            out_dir,
            skip=default_skip(include_tests, docs),
            max_files=max_files,
            poll_seconds=poll_seconds,
            on_reindex=on_reindex,
        )
    except KeyboardInterrupt:
        print("\ncodebase-wiki: watch encerrado.")
    return 0


def run_cli_mcp(out_dir: Path) -> int:
    """Register the wiki query tools on the HAOS LocalMCPAggregator (Fase 3)."""
    from hermes.platform.codebase.mcp_export import register_wiki_server
    from hermes.platform.mcp.aggregator import get_local_aggregator

    out_dir = out_dir.resolve()
    graph_path = out_dir / "graph.json"
    if not graph_path.is_file():
        print(f"codebase-wiki --mcp: graph não encontrado em {graph_path}", file=sys.stderr)
        return 1
    aggregator = get_local_aggregator()
    names = register_wiki_server(aggregator, graph_path)
    print("codebase-wiki --mcp: ferramentas registradas no LocalMCPAggregator:")
    for name in names:
        print(f"  - {name}")
    return 0
