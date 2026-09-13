"""``hermes codebase-wiki`` — build/serve the HAOS Codebase Wiki.

Usage:
    hermes codebase-wiki [ROOT] [--out DIR] [--include-tests] [--max-files N]
    hermes codebase-wiki [ROOT] --watch          # re-index on file changes
    hermes codebase-wiki [ROOT] --mcp            # expose wiki query via MCP stdio
    hermes codebase-wiki --help

The default output directory is ``<HERMES_HOME>/codebase-wiki``; artifacts are
``graph.json``, ``index.md`` and ``modules/*.md`` (see
``docs/haos/CODEBASE_WIKI.md``). All heavy imports happen at call time so this
command costs nothing at CLI startup.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional


def build_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "codebase-wiki",
        help="Index a code root into a local knowledge-graph wiki (HAOS).",
        description="Build or refresh the HAOS Codebase Wiki: a deterministic, "
            "offline knowledge graph (graph.json + index.md + per-community "
            "articles) over a local Python tree.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Root tree to index (default: .)")
    parser.add_argument(
        "--out", default=None,
        help="Output dir (default: <HERMES_HOME>/codebase-wiki)",
    )
    parser.add_argument(
        "--include-tests", action="store_true",
        help="Also index tests/ trees (excluded by default).",
    )
    parser.add_argument(
        "--docs", action="store_true",
        help="Also index .md files as concept nodes with `cita` edges to code.",
    )
    parser.add_argument(
        "--max-files", type=int, default=None,
        help="Cap the number of modules indexed (deterministic prefix; visible truncation marker).",
    )
    parser.add_argument("--json", action="store_true", help="Print the run report as JSON.")
    parser.add_argument(
        "--watch", action="store_true",
        help="Re-index whenever files under ROOT change (Ctrl+C to stop).",
    )
    parser.add_argument(
        "--poll", type=float, default=1.5,
        help="Seconds between change checks in --watch mode (default: 1.5).",
    )
    parser.add_argument(
        "--mcp", action="store_true",
        help="Register wiki query tools on the HAOS LocalMCPAggregator and exit.",
    )
    parser.set_defaults(func=codebase_wiki_command)
    return parser


def _default_out_dir() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "codebase-wiki"


def codebase_wiki_command(args: argparse.Namespace) -> int:
    """Entry point from ``hermes codebase-wiki …``; returns a shell-style exit code."""
    from .codebase_wiki_impl import run_cli, run_cli_mcp, run_cli_watch

    root = Path(args.root or ".")
    out_dir = Path(args.out) if args.out else _default_out_dir()
    include_tests = bool(getattr(args, "include_tests", False))
    max_files = getattr(args, "max_files", None)

    if getattr(args, "watch", False):
        return run_cli_watch(
            root=root,
            out_dir=out_dir,
            include_tests=include_tests,
            max_files=max_files,
            docs=bool(getattr(args, "docs", False)),
            poll_seconds=float(getattr(args, "poll", 1.5)),
        )
    if getattr(args, "mcp", False):
        # Ensure a fresh index exists first, then register on the aggregator.
        rc = run_cli(
            root=root, out_dir=out_dir, include_tests=include_tests,
            max_files=max_files, docs=bool(getattr(args, "docs", False)),
            as_json=False,
        )
        if rc != 0:
            return rc
        return run_cli_mcp(out_dir=out_dir)

    return run_cli(
        root=root,
        out_dir=out_dir,
        include_tests=include_tests,
        max_files=max_files,
        docs=bool(getattr(args, "docs", False)),
        as_json=bool(getattr(args, "json", False)),
    )
