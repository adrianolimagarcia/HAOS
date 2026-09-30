"""Authenticated Civilization overview, graph projection, and memory/evolution endpoints."""

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from hermes_cli.web_deps import late
from hermes_constants import get_hermes_home
from hermes.platform.kernel.simulation import SimulationEngine
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
_load_config = late("load_config", "hermes_cli.config")


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


# ==============================================================================
# Phase 1 & 2: Civilization Control Plane, Agent Studio & Mission Orchestrator
# ==============================================================================

AVAILABLE_MODELS = [
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "context_length": 131072,
        "cost_per_1m_input_usd": 0.14,
        "cost_per_1m_output_usd": 0.28,
        "recommended": True,
    },
    {
        "id": "deepseek-chat",
        "name": "DeepSeek Chat (V3)",
        "provider": "deepseek",
        "context_length": 65536,
        "cost_per_1m_input_usd": 0.14,
        "cost_per_1m_output_usd": 0.28,
        "recommended": False,
    },
    {
        "id": "claude-3-7-sonnet",
        "name": "Claude 3.7 Sonnet (Hybrid)",
        "provider": "anthropic",
        "context_length": 200000,
        "cost_per_1m_input_usd": 3.00,
        "cost_per_1m_output_usd": 15.00,
        "recommended": True,
    },
    {
        "id": "claude-3-5-haiku",
        "name": "Claude 3.5 Haiku",
        "provider": "anthropic",
        "context_length": 200000,
        "cost_per_1m_input_usd": 0.80,
        "cost_per_1m_output_usd": 4.00,
        "recommended": False,
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o Omnimodal",
        "provider": "openai",
        "context_length": 128000,
        "cost_per_1m_input_usd": 2.50,
        "cost_per_1m_output_usd": 10.00,
        "recommended": False,
    },
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "provider": "openai",
        "context_length": 128000,
        "cost_per_1m_input_usd": 0.15,
        "cost_per_1m_output_usd": 0.60,
        "recommended": False,
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "google",
        "context_length": 1000000,
        "cost_per_1m_input_usd": 1.25,
        "cost_per_1m_output_usd": 5.00,
        "recommended": False,
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "google",
        "context_length": 1000000,
        "cost_per_1m_input_usd": 0.075,
        "cost_per_1m_output_usd": 0.30,
        "recommended": False,
    },
]

