#!/usr/bin/env python3
"""Query helper for the HAOS Codebase Wiki graph.

Reads ``graph.json`` produced by ``hermes codebase-wiki`` and answers
structural questions: edges touching a node, shortest path between two nodes,
god nodes. Stdlib-only and offline so it can run anywhere the wiki lives.

Usage:
    python3 haos_wiki_query.py --edges tools.registry::dispatch
    python3 haos_wiki_query.py --path agent.run_agent hermes_cli.main
    python3 haos_wiki_query.py --gods --limit 5
    python3 haos_wiki_query.py --nodes --search registry
    python3 haos_wiki_query.py --root /path/to/codebase-wiki --gods
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def default_graph_path() -> Path:
    """Locate graph.json: --root > HERMES_HOME/codebase-wiki > $HOME/.hermes/codebase-wiki."""
    root = os.environ.get("HERMES_HOME")
    if root:
        return Path(root) / "codebase-wiki" / "graph.json"
    return Path.home() / ".hermes" / "codebase-wiki" / "graph.json"


def load_graph(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        sys.exit(f"graph not found: {path} — run `hermes codebase-wiki` first")
    return json.loads(path.read_text(encoding="utf-8"))


def node_map(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {n["id"]: n for n in graph["nodes"]}


def _label(graph: Dict[str, Any], node_id: str) -> str:
    node = node_map(graph).get(node_id)
    if node is None:
        return node_id
    loc = node.get("source_file") or ""
    line = node.get("source_location")
    if line:
        return f"{node.get('kind','')} {node_id} ({loc}:{line})"
    return f"{node.get('kind','')} {node_id}"


def cmd_edges(graph: Dict[str, Any], node_id: str, direction: str, limit: int) -> int:
    known_rels = ("importa", "chama", "herda")
    if direction:
        rels = [r.strip() for r in direction.split(",") if r.strip() in known_rels]
    else:
        rels = list(known_rels)
    hits = 0
    print(f"# edges touching {node_id}")
    for e in graph["edges"]:
        if e["relation"] not in rels:
            continue
        touched = e["source"] == node_id or e["target"] == node_id
        if not touched:
            continue
        arrow = "->" if e["source"] == node_id else "<-"
        other = e["target"] if e["source"] == node_id else e["source"]
        print(
            f"- {e['relation']} [{e['confidence']}] {_label(graph, node_id)} "
            f"{arrow} {_label(graph, other)}"
        )
        hits += 1
        if limit and hits >= limit:
            break
    if not hits:
        print("(no edges)")
    return 0


def _neighbors(graph: Dict[str, Any], node_id: str, rels: Set[str]) -> List[str]:
    out: List[str] = []
    for e in graph["edges"]:
        if e["relation"] not in rels:
            continue
        if e["source"] == node_id:
            out.append(e["target"])
        elif e["target"] == node_id:
            out.append(e["source"])
    return out


def cmd_path(graph: Dict[str, Any], start: str, end: str) -> int:
    nodes = node_map(graph)
    if start not in nodes or end not in nodes:
        print(f"unknown node: {start if start not in nodes else end}")
        return 1
    rels = {"importa", "chama", "herda"}
    # undirected BFS
    prev: Dict[str, Optional[str]] = {start: None}
    queue: deque = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == end:
            break
        for nxt in _neighbors(graph, cur, rels):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if end not in prev:
        print(f"no path between {start} and {end}")
        return 1
    chain: List[str] = []
    cur: Optional[str] = end
    while cur is not None:
        chain.append(cur)
        cur = prev[cur]
    chain.reverse()
    print(f"# path ({len(chain)-1} hops)")
    for i, nid in enumerate(chain):
        arrow = "--" if i == len(chain) - 1 else "->"
        print(f"{_label(graph, nid)} {arrow}")
    return 0


def cmd_gods(graph: Dict[str, Any], limit: int) -> int:
    deg: Dict[str, int] = {}
    for e in graph["edges"]:
        deg[e["source"]] = deg.get(e["source"], 0) + e.get("count", 1)
        deg[e["target"]] = deg.get(e["target"], 0) + e.get("count", 1)
    ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    print("# god nodes (by degree)")
    for nid, d in ranked:
        print(f"- grau {d}: {_label(graph, nid)}")
    return 0


def cmd_nodes(graph: Dict[str, Any], search: str, limit: int) -> int:
    needle = search.lower()
    hits = 0
    print(f"# nodes matching '{search}'")
    for n in sorted(graph["nodes"], key=lambda n: n["id"]):
        if needle in n["id"].lower() or needle in (n.get("label") or "").lower():
            print(f"- {_label(graph, n['id'])}")
            hits += 1
            if limit and hits >= limit:
                break
    if not hits:
        print("(no matches)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Query the HAOS codebase-wiki graph")
    p.add_argument("--root", default=None, help="path to a codebase-wiki dir containing graph.json")
    p.add_argument("--edges", metavar="NODE", help="show edges touching NODE")
    p.add_argument("--direction", default="", help="comma-separated: importa,chama,herda")
    p.add_argument("--path", nargs=2, metavar=("A", "B"), help="shortest path A -> B")
    p.add_argument("--gods", action="store_true", help="list god nodes")
    p.add_argument("--nodes", action="store_true", help="search nodes")
    p.add_argument("--search", default="", help="substring for --nodes")
    p.add_argument("--limit", type=int, default=0, help="cap results (0 = unlimited)")
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else None
    gpath = (root / "graph.json") if root else default_graph_path()
    graph = load_graph(gpath)

    ran = False
    if args.edges:
        cmd_edges(graph, args.edges, args.direction, args.limit); ran = True
    if args.path:
        cmd_path(graph, args.path[0], args.path[1]); ran = True
    if args.gods:
        cmd_gods(graph, args.limit or 10); ran = True
    if args.nodes or args.search:
        cmd_nodes(graph, args.search, args.limit); ran = True
    if not ran:
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
