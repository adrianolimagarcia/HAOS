"""Contract tests for durable HAOS artifact retrieval (no LLM/network required)."""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from hermes.platform.context.budget import artifact_index as ai
from hermes.platform.context.budget.artifact_index import ContextArtifactIndex


@pytest.fixture()
def index(tmp_path, monkeypatch):
    db = tmp_path / "artifacts.db"
    monkeypatch.setattr(ai, "_default_path", lambda: db)
    return ContextArtifactIndex(db)


def test_artifact_exact_roundtrip_and_pagination(index):
    text = "HEAD:" + ("A" * 3000) + " NEEDLE" + ("B" * 5000)
    uri = index.put(text, title="log", kind="mcp_output", source="example")
    assert re.fullmatch(r"artifact://store/[0-9a-f]{32}", uri)
    first = index.read(uri, max_chars=1200)
    assert first["content"] == text[:1200]
    assert first["has_more"]
    assert first["next_offset"] == 1200
    next_part = ContextArtifactIndex(index.path).read(
        uri, offset=first["next_offset"], max_chars=1200,
    )
    assert next_part["content"] == text[1200:2400]
    section = index.read(uri, section="NEEDLE", max_chars=300)
    assert "NEEDLE" in section["content"]
    assert index.read(uri, level="summary")["content"] == text[:600]
    assert index.read(uri, level="abstract")["content"] == text[:160]


def test_recovery_tool_reads_bytes_instead_of_placeholder(index):
    from tools.context_expand_tool import _handle_context_expand
    uri = index.put("original content " * 100, title="payload")
    result = json.loads(_handle_context_expand({
        "target_uri": uri, "level": "full", "offset": 9, "max_chars": 300,
    }))
    assert result["content"] == ("original content " * 100)[9:309]
    assert result["has_more"]
    assert json.loads(_handle_context_expand({
        "target_uri": "artifact://store/nonexistent",
    }))["error"] == "artifact_not_found_or_expired"


def test_search_returns_only_matched_profile(index):
    uri = index.put("Failure: missing libc++.a(shr_64.o)", title="AIX runtime")
    assert any(hit["uri"] == uri for hit in index.search("libc"))
    other = ContextArtifactIndex(index.path, scope="other")
    assert other.search("libc") == []
    assert other.read(uri) is None


def test_expired_artifact_not_retrievable_and_prunable(index):
    uri = index.put("expires soon")
    with sqlite3.connect(index.path) as db:
        db.execute("UPDATE context_artifacts SET expires_at=0 WHERE uri=?", (uri,))
    assert index.read(uri) is None
    assert index.prune() == 1
    assert index.search("expires") == []


def test_checkpoints_roundtrip_across_instances(index):
    index.save_checkpoint("session-1", {"branch": "feat/a", "tests": "red"})
    second = index.save_checkpoint("session-1", {
        "branch": "feat/a", "tests": "green", "commits": ["abc"],
    })
    index.save_checkpoint("session-2", {"secret": "other"})
    latest = ContextArtifactIndex(index.path).latest_checkpoint("session-1")
    assert latest["id"] == second
    assert latest["payload"]["tests"] == "green"
    assert ContextArtifactIndex(index.path, scope="other").latest_checkpoint("session-1") is None


def test_mcp_opt_in_indexes_entire_output(index, tmp_path, monkeypatch):
    from hermes.platform.mcp.spill import apply_mcp_result_budget
    monkeypatch.setenv("HAOS_CONTEXT_RETRIEVAL_ENABLED", "1")
    full = "begin\n" + ("ordinary context\n" * 200) + "ERROR: connection refused\n" + "end"
    response = apply_mcp_result_budget(full, "test_mcp", budget=2000,
                                       spills_dir=tmp_path)
    assert "ERROR: connection refused" in response
    uri = re.search(r"artifact://store/[0-9a-f]{32}", response).group(0)
    recovered = index.read(uri, max_chars=10000)
    assert recovered["content"] == full
    assert not list(tmp_path.glob("*-test_mcp.txt"))


def test_mcp_failure_falls_back_to_lossless_file(tmp_path, monkeypatch):
    from hermes.platform.mcp.spill import apply_mcp_result_budget
    monkeypatch.setenv("HAOS_CONTEXT_RETRIEVAL_ENABLED", "1")

    def fail(*args, **kwargs):
        raise OSError("no space")

    monkeypatch.setattr(ContextArtifactIndex, "put", fail)
    full = "A" * 1200
    response = apply_mcp_result_budget(full, "mcp", budget=100,
                                       spills_dir=tmp_path)
    assert "[truncado; completo em " in response
    files = list(tmp_path.glob("*-mcp.txt"))
    assert len(files) == 1
    assert files[0].read_text() == full


def test_budgeter_returns_expandable_pointer(index):
    from hermes.platform.context.budget.budgeter import ArtifactStore
    item = ArtifactStore(token_threshold=10).store_or_pass(
        "doc", "document", "true artifact content " * 30,
    )
    assert item.source_uri.startswith("artifact://store/")
    assert index.read(item.source_uri)["content"] == "true artifact content " * 30


def test_budgeter_never_returns_broken_pointer(index, monkeypatch):
    from hermes.platform.context.budget.budgeter import ArtifactStore

    def fail(*args, **kwargs):
        raise OSError("sqlite readonly")

    monkeypatch.setattr(ContextArtifactIndex, "put", fail)
    item = ArtifactStore(token_threshold=10).store_or_pass(
        "doc", "document", "data" * 100,
    )
    assert item.source_uri.startswith("artifact://inline/")
    assert item.content == "data" * 100