DEFAULT_CIV_AGENTS = [
    {
        "id": "architect-001",
        "name": "Software Architect",
        "role": "planner",
        "domain": "Software Architecture",
        "description": "System design, architectural constraints and decomposition.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.2, "max_tokens": 4096},
        "capabilities": {"allowed_tools": ["read_file", "terminal", "git"], "max_risk_tier": "MEDIUM", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": True, "global_civ": False},
        "budget": {"max_tokens": 200000, "timeout_seconds": 1800, "max_cost_usd": 5.0, "max_iterations": 25},
        "policies": {"require_human_approval": ["merge", "deploy"], "risk_tolerance": "CONSERVATIVE"},
    },
    {
        "id": "builder-002",
        "name": "Code Builder",
        "role": "builder",
        "domain": "Implementation",
        "description": "Implementation, refactoring, and code generation.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.3, "max_tokens": 8192},
        "capabilities": {"allowed_tools": ["read_file", "write_file", "edit_file", "terminal", "git"], "max_risk_tier": "HIGH", "allowed_write_paths": ["."]},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": False},
        "budget": {"max_tokens": 300000, "timeout_seconds": 2400, "max_cost_usd": 8.0, "max_iterations": 40},
        "policies": {"require_human_approval": ["deploy"], "risk_tolerance": "BALANCED"},
    },
    {
        "id": "critic-003",
        "name": "Adversarial Critic",
        "role": "critic",
        "domain": "Verification & Code Review",
        "description": "Adversarial reviews, edge cases, and design flaw detection.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.1, "max_tokens": 4096},
        "capabilities": {"allowed_tools": ["read_file", "git"], "max_risk_tier": "READ", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": False},
        "budget": {"max_tokens": 150000, "timeout_seconds": 1200, "max_cost_usd": 3.0, "max_iterations": 15},
        "policies": {"require_human_approval": [], "risk_tolerance": "CONSERVATIVE"},
    },
    {
        "id": "validator-004",
        "name": "Quality Validator",
        "role": "validator",
        "domain": "Testing & QA",
        "description": "Deterministic test suite execution, regression analysis.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.0, "max_tokens": 4096},
        "capabilities": {"allowed_tools": ["read_file", "terminal", "git"], "max_risk_tier": "LOW", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": False},
        "budget": {"max_tokens": 150000, "timeout_seconds": 1200, "max_cost_usd": 3.0, "max_iterations": 20},
        "policies": {"require_human_approval": ["merge"], "risk_tolerance": "CONSERVATIVE"},
    },
    {
        "id": "promoter-005",
        "name": "Civilization Promoter",
        "role": "promoter",
        "domain": "Civilization Memory & Skills",
        "description": "Curates and promotes verified knowledge into civilization global memory.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.1, "max_tokens": 4096},
        "capabilities": {"allowed_tools": ["read_file", "write_file", "git"], "max_risk_tier": "HIGH", "allowed_write_paths": [".hermes/obsidian_vault"]},
        "memory": {"working": True, "session": True, "project": True, "domain": True, "global_civ": True},
        "budget": {"max_tokens": 200000, "timeout_seconds": 1800, "max_cost_usd": 5.0, "max_iterations": 25},
        "policies": {"require_human_approval": ["memory_promotion"], "risk_tolerance": "CONSERVATIVE"},
    },
    {
        "id": "security-006",
        "name": "Security Auditor",
        "role": "security",
        "domain": "Security & Vulnerability Analysis",
        "description": "Vulnerability scanning, secret leakage prevention, path traversal checks.",
        "status": "active",
        "model": {"inherit": True, "provider": None, "model_name": None, "temperature": 0.1, "max_tokens": 4096},
        "capabilities": {"allowed_tools": ["read_file", "git"], "max_risk_tier": "READ", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": False},
        "budget": {"max_tokens": 150000, "timeout_seconds": 1200, "max_cost_usd": 3.0, "max_iterations": 20},
        "policies": {"require_human_approval": ["deploy"], "risk_tolerance": "CONSERVATIVE"},
    },
]

DEFAULT_CIV_MISSIONS = [
    {
        "id": "mis-001",
        "title": "OAuth2 Authentication Hardening",
        "goal": "Implement asymmetric JWT signing and strict rate-limiting for auth endpoints.",
        "status": "READY",
        "agents": ["architect-001", "builder-002", "critic-003", "validator-004"],
        "workflow": {
            "nodes": [
                {"id": "step-1", "agent_id": "architect-001", "role": "planner", "action": "Design auth token flow", "status": "COMPLETED"},
                {"id": "step-2", "agent_id": "builder-002", "role": "builder", "action": "Implement asymmetric JWT key pair", "status": "READY"},
                {"id": "step-3", "agent_id": "critic-003", "role": "critic", "action": "Audit token expiry and entropy", "status": "PENDING"},
                {"id": "step-4", "agent_id": "validator-004", "role": "validator", "action": "Run auth regression tests", "status": "PENDING"},
            ],
            "edges": [
                {"from_node": "step-1", "to_node": "step-2"},
                {"from_node": "step-2", "to_node": "step-3"},
                {"from_node": "step-3", "to_node": "step-4"},
            ],
        },
        "budget": {
            "max_cost_usd": 10.0,
            "max_tokens": 250000,
            "timeout_seconds": 2400,
        },
        "policies": {
            "require_human_gate_on_merge": True,
            "allow_learning": True,
        },
        "simulation": None,
        "events": [
            {"time": 1774000000.0, "type": "civ.mission.created", "agent_id": "architect-001", "description": "Mission created and initialized in READY state.", "status": "completed"},
            {"time": 1774000500.0, "type": "civ.mission.agent_state_changed", "agent_id": "architect-001", "description": "Architect finished token flow plan.", "status": "completed"},
        ],
        "progress_pct": 25.0,
        "created_at": 1774000000.0,
        "updated_at": 1774000500.0,
    }
]


class ModelConfigDTO(BaseModel):
    inherit: bool = True
    provider: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 4096


class CapabilitiesDTO(BaseModel):
    allowed_tools: List[str] = ["read_file", "terminal", "git"]
    max_risk_tier: str = "MEDIUM"
    allowed_write_paths: List[str] = []


class MemoryScopeDTO(BaseModel):
    working: bool = True
    session: bool = True
    project: bool = True
    domain: bool = False
    global_civ: bool = False


class BudgetDTO(BaseModel):
    max_tokens: int = 150000
    timeout_seconds: int = 1800
    max_cost_usd: float = 5.0
    max_iterations: int = 25


class PoliciesDTO(BaseModel):
    require_human_approval: List[str] = ["merge", "deploy"]
    risk_tolerance: str = "CONSERVATIVE"


class AgentConfigDTO(BaseModel):
    id: str
    name: str
    role: str = "builder"
    domain: str = "Software Engineering"
    description: str = ""
    status: str = "active"
    model: ModelConfigDTO = ModelConfigDTO()
    capabilities: CapabilitiesDTO = CapabilitiesDTO()
    memory: MemoryScopeDTO = MemoryScopeDTO()
    budget: BudgetDTO = BudgetDTO()
    policies: PoliciesDTO = PoliciesDTO()


class MissionWorkflowNodeDTO(BaseModel):
    id: str
    agent_id: str
    role: str
    action: str
    status: str = "PENDING"


class MissionWorkflowEdgeDTO(BaseModel):
    from_node: str
    to_node: str


class MissionWorkflowDTO(BaseModel):
    nodes: List[MissionWorkflowNodeDTO] = []
    edges: List[MissionWorkflowEdgeDTO] = []


class MissionCreateDTO(BaseModel):
    id: Optional[str] = None
    title: str
    goal: str
    agents: List[str] = []
    workflow: Optional[MissionWorkflowDTO] = None
    budget: Optional[Dict[str, Any]] = None
    policies: Optional[Dict[str, Any]] = None


class ApprovalDecisionDTO(BaseModel):
    approved: bool
    reason: str = ""


def _get_session_model() -> str:
    try:
        cfg = _load_config()
        model_val = cfg.get("model", "")
        if isinstance(model_val, dict):
            return str(model_val.get("default") or "deepseek-v4-flash")
        if isinstance(model_val, str) and model_val.strip():
            return model_val.strip()
    except Exception:
        pass
    return "deepseek-v4-flash"


def _resolve_agent_model(
    agent_model: dict,
    mission_override: Optional[str] = None,
    session_model: Optional[str] = None,
    global_default: str = "deepseek-v4-flash",
) -> tuple[str, str]:
    inherit = agent_model.get("inherit", True) if isinstance(agent_model, dict) else True
    custom_model = agent_model.get("model_name") if isinstance(agent_model, dict) else None
    if not inherit and custom_model:
        return custom_model, "AGENT_OVERRIDE"
    if mission_override:
        return mission_override, "MISSION_OVERRIDE"
    if session_model and isinstance(session_model, str) and session_model.strip():
        return session_model.strip(), "SESSION_MODEL"
    return global_default, "GLOBAL_DEFAULT"


def _civ_agents_path() -> Path:
    return Path(get_hermes_home()) / "civ_agents.json"


def _civ_missions_path() -> Path:
    return Path(get_hermes_home()) / "civ_missions.json"


def _load_agents() -> List[dict]:
    p = _civ_agents_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return [dict(a) for a in DEFAULT_CIV_AGENTS]


def _save_agents(agents: List[dict]) -> None:
    p = _civ_agents_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(agents, indent=2), encoding="utf-8")


def _load_missions() -> List[dict]:
    p = _civ_missions_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return [dict(m) for m in DEFAULT_CIV_MISSIONS]


def _save_missions(missions: List[dict]) -> None:
    p = _civ_missions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(missions, indent=2), encoding="utf-8")


def _maybe_append_event(name: str, payload: dict) -> None:
    store_path = default_event_store_path()
    if store_path.is_file():
        try:
            with EventStore(str(store_path)) as store:
                store.append(Event(name=name, payload=payload))
        except Exception as e:
            logger.warning("Could not append event to event store: %s", e)


@router.get("/api/civilization/models/available")
def get_available_models(request: Request, profile: Optional[str] = None):
    """List available LLM models with pricing and context specifications."""
    _require_token(request)
    with _config_profile_scope(profile):
        session_model = _get_session_model()
        return {
            "models": AVAILABLE_MODELS,
            "session_model": session_model,
            "global_default": "deepseek-v4-flash",
        }


@router.get("/api/civilization/agents")
def get_civilization_agents(request: Request, profile: Optional[str] = None):
    """List configured agents with effective model cascade resolutions."""
    _require_token(request)
    with _config_profile_scope(profile):
        agents = _load_agents()
        session_model = _get_session_model()
        results = []
        for a in agents:
            eff_model, source = _resolve_agent_model(a.get("model", {}), session_model=session_model)
            enriched = dict(a)
            enriched["effective_model"] = eff_model
            enriched["model_source"] = source
            results.append(enriched)
        return {"agents": results, "session_model": session_model}


@router.get("/api/civilization/agents/{agent_id}")
def get_civilization_agent_detail(request: Request, agent_id: str, profile: Optional[str] = None):
    """Retrieve detailed agent configuration with resolution breakdown."""
    _require_token(request)
    with _config_profile_scope(profile):
        agents = _load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        session_model = _get_session_model()
        eff_model, source = _resolve_agent_model(agent.get("model", {}), session_model=session_model)
        res = dict(agent)
        res["effective_model"] = eff_model
        res["model_source"] = source
        return res


@router.post("/api/civilization/agents")
def create_civilization_agent(request: Request, body: AgentConfigDTO, profile: Optional[str] = None):
    """Create a new agent enforcing constitutional invariants."""
    _require_token(request)
    with _config_profile_scope(profile):
        agent_dict = body.dict()
        if not agent_dict["id"].strip():
            raise HTTPException(status_code=400, detail="agent id cannot be empty")
        # Enforce Constitutional Invariant: Only PROMOTER may write to GLOBAL memory scope
        if agent_dict.get("memory", {}).get("global_civ"):
            role = str(agent_dict.get("role", "")).lower()
            if role != "promoter":
                raise HTTPException(
                    status_code=400,
                    detail=f"Role '{agent_dict.get('role')}' is strictly forbidden from writing to GLOBAL memory scope. Only PROMOTER agents may write to civilization-level memory.",
                )
        agents = _load_agents()
        if any(a["id"] == agent_dict["id"] for a in agents):
            raise HTTPException(status_code=409, detail=f"Agent with id '{agent_dict['id']}' already exists")
        agents.append(agent_dict)
        _save_agents(agents)
        _maybe_append_event("civ.bot.spec-created", {"agent_id": agent_dict["id"], "name": agent_dict["name"]})
        session_model = _get_session_model()
        eff_model, source = _resolve_agent_model(agent_dict.get("model", {}), session_model=session_model)
        agent_dict["effective_model"] = eff_model
        agent_dict["model_source"] = source
        return agent_dict


@router.put("/api/civilization/agents/{agent_id}")
def update_civilization_agent(request: Request, agent_id: str, body: AgentConfigDTO, profile: Optional[str] = None):
    """Update an existing agent configuration enforcing constitutional invariants."""
    _require_token(request)
    with _config_profile_scope(profile):
        agent_dict = body.dict()
        if agent_dict.get("memory", {}).get("global_civ"):
            role = str(agent_dict.get("role", "")).lower()
            if role != "promoter":
                raise HTTPException(
                    status_code=400,
                    detail=f"Role '{agent_dict.get('role')}' is strictly forbidden from writing to GLOBAL memory scope. Only PROMOTER agents may write to civilization-level memory.",
                )
        agents = _load_agents()
        idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
        if idx is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        agents[idx] = agent_dict
        _save_agents(agents)
        _maybe_append_event("civ.bot.spec-updated", {"agent_id": agent_id, "name": agent_dict["name"]})
        session_model = _get_session_model()
        eff_model, source = _resolve_agent_model(agent_dict.get("model", {}), session_model=session_model)
        agent_dict["effective_model"] = eff_model
        agent_dict["model_source"] = source
        return agent_dict


@router.post("/api/civilization/agents/{agent_id}/duplicate")
def duplicate_civilization_agent(request: Request, agent_id: str, profile: Optional[str] = None):
    """Clone an agent configuration under a new unique ID."""
    _require_token(request)
    with _config_profile_scope(profile):
        agents = _load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        new_id = f"{agent_id}-copy"
        counter = 1
        while any(a["id"] == new_id for a in agents):
            counter += 1
            new_id = f"{agent_id}-copy-{counter}"
        cloned = dict(agent)
        cloned["id"] = new_id
        cloned["name"] = f"{agent.get('name', agent_id)} (Copy)"
        agents.append(cloned)
        _save_agents(agents)
        _maybe_append_event("civ.bot.spec-duplicated", {"source_id": agent_id, "new_id": new_id})
        session_model = _get_session_model()
        eff_model, source = _resolve_agent_model(cloned.get("model", {}), session_model=session_model)
        cloned["effective_model"] = eff_model
        cloned["model_source"] = source
        return cloned


@router.post("/api/civilization/agents/{agent_id}/export")
def export_civilization_agent(request: Request, agent_id: str, profile: Optional[str] = None):
    """Export agent configuration as portable JSON."""
    _require_token(request)
    with _config_profile_scope(profile):
        agents = _load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent


@router.post("/api/civilization/agents/import")
def import_civilization_agent(request: Request, body: AgentConfigDTO, profile: Optional[str] = None):
    """Import and validate an agent JSON specification."""
    _require_token(request)
    with _config_profile_scope(profile):
        agent_dict = body.dict()
        if not agent_dict["id"].strip():
            raise HTTPException(status_code=400, detail="agent id cannot be empty")
        if agent_dict.get("memory", {}).get("global_civ"):
            role = str(agent_dict.get("role", "")).lower()
            if role != "promoter":
                raise HTTPException(
                    status_code=400,
                    detail=f"Role '{agent_dict.get('role')}' is strictly forbidden from writing to GLOBAL memory scope. Only PROMOTER agents may write to civilization-level memory.",
                )
        agents = _load_agents()
        idx = next((i for i, a in enumerate(agents) if a["id"] == agent_dict["id"]), None)
        if idx is not None:
            agents[idx] = agent_dict
        else:
            agents.append(agent_dict)
        _save_agents(agents)
        _maybe_append_event("civ.bot.spec-imported", {"agent_id": agent_dict["id"]})
        session_model = _get_session_model()
        eff_model, source = _resolve_agent_model(agent_dict.get("model", {}), session_model=session_model)
        agent_dict["effective_model"] = eff_model
        agent_dict["model_source"] = source
        return agent_dict


@router.get("/api/civilization/missions")
def get_civilization_missions(request: Request, profile: Optional[str] = None):
    """List coordinated multi-agent missions."""
    _require_token(request)
    with _config_profile_scope(profile):
        return {"missions": _load_missions()}


@router.post("/api/civilization/missions")
def create_civilization_mission(request: Request, body: MissionCreateDTO, profile: Optional[str] = None):
    """Create a new mission specification."""
    _require_token(request)
    with _config_profile_scope(profile):
        mission_id = body.id or f"mis-{uuid.uuid4().hex[:8]}"
        now = time.time()
        workflow = body.workflow.dict() if body.workflow else {
            "nodes": [
                {"id": f"node-{i+1}", "agent_id": aid, "role": "agent", "action": f"Step {i+1}", "status": "PENDING"}
                for i, aid in enumerate(body.agents)
            ],
            "edges": [
                {"from_node": f"node-{i}", "to_node": f"node-{i+1}"}
                for i in range(1, len(body.agents))
            ],
        }
        mission = {
            "id": mission_id,
            "title": body.title,
            "goal": body.goal,
            "status": "DRAFT",
            "agents": list(body.agents),
            "workflow": workflow,
            "budget": body.budget or {"max_cost_usd": 10.0, "max_tokens": 250000, "timeout_seconds": 3600},
            "policies": body.policies or {"require_human_gate_on_merge": True, "allow_learning": True},
            "simulation": None,
            "events": [
                {"time": now, "type": "civ.mission.created", "agent_id": "operator", "description": f"Mission '{body.title}' created in DRAFT mode.", "status": "completed"}
            ],
            "progress_pct": 0.0,
            "created_at": now,
            "updated_at": now,
        }
        missions = _load_missions()
        missions.append(mission)
        _save_missions(missions)
        _maybe_append_event("civ.mission.created", {"mission_id": mission_id, "title": body.title})
        return mission


@router.get("/api/civilization/missions/{mission_id}")
def get_civilization_mission_detail(request: Request, mission_id: str, profile: Optional[str] = None):
    """Get mission details including workflow DAG and execution state."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        return mission


@router.post("/api/civilization/missions/{mission_id}/simulate")
def simulate_civilization_mission(request: Request, mission_id: str, profile: Optional[str] = None):
    """Perform dry-run simulation of mission without environment mutations."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        sim_engine = SimulationEngine()
        goal = mission.get("goal") or mission.get("title") or "Mission Execution"
        report = sim_engine.simulate_task(task_prompt=goal, complexity_hint="medium")
        rep_dict = report.to_dict()
        mission["simulation"] = rep_dict
        mission["status"] = "READY"
        _save_missions(missions)
        _maybe_append_event("civ.mission.simulated", {"mission_id": mission_id, "simulation_id": rep_dict.get("simulation_id")})
        return rep_dict


@router.post("/api/civilization/missions/{mission_id}/start")
def start_civilization_mission(request: Request, mission_id: str, profile: Optional[str] = None):
    """Transition mission to RUNNING state."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        now = time.time()
        mission["status"] = "RUNNING"
        mission["progress_pct"] = max(mission.get("progress_pct", 0.0), 10.0)
        mission["updated_at"] = now
        events = mission.setdefault("events", [])
        events.append({
            "time": now,
            "type": "civ.mission.started",
            "agent_id": mission["agents"][0] if mission.get("agents") else "system",
            "description": f"Mission '{mission['title']}' started execution.",
            "status": "in_progress",
        })
        _save_missions(missions)
        _maybe_append_event("civ.mission.started", {"mission_id": mission_id})
        return mission


@router.post("/api/civilization/missions/{mission_id}/pause")
def pause_civilization_mission(request: Request, mission_id: str, profile: Optional[str] = None):
    """Pause an active mission."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        now = time.time()
        mission["status"] = "PAUSED"
        mission["updated_at"] = now
        events = mission.setdefault("events", [])
        events.append({
            "time": now,
            "type": "civ.mission.paused",
            "agent_id": "operator",
            "description": f"Mission '{mission['title']}' paused by operator.",
            "status": "paused",
        })
        _save_missions(missions)
        _maybe_append_event("civ.mission.paused", {"mission_id": mission_id})
        return mission


@router.post("/api/civilization/missions/{mission_id}/resume")
def resume_civilization_mission(request: Request, mission_id: str, profile: Optional[str] = None):
    """Resume a paused mission."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        now = time.time()
        mission["status"] = "RUNNING"
        mission["updated_at"] = now
        events = mission.setdefault("events", [])
        events.append({
            "time": now,
            "type": "civ.mission.resumed",
            "agent_id": "operator",
            "description": f"Mission '{mission['title']}' resumed by operator.",
            "status": "in_progress",
        })
        _save_missions(missions)
        _maybe_append_event("civ.mission.resumed", {"mission_id": mission_id})
        return mission


@router.get("/api/civilization/missions/{mission_id}/events")
def get_civilization_mission_events(request: Request, mission_id: str, profile: Optional[str] = None):
    """Get chronological event timeline for mission replay."""
    _require_token(request)
    with _config_profile_scope(profile):
        missions = _load_missions()
        mission = next((m for m in missions if m["id"] == mission_id), None)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        return {"events": mission.get("events", [])}


@router.post("/api/civilization/approvals/{approval_id}/decision")
def record_approval_decision(request: Request, approval_id: str, body: ApprovalDecisionDTO, profile: Optional[str] = None):
    """Record human operator approval or rejection decision."""
    _require_token(request)
    with _config_profile_scope(profile):
        _maybe_append_event("civ.council.approval_decided", {
            "approval_id": approval_id,
            "approved": body.approved,
            "reason": body.reason,
            "timestamp": time.time(),
        })
        return {
            "status": "recorded",
            "approval_id": approval_id,
            "approved": body.approved,
            "reason": body.reason,
        }
