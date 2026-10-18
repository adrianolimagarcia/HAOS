"""Tests for scripts/guardrail_wakeup_watcher.py (Phase 3 watcher + orphan scan).

Synthetic sqlite DBs; the WebUI is never contacted (inject_wakeup is patched).
Runs under pytest, or directly::

    python3 tests/agent/test_guardrail_wakeup_watcher.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import time
import traceback
from pathlib import Path
from unittest import mock


def load_watcher():
    path = Path(__file__).resolve().parents[2] / "scripts" / "guardrail_wakeup_watcher.py"
    spec = importlib.util.spec_from_file_location("guardrail_wakeup_watcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = load_watcher()


def make_dbs(tmp):
    events_db = os.path.join(tmp, "events.db")
    state_db = os.path.join(tmp, "state.db")
    state_file = os.path.join(tmp, "handled.json")
    e = sqlite3.connect(events_db)
    e.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY, timestamp REAL, name TEXT, payload TEXT)")
    e.commit()
    e.close()
    s = sqlite3.connect(state_db)
    s.execute(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY, ended_at REAL, "
        "parent_session_id TEXT, source TEXT, model_config TEXT, end_reason TEXT)"
    )
    s.execute("CREATE TABLE messages(session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INTEGER)")
    s.execute("CREATE TABLE session_turn_leases(conversation_id TEXT PRIMARY KEY, holder TEXT, acquired_at REAL, expires_at REAL)")
    s.commit()
    s.close()
    return events_db, state_db, state_file


def add_session(state_db, sid, ended_at=None):
    s = sqlite3.connect(state_db)
    s.execute("INSERT INTO sessions(id, ended_at) VALUES (?, ?)", (sid, ended_at))
    s.commit()
    s.close()


def add_session_full(state_db, sid, ended_at=None, parent_session_id=None,
                   source=None, model_config=None, end_reason=None):
    s = sqlite3.connect(state_db)
    s.execute(
        "INSERT INTO sessions(id, ended_at, parent_session_id, source, model_config, end_reason)"
        " VALUES (?,?,?,?,?,?)",
        (sid, ended_at, parent_session_id, source, model_config, end_reason),
    )
    s.commit()
    s.close()


def add_message(state_db, sid, role, content, ts, active=1):
    s = sqlite3.connect(state_db)
    s.execute(
        "INSERT INTO messages(session_id, role, content, timestamp, active) VALUES (?,?,?,?,?)",
        (sid, role, content, ts, active),
    )
    s.commit()
    s.close()


def add_lease(state_db, sid, expires_at, holder="agent"):
    s = sqlite3.connect(state_db)
    s.execute(
        "INSERT INTO session_turn_leases(conversation_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
        (sid, holder, expires_at - 300, expires_at),
    )
    s.commit()
    s.close()


def add_block_event(events_db, event_id, session_id, ts, escalated=False):
    e = sqlite3.connect(events_db)
    payload = json.dumps(
        {"session_id": session_id, "tool_name": "search_files", "code": "x", "escalated": escalated}
    )
    e.execute(
        "INSERT INTO events(event_id, timestamp, name, payload) VALUES (?,?,?,?)",
        (event_id, ts, "agent.guardrail.blocked", payload),
    )
    e.commit()
    e.close()


class Harness:
    def __init__(self, max_wakeups="5"):
        self.tmp = tempfile.mkdtemp()
        self.events_db, self.state_db, self.state_file = make_dbs(self.tmp)
        self.calls = []  # [(session_id, kind, message)]
        self.summary = {}
        self.env = {
            "GUARDRAIL_WATCHER_EVENTS_DB": self.events_db,
            "GUARDRAIL_WATCHER_STATE_DB": self.state_db,
            "GUARDRAIL_WATCHER_STATE_FILE": self.state_file,
            "GUARDRAIL_WATCHER_WEBUI": "http://127.0.0.1:9",
            "GUARDRAIL_WATCHER_LOOKBACK_MIN": "120",
            "GUARDRAIL_WATCHER_QUIET_MIN": "5",
            "GUARDRAIL_WATCHER_ORPHAN_MIN": "10",
            "GUARDRAIL_WATCHER_ORPHAN_LOOKBACK_MIN": "180",
            "GUARDRAIL_WATCHER_MAX_WAKEUPS": max_wakeups,
        }

    def run(self):
        def fake_inject(webui, session_id, message):
            kind = "orphan" if "service restart" in message else "block"
            self.calls.append((session_id, kind, message))
            return True, "http=200 ok"

        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch.object(
            W, "inject_wakeup", fake_inject
        ), contextlib.redirect_stdout(buf):
            rc = W.main()
        assert rc == 0, f"main() returned {rc}"
        self.summary = json.loads(buf.getvalue().strip().splitlines()[-1])
        return rc


def test_orphan_typical_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s1")
    add_message(h.state_db, "s1", "assistant", "Vou verificar o mapeamento em codex_web.rs", now - 900)
    add_message(h.state_db, "s1", "tool", "{\"output\": \"...\"}", now - 895)
    add_lease(h.state_db, "s1", now - 600)  # expired lease
    h.run()
    assert h.summary["orphans_seen"] == 1, h.summary
    assert len(h.calls) == 1, h.calls
    sid, kind, msg = h.calls[0]
    assert sid == "s1" and kind == "orphan"
    assert "service restart" in msg and "resume where you left off" in msg


def test_orphan_concluded_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s2")
    add_message(
        h.state_db, "s2", "assistant",
        "### Relat\u00f3rio executivo final: trabalho conclu\u00eddo com sucesso", now - 900,
    )
    h.run()
    assert h.summary["orphans_seen"] == 0, h.summary
    assert h.calls == [], h.calls


def test_orphan_live_lease_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s3")
    add_message(h.state_db, "s3", "assistant", "Vou rodar os testes agora", now - 900)
    add_lease(h.state_db, "s3", now + 600)  # live lease: agent attached
    h.run()
    assert h.calls == [], h.calls


def test_orphan_ended_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s4", ended_at=now - 800)
    add_message(h.state_db, "s4", "assistant", "Vou verificar X", now - 900)
    h.run()
    assert h.calls == [], h.calls


def test_orphan_idempotent_second_run():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s5")
    add_message(h.state_db, "s5", "assistant", "Continuando a investiga\u00e7\u00e3o", now - 900)
    h.run()
    assert len(h.calls) == 1
    h.run()  # second run: already handled
    assert len(h.calls) == 1, h.calls


def test_orphan_stale_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s6")
    add_message(h.state_db, "s6", "assistant", "Vou verificar X", now - 200 * 60)  # 200m > lookback 180
    h.run()
    assert h.calls == [], h.calls


def test_orphan_user_last_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s7")
    add_message(h.state_db, "s7", "assistant", "Preciso que voc\u00ea decida X", now - 950)
    add_message(h.state_db, "s7", "user", "pode continuar do jeito Y", now - 900)
    h.run()
    assert h.calls == [], h.calls


def test_orphan_too_soon_not_woken():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s8")
    add_message(h.state_db, "s8", "assistant", "Vou verificar X", now - 180)  # 3m < orphan_min 10
    h.run()
    assert h.calls == [], h.calls


def test_block_path_regression():
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s9")
    add_message(h.state_db, "s9", "assistant", "tentando de novo", now - 600)
    add_block_event(h.events_db, "ev1", "s9", now - 360)  # 6m ago > quiet 5m
    h.run()
    assert h.summary["events_seen"] == 1, h.summary
    assert len(h.calls) == 1, h.calls
    sid, kind, msg = h.calls[0]
    assert sid == "s9" and kind == "block"
    assert "tool guardrail" in msg


def test_shared_cap_blocks_and_orphans():
    h = Harness(max_wakeups="1")
    now = time.time()
    add_session(h.state_db, "sb")
    add_message(h.state_db, "sb", "assistant", "tentando", now - 600)
    add_block_event(h.events_db, "ev1", "sb", now - 360)
    add_session(h.state_db, "so")
    add_message(h.state_db, "so", "assistant", "verificando", now - 900)
    h.run()
    assert len(h.calls) == 1, h.calls
    assert h.calls[0][0] == "sb"  # block path wins; orphan capped
    assert h.summary["orphans_seen"] == 2  # sb also qualifies as orphan; already woken via block, no double-wake


def test_block_live_lease_on_lineage_root_not_woken():
    """H1 regression: the live-lease guard resolves session_id -> conversation_id.

    The lease row is keyed by the compression-lineage root, not by the child
    session id. The watcher must NOT wake a blocked child whose lineage holds
    a live turn lease.
    """
    h = Harness()
    now = time.time()
    add_session_full(h.state_db, "root", ended_at=now - 1000, end_reason="compression")
    add_session_full(h.state_db, "child", parent_session_id="root")
    add_message(h.state_db, "child", "assistant", "tentando de novo", now - 600)
    add_lease(h.state_db, "root", now + 600)  # live lease on the lineage root
    add_block_event(h.events_db, "ev1", "child", now - 360)
    h.run()
    assert h.summary["events_seen"] == 1, h.summary
    assert h.calls == [], h.calls
    assert any(s.get("reason") == "live lease" for s in h.summary["skipped"]), h.summary


def test_orphan_live_lease_on_lineage_root_not_woken():
    """H1 regression (orphan path): same lineage-root lease skips the orphan wake."""
    h = Harness()
    now = time.time()
    add_session_full(h.state_db, "root", ended_at=now - 2000, end_reason="compression")
    add_session_full(h.state_db, "child", parent_session_id="root")
    add_message(h.state_db, "child", "assistant", "verificando o deploy", now - 900)
    add_lease(h.state_db, "root", now + 600)  # live lease on the lineage root
    h.run()
    assert h.summary["orphans_seen"] == 0, h.summary
    assert h.calls == [], h.calls


def test_block_expired_lease_on_lineage_root_woken():
    """Control for the H1 tests: an EXPIRED lease on the lineage root still wakes."""
    h = Harness()
    now = time.time()
    add_session_full(h.state_db, "root", ended_at=now - 1000, end_reason="compression")
    add_session_full(h.state_db, "child", parent_session_id="root")
    add_message(h.state_db, "child", "assistant", "tentando de novo", now - 600)
    add_lease(h.state_db, "root", now - 60)  # expired lease
    add_block_event(h.events_db, "ev1", "child", now - 360)
    h.run()
    assert len(h.calls) == 1, h.calls
    assert h.calls[0][0] == "child" and h.calls[0][1] == "block"


def test_block_wakeup_cooldown_per_session():
    """M1 regression: no re-wake for the same session inside the wake cooldown.

    A stuck session re-blocks with a fresh event_id every few minutes; the
    watcher must skip (reason=cooldown) instead of waking again.
    """
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s10")
    add_message(h.state_db, "s10", "assistant", "tentando de novo", now - 600)
    add_block_event(h.events_db, "ev1", "s10", now - 360)
    h.run()
    assert len(h.calls) == 1, h.calls

    # A second block event for the same session, minutes later: cooled down.
    e = sqlite3.connect(h.events_db)
    e.execute(
        "INSERT INTO events(event_id, timestamp, name, payload) VALUES (?,?,?,?)",
        ("ev2", time.time() - 60, "agent.guardrail.blocked",
         json.dumps({"session_id": "s10", "tool_name": "search_files",
                     "code": "x", "escalated": False})),
    )
    e.commit()
    e.close()
    h.run()
    assert len(h.calls) == 1, h.calls  # no second wake-up
    assert any(s.get("reason") == "cooldown" for s in h.summary["skipped"]), h.summary


def test_handled_entries_pruned_after_ttl():
    """M10 regression: handled entries older than HANDLED_TTL_DAYS are pruned."""
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s11")
    add_message(h.state_db, "s11", "assistant", "tentando", now - 600)
    add_block_event(h.events_db, "ev1", "s11", now - 360)
    h.run()
    assert len(h.calls) == 1

    state = json.load(open(h.state_file, encoding="utf-8"))
    assert "s11:ev1" in state["handled"], state
    # Age the entry 31 days back and re-run: it must be pruned from the store.
    state["handled_ts"]["s11:ev1"] = now - 31 * 86400
    json.dump(state, open(h.state_file, "w", encoding="utf-8"))
    h.run()
    state = json.load(open(h.state_file, encoding="utf-8"))
    assert "s11:ev1" not in state["handled"], state
    assert "s11:ev1" not in state["handled_ts"], state


def test_handled_legacy_entries_without_timestamp_are_kept():
    """M10: pre-timestamp state files are stamped (kept one full TTL), not dropped."""
    h = Harness()
    now = time.time()
    add_session(h.state_db, "s12")
    add_message(h.state_db, "s12", "assistant", "tentando", now - 600)
    add_block_event(h.events_db, "ev1", "s12", now - 360)
    # Legacy format: only "handled", no timestamps.
    json.dump({"handled": ["s12:ev1"]}, open(h.state_file, "w", encoding="utf-8"))
    h.run()
    state = json.load(open(h.state_file, encoding="utf-8"))
    assert "s12:ev1" in state["handled"], state
    assert state["handled_ts"]["s12:ev1"] >= now - 5, state


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS {n}")
        except Exception:
            failed += 1
            print(f"FAIL {n}")
            traceback.print_exc()
    print(f"{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
