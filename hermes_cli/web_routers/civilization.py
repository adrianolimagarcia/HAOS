"""Authenticated Civilization overview, graph projection, and memory/evolution endpoints."""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from hermes_cli.web_deps import late
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.memory_projection import CouncilMemoryProjectionEngine
from hermes.platform.evolution.bot_curator import EvolutionCurator
from hermes.platform.evolution.bot_evolution import BotEvolutionManager
from hermes.platform.observability.event_store import EventStore, default_event_store_path
from hermes.platform.observability.events import Event
from hermes.platform.society.manager import SocietyManager

logger = logging.getLogger("hermes_cli.web_routers.civilization")

router = APIRouter()
_require_token = late("_require_token")
_config_profile_scope = late("_config_profile_scope", "hermes_cli.web_server_profiles")


def _event_links(event):
    """Only known structured identifiers cross the HTTP boundary, never raw event payloads."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    source = {
        "civ.council.created": "spec",
        "civ.council.session-started": "session",
        "civ.council.decision-recorded": "decision",
        "civ.leaf.created": "leaf",
        "civ.evolution.proposal_created": "proposal",
        "civ.evolution.proposal_updated": "proposal",
    }.get(event.name)
    nested = payload.get(source) if source else None
    nested = nested if isinstance(nested, dict) else {}
    result = {"seq": getattr(event, "seq", 0), "name": event.name, "timestamp": event.timestamp}
    for key, candidates in {
        "bot_id": (payload.get("bot_id"), nested.get("parent_bot_id"), nested.get("bot_id")),
        "council_id": (payload.get("council_id"), nested.get("council_id"), nested.get("id") if source == "spec" else None),
        "leaf_id": (payload.get("leaf_id"), nested.get("leaf_id")),
        "decision_id": (payload.get("decision_id"), nested.get("id") if source == "decision" else None),
    }.items():
        value = next((v for v in candidates if isinstance(v, str) and v), None)
        if value is not None:
            result[key] = value
    return result


from hermes.platform.civilization.graph import build_civ_graph


def _overview(store) -> dict:
    bot_manager = BotSpecManager(store)
    identities = IdentityManager(store)
    council_manager = CouncilManager(store)
    society = SocietyManager(store)
    evolution = BotEvolutionManager(store)
    civilization = CivilizationManager(store)
    bots = []
    for spec in bot_manager.list():
        active = identities.get_active_version(spec.id)
        bots.append({
            "bot_id": spec.id,
            "name": spec.name,
            "status": "paused" if bot_manager.is_paused(spec.id) else "active",
            "version": active.version if active else None,
            "bundle_hash": active.bundle_hash if active else None,
        })
    seen = {bot["bot_id"] for bot in bots}
    identity_ids = {
        e.payload.get("bot_id")
        for e in store.get_all()
        if e.name == "civ.bot.identity-version-created"
        and isinstance(e.payload, dict)
        and isinstance(e.payload.get("bot_id"), str)
    }
    for bot_id in sorted(identity_ids - seen):
        active = identities.get_active_version(bot_id)
        if active is not None:
            bots.append({
                "bot_id": bot_id,
                "name": bot_id,
                "status": "unknown",
                "version": active.version,
                "bundle_hash": active.bundle_hash,
            })
    councils = [
        {
            "council_id": item.id,
            "purpose": item.purpose,
            "members": item.members,
            "version": item.version,
        }
        for item in council_manager.list()
    ]
    decisions = [
        {
            "decision_id": item.id,
            "council_id": item.council_id,
            "council_session_id": item.council_session_id,
            "created_at": item.created_at,
        }
        for item in council_manager.list_decisions()
    ]
    leaves = {}
    all_events = store.get_all()
    for event in all_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.name == "civ.leaf.created":
            leaf = payload.get("leaf")
            if not isinstance(leaf, dict) or not isinstance(leaf.get("leaf_id"), str):
                continue
            leaves[leaf["leaf_id"]] = {
                "leaf_id": leaf["leaf_id"],
                "parent_bot_id": leaf.get("parent_bot_id"),
                "council_id": (leaf.get("identity_snapshot") or {}).get("council_id")
                if isinstance(leaf.get("identity_snapshot"), dict)
                else None,
                "council_session_id": (leaf.get("identity_snapshot") or {}).get("council_session_id")
                if isinstance(leaf.get("identity_snapshot"), dict)
                else None,
                "status": "active",
            }
        elif event.name in ("civ.leaf.completed", "civ.leaf.failed"):
            leaf = leaves.get(payload.get("leaf_id"))
            if leaf is not None:
                leaf["status"] = "completed" if event.name.endswith("completed") else "failed"

    proposals = [
        {
            "proposal_id": item.id,
            "bot_id": item.bot_id,
            "status": item.status,
            "risk_class": item.risk_class,
            "rationale": item.rationale,
        }
        for item in evolution.get_proposals()
    ]
    constitution = civilization.get_active_constitution()
    reps = society.get_all_reputations()
    return {
        "bots": sorted(bots, key=lambda x: x["bot_id"]),
        "councils": sorted(councils, key=lambda x: x["council_id"]),
        "leaves": list(leaves.values()),
        "decisions": decisions,
        "proposals": proposals,
        "constitution": {"version": constitution.version, "title": constitution.title} if constitution else None,
        "reputation": [
            {
                "bot_id": bot_id,
                "score": vector.overall_score,
                "evidence_count": vector.evidence_count,
            }
            for bot_id, vector in sorted(reps.items())
        ],
        "events": [_event_links(e) for e in all_events if e.name.startswith("civ.")][-50:],
        "cursor": getattr(all_events[-1], "seq", len(all_events)) if all_events else 0,
    }


def _build_graph(store) -> dict:
    """Project complete graph DAG (nodes and edges) from canonical event store."""
    return build_civ_graph(store)


def _get_read_only_store():
    path = default_event_store_path()
    if not Path(path).is_file():
        return None
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT seq, name, timestamp, payload FROM events ORDER BY seq"
        ).fetchall()
    finally:
        connection.close()

    class ReadOnlyEvents:
        def get_all(self):
            return events

    events = []
    for seq, name, timestamp, payload in rows:
        event = Event(name=name, timestamp=timestamp, payload=json.loads(payload))
        event.seq = seq
        events.append(event)
    return ReadOnlyEvents()


@router.get("/api/civilization/overview")
def civilization_overview(request: Request, profile: Optional[str] = None):
    """Return an allowlisted projection summary."""
    _require_token(request)
    with _config_profile_scope(profile):
        store = _get_read_only_store()
        if not store:
            return {
                "bots": [],
                "councils": [],
                "leaves": [],
                "decisions": [],
                "proposals": [],
                "constitution": None,
                "reputation": [],
                "events": [],
                "cursor": 0,
            }
        return _overview(store)


@router.get("/api/civilization/graph")
def civilization_graph(request: Request, profile: Optional[str] = None):
    """Return full graph DAG linking bots, councils, leaves, decisions, and evolution."""
    _require_token(request)
    with _config_profile_scope(profile):
        store = _get_read_only_store()
        if not store:
            return {"schema_version": 1, "nodes": [], "edges": [], "cursor": 0, "counts": {}}
        return _build_graph(store)


@router.get("/api/civilization/councils/{council_id}/memory")
def get_council_memory(request: Request, council_id: str, profile: Optional[str] = None):
    """Get projected Council MEMORY.md content and projection metadata."""
    _require_token(request)
    with _config_profile_scope(profile):
        path = default_event_store_path()
        if not Path(path).is_file():
            raise HTTPException(status_code=404, detail="No event store found for profile")
        store = EventStore(path)
        engine = CouncilMemoryProjectionEngine(store, profile_id=profile or "default")
        rec = engine.project_council(council_id, force_full_rebuild=False)
        content = ""
        if Path(rec.file_path).exists():
            content = Path(rec.file_path).read_text(encoding="utf-8")
        return {
            "record": rec.to_dict(),
            "content": content,
        }


@router.post("/api/civilization/councils/{council_id}/memory/rebuild")
def rebuild_council_memory(request: Request, council_id: str, profile: Optional[str] = None):
    """Force rebuild of a council's MEMORY.md projection from canonical events."""
    _require_token(request)
    with _config_profile_scope(profile):
        path = default_event_store_path()
        if not Path(path).is_file():
            raise HTTPException(status_code=404, detail="No event store found for profile")
        store = EventStore(path)
        engine = CouncilMemoryProjectionEngine(store, profile_id=profile or "default")
        rec = engine.project_council(council_id, force_full_rebuild=True)
        return {
            "status": "rebuilt",
            "record": rec.to_dict(),
        }


