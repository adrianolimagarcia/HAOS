"""Unit and integration tests for Agent Mesh, Router, Event Bus and Powerline Context HUD."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from hermes.platform.execution.agent_mesh import (
    AgentCapability,
    AgentEvent,
    AgentEventBus,
    AgentMeshRouter,
    AgentProfile,
    PowerlineContextHUD,
)


def test_agent_profile_and_capabilities():
    profile = AgentProfile(
        agent_id="test-agent",
        name="Test Agent",
        role="worker",
        capabilities=["rust_native", "vector_search"],
        trust_score=0.9,
    )
    d = profile.to_dict()
    assert d["agent_id"] == "test-agent"
    assert "rust_native" in d["capabilities"]
    assert d["trust_score"] == 0.9


def test_event_bus_pub_sub(tmp_path):
    bus = AgentEventBus(haos_home=tmp_path)
    received = []

    def on_event(ev: AgentEvent):
        received.append(ev)

    bus.subscribe("agent.routed", on_event)

    ev1 = AgentEvent(
        event_id="ev-1",
        event_type="agent.routed",
        agent_id="planner",
        payload={"task": "plan test"},
    )
    ev2 = AgentEvent(
        event_id="ev-2",
        event_type="agent.completed",
        agent_id="planner",
        payload={"task": "done"},
    )

    bus.emit(ev1)
    bus.emit(ev2)

    assert len(received) == 1
    assert received[0].event_id == "ev-1"

    # Verify SQLite persistence in raggraph.db fallback
    db_file = tmp_path / "memory" / "raggraph.db"
    assert db_file.exists()
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT event_id, event_type, agent_id FROM haos_agent_events WHERE event_id = 'ev-1'").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "agent.routed"


def test_mesh_router_keyword_routing(tmp_path):
    router = AgentMeshRouter(haos_home=tmp_path)

    # Test routing to rust memory agent for vector SIMD queries
    agent_rust = router.route("Need high speed SIMD vector search and chunk embedding")
    assert agent_rust.agent_id == "rust-memory-agent"

    # Test routing to security reviewer for vulnerability audit
    agent_sec = router.route("Audit the sandbox permissions and inspect vulnerability attack surface")
    assert agent_sec.agent_id == "security-reviewer"

    # Test routing to researcher for web/docs search
    agent_res = router.route("Search web and synthesize documentation for arXiv paper")
    assert agent_res.agent_id == "researcher"


def test_mesh_router_reinforcement_and_penalization(tmp_path):
    router = AgentMeshRouter(haos_home=tmp_path)
    agent = router.get_agent("planner")
    initial_trust = agent.trust_score

    # Reinforce
    router.record_task_outcome("planner", success=True)
    assert agent.trust_score > initial_trust
    assert agent.success_count == 1
    assert agent.execution_count == 1

    # Penalize
    curr_trust = agent.trust_score
    router.record_task_outcome("planner", success=False)
    assert agent.trust_score < curr_trust
    assert agent.failure_count == 1
    assert agent.execution_count == 2


def test_powerline_context_hud(tmp_path):
    router = AgentMeshRouter(haos_home=tmp_path)
    hud = PowerlineContextHUD(router=router)

    segments = hud.render_segments(haos_home=tmp_path)
    assert len(segments) >= 5

    labels = [s["label"] for s in segments]
    assert "MEM" in labels
    assert "GRAPH" in labels
    assert "MESH" in labels
    assert "SENTINEL" in labels
    assert "GOAL" in labels

    p_str = hud.render_powerline_string(segments)
    assert "[MEM:" in p_str
    assert "[MESH:" in p_str
    assert "" in p_str
