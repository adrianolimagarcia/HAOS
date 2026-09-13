"""Deterministic query layer over a serialized Codebase Wiki graph.

Reads ``graph.json`` (produced by ``runner.run_index``) and answers structural
questions without re-deriving the graph: node lookup, substring search,
neighbourhood expansion, shortest path, god nodes and bridges. Pure stdlib,
offline; the same functions back the CLI ``--query``, the ``--watch`` status
line and the MCP ``wiki/*`` tools.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .clusters import ROOT_COMMUNITY
from .graph import MODULE_SEP

# All relation labels the pipeline can emit (PT, matching graph constants).
RELATIONS = ("importa", "chama", "herda", "cita")
EDGE_RELATIONS = RELATIONS


def load_graph_json(path: Path) -> Dict[str, Any]:
    """Load a persisted graph payload (nodes carry ``community``)."""
    if not path.is_file():
        raise FileNotFoundError(f"graph not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def node_index(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {n["id"]: n for n in payload.get("nodes", [])}


def search_nodes(
    payload: Dict[str, Any],
    needle: str,
    *,
    limit: int = 20,
    kinds: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Nodes whose id or label contains ``needle`` (case-insensitive)."""
    hay = needle.lower()
    allowed = set(kinds) if kinds is not None else None
    hits: List[Dict[str, Any]] = []
    for n in payload.get("nodes", []):
        if allowed is not None and n.get("kind") not in allowed:
            continue
        if hay in n["id"].lower() or hay in (n.get("label") or "").lower():
            hits.append(n)
        if len(hits) >= limit:
            break
    return hits


def _edges_between(payload: Dict[str, Any]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Map (source, target) -> matching edges (undirected lookup helper)."""
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for e in payload.get("edges", []):
        key = (e["source"], e["target"])
        out.setdefault(key, []).append(e)
        rev = (e["target"], e["source"])
        out.setdefault(rev, []).append(e)
    return out


def neighbors(
    payload: Dict[str, Any],
    node_id: str,
    *,
    relations: Optional[Iterable[str]] = None,
    direction: str = "both",
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Edges touching ``node_id`` with resolved node details, sorted by id.

    ``direction``: ``out`` (source->target), ``in`` (target->source) or
    ``both``. ``relations`` filters by relation label (default: all three).
    """
    rels = set(relations) if relations is not None else set(EDGE_RELATIONS)
    nodes = node_index(payload)
    out: List[Dict[str, Any]] = []
    for e in payload.get("edges", []):
        if e["relation"] not in rels:
            continue
        if direction in ("out", "both") and e["source"] == node_id:
            other = e["target"]
        elif direction in ("in", "both") and e["target"] == node_id:
            other = e["source"]
        else:
            continue
        out.append(
            {
                "relation": e["relation"],
                "confidence": e["confidence"],
                "count": e.get("count", 1),
                "node": nodes.get(other),
            }
        )
        if len(out) >= limit:
            break
    out.sort(key=lambda x: (x["node"]["id"] if x["node"] else "", x["relation"]))
    return out


def shortest_path(
    payload: Dict[str, Any],
    start: str,
    end: str,
    *,
    relations: Optional[Iterable[str]] = None,
) -> Optional[List[str]]:
    """BFS shortest undirected path (by node id chain) or None."""
    rels = set(relations) if relations is not None else set(EDGE_RELATIONS)
    adj: Dict[str, List[str]] = {}
    for e in payload.get("edges", []):
        if e["relation"] not in rels:
            continue
        adj.setdefault(e["source"], []).append(e["target"])
        adj.setdefault(e["target"], []).append(e["source"])
    if start not in adj or end not in adj:
        if start == end:
            return [start]
        return None
    prev: Dict[str, str] = {}
    seen = {start}
    queue: deque = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == end:
            break
        for nxt in sorted(adj.get(cur, [])):
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = cur
            queue.append(nxt)
    if end not in seen:
        return None
    chain = [end]
    while chain[-1] != start:
        chain.append(prev[chain[-1]])
    chain.reverse()
    return chain


def god_nodes(payload: Dict[str, Any], top: int = 10) -> List[Dict[str, Any]]:
    """Top node ids by total degree, ties broken by id."""
    deg: Dict[str, int] = {}
    for e in payload.get("edges", []):
        deg[e["source"]] = deg.get(e["source"], 0) + e.get("count", 1)
        deg[e["target"]] = deg.get(e["target"], 0) + e.get("count", 1)
    nodes = node_index(payload)
    ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    out: List[Dict[str, Any]] = []
    for nid, d in ranked:
        n = nodes.get(nid) or {}
        out.append(
            {
                "id": nid,
                "degree": d,
                "kind": n.get("kind"),
                "community": n.get("community", ""),
            }
        )
    return out


def community_stats(payload: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Community -> {modules, symbols} counts from node ``community`` tags."""
    stats: Dict[str, Dict[str, int]] = {}
    for n in payload.get("nodes", []):
        comm = n.get("community") or ROOT_COMMUNITY
        entry = stats.setdefault(comm, {"modules": 0, "symbols": 0})
        if n.get("kind") == "module":
            entry["modules"] += 1
        else:
            entry["symbols"] += 1
    return {k: stats[k] for k in sorted(stats)}


def summarize(payload: Dict[str, Any]) -> Dict[str, Any]:
    """One-shot summary used by --query=status and the MCP wiki/status tool."""
    stats = community_stats(payload)
    return {
        "root": payload.get("root"),
        "file_count": payload.get("file_count"),
        "node_count": len(payload.get("nodes", [])),
        "edge_count": len(payload.get("edges", [])),
        "truncated": payload.get("truncated", False),
        "communities": len(stats),
        "god_nodes": [g["id"] for g in god_nodes(payload, 5)],
    }


def node_line(n: Optional[Dict[str, Any]], with_community: bool = False) -> str:
    """Human line for a node dict: ``kind id (source_file:line)``."""
    if n is None:
        return "(desconhecido)"
    loc = n.get("source_file") or ""
    line = n.get("source_location")
    suffix = f" — comunidade {n.get('community')}" if with_community and n.get("community") else ""
    if line:
        return f"{n.get('kind')} `{n['id']}` ({loc}:{line}){suffix}"
    return f"{n.get('kind')} `{n['id']}` ({loc}){suffix}"


def format_neighbors(payload: Dict[str, Any], node_id: str, **kw: Any) -> str:
    """Markdown for a node's edges (MCP text content + CLI --query=edges)."""
    rows = neighbors(payload, node_id, **kw)
    if not rows:
        return f"Sem arestas para `{node_id}`."
    lines = [f"Arestas de `{node_id}` ({len(rows)}):"]
    for r in rows:
        other = r["node"]["id"] if r["node"] else "?"
        conf = r["confidence"]
        lines.append(f"- {r['relation']} [{conf}] -> `{other}`")
    return "\n".join(lines)
