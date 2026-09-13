"""Markdown/JSON renderer for the HAOS Codebase Wiki.

Pass 4: turn a resolved ``CodeGraph`` (plus its community assignment) into the
on-disk artifact set the agent actually reads:

* ``graph.json``        — full deterministic serialization (nodes + edges);
* ``index.md``          — the always-on entry point: god nodes, community
                          index with one-line summaries, top bridges and a
                          short "Limits" note;
* ``modules/<slug>.md`` — one article per community: responsibilities,
                          main concepts (top members by degree), internal
                          relations and external bridges, each item pointing
                          at ``source_file:line``.

Everything is deterministic: the same graph always renders the same bytes
(sorted keys, stable ordering, no timestamps), so ``--update`` on an
unchanged tree is a no-op byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .clusters import (
    ROOT_COMMUNITY,
    assign_communities,
    bridges,
    community_edges,
)
from .graph import (
    CONF_EXTRACTED,
    EDGE_CHAMA,
    EDGE_CITA,
    EDGE_HERDA,
    EDGE_IMPORTA,
    MODULE_SEP,
    CodeGraph,
    degree_map,
)

RELATION_VERB = {
    EDGE_IMPORTA: "importa",
    EDGE_CHAMA: "chama",
    EDGE_HERDA: "herda de",
    EDGE_CITA: "cita",
}


def _slug(key: str) -> str:
    # Keep "_" (so "_root.md" stays readable); only path separators change.
    return key.replace(".", "-").replace("::", "-")


def _loc(node_id: str, graph: CodeGraph) -> str:
    n = graph.nodes.get(node_id)
    if n is None:
        return ""
    if n.kind == "module":
        return n.source_file
    return f"{n.source_file}:{n.source_location}"


def _short(node_id: str) -> str:
    if MODULE_SEP in node_id:
        return node_id.split(MODULE_SEP, 1)[1]
    return node_id.rsplit(".", 1)[-1] if "." in node_id else node_id


def _kind_label(kind: str) -> str:
    return {
        "module": "módulo",
        "class": "classe",
        "function": "função",
        "async_function": "função async",
        "concept": "conceito (docs)",
    }.get(kind, kind)


# --------------------------------------------------------------------------
# graph.json
# --------------------------------------------------------------------------
def write_graph_json(graph: CodeGraph, node_to_comm: Dict[str, str], out_path: Path) -> None:
    payload = graph.to_dict()
    # Attach community per node so consumers never need to re-derive it.
    for n in payload["nodes"]:
        n["community"] = node_to_comm.get(n["id"], "")
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# index.md
# --------------------------------------------------------------------------
def _god_node_lines(graph: CodeGraph, top: int = 8) -> List[str]:
    deg = degree_map(graph)
    ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))
    lines: List[str] = []
    for nid, d in ranked[:top]:
        n = graph.nodes.get(nid)
        kind = _kind_label(n.kind) if n else "nó"
        lines.append(f"- `{nid}` ({kind}, grau {d}) — {_loc(nid, graph)}")
    return lines


def _community_index_lines(
    graph: CodeGraph,
    comms,
    node_to_comm: Dict[str, str],
) -> List[str]:
    deg = degree_map(graph)
    lines: List[str] = []
    for key in sorted(comms):
        c = comms[key]
        members = [nid for nid in c.member_node_ids if nid in deg]
        top_member = sorted(members, key=lambda nid: (-deg[nid], nid))[:1]
        desc = ""
        if top_member:
            tm = top_member[0]
            desc = f" — god node local: `{_short(tm)}` ({_loc(tm, graph)})"
        lines.append(
            f"- [{key}](modules/{_slug(key)}.md) — {c.module_count} módulos, "
            f"{c.symbol_count} símbolos{desc}"
        )
    return lines


def _suggested_questions(
    graph: CodeGraph,
    bridges_list: List[object],
    top_gods: List[Tuple[str, int]],
) -> List[str]:
    q: List[str] = []
    if top_gods:
        god = top_gods[0][0]
        label = _short(god)
        q.append(
            f"O que `{label}` faz e quem o usa? "
            f"(consultar `graph.json` por arestas de `{god}`)"
        )
    for b in bridges_list[:3]:
        q.append(
            f"Como a comunidade `{b.source_community}` se conecta a "
            f"`{b.target_community}`? ({b.count} ponte(s))"
        )
    if len(bridges_list) >= 2:
        # a structural question from the strongest bridge's sample edges
        e0 = bridges_list[0].sample_edges[0] if bridges_list[0].sample_edges else None
        if e0:
            q.append(
                f"Qual o caminho de `{_short(e0['source'])}` a `{_short(e0['target'])}`? "
                f"(" + RELATION_VERB.get(e0["relation"], e0["relation"]) + ")"
            )
    return q


def render_index_md(
    graph: CodeGraph,
    comms,
    node_to_comm: Dict[str, str],
    *,
    limits_note: str = "",
) -> str:
    deg = degree_map(graph)
    all_ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))
    top_gods: List[Tuple[str, int]] = all_ranked[:8]
    bridge_list = bridges(graph, node_to_comm)
    ext_edges = sum(1 for e in graph.edges if e.confidence == CONF_EXTRACTED)
    inf_edges = sum(1 for e in graph.edges if e.confidence != CONF_EXTRACTED)

    lines: List[str] = []
    lines.append("# Mapa de Código (Codebase Wiki)\n")
    lines.append(f"Raiz indexada: `{graph.root}` — {graph.file_count} arquivos, "
                 f"{len(graph.nodes)} nós, {len(graph.edges)} arestas "
                 f"({ext_edges} EXTRACTED, {inf_edges} INFERRED).\n")
    lines.append("Use este índice antes de perguntas de arquitetura; cada artigo "
                 "aponta `arquivo:linha` reais.\n")

    lines.append("## God nodes (conceitos mais conectados)\n")
    lines.extend(_god_node_lines(graph))
    lines.append("")

    lines.append("## Comunidades\n")
    lines.extend(_community_index_lines(graph, comms, node_to_comm))
    lines.append("")

    if bridge_list:
        lines.append("## Pontes entre comunidades\n")
        for b in bridge_list[:10]:
            lines.append(
                f"- `{b.source_community}` → `{b.target_community}` — {b.count} aresta(s)"
            )
        lines.append("")

    qs = _suggested_questions(graph, bridge_list, top_gods)
    if qs:
        lines.append("## Perguntas que este mapa pode responder\n")
        for q in qs:
            lines.append(f"- {q}")
        lines.append("")

    lines.append("## Limites\n")
    lines.append("- A indexação é **AST local (stdlib)**: chamadas dinâmicas "
                 "(`getattr`, `exec`, dispatch por string) não geram arestas.")
    lines.append("- Arestas apontam para símbolos **confirmados** (`EXTRACTED`); "
                 "quando só o módulo é confirmado, a aresta é `INFERRED`.")
    lines.append("- `graph.json` é a fonte canônica; `index.md`/artigos são visões geradas.")
    if graph.truncated:
        lines.append(f"- **Aviso:** corpus truncado em {graph.file_count} arquivos "
                     f"(limite de arquivos atingido).")
    if limits_note:
        lines.append(limits_note)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# module article
# --------------------------------------------------------------------------
def _internal_relations(
    comm_key: str, graph: CodeGraph, node_to_comm: Dict[str, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in graph.edges:
        if node_to_comm.get(e.source) == comm_key and node_to_comm.get(e.target) == comm_key:
            out.append(
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "confidence": e.confidence,
                    "count": e.count,
                }
            )
    out.sort(key=lambda d: (d["source"], d["target"], d["relation"]))
    return out


def _external_relations(
    comm_key: str, graph: CodeGraph, node_to_comm: Dict[str, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in graph.edges:
        sc = node_to_comm.get(e.source)
        tc = node_to_comm.get(e.target)
        if sc == comm_key and tc != comm_key:
            out.append({"dir": "out", "source": e.source, "target": e.target,
                        "relation": e.relation, "confidence": e.confidence, "count": e.count,
                        "other": tc})
        elif tc == comm_key and sc != comm_key:
            out.append({"dir": "in", "source": e.source, "target": e.target,
                        "relation": e.relation, "confidence": e.confidence, "count": e.count,
                        "other": sc})
    out.sort(key=lambda d: (d["other"], d["source"], d["target"]))
    return out


def _render_relation_line(item: Dict[str, Any]) -> str:
    verb = RELATION_VERB.get(item["relation"], item["relation"])
    return (f"- `{item['source']}` {verb} `{item['target']}` "
            f"({item['confidence'].lower()}, ×{item['count']})")


def render_community_md(
    key: str,
    graph: CodeGraph,
    comm: Any,
    node_to_comm: Dict[str, str],
) -> str:
    deg = degree_map(graph)
    members = [nid for nid in comm.member_node_ids if nid in deg]
    ranked = sorted(members, key=lambda nid: (-deg[nid], nid))

    lines: List[str] = []
    lines.append(f"# Comunidade: {key}\n")
    lines.append(f"- {comm.module_count} módulos · {comm.symbol_count} símbolos\n")

    lines.append("## Conceitos principais (por grau)\n")
    for nid in ranked[:15]:
        n = graph.nodes.get(nid)
        kind = _kind_label(n.kind) if n else ""
        lines.append(f"- `{_short(nid)}` ({kind}, grau {deg[nid]}) — {_loc(nid, graph)}")
    lines.append("")

    internal = _internal_relations(key, graph, node_to_comm)
    if internal:
        lines.append("## Relações internas\n")
        for item in internal[:25]:
            lines.append(_render_relation_line(item))
        lines.append("")

    external = _external_relations(key, graph, node_to_comm)
    if external:
        lines.append("## Relações externas (pontes)\n")
        for item in external[:25]:
            direction = "recebe de" if item["dir"] == "in" else "envia para"
            lines.append(
                f"- {direction} `{item['other']}`: "
                f"`{_short(item['source'])}` → `{_short(item['target'])}` "
                f"({item['relation']}, {item['confidence'].lower()})"
            )
        lines.append("")

    lines.append("---\n")
    footer = "*Gerado pelo HAOS Codebase Wiki — consulte `graph.json` para o grafo completo.*"
    lines.append(footer + "\n")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# top-level writer
# --------------------------------------------------------------------------
def render_all(
    graph: CodeGraph,
    out_dir: Path,
    *,
    limits_note: str = "",
) -> Dict[str, Path]:
    """Write graph.json + index.md + modules/*.md; returns written paths."""
    node_to_comm, comms = assign_communities(graph)

    out_dir.mkdir(parents=True, exist_ok=True)
    modules_dir = out_dir / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    write_graph_json(graph, node_to_comm, out_dir / "graph.json")
    (out_dir / "index.md").write_text(
        render_index_md(graph, comms, node_to_comm, limits_note=limits_note),
        encoding="utf-8",
    )
    written: Dict[str, Path] = {}
    for key in sorted(comms):
        p = modules_dir / f"{_slug(key)}.md"
        p.write_text(render_community_md(key, graph, comms[key], node_to_comm), encoding="utf-8")
        written[key] = p
    written["_index"] = out_dir / "index.md"
    written["_graph_json"] = out_dir / "graph.json"
    return written
