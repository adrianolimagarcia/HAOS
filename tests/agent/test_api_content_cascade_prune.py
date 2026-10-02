"""Cascade cleanup runs from the stamp: older rows' <memory-context> fences go,
the just-stamped sidecar keeps its exact bytes.

`_stamp_api_content_sidecar` is the single choke point every durable sidecar write
flows through (CHANGELOG #6757 byte-stable replay), so the stale-fence prune hooks
in right after `set_message_api_content` — guarded so a stub `_db` (test agents, older
doubles) skips it, and `suppress`-wrapped so prune failure never fails the stamp.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.turn_context import _stamp_api_content_sidecar
from hermes_state import SessionDB


def test_stamp_prunes_older_fences_and_keeps_live_bytes(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s1", source="cli")
    db.append_message(
        "s1", "user", content="q1",
        api_content="q1\n\n<memory-context>\nMEM-A\n</memory-context>",
    )
    db.append_message("s1", "assistant", content="a1")
    row_id = db.append_message("s1", "user", content="q2")

    agent = SimpleNamespace(session_id="s1", _session_db=db)
    msg = {"content": "q2", "_row_id": row_id}
    _stamp_api_content_sidecar(
        agent, [msg], 0, "", "PLUGIN-CTX", preflight_compressed=False,
    )

    rows = {row["content"]: row for row in db.get_messages("s1")}
    # live dict + stamped row carry the exact composed bytes (prompt-cache contract)
    assert msg["api_content"] == "q2\n\nPLUGIN-CTX"
    assert rows["q2"]["api_content"] == "q2\n\nPLUGIN-CTX"
    # the older fenced row was pruned in the same call (strip == content → NULL)
    assert rows["q1"]["api_content"] is None
    # transcript content untouched
    assert msg["content"] == "q2"


def test_stamp_without_db_sidecar_still_returns_silently(tmp_path):
    agent = SimpleNamespace(session_id="s1", _session_db=None)
    msg = {"content": "q2", "_row_id": 1}
    _stamp_api_content_sidecar(
        agent, [msg], 0, "", "PLUGIN-CTX", preflight_compressed=False,
    )
    assert msg["content"] == "q2"
