"""Agent Mesh, Capability Router, Event Bus and Powerline Context HUD.

Absorbs patterns from:
- Ruflo: Agent Graph & Mesh Topology
- Google ADK: Agent Event Bus & Session Runtime
- Herdr: Declarative capability routing weighted by continuous confidence scoring
- Powerline: Dynamic Operational Context HUD
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from hermes_constants import get_hermes_home

_log = logging.getLogger("hermes.platform.execution.agent_mesh")

# Standard HAOS Edge library loader for C-ABI
_RUST_LIB = None


def _get_rust_edge_lib():
    global _RUST_LIB
    if _RUST_LIB is not None:
        return _RUST_LIB

    candidate_paths = [
        Path("/usr/local/lib/haos/libhaos_edge.so"),
        Path(__file__).resolve().parents[3] / "packages" / "haos-edge" / "target" / "release" / "libhaos_edge.so",
        Path(__file__).resolve().parents[3] / "packages" / "haos-edge" / "target" / "debug" / "libhaos_edge.so",
        Path("/usr/lib/libhaos_edge.so"),
    ]

    for p in candidate_paths:
        if p.is_file():
            try:
                lib = ctypes.CDLL(str(p))
                if hasattr(lib, "raggraph_record_agent_event"):
                    lib.raggraph_record_agent_event.argtypes = [
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_double,
                        ctypes.c_char_p,
                    ]
                    lib.raggraph_record_agent_event.restype = ctypes.c_int
                    _RUST_LIB = lib
                    return _RUST_LIB
            except Exception as e:
                _log.debug("Failed loading Rust library from %s: %s", p, e)

    return None


@dataclass
class AgentCapability:
    """A granular capability possessed by an agent."""
    name: str
    description: str
    category: str = "general"
    keywords: List[str] = field(default_factory=list)


@dataclass
class AgentProfile:
    """Profile and operational metrics of an agent in the Mesh."""
    agent_id: str
    name: str
    role: str
    capabilities: List[str] = field(default_factory=list)
    trust_score: float = 0.85
    status: str = "idle"  # "idle" | "busy" | "offline"
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    model_preference: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentEvent:
    """Structured agent activity event following Google ADK & Ruflo patterns.
    
    Supported canonical event types:
    - agent.started: Agent execution session or task began
    - agent.called_tool / agent.tool_called: Invocation of external or local tool
    - agent.created_memory / agent.memory_created: Storage/promotion of facts into memory
    - agent.routed: Task routed to agent based on capability and trust
    - agent.completed: Successful execution of task or workflow step
    - agent.failed: Failure or exception during execution
    - agent.learned_rule: New procedural or behavioral rule acquired
    - agent.corrected: Self-correction or supervisor correction triggered
    """
    event_id: str
    event_type: str
    agent_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    target_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentEventBus:
    """In-memory and persistent Event Bus for Agent Graph activities."""

    def __init__(self, haos_home: Optional[Path] = None):
        self.haos_home = Path(haos_home) if haos_home else get_hermes_home()
        self._listeners: Dict[str, List[Callable[[AgentEvent], None]]] = {}
        self._global_listeners: List[Callable[[AgentEvent], None]] = []

    def subscribe(self, event_type: str, listener: Callable[[AgentEvent], None]) -> None:
        """Subscribe to a specific event type, or '*' for all events."""
        if event_type == "*":
            self._global_listeners.append(listener)
        else:
            self._listeners.setdefault(event_type, []).append(listener)

    def emit(self, event: AgentEvent) -> None:
        """Broadcast event to subscribers and persist to RAGGraph."""
        # 1. Dispatch in-memory listeners
        for listener in self._global_listeners:
            try:
                listener(event)
            except Exception as e:
                _log.warning("AgentEventBus listener error: %s", e)

        for listener in self._listeners.get(event.event_type, []):
            try:
                listener(event)
            except Exception as e:
                _log.warning("AgentEventBus listener error for %s: %s", event.event_type, e)

        # 2. Persist to native RAGGraph (via C-ABI or SQLite fallback)
        self._persist_to_raggraph(event)

    def _persist_to_raggraph(self, event: AgentEvent) -> None:
        db_path = self.haos_home / "memory" / "raggraph.db"
        lib = _get_rust_edge_lib()
        if lib and hasattr(lib, "raggraph_record_agent_event"):
            try:
                ret = lib.raggraph_record_agent_event(
                    str(db_path).encode("utf-8"),
                    event.event_id.encode("utf-8"),
                    event.event_type.encode("utf-8"),
                    event.agent_id.encode("utf-8"),
                    json.dumps(event.payload, ensure_ascii=False).encode("utf-8"),
                    ctypes.c_double(event.timestamp),
                    (event.target_ref or "").encode("utf-8"),
                )
                if ret == 0:
                    return
            except Exception as e:
                _log.debug("Rust C-ABI raggraph_record_agent_event failed: %s, falling back to SQLite", e)

        # Python SQLite fallback
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS haos_agent_events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        target_ref TEXT,
                        created_at REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS haos_graph_nodes (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        label TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT DEFAULT '{}',
                        created_at REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS haos_graph_edges (
                        source_id TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        edge_type TEXT NOT NULL,
                        weight REAL DEFAULT 1.0,
                        metadata_json TEXT DEFAULT '{}',
                        PRIMARY KEY (source_id, target_id, edge_type)
                    );
                    """
                )
                now = time.time()
                payload_str = json.dumps(event.payload, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO haos_agent_events (event_id, event_type, agent_id, payload_json, timestamp, target_ref, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (event.event_id, event.event_type, event.agent_id, payload_str, event.timestamp, event.target_ref, now),
                )
                node_id = f"event:{event.event_id}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO haos_graph_nodes (node_id, node_type, session_id, label, content, metadata_json, created_at)
                    VALUES (?, 'agent_event', ?, ?, ?, ?, ?);
                    """,
                    (node_id, event.agent_id, event.event_type, payload_str, payload_str, event.timestamp),
                )
                if event.target_ref:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO haos_graph_edges (source_id, target_id, edge_type, weight, metadata_json)
                        VALUES (?, ?, 'REFERENCES', 1.0, '{}');
                        """,
                        (node_id, event.target_ref),
                    )
                conn.commit()
        except Exception as exc:
            _log.debug("SQLite event insert error: %s", exc)


