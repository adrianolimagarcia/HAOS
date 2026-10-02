"""Civilization Graph DAG Projection Engine.

Projects bots, councils, decisions, leaves, evolution proposals, and social
relationships into an interconnected directed graph model (nodes and edges).
"""

from __future__ import annotations

from typing import Any, Dict, List

from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.council.manager import CouncilManager
from hermes.platform.evolution.bot_evolution import BotEvolutionManager
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.society.manager import SocietyManager


def build_civ_graph(store) -> dict:
    """Project complete graph DAG (nodes and edges) from canonical event store."""
    bot_spec = BotSpecManager(store)
    bot_id_mgr = IdentityManager(store)
    council_mgr = CouncilManager(store)
    evolution_mgr = BotEvolutionManager(store)
    civ_mgr = CivilizationManager(store)
    society = SocietyManager(store)

    bots = bot_spec.list()
    councils = council_mgr.list()
    decisions = council_mgr.list_decisions()
    proposals = evolution_mgr.get_proposals()
    constitution = civ_mgr.get_active_constitution()
    reps = society.get_all_reputations()

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_edges: set[str] = set()

    # 1. Bots
    for b in bots:
        bid = b.id
        active_ver = bot_id_mgr.get_active_version(bid)
        rep = reps.get(bid)
        nodes.append({
            "id": f"bot:{bid}",
            "kind": "bot",
            "label": b.name or bid,
            "status": "active" if active_ver else "uninitialized",
            "bot_id": bid,
            "version": active_ver.version if active_ver else b.version,
            "score": rep.overall_score if rep else 1.0,
            "evidence_count": rep.evidence_count if rep else 0,
        })

    # 2. Councils
    for c in councils:
        cid = c.id
        c_node_id = f"council:{cid}"
        purpose = getattr(c, "purpose", None) or getattr(c, "name", "")
        nodes.append({
            "id": c_node_id,
            "kind": "council",
            "label": purpose or cid,
            "status": "active",
            "council_id": cid,
            "members": c.members,
            "decision_mode": getattr(c, "decision_mode", "consensus_with_dissent"),
        })
        for member_bot in c.members:
            edges.append({
                "id": f"edge:council-member:{cid}->{member_bot}",
                "source": c_node_id,
                "target": f"bot:{member_bot}",
                "kind": "has_member",
                "directed": True,
            })

    # 3. Decisions
    for d in decisions:
        did = d.id
        d_node_id = f"decision:{did}"
        nodes.append({
            "id": d_node_id,
            "kind": "decision",
            "label": d.decision or did,
            "status": "ratified",
            "decision_id": did,
            "council_id": d.council_id,
            "confidence": getattr(d, "confidence", 1.0),
        })
        if d.council_id:
            edges.append({
                "id": f"edge:council-decision:{d.council_id}->{did}",
                "source": f"council:{d.council_id}",
                "target": d_node_id,
                "kind": "produced",
                "directed": True,
            })

    # 4. Leaves (from leaf events)
    all_events = store.get_all()
    leaves_by_id: Dict[str, Dict[str, Any]] = {}
    for ev in all_events:
        if ev.name in ("civ.leaf.created", "civ.leaf.spawned"):
            p = ev.payload or {}
            # Two producers use two shapes: delegation.py emits a FLAT payload
            # (leaf_id/bot_id/...), shadow_leaf.py emits a NESTED {"leaf": {...}}
            # with parent_bot_id. Normalize both here.
            if "leaf" in p and isinstance(p["leaf"], dict):
                p = p["leaf"]
            lid = p.get("leaf_id")
            if lid:
                leaves_by_id[lid] = {
                    "leaf_id": lid,
                    "parent_bot_id": p.get("parent_bot_id") or p.get("bot_id"),
                    "task_description": p.get("task_description", p.get("goal", "")),
                    "status": "active",
                    "council_id": p.get("council_id"),
                }
        elif ev.name in ("civ.leaf.completed", "civ.leaf.failed", "civ.leaf.terminated"):
            lid = (ev.payload or {}).get("leaf_id")
            if lid and lid in leaves_by_id:
                leaves_by_id[lid]["status"] = ev.name.split(".")[-1]

    for leaf in leaves_by_id.values():
        leaf_node_id = f"leaf:{leaf['leaf_id']}"
        nodes.append({
            "id": leaf_node_id,
            "kind": "leaf",
            "label": leaf["leaf_id"],
            "status": leaf.get("status", "active"),
            "leaf_id": leaf["leaf_id"],
            "parent_bot_id": leaf.get("parent_bot_id"),
        })
        if leaf.get("parent_bot_id"):
            edges.append({
                "id": f"edge:bot-leaf:{leaf['parent_bot_id']}->{leaf['leaf_id']}",
                "source": f"bot:{leaf['parent_bot_id']}",
                "target": leaf_node_id,
                "kind": "parent_of",
                "directed": True,
            })
        if leaf.get("council_id"):
            edges.append({
                "id": f"edge:leaf-council:{leaf['leaf_id']}->{leaf['council_id']}",
                "source": leaf_node_id,
                "target": f"council:{leaf['council_id']}",
                "kind": "participated_in",
                "directed": True,
            })

    # 5. Proposals
    for p in proposals:
        pid = p.id
        p_node_id = f"proposal:{pid}"
        nodes.append({
            "id": p_node_id,
            "kind": "proposal",
            "label": f"Proposal: {p.bot_id} ({p.risk_class})",
            "status": p.status,
            "proposal_id": pid,
            "bot_id": p.bot_id,
            "risk_class": p.risk_class,
        })
        edges.append({
            "id": f"edge:bot-proposal:{p.bot_id}->{pid}",
            "source": f"bot:{p.bot_id}",
            "target": p_node_id,
            "kind": "evolution_proposal",
            "directed": True,
        })

    # 6. Constitution
    if constitution:
        const_node_id = f"constitution:v{constitution.version}"
        nodes.append({
            "id": const_node_id,
            "kind": "constitution",
            "label": f"Constitution v{constitution.version}: {constitution.title}",
            "status": "enacted",
            "version": constitution.version,
        })
        for c in councils:
            edges.append({
                "id": f"edge:constitution-council:{constitution.version}->{c.id}",
                "source": const_node_id,
                "target": f"council:{c.id}",
                "kind": "governs",
                "directed": True,
            })

    # 7. Inter-Agent Social Relationships
    for b in bots:
        bid = b.id
        for rel in society.get_relationships(bid):
            edge_id = f"edge:rel:{rel.from_bot}->{rel.to_bot}:{rel.relation_type}"
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edges.append({
                    "id": edge_id,
                    "source": f"bot:{rel.from_bot}",
                    "target": f"bot:{rel.to_bot}",
                    "kind": rel.relation_type,
                    "weight": rel.weight,
                    "directed": True,
                })

    cursor = getattr(all_events[-1], "seq", len(all_events)) if all_events else 0

    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges,
        "cursor": cursor,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "bots": len(bots),
            "councils": len(councils),
            "decisions": len(decisions),
            "leaves": len(leaves_by_id),
            "proposals": len(proposals),
            "relationships": len(seen_edges),
        },
    }
