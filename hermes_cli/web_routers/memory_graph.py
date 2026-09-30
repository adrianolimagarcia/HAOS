"""Authenticated Memory Graph and Multi-Tier Memory Topology API routes.

Provides topological views and deep-dives across all HAOS memory systems:
- Memory Fabric (Federated Coordinator & Canonical Spine)
- Canonical SQLite Database (fabric.db: committed facts, supersessions, outbox, acks)
- GraphRAG Store (graphrag.db: entities, relations, communities)
- Obsidian Vault (Markdown notes, ADRs, daily logs, curations)
- Decisions Store (decisions.db: ADR records)
- Vectors & Embeddings (vectors.db: semantic search)
- Graphify Engine (Code Knowledge Graph: AST symbols & dependency call graph)
- File Memory & Curator (memories/MEMORY.md, USER.md)
- Memory Providers (Holographic, Honcho, Mem0, ByteRover, Supermemory, etc.)
- Memory Scopes (Global, Project, Team, Private)
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from hermes_cli.web_deps import late
from hermes_constants import get_hermes_home

_log = logging.getLogger("hermes_cli.web_routers.memory_graph")
router = APIRouter()

_require_token = late("_require_token")
_config_profile_scope = late("_config_profile_scope", "hermes_cli.web_server_profiles")
_load_config = late("load_config", "hermes_cli.config")
_discover_memory_provider_statuses = late(
    "_discover_memory_provider_statuses", "hermes_cli.web_server_memory"
)

# Kinds mapped to constellation rendering
KIND_FABRIC = "fabric"
KIND_STORE = "store"
KIND_PROJECTION = "projection"
KIND_PROVIDER = "provider"
KIND_SCOPE = "scope"
KIND_ENTITY = "entity"
KIND_NOTE = "note"
KIND_DECISION = "decision"
KIND_CODE = "code"


def _safe_sqlite_query(db_path: Path, sql: str, params: Tuple = ()) -> List[Tuple]:
    """Execute a read-only query safely without locking the database."""
    if not db_path.is_file():
        return []
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            return conn.execute(sql, params).fetchall()
    except Exception as exc:
        _log.debug("SQLite read error on %s: %s", db_path, exc)
        return []


def _get_active_provider() -> str:
    try:
        cfg = _load_config()
        mem = cfg.get("memory") if isinstance(cfg, dict) else {}
        if isinstance(mem, dict):
            return str(mem.get("provider", "holographic") or "holographic")
    except Exception:
        pass
    return "holographic"


@router.get("/api/memory/graph/overview")
def get_memory_overview(request: Request, profile: Optional[str] = None) -> Dict[str, Any]:
    """Overview statistics and health indicators for all HAOS memory subsystems."""
    _require_token(request)
    with _config_profile_scope(profile):
        home = get_hermes_home()
        memory_dir = home / "memory"
        vault_dir = home / "obsidian_vault"
        memories_dir = home / "memories"

        # 1. Fabric stats
        fabric_db = memory_dir / "fabric.db"
        fabric_records = 0
        fabric_acks = 0
        fabric_outbox = 0
        if fabric_db.is_file():
            rows = _safe_sqlite_query(
                fabric_db,
                "SELECT "
                "(SELECT count(*) FROM memory_records), "
                "(SELECT count(*) FROM memory_projection_ack), "
                "(SELECT count(*) FROM memory_outbox)"
            )
            if rows and rows[0]:
                fabric_records, fabric_acks, fabric_outbox = rows[0]

        # 2. GraphRAG stats
        graphrag_db = memory_dir / "graphrag.db"
        gr_entities = 0
        gr_relations = 0
        gr_communities = 0
        gr_events = 0
        if graphrag_db.is_file():
            rows = _safe_sqlite_query(
                graphrag_db,
                "SELECT "
                "(SELECT count(*) FROM entities), "
                "(SELECT count(*) FROM relations), "
                "(SELECT count(*) FROM communities), "
                "(SELECT count(*) FROM applied_events)"
            )
            if rows and rows[0]:
                gr_entities, gr_relations, gr_communities, gr_events = rows[0]

        # 3. Obsidian stats
        vault_notes = 0
        vault_folders: Dict[str, int] = {}
        if vault_dir.is_dir():
            for root, _, files in os.walk(vault_dir):
                md_count = sum(1 for f in files if f.endswith(".md"))
                if md_count > 0:
                    rel = os.path.relpath(root, vault_dir)
                    top_folder = rel.split(os.sep)[0]
                    vault_folders[top_folder] = vault_folders.get(top_folder, 0) + md_count
                    vault_notes += md_count

        # 4. Decisions stats
        dec_db = memory_dir / "decisions.db"
        decisions_count = 0
        if dec_db.is_file():
            rows = _safe_sqlite_query(dec_db, "SELECT count(*) FROM memory_decisions")
            if rows and rows[0]:
                decisions_count = rows[0][0]

        # 5. Graphify stats
        graphify_nodes = 0
        graphify_edges = 0
        g_json = Path(".haos/graphify-out/graph.json")
        if not g_json.is_file():
            g_json = home / "graphify-out" / "graph.json"
        if g_json.is_file():
            try:
                with open(g_json, encoding="utf-8") as f:
                    g_data = json.load(f)
                    graphify_nodes = len(g_data.get("nodes", []))
                    graphify_edges = len(g_data.get("edges", []))
            except Exception:
                pass

        # 6. File memories
        curator_files = {}
        if memories_dir.is_dir():
            for fname in ["MEMORY.md", "USER.md"]:
                p = memories_dir / fname
                if p.is_file():
                    curator_files[fname] = p.stat().st_size

        # 7. Providers
        try:
            providers = _discover_memory_provider_statuses()
        except Exception:
            providers = []

        active_prov = _get_active_provider()

        return {
            "active_provider": active_prov,
            "fabric": {
                "records": fabric_records,
                "projection_acks": fabric_acks,
                "outbox_pending": fabric_outbox,
                "healthy": fabric_records > 0,
            },
            "graphrag": {
                "entities": gr_entities,
                "relations": gr_relations,
                "communities": gr_communities,
                "applied_events": gr_events,
                "healthy": gr_entities > 0,
            },
            "obsidian": {
                "total_notes": vault_notes,
                "folders": vault_folders,
                "healthy": vault_notes > 0,
            },
            "decisions": {
                "total": decisions_count,
            },
            "graphify": {
                "nodes": graphify_nodes,
                "edges": graphify_edges,
                "healthy": graphify_nodes > 0,
            },
            "curator": curator_files,
            "providers": providers,
            "scopes": ["global", "project", "team", "private"],
        }


@router.get("/api/memory/graph")
def get_memory_graph(
    request: Request,
    view: str = Query("unified", description="View filter: unified, graphrag, obsidian, db, graphify"),
    limit: int = Query(500, ge=10, le=2000),
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the memory graph topology and interlinked structures."""
    _require_token(request)
    with _config_profile_scope(profile):
        home = get_hermes_home()
        memory_dir = home / "memory"
        vault_dir = home / "obsidian_vault"

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        added_nodes: Set[str] = set()
        added_edges: Set[str] = set()

        def add_node(
            node_id: str,
            kind: str,
            label: str,
            group: str = "General",
            status: str = "active",
            score: float = 1.0,
            meta: Optional[Dict] = None,
        ):
            if node_id in added_nodes:
                return
            added_nodes.add(node_id)
            nodes.append({
                "id": node_id,
                "kind": kind,
                "label": label,
                "group": group,
                "status": status,
                "score": score,
                "meta": meta or {},
            })

        def add_edge(source: str, target: str, kind: str = "relates", directed: bool = True):
            if source == target:
                return
            eid = f"{source}->{target}:{kind}"
            revid = f"{target}->{source}:{kind}"
            if eid in added_edges or revid in added_edges:
                return
            added_edges.add(eid)
            edges.append({
                "id": eid,
                "source": source,
                "target": target,
                "kind": kind,
                "directed": directed,
            })

        # --- VIEW 1: UNIFIED ARCHITECTURE GRAPH (FULL FEDERATED GALAXY) ---
        if view == "unified":
            # Core Nexus & Hubs
            add_node(
                "fabric:coordinator",
                "fabric",
                "HAOS Memory Fabric",
                group="Scopes & Nexus",
                status="active",
                score=1.6,
                meta={
                    "description": "Federated Memory Coordinator. Consolidates candidate facts into canonical journal and coordinates durable fan-out projections.",
                    "architecture": "Event-driven federated memory with SQLite canonical journal.",
                }
            )

            add_node(
                "db:canonical_sqlite",
                "store",
                "Canonical SQLite Journal",
                group="Canonical DB",
                status="active",
                score=1.4,
                meta={
                    "path": str(memory_dir / "fabric.db"),
                    "description": "Single source of truth. Stores immutable versioned facts, confidence scores, and projection outbox log.",
                }
            )
            add_edge("fabric:coordinator", "db:canonical_sqlite", "commits_to")

            # 4 Projections
            projections = [
                ("projection:obsidian", "Obsidian Vault", "Obsidian Notes", "Audit & human-readable markdown notes with wikilinks and ADRs", str(vault_dir)),
                ("projection:graphrag", "GraphRAG Knowledge Graph", "GraphRAG Knowledge", "Extracted entities, typed relationships and hierarchical community clusters", str(memory_dir / "graphrag.db")),
                ("projection:vectors", "Vector Embeddings Index", "Canonical DB", "Semantic embeddings index for multi-scope similarity retrieval", str(memory_dir / "vectors.db")),
                ("projection:decisions", "ADR Decision Records", "Scopes & Nexus", "Formal architecture decisions with supersession DAG tracking", str(memory_dir / "decisions.db")),
            ]
            for pid, plabel, pgroup, pdesc, ppath in projections:
                add_node(pid, "projection", plabel, group=pgroup, status="active", score=1.3, meta={"description": pdesc, "path": ppath})
                add_edge("fabric:coordinator", pid, "projects_to")

            # Graphify Code Intelligence
            add_node(
                "knowledge:graphify",
                "code",
                "Graphify Code KG",
                group="Graphify Code AST",
                status="active",
                score=1.3,
                meta={
                    "description": "Deterministic AST/Tree-Sitter symbol call graph, blast-radius analyzer and code structure index.",
                    "path": ".haos/graphify-out/graph.json",
                }
            )
            add_edge("knowledge:graphify", "fabric:coordinator", "informs_runtime")

            # Inter-projection linkages
            add_edge("projection:graphrag", "db:canonical_sqlite", "indexes_records")
            add_edge("projection:graphrag", "projection:obsidian", "extracts_entities")
            add_edge("projection:decisions", "projection:obsidian", "mirrors_adrs")
            add_edge("projection:vectors", "db:canonical_sqlite", "embeds_facts")

            # Scopes
            scopes = [("scope:global", "Global Scope"), ("scope:project", "Project Scope"), ("scope:team", "Team Scope"), ("scope:private", "Private Scope")]
            for sid, slabel in scopes:
                add_node(sid, "scope", slabel, group="Scopes & Nexus", status="active", score=1.0, meta={"type": "Access Boundary"})
                add_edge("fabric:coordinator", sid, "governs_scope")

            # Memory Providers
            active_p = _get_active_provider()
            try:
                prov_statuses = _discover_memory_provider_statuses()
            except Exception:
                prov_statuses = []
            for p in prov_statuses[:10]:
                pname = p.get("name", "")
                if not pname:
                    continue
                p_id = f"provider:{pname}"
                is_active = (pname == active_p)
                p_status = "active" if is_active or p.get("status") == "ready" else "idle"
                add_node(
                    p_id,
                    "provider",
                    f"{pname}{' (Active)' if is_active else ''}",
                    group="Memory Providers",
                    status=p_status,
                    score=1.1 if is_active else 0.85,
                    meta={"description": p.get("description", ""), "status": p.get("status", "")}
                )
                add_edge(p_id, "fabric:coordinator", "pluggable_into")

            # 1. GraphRAG Entities & Relations
            graphrag_db = memory_dir / "graphrag.db"
            if graphrag_db.is_file():
                ents = _safe_sqlite_query(
                    graphrag_db,
                    "SELECT entity, entity_type, description FROM entities LIMIT ?",
                    (min(limit, 200),)
                )
                for ent_name, ent_type, ent_desc in ents:
                    node_id = f"entity:{ent_name}"
                    add_node(node_id, "entity", ent_name, group="GraphRAG Knowledge", status="active", score=0.9, meta={"type": ent_type, "description": ent_desc[:120]})

                rels = _safe_sqlite_query(
                    graphrag_db,
                    "SELECT source, target, relation_type FROM relations LIMIT ?",
                    (min(limit * 2, 350),)
                )
                for src, tgt, rel_type in rels:
                    s_id = f"entity:{src}" if f"entity:{src}" in added_nodes else f"note:{src}"
                    t_id = f"entity:{tgt}" if f"entity:{tgt}" in added_nodes else f"note:{tgt}"
                    if s_id in added_nodes and t_id in added_nodes:
                        add_edge(s_id, t_id, rel_type or "relates")

            # 2. Obsidian Vault Notes (All Notes & ADR references)
            if vault_dir.is_dir():
                note_count = 0
                for root, _, files in os.walk(vault_dir):
                    md_files = [f for f in files if f.endswith(".md")]
                    for fname in md_files:
                        if note_count >= min(limit, 150):
                            break
                        note_count += 1
                        stem = fname[:-3]
                        rel = os.path.relpath(root, vault_dir)
                        note_id = f"note:{stem}"
                        add_node(
                            note_id,
                            "note",
                            stem,
                            group="Obsidian Notes",
                            status="active",
                            score=0.85,
                            meta={"file": fname, "folder": rel}
                        )
                        add_edge("projection:obsidian", note_id, "contains_note")

                        # Cross-link note to GraphRAG ADR entity if referenced
                        try:
                            fpath = Path(root) / fname
                            content = fpath.read_text(encoding="utf-8", errors="ignore")
                            matches = set(re.findall(r"ADR-\d+", content))
                            for m in matches:
                                ent_id = f"entity:{m}"
                                if ent_id in added_nodes:
                                    add_edge(note_id, ent_id, "references_adr")
                        except Exception:
                            pass

            # 3. Canonical SQLite Facts
            fabric_db = memory_dir / "fabric.db"
            if fabric_db.is_file():
                records = _safe_sqlite_query(
                    fabric_db,
                    "SELECT record_id, scope, kind, content, confidence FROM memory_records LIMIT 60"
                )
                for rid, scope, kind, content, conf in records:
                    fact_id = f"fact:{rid}"
                    first_line = content.split("\n")[0][:32] if content else rid[:12]
                    group_name = "Canonical DB"
                    node_kind = "store"
                    if kind == "world_fact":
                        group_name = "Hindsight: World Facts"
                        node_kind = "world_fact"
                    elif kind == "experience":
                        group_name = "Hindsight: Experiences"
                        node_kind = "experience"
                    elif kind == "observation":
                        group_name = "Hindsight: Observations"
                        node_kind = "observation"
                    elif kind == "mental_model":
                        group_name = "Hindsight: Mental Models"
                        node_kind = "mental_model"
                    elif kind == "decision":
                        node_kind = "decision"
                    conf_val = float(conf if conf is not None else 1.0)
                    tier = "high" if conf_val >= 0.85 else ("verified" if conf_val >= 0.70 else "exploratory")
                    add_node(
                        fact_id,
                        node_kind,
                        first_line,
                        group=group_name,
                        status="active",
                        score=conf_val,
                        meta={
                            "scope": scope,
                            "record_id": rid,
                            "confidence": conf_val,
                            "confidence_tier": tier,
                            "content": content[:140]
                        }
                    )
                    add_edge("db:canonical_sqlite", fact_id, "persists_fact")

            # 3.1 Parent-Child Vectors Links
            vectors_db = memory_dir / "vectors.db"
            if vectors_db.is_file():
                v_rows = _safe_sqlite_query(
                    vectors_db,
                    "SELECT record_id, parent_id FROM memory_vectors WHERE parent_id IS NOT NULL LIMIT 80"
                )
                for child_id, parent_id in v_rows:
                    c_nid = f"fact:{child_id}"
                    p_nid = f"note:{parent_id}"
                    if c_nid in added_nodes and p_nid in added_nodes:
                        add_edge(p_nid, c_nid, "parent_of")

            # 4. Graphify Code AST Nodes
            g_json = Path(".haos/graphify-out/graph.json")
            if not g_json.is_file():
                g_json = home / "graphify-out" / "graph.json"
            if g_json.is_file():
                try:
                    with open(g_json, encoding="utf-8") as f:
                        g_data = json.load(f)
                    all_nodes = g_data.get("nodes", [])[:60]
                    g_map = {}
                    for n in all_nodes:
                        sname = n.get("name", "symbol")
                        cid = f"code:{sname}"
                        g_map[n.get("id", "")] = cid
                        add_node(
                            cid,
                            "code",
                            sname,
                            group="Graphify Code AST",
                            status="active",
                            score=0.75,
                            meta={"file": n.get("file", ""), "kind": n.get("kind", "")}
                        )
                        add_edge("knowledge:graphify", cid, "indexes_symbol")
                    for e in g_data.get("edges", [])[:120]:
                        s = g_map.get(e.get("source"))
                        t = g_map.get(e.get("target"))
                        if s and t and s in added_nodes and t in added_nodes:
                            add_edge(s, t, e.get("type", "calls"))
                except Exception:
                    pass

        # --- VIEW 2: GRAPHRAG DEEP-DIVE ---
        elif view == "graphrag":
            graphrag_db = memory_dir / "graphrag.db"
            add_node("hub:graphrag", "projection", "GraphRAG Nexus", group="GraphRAG Knowledge", status="active", score=1.5)
            if graphrag_db.is_file():
                ents = _safe_sqlite_query(
                    graphrag_db,
                    "SELECT entity, entity_type, description FROM entities LIMIT ?",
                    (limit,)
                )
                for ent_name, ent_type, ent_desc in ents:
                    node_id = f"entity:{ent_name}"
                    add_node(node_id, "entity", ent_name, group="GraphRAG Knowledge", status="active", score=0.9, meta={"type": ent_type, "description": ent_desc})
                    add_edge("hub:graphrag", node_id, "indexes")

                rels = _safe_sqlite_query(
                    graphrag_db,
                    "SELECT source, target, relation_type, description FROM relations LIMIT ?",
                    (limit * 2,)
                )
                for src, tgt, rel_type, r_desc in rels:
                    s_id = f"entity:{src}"
                    t_id = f"entity:{tgt}"
                    if s_id in added_nodes and t_id in added_nodes:
                        add_edge(s_id, t_id, rel_type or "relates_to")

        # --- VIEW 3: OBSIDIAN VAULT ---
        elif view == "obsidian":
            add_node("hub:obsidian", "projection", "Obsidian Vault Root", group="Obsidian Notes", status="active", score=1.5)
            if vault_dir.is_dir():
                count = 0
                for root, _, files in os.walk(vault_dir):
                    md_files = [f for f in files if f.endswith(".md")]
                    if not md_files:
                        continue
                    rel = os.path.relpath(root, vault_dir)
                    folder_id = f"folder:{rel}"
                    add_node(folder_id, "scope", rel, group="Obsidian Notes", status="active", score=1.1)
                    add_edge("hub:obsidian", folder_id, "folder")

                    for fname in md_files:
                        if count >= limit:
                            break
                        count += 1
                        stem = fname[:-3]
                        note_path = Path(root) / fname
                        note_id = f"note:{stem}"
                        add_node(
                            note_id,
                            "note",
                            stem,
                            group="Obsidian Notes",
                            status="active",
                            score=0.85,
                            meta={"folder": rel, "size": note_path.stat().st_size}
                        )
                        add_edge(folder_id, note_id, "contains")

        # --- VIEW 4: CANONICAL DB (FABRIC) ---
        elif view == "db":
            fabric_db = memory_dir / "fabric.db"
            add_node("hub:fabric_db", "store", "Canonical Fabric Store", group="Canonical DB", status="active", score=1.5)
            if fabric_db.is_file():
                records = _safe_sqlite_query(
                    fabric_db,
                    "SELECT record_id, scope, kind, status, content, confidence, supersedes_json FROM memory_records LIMIT ?",
                    (limit,)
                )
                for rid, scope, kind, st, content, conf, sup_json in records:
                    node_id = f"fact:{rid}"
                    first_line = content.split("\n")[0][:45] if content else rid
                    group_name = "Canonical DB"
                    node_kind = "store"
                    if kind == "world_fact":
                        group_name = "Hindsight: World Facts"
                        node_kind = "world_fact"
                    elif kind == "experience":
                        group_name = "Hindsight: Experiences"
                        node_kind = "experience"
                    elif kind == "observation":
                        group_name = "Hindsight: Observations"
                        node_kind = "observation"
                    elif kind == "mental_model":
                        group_name = "Hindsight: Mental Models"
                        node_kind = "mental_model"
                    elif kind == "decision":
                        node_kind = "decision"
                    conf_val = float(conf if conf is not None else 1.0)
                    tier = "high" if conf_val >= 0.85 else ("verified" if conf_val >= 0.70 else "exploratory")
                    add_node(
                        node_id,
                        node_kind,
                        first_line,
                        group=group_name,
                        status=st or "active",
                        score=conf_val,
                        meta={
                            "record_id": rid,
                            "scope": scope,
                            "confidence": conf_val,
                            "confidence_tier": tier,
                            "content": content[:300]
                        }
                    )
                    add_edge("hub:fabric_db", node_id, "contains_fact")

                    if sup_json:
                        try:
                            sups = json.loads(sup_json)
                            if isinstance(sups, list):
                                for s in sups:
                                    target_id = f"fact:{s}"
                                    if target_id in added_nodes:
                                        add_edge(node_id, target_id, "supersedes")
                        except Exception:
                            pass

                # Parent-Child hierarchy from vectors.db
                vectors_db = memory_dir / "vectors.db"
                if vectors_db.is_file():
                    v_rows = _safe_sqlite_query(
                        vectors_db,
                        "SELECT record_id, parent_id FROM memory_vectors WHERE parent_id IS NOT NULL LIMIT ?",
                        (limit,)
                    )
                    for child_id, parent_id in v_rows:
                        c_nid = f"fact:{child_id}"
                        p_nid = f"fact:{parent_id}" if f"fact:{parent_id}" in added_nodes else f"note:{parent_id}"
                        if c_nid in added_nodes and p_nid in added_nodes:
                            add_edge(p_nid, c_nid, "parent_of")

        # --- VIEW 5: GRAPHIFY CODE GRAPH ---
        elif view == "graphify":
            g_json = Path(".haos/graphify-out/graph.json")
            if not g_json.is_file():
                g_json = home / "graphify-out" / "graph.json"
            if g_json.is_file():
                try:
                    with open(g_json, encoding="utf-8") as f:
                        g_data = json.load(f)
                    nodes_raw = g_data.get("nodes", [])
                    edges_raw = g_data.get("edges", [])

                    id_to_nid: Dict[str, str] = {}
                    name_to_nid: Dict[str, str] = {}
                    for n in nodes_raw:
                        nid = f"code:{n.get('id', '')}"
                        id_to_nid[n.get("id", "")] = nid
                        name_to_nid[n.get("name", "")] = nid

                    # Calculate real connectivity degrees
                    deg_counts: Dict[str, int] = {}
                    for e in edges_raw:
                        s = id_to_nid.get(e.get("source")) or name_to_nid.get(e.get("source"))
                        t = id_to_nid.get(e.get("target")) or name_to_nid.get(e.get("target"))
                        if s and t and s != t:
                            deg_counts[s] = deg_counts.get(s, 0) + 1
                            deg_counts[t] = deg_counts.get(t, 0) + 1

                    # Pick top nodes by degree
                    sorted_nodes = sorted(
                        nodes_raw,
                        key=lambda n: deg_counts.get(f"code:{n.get('id', '')}", 0),
                        reverse=True
                    )[:limit]

                    def classify_code_group(file_path: str) -> str:
                        p = file_path.lower()
                        if "memory" in p or "graphrag" in p:
                            return "Code: Memory"
                        if "skill" in p:
                            return "Code: Skills"
                        if "observab" in p or "event" in p:
                            return "Code: Observability"
                        if "lane" in p or "execut" in p:
                            return "Code: Execution"
                        if "task" in p or "plan" in p:
                            return "Code: Tasks"
                        if "web" in p:
                            return "Code: WebUI"
                        if "capab" in p or "regist" in p:
                            return "Code: Capabilities"
                        return "Code: Core"

                    for n in sorted_nodes:
                        nid = f"code:{n.get('id', '')}"
                        grp = classify_code_group(n.get("file", ""))
                        add_node(
                            nid,
                            "code",
                            n.get("name", "symbol"),
                            group=grp,
                            status="active",
                            score=0.85,
                            meta={
                                "file": n.get("file", ""),
                                "kind": n.get("kind", ""),
                                "line": n.get("line", 0),
                                "id": n.get("id", "")
                            }
                        )

                    edge_pairs: Set[Tuple[str, str]] = set()
                    for e in edges_raw:
                        s = id_to_nid.get(e.get("source")) or name_to_nid.get(e.get("source"))
                        t = id_to_nid.get(e.get("target")) or name_to_nid.get(e.get("target"))
                        if s in added_nodes and t in added_nodes and s != t:
                            pair = (min(s, t), max(s, t))
                            if pair not in edge_pairs:
                                edge_pairs.add(pair)
                                add_edge(s, t, e.get("type", "calls"))
                except Exception as exc:
                    _log.warning("Failed to parse Graphify data: %s", exc)

        # Calculate node degrees
        degrees: Dict[str, int] = {}
        for e in edges:
            degrees[e["source"]] = degrees.get(e["source"], 0) + 1
            degrees[e["target"]] = degrees.get(e["target"], 0) + 1
        for n in nodes:
            n["degree"] = degrees.get(n["id"], 0)

        # Calculate group counts
        group_counts: Dict[str, int] = {}
        for n in nodes:
            grp = n.get("group", "General")
            group_counts[grp] = group_counts.get(grp, 0) + 1

        return {
            "schema_version": 1,
            "view": view,
            "nodes": nodes,
            "edges": edges,
            "group_counts": group_counts,
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
            },
        }