@router.get("/api/civilization/evolution/curator/status")
def get_curator_status(request: Request, profile: Optional[str] = None):
    """Return status and lease information of the background evolution curator."""
    _require_token(request)
    with _config_profile_scope(profile):
        path = default_event_store_path()
        if not Path(path).is_file():
            return {"status": "inactive", "reason": "no_event_store"}
        store = EventStore(path)
        id_mgr = IdentityManager(store)
        evo_mgr = BotEvolutionManager(store)
        curator = EvolutionCurator(store, id_mgr, evo_mgr, profile_id=profile or "default")
        return curator.get_status()


class CuratorRunRequest(BaseModel):
    dry_run: bool = False


@router.post("/api/civilization/evolution/curator/run")
def run_curator_cycle(request: Request, body: CuratorRunRequest, profile: Optional[str] = None):
    """Trigger a single cycle of the background evolution curator."""
    _require_token(request)
    with _config_profile_scope(profile):
        path = default_event_store_path()
        if not Path(path).is_file():
            raise HTTPException(status_code=404, detail="No event store found for profile")
        store = EventStore(path)
        id_mgr = IdentityManager(store)
        evo_mgr = BotEvolutionManager(store)
        curator = EvolutionCurator(store, id_mgr, evo_mgr, profile_id=profile or "default")
        return curator.run_cycle(dry_run=body.dry_run)
