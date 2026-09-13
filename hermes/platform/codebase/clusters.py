"""Community detection by path prefix for the HAOS Codebase Wiki.

Pass 3 (lightweight, deterministic): assign every module to a *community*
derived from its real root-relative path, and compute the bridges between
communities. This is deliberately NOT Leiden or any modularity optimisation —
it is the "proxy of very high fidelity" the spec asks for, at zero cost:

* a flat top-level package (``tools/registry.py``, ``tools/tool_search.py``…)
  is one community, because the directory boundary already IS the module
  boundary there — splitting it further would fabricate communities;
* a top-level package that is mostly a namespace shell (``hermes/`` holding
  only ``hermes/platform/*``, ``plugins/`` holding ``platforms/``,
  ``memory/``, …) splits at the SECOND path segment, producing communities
  like ``hermes.platform`` and ``plugins.platforms`` that mirror the real
  sub-package layout;
* root-level files (``cli.py``, ``run_agent.py``) share the ``_root``
  community.

The rule is size-aware so a synthetic corpus with one file per directory does
not fragment into single-file communities: depth-2 splitting only kicks in
when a top directory has enough nested files to justify it.

Only the graph nodes (which carry ``source_file``) are needed, so this stays a
pure function over the corpus. No side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from .graph import CodeGraph, MODULE_SEP

ROOT_COMMUNITY = "_root"

# Depth-2 splitting threshold: a top directory splits at its second segment
# only when it holds at least these many nested files AND clearly more nested
# than direct ones. Keeps flat packages and tiny corpora as one community.
_MIN_NESTED_FOR_SPLIT = 8
_MIN_NESTED_DIRECT_RATIO = 2


def _module_source_paths(graph: CodeGraph) -> List[str]:
    return sorted(
        n.source_file for n in graph.nodes.values() if n.kind == "module" and n.source_file
    )


def _top_stats(source_paths: List[str]) -> Dict[str, Tuple[int, int]]:
    """Per top directory: (files directly in it, files in its subdirectories)."""
    stats: Dict[str, List[int]] = {}
    for sp in source_paths:
        parts = PurePosixPath(sp).parts
        top = parts[0] if len(parts) >= 2 else ROOT_COMMUNITY
        entry = stats.setdefault(top, [0, 0])
        if len(parts) == 2:          # "top/file.py"
            entry[0] += 1
        else:                         # "top/sub/.../file.py" or root file
            entry[1] += 1
    return {top: tuple(v) for top, v in stats.items()}


def community_for_path(source_file: str, top_stats: Dict[str, Tuple[int, int]]) -> str:
    """Community key for one module's root-relative path."""
    parts = PurePosixPath(source_file).parts
    if len(parts) <= 1:
        return ROOT_COMMUNITY
    top = parts[0]
    direct, nested = top_stats.get(top, (0, 0))
    if nested >= _MIN_NESTED_FOR_SPLIT and nested >= _MIN_NESTED_DIRECT_RATIO * max(direct, 1):
        return f"{top}.{parts[1]}" if len(parts) >= 3 else top
    return top


def community_of_node(node_id: str, top_stats: Dict[str, Tuple[int, int]]) -> str:
    """Community of any node, derived from the module id's path shape.

    Used when source_file is not authoritative (tests, direct calls); module
    ids mirror the path (``hermes.platform.mcp.aggregator``), so the same
    depth rule applies to the dotted id.
    """
    module_id = node_id.split(MODULE_SEP, 1)[0] if MODULE_SEP in node_id else node_id
    segs = module_id.split(".")
    if len(segs) <= 1:
        return ROOT_COMMUNITY
    top = segs[0]
    direct, nested = top_stats.get(top, (0, 0))
    if nested >= _MIN_NESTED_FOR_SPLIT and nested >= _MIN_NESTED_DIRECT_RATIO * max(direct, 1):
        return ".".join(segs[:2]) if len(segs) >= 3 else top
    return top


@dataclass
class Community:
    key: str
    module_count: int = 0
    symbol_count: int = 0
    member_node_ids: List[str] = field(default_factory=list)


@dataclass
class Bridge:
    source_community: str
    target_community: str
    count: int = 0
    sample_edges: List[Dict[str, object]] = field(default_factory=list)


def _stats_from_graph(graph: CodeGraph) -> Dict[str, Tuple[int, int]]:
    return _top_stats(_module_source_paths(graph))


def assign_communities(graph: CodeGraph) -> Tuple[Dict[str, str], Dict[str, Community]]:
    """Return (node_id -> community, community key -> Community)."""
    top_stats = _stats_from_graph(graph)
    node_to_comm: Dict[str, str] = {}
    # Module nodes -> community by path; symbol nodes inherit their module's.
    module_comm: Dict[str, str] = {}
    for nid, n in graph.nodes.items():
        if n.kind == "module":
            comm = community_for_path(n.source_file, top_stats)
            module_comm[nid] = comm
            node_to_comm[nid] = comm
    for nid, n in graph.nodes.items():
        if n.kind != "module":
            node_to_comm[nid] = module_comm.get(n.module, ROOT_COMMUNITY)
    comms: Dict[str, Community] = {}
    for nid, comm in node_to_comm.items():
        c = comms.setdefault(comm, Community(key=comm))
        c.member_node_ids.append(nid)
        node = graph.nodes[nid]
        if node.kind == "module":
            c.module_count += 1
        else:
            c.symbol_count += 1
    for c in comms.values():
        c.member_node_ids.sort()
    return node_to_comm, comms


def community_edges(
    graph: CodeGraph, node_to_comm: Dict[str, str]
) -> List[Tuple[str, str, object]]:
    """Inter-community edges as (src_comm, dst_comm, edge)."""
    out: List[Tuple[str, str, object]] = []
    for e in graph.edges:
        sc = node_to_comm.get(e.source)
        tc = node_to_comm.get(e.target)
        if sc is None or tc is None or sc == tc:
            continue
        out.append((sc, tc, e))
    return out


def bridges(
    graph: CodeGraph, node_to_comm: Dict[str, str], cap_samples: int = 3
) -> List[Bridge]:
    """Aggregate inter-community edges into bridge summaries (deterministic)."""
    agg: Dict[Tuple[str, str], List[object]] = {}
    for sc, tc, e in community_edges(graph, node_to_comm):
        agg.setdefault((sc, tc), []).append(e)
    result: List[Bridge] = []
    for (sc, tc), edges in agg.items():
        result.append(
            Bridge(
                source_community=sc,
                target_community=tc,
                count=sum(e.count for e in edges),  # type: ignore[attr-defined]
                sample_edges=[
                    {"source": e.source, "target": e.target, "relation": e.relation}
                    for e in sorted(
                        edges, key=lambda e: (e.source, e.target, e.relation)
                    )[:cap_samples]
                ],
            )
        )
    result.sort(key=lambda b: (-b.count, b.source_community, b.target_community))
    return result
