"""Behavioral integration tests for Memory Graph dashboard API endpoints."""

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from starlette.testclient import TestClient


@pytest.fixture
def memory_home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    mem_dir = root / "memory"
    vault_dir = root / "obsidian_vault"
    mem_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    # Populate dummy fabric.db
    fabric_db = mem_dir / "fabric.db"
    with sqlite3.connect(fabric_db) as conn:
        conn.execute("CREATE TABLE memory_records (record_id TEXT PRIMARY KEY, scope TEXT, kind TEXT, status TEXT, content TEXT, confidence REAL, metadata_json TEXT, created_at TEXT, supersedes_json TEXT)")
        conn.execute("CREATE TABLE memory_projection_ack (ack_id INTEGER PRIMARY KEY, projection TEXT)")
        conn.execute("CREATE TABLE memory_outbox (outbox_id INTEGER PRIMARY KEY)")
        conn.execute(
            "INSERT INTO memory_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("rec-1", "global", "fact", "active", "HAOS is federated", 0.99, "{}", "2026-03-30", "[]")
        )
        conn.execute("INSERT INTO memory_projection_ack VALUES (1, 'obsidian')")

    # Populate dummy graphrag.db
    graphrag_db = mem_dir / "graphrag.db"
    with sqlite3.connect(graphrag_db) as conn:
        conn.execute("CREATE TABLE entities (entity TEXT PRIMARY KEY, entity_type TEXT, description TEXT)")
        conn.execute("CREATE TABLE relations (source TEXT, target TEXT, relation_type TEXT, description TEXT)")
        conn.execute("CREATE TABLE communities (community_id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE applied_events (seq INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO entities VALUES (?, ?, ?)", ("MemoryFabric", "Component", "Federated coordinator"))
        conn.execute("INSERT INTO entities VALUES (?, ?, ?)", ("GraphRAG", "Component", "Knowledge graph projection"))
        conn.execute("INSERT INTO relations VALUES (?, ?, ?, ?)", ("MemoryFabric", "GraphRAG", "projects_to", "Fan-out"))

    # Populate dummy vectors.db with parent-child chunking
    vectors_db = mem_dir / "vectors.db"
    with sqlite3.connect(vectors_db) as conn:
        conn.execute(
            "CREATE TABLE memory_vectors (record_id TEXT PRIMARY KEY, dim INTEGER, vector_blob BLOB, text_content TEXT, parent_id TEXT, parent_content TEXT)"
        )
        conn.execute(
            "INSERT INTO memory_vectors VALUES (?, ?, ?, ?, ?, ?)",
            ("rec-1", 4, b"\x00" * 16, "HAOS is federated", "note:adrs/ADR-001-fabric.md", "# Full Architecture Section")
        )

    # Populate dummy obsidian note
    adrs_dir = vault_dir / "adrs"
    adrs_dir.mkdir()
    (adrs_dir / "ADR-001-fabric.md").write_text("# ADR 001\nFabric federation.", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


@pytest.fixture
def client():
    from hermes_cli import web_server

    c = TestClient(web_server.app)
    c.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return c


def test_memory_overview_endpoint(client, memory_home):
    res = client.get("/api/memory/graph/overview")
    assert res.status_code == 200
    data = res.json()
    assert "fabric" in data
    assert "graphrag" in data
    assert "obsidian" in data
    assert data["fabric"]["records"] == 1
    assert data["graphrag"]["entities"] == 2
    assert data["obsidian"]["total_notes"] == 1


def test_memory_graph_views(client, memory_home):
    # Unified view
    res = client.get("/api/memory/graph?view=unified")
    assert res.status_code == 200
    data = res.json()
    assert data["schema_version"] == 1
    assert data["counts"]["nodes"] > 0
    assert any(n["id"] == "fabric:coordinator" for n in data["nodes"])

    # GraphRAG view
    res_gr = client.get("/api/memory/graph?view=graphrag")
    assert res_gr.status_code == 200
    data_gr = res_gr.json()
    assert any(n["id"] == "entity:MemoryFabric" for n in data_gr["nodes"])
    assert any(e["source"] == "entity:MemoryFabric" for e in data_gr["edges"])

    # Obsidian view
    res_obs = client.get("/api/memory/graph?view=obsidian")
    assert res_obs.status_code == 200
    data_obs = res_obs.json()
    assert any("ADR-001" in n["id"] for n in data_obs["nodes"])

    # DB view
    res_db = client.get("/api/memory/graph?view=db")
    assert res_db.status_code == 200
    data_db = res_db.json()
    assert any(n["id"] == "fact:rec-1" for n in data_db["nodes"])


def test_memory_node_details(client, memory_home):
    # Entity detail
    res_ent = client.get("/api/memory/graph/node/entity:MemoryFabric")
    assert res_ent.status_code == 200
    assert res_ent.json()["entity"] == "MemoryFabric"

    # Note detail
    res_note = client.get("/api/memory/graph/node/note:adrs/ADR-001-fabric.md")
    assert res_note.status_code == 200
    assert "ADR 001" in res_note.json()["content"]

    # Fact detail
    res_fact = client.get("/api/memory/graph/node/fact:rec-1")
    assert res_fact.status_code == 200
    fact_data = res_fact.json()
    assert fact_data["record_id"] == "rec-1"
    assert fact_data["confidence_tier"] == "high"
    assert fact_data["parent_id"] == "note:adrs/ADR-001-fabric.md"
    assert fact_data["parent_content"] == "# Full Architecture Section"
