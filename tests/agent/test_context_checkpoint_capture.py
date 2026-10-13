"""Pre-compaction snapshot contract tests."""
from __future__ import annotations

import json
from types import SimpleNamespace

from hermes.platform.context.budget import artifact_index as ai
from hermes.platform.context.budget.artifact_index import ContextArtifactIndex


def test_checkpoint_capture_does_not_touch_messages(tmp_path, monkeypatch):
    from agent.context_checkpoint_capture import capture_compaction_checkpoint
    monkeypatch.setenv("HAOS_CONTEXT_RETRIEVAL_ENABLED", "1")
    monkeypatch.setattr(ai, "_default_path", lambda: tmp_path / "context.sqlite3")
    messages = [
        {"role": "user", "content": "Fix the Linux agent regression"},
        {"role": "assistant", "tool_calls": [{
            "type": "function", "id": "c1", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "agent/a.py"}),
            },
        }]},
        {"role": "tool", "tool_call_id": "c1", "content": "5 passed, 1 failed"},
    ]
    original = json.loads(json.dumps(messages))
    assert capture_compaction_checkpoint(
        SimpleNamespace(session_id="s1"), messages, "task-44",
    )
    checkpoint = ContextArtifactIndex().latest_checkpoint("s1")["payload"]
    assert checkpoint["task_id"] == "task-44"
    assert "agent/a.py" in checkpoint["paths"]
    assert "read_file" in checkpoint["tools"]
    assert "failed" in checkpoint["test_errors_and_status"][0]
    assert messages == original


def test_checkpoint_capture_off_is_noop(tmp_path, monkeypatch):
    from agent.context_checkpoint_capture import capture_compaction_checkpoint
    monkeypatch.delenv("HAOS_CONTEXT_RETRIEVAL_ENABLED", raising=False)
    monkeypatch.setattr(ai, "_default_path", lambda: tmp_path / "context.sqlite3")
    assert not capture_compaction_checkpoint(
        SimpleNamespace(session_id="s1"), [{"role": "user", "content": "text"}], "task",
    )
    assert not (tmp_path / "context.sqlite3").exists()