@router.get("/api/memory/graph/node/{node_id:path}")
def get_memory_node_details(
    request: Request,
    node_id: str,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve full details, raw text, or record payload for a selected graph node."""
    _require_token(request)
    with _config_profile_scope(profile):
        home = get_hermes_home()
        memory_dir = home / "memory"
        vault_dir = home / "obsidian_vault"

        # 1. Note lookup
        if node_id.startswith("note:"):
            stem_or_path = node_id[len("note:"):]
            file_path = None
            if (vault_dir / stem_or_path).is_file():
                file_path = vault_dir / stem_or_path
            elif (vault_dir / f"{stem_or_path}.md").is_file():
                file_path = vault_dir / f"{stem_or_path}.md"
            elif vault_dir.is_dir():
                for root, _, files in os.walk(vault_dir):
                    for f in files:
                        if f == f"{stem_or_path}.md" or f == stem_or_path or Path(f).stem == stem_or_path:
                            file_path = Path(root) / f
                            break
                    if file_path:
                        break
            if file_path and file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    return {
                        "id": node_id,
                        "type": "obsidian_note",
                        "path": str(file_path),
                        "content": content,
                        "size": len(content),
                    }
                except Exception as e:
                    return {"id": node_id, "error": str(e)}

        # 2. Entity lookup
        if node_id.startswith("entity:"):
            ent_name = node_id[len("entity:"):]
            graphrag_db = memory_dir / "graphrag.db"
            if graphrag_db.is_file():
                rows = _safe_sqlite_query(
                    graphrag_db,
                    "SELECT entity, entity_type, description FROM entities WHERE entity = ?",
                    (ent_name,)
                )
                if rows:
                    e_name, e_type, e_desc = rows[0]
                    rels = _safe_sqlite_query(
                        graphrag_db,
                        "SELECT target, relation_type, description FROM relations WHERE source = ? "
                        "UNION SELECT source, relation_type, description FROM relations WHERE target = ?",
                        (ent_name, ent_name)
                    )
                    return {
                        "id": node_id,
                        "type": "graphrag_entity",
                        "entity": e_name,
                        "entity_type": e_type,
                        "description": e_desc,
                        "relations": [{"target": r[0], "relation": r[1], "desc": r[2]} for r in rels],
                    }

        # 3. Canonical Fact lookup
        if node_id.startswith("fact:"):
            rec_id = node_id[len("fact:"):]
            fabric_db = memory_dir / "fabric.db"
            if fabric_db.is_file():
                rows = _safe_sqlite_query(
                    fabric_db,
                    "SELECT record_id, scope, kind, status, content, confidence, metadata_json, created_at FROM memory_records WHERE record_id = ?",
                    (rec_id,)
                )
                if rows:
                    r = rows[0]
                    conf_val = float(r[5] if r[5] is not None else 1.0)
                    tier = "high" if conf_val >= 0.85 else ("verified" if conf_val >= 0.70 else "exploratory")

                    parent_id = None
                    parent_content = None
                    vectors_db = memory_dir / "vectors.db"
                    if vectors_db.is_file():
                        v_rows = _safe_sqlite_query(
                            vectors_db,
                            "SELECT parent_id, parent_content FROM memory_vectors WHERE record_id = ? LIMIT 1",
                            (r[0],)
                        )
                        if v_rows and v_rows[0]:
                            parent_id = v_rows[0][0]
                            parent_content = v_rows[0][1]

                    return {
                        "id": node_id,
                        "type": "canonical_fact",
                        "record_id": r[0],
                        "scope": r[1],
                        "kind": r[2],
                        "status": r[3],
                        "content": r[4],
                        "confidence": conf_val,
                        "confidence_tier": tier,
                        "parent_id": parent_id,
                        "parent_content": parent_content,
                        "metadata": json.loads(r[6]) if r[6] else {},
                        "created_at": r[7],
                    }

        # 4. Code Symbol lookup
        if node_id.startswith("code:"):
            raw_id = node_id[len("code:"):]
            g_json = Path(".haos/graphify-out/graph.json")
            if not g_json.is_file():
                g_json = home / "graphify-out" / "graph.json"
            if g_json.is_file():
                try:
                    with open(g_json, encoding="utf-8") as f:
                        g_data = json.load(f)
                    for n in g_data.get("nodes", []):
                        if n.get("id") == raw_id or n.get("name") == raw_id:
                            fpath_str = n.get("file", "")
                            line = n.get("line", 1)
                            # Attempt to read source preview
                            candidate_paths = [
                                Path("hermes/platform") / fpath_str,
                                Path(fpath_str),
                                home / fpath_str,
                            ]
                            snippet = ""
                            found_path = ""
                            for cp in candidate_paths:
                                if cp.is_file():
                                    found_path = str(cp)
                                    lines = cp.read_text(encoding="utf-8", errors="ignore").splitlines()
                                    start = max(0, line - 3)
                                    end = min(len(lines), line + 30)
                                    snippet = "\n".join(lines[start:end])
                                    break
                            return {
                                "id": node_id,
                                "type": "code_symbol",
                                "name": n.get("name", ""),
                                "kind": n.get("kind", ""),
                                "file": fpath_str,
                                "real_path": found_path,
                                "line": line,
                                "snippet": snippet,
                            }
                except Exception as exc:
                    return {"id": node_id, "error": str(exc)}

        return {
            "id": node_id,
            "type": "generic_node",
            "message": "Node inspection available.",
        }