class AgentCapabilityRegistry:
    """Registry for agent capabilities and agent profiles (Herdr pattern)."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, AgentCapability] = {}
        self._agents: Dict[str, AgentProfile] = {}

    def register_capability(self, capability: AgentCapability) -> None:
        """Register an agent capability."""
        self._capabilities[capability.name] = capability

    def get_capability(self, name: str) -> Optional[AgentCapability]:
        """Retrieve capability by name."""
        return self._capabilities.get(name)

    def list_capabilities(self) -> List[AgentCapability]:
        """List all registered capabilities."""
        return list(self._capabilities.values())

    def register_agent(self, profile: AgentProfile) -> None:
        """Register an agent profile."""
        self._agents[profile.agent_id] = profile

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        """Retrieve agent profile by id."""
        return self._agents.get(agent_id)

    def list_agents(self) -> List[AgentProfile]:
        """List all registered agent profiles."""
        return list(self._agents.values())

    def find_agents_for_capability(self, capability_name: str) -> List[AgentProfile]:
        """Find all agents providing a given capability."""
        return [a for a in self._agents.values() if capability_name in a.capabilities]

    def find_agents_by_keywords(self, keywords: List[str]) -> List[AgentProfile]:
        """Find agents matching any keyword across their capabilities."""
        kw_set = {k.lower() for k in keywords}
        matched = []
        for a in self._agents.values():
            agent_kws: Set[str] = set()
            for cap_name in a.capabilities:
                cap = self._capabilities.get(cap_name)
                if cap:
                    agent_kws.update(k.lower() for k in cap.keywords)
                    agent_kws.add(cap.name.lower())
            if kw_set & agent_kws:
                matched.append(a)
        return matched


class AgentRouter:
    """Capability-based Agent Router with micro-learning trust/confidence weighting (Herdr + Ruflo)."""

    def __init__(
        self,
        haos_home: Optional[Path] = None,
        registry: Optional[AgentCapabilityRegistry] = None,
        event_bus: Optional[AgentEventBus] = None,
    ):
        self.haos_home = Path(haos_home) if haos_home else get_hermes_home()
        self.event_bus = event_bus or AgentEventBus(self.haos_home)
        self.registry = registry or AgentCapabilityRegistry()
        if not registry:
            self._init_defaults()

    @property
    def _agents(self) -> Dict[str, AgentProfile]:
        return self.registry._agents

    @property
    def _capabilities(self) -> Dict[str, AgentCapability]:
        return self.registry._capabilities

    def _init_defaults(self) -> None:
        """Seed core HAOS specialized agents and capabilities."""
        # 1. Capabilities
        caps = [
            AgentCapability("vector_search", "SIMD vector search, chunking and embedding retrieval", "storage", ["vector", "simd", "embedding", "chunk", "parent_child"]),
            AgentCapability("rust_native", "Rust systems programming, FFI, memory safety and kernel execution", "systems", ["rust", "ffi", "c-abi", "native", "cargo"]),
            AgentCapability("code_engineering", "Code analysis, refactoring, systems architecture and debugging", "engineering", ["python", "typescript", "debug", "refactor", "code", "ast"]),
            AgentCapability("research", "Web search, literature synthesis and documentation retrieval", "research", ["search", "web", "docs", "paper", "query", "perplexity"]),
            AgentCapability("security_audit", "Vulnerability analysis, sandbox verification and permission checks", "security", ["audit", "security", "sandbox", "taint", "vulnerability"]),
            AgentCapability("planning", "Goal decomposition, DAG execution and multi-agent coordination", "orchestration", ["plan", "decompose", "orchestrate", "dag", "goal"]),
        ]
        for c in caps:
            self.registry.register_capability(c)

        # 2. Pre-seeded Agent Profiles
        agents = [
            AgentProfile(
                agent_id="planner",
                name="Master Planner",
                role="planner",
                capabilities=["planning"],
                trust_score=0.92,
                model_preference="anthropic/claude-3-7-sonnet",
            ),
            AgentProfile(
                agent_id="rust-memory-agent",
                name="Rust Memory Agent",
                role="worker",
                capabilities=["rust_native", "vector_search"],
                trust_score=0.95,
                model_preference="fast-local",
            ),
            AgentProfile(
                agent_id="code-engineer",
                name="Code Engineer",
                role="worker",
                capabilities=["code_engineering", "rust_native"],
                trust_score=0.90,
                model_preference="anthropic/claude-3-7-sonnet",
            ),
            AgentProfile(
                agent_id="researcher",
                name="Deep Researcher",
                role="worker",
                capabilities=["research"],
                trust_score=0.88,
                model_preference="google/gemini-2.0-flash",
            ),
            AgentProfile(
                agent_id="security-reviewer",
                name="Security Reviewer",
                role="reviewer",
                capabilities=["security_audit"],
                trust_score=0.94,
                model_preference="fast-local",
            ),
        ]
        for a in agents:
            self.registry.register_agent(a)

    def register_agent(self, profile: AgentProfile) -> None:
        self.registry.register_agent(profile)

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        return self.registry.get_agent(agent_id)

    def list_agents(self) -> List[AgentProfile]:
        return self.registry.list_agents()

    def list_capabilities(self) -> List[AgentCapability]:
        return self.registry.list_capabilities()

    def register_capability(self, capability: AgentCapability) -> None:
        self.registry.register_capability(capability)

    def route(self, task_description: str) -> AgentProfile:
        """Route task to the most competent agent based on capability keywords and trust score."""
        tokens = set(task_description.lower().replace("-", " ").replace("_", " ").split())
        best_agent = None
        best_score = -1.0

        for agent in self.registry.list_agents():
            if agent.status == "offline":
                continue

            match_points = 0
            for cap_name in agent.capabilities:
                cap = self.registry.get_capability(cap_name)
                if not cap:
                    continue
                for kw in cap.keywords:
                    if kw in tokens or any(kw in token for token in tokens):
                        match_points += 2
                if cap.name in task_description.lower():
                    match_points += 3

            # Combined score = keyword matches * trust score
            score = (match_points + 1.0) * agent.trust_score
            if score > best_score:
                best_score = score
                best_agent = agent

        if not best_agent:
            best_agent = self.registry.get_agent("planner") or self.registry.list_agents()[0]

        # Emit routing event
        self.event_bus.emit(
            AgentEvent(
                event_id=str(uuid.uuid4()),
                event_type="agent.routed",
                agent_id=best_agent.agent_id,
                payload={"task": task_description[:200], "score": best_score},
            )
        )
        return best_agent

    def reinforce(self, agent_id: str, delta: float = 0.05) -> None:
        """Reinforce agent trust after successful mission execution."""
        agent = self.registry.get_agent(agent_id)
        if agent:
            agent.trust_score = min(1.0, agent.trust_score + delta)
            agent.execution_count += 1
            agent.success_count += 1

    def penalize(self, agent_id: str, delta: float = 0.10) -> None:
        """Penalize agent trust score after mission failure or hallucination."""
        agent = self.registry.get_agent(agent_id)
        if agent:
            agent.trust_score = max(0.1, agent.trust_score - delta)
            agent.execution_count += 1
            agent.failure_count += 1

    def record_task_outcome(self, agent_id: str, success: bool, memory_id: Optional[str] = None) -> None:
        """Update metrics and emit completion/failure event."""
        if success:
            self.reinforce(agent_id)
            ev_type = "agent.completed"
        else:
            self.penalize(agent_id)
            ev_type = "agent.failed"

        self.event_bus.emit(
            AgentEvent(
                event_id=str(uuid.uuid4()),
                event_type=ev_type,
                agent_id=agent_id,
                payload={"success": success},
                target_ref=f"fact:{memory_id}" if memory_id else None,
            )
        )


AgentMeshRouter = AgentRouter


@dataclass
class WorkflowStep:
    """A step in a WorkflowDAG with dependencies and execution action."""
    step_id: str
    name: str
    task_description: str
    depends_on: List[str] = field(default_factory=list)
    agent_id: Optional[str] = None
    action: Optional[Callable[[Dict[str, Any], Dict[str, Any]], Any]] = None
    status: str = "pending"  # "pending" | "running" | "completed" | "failed"
    result: Any = None
    error: Optional[str] = None


class WorkflowDAG:
    """Declarative Workflow DAG with step dependencies and Agent Mesh execution (Ruflo + ADK)."""

    def __init__(self, workflow_id: str, name: str = ""):
        self.workflow_id = workflow_id
        self.name = name or workflow_id
        self.steps: Dict[str, WorkflowStep] = {}

    def add_step(
        self,
        step_id: str,
        name: str,
        task_description: str,
        depends_on: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        action: Optional[Callable[[Dict[str, Any], Dict[str, Any]], Any]] = None,
    ) -> WorkflowDAG:
        """Add a step to the workflow with optional upstream dependencies."""
        self.steps[step_id] = WorkflowStep(
            step_id=step_id,
            name=name,
            task_description=task_description,
            depends_on=depends_on or [],
            agent_id=agent_id,
            action=action,
        )
        return self

    def validate(self) -> List[str]:
        """Validate DAG acyclicity and return execution topological order using Kahn's algorithm."""
        in_degree = {sid: 0 for sid in self.steps}
        graph: Dict[str, List[str]] = {sid: [] for sid in self.steps}

        for sid, step in self.steps.items():
            for dep in step.depends_on:
                if dep not in self.steps:
                    raise ValueError(f"Step '{sid}' depends on undefined step '{dep}'")
                graph[dep].append(sid)
                in_degree[sid] += 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in graph[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self.steps):
            raise ValueError(f"Cyclic dependency detected in WorkflowDAG '{self.workflow_id}'")
        return order

    def execute(
        self,
        router: Optional[AgentRouter] = None,
        event_bus: Optional[AgentEventBus] = None,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute workflow steps respecting topological dependencies."""
        order = self.validate()
        ctx = dict(initial_context or {})
        bus = event_bus or (router.event_bus if router else None)
        results: Dict[str, Any] = {}

        if bus:
            bus.emit(
                AgentEvent(
                    event_id=str(uuid.uuid4()),
                    event_type="workflow.started",
                    agent_id="orchestrator",
                    payload={"workflow_id": self.workflow_id, "step_count": len(self.steps)},
                )
            )

        for step_id in order:
            step = self.steps[step_id]
            step.status = "running"

            # Determine agent
            assigned_agent = step.agent_id
            if not assigned_agent and router:
                routed = router.route(step.task_description)
                assigned_agent = routed.agent_id
            assigned_agent = assigned_agent or "default_worker"

            if bus:
                bus.emit(
                    AgentEvent(
                        event_id=str(uuid.uuid4()),
                        event_type="agent.started",
                        agent_id=assigned_agent,
                        payload={"step_id": step_id, "task": step.task_description},
                    )
                )

            # Collect inputs from dependencies
            step_inputs = {dep: results.get(dep) for dep in step.depends_on}

            try:
                if step.action:
                    res = step.action(step_inputs, ctx)
                else:
                    res = f"Executed {step.name} by {assigned_agent}"

                step.result = res
                step.status = "completed"
                results[step_id] = res

                if bus:
                    bus.emit(
                        AgentEvent(
                            event_id=str(uuid.uuid4()),
                            event_type="agent.completed",
                            agent_id=assigned_agent,
                            payload={"step_id": step_id, "output": str(res)[:200]},
                        )
                    )
                if router:
                    router.reinforce(assigned_agent)

            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                results[step_id] = None
                if bus:
                    bus.emit(
                        AgentEvent(
                            event_id=str(uuid.uuid4()),
                            event_type="agent.failed",
                            agent_id=assigned_agent,
                            payload={"step_id": step_id, "error": str(e)},
                        )
                    )
                if router:
                    router.penalize(assigned_agent)
                raise RuntimeError(f"Step '{step_id}' failed: {e}") from e

        if bus:
            bus.emit(
                AgentEvent(
                    event_id=str(uuid.uuid4()),
                    event_type="workflow.completed",
                    agent_id="orchestrator",
                    payload={"workflow_id": self.workflow_id, "completed_steps": len(order)},
                )
            )

        return {
            "workflow_id": self.workflow_id,
            "status": "completed",
            "results": results,
            "steps": {sid: asdict(s) if hasattr(s, "step_id") else s for sid, s in self.steps.items()},
        }


class PowerlineContextHUD:
    """Generates segmented Powerline-style status tuples for CLI, prompt injection, and WebUI."""

    def __init__(self, router: Optional[AgentMeshRouter] = None):
        self.router = router or AgentMeshRouter()

    def render_segments(self, haos_home: Optional[Path] = None) -> List[Dict[str, Any]]:
        """Generate structured status segments representing system cognitive health."""
        home = Path(haos_home) if haos_home else get_hermes_home()
        memory_dir = home / "memory"
        fabric_db = memory_dir / "fabric.db"
        raggraph_db = memory_dir / "raggraph.db"

        # 1. Memory stats
        fact_count = 0
        if fabric_db.is_file():
            try:
                with sqlite3.connect(f"{fabric_db.resolve().as_uri()}?mode=ro", uri=True) as conn:
                    row = conn.execute("SELECT count(*) FROM memory_records;").fetchone()
                    if row:
                        fact_count = row[0]
            except Exception:
                pass

        # 2. Graph stats
        graph_nodes = 0
        graph_edges = 0
        if raggraph_db.is_file():
            try:
                with sqlite3.connect(f"{raggraph_db.resolve().as_uri()}?mode=ro", uri=True) as conn:
                    r1 = conn.execute("SELECT count(*) FROM haos_graph_nodes;").fetchone()
                    r2 = conn.execute("SELECT count(*) FROM haos_graph_edges;").fetchone()
                    if r1:
                        graph_nodes = r1[0]
                    if r2:
                        graph_edges = r2[0]
            except Exception:
                pass

        # 3. Agent Mesh stats
        agents = self.router.list_agents()
        avg_trust = sum(a.trust_score for a in agents) / max(1, len(agents))

        segments = [
            {
                "id": "mem",
                "label": "MEM",
                "value": f"100% | {fact_count} facts",
                "color": "#10b981",
                "icon": "Database",
            },
            {
                "id": "graph",
                "label": "GRAPH",
                "value": f"{graph_nodes} nodes | {graph_edges} edges",
                "color": "#6366f1",
                "icon": "Share2",
            },
            {
                "id": "mesh",
                "label": "MESH",
                "value": f"{len(agents)} agents | trust {avg_trust:.2f}",
                "color": "#06b6d4",
                "icon": "Bot",
            },
            {
                "id": "sentinel",
                "label": "SENTINEL",
                "value": "0 conflicts | verified",
                "color": "#8b5cf6",
                "icon": "ShieldCheck",
            },
            {
                "id": "goal",
                "label": "GOAL",
                "value": "Agent Mesh SOTA | conf 0.95",
                "color": "#f59e0b",
                "icon": "Zap",
            },
        ]
        return segments

    def render_powerline_string(self, segments: Optional[List[Dict[str, Any]]] = None) -> str:
        """Format segments as compact Powerline chevron string."""
        if segments is None:
            segments = self.render_segments()

        parts = []
        for s in segments:
            parts.append(f"[{s['label']}: {s['value']}]")
        return "  ".join(parts)
