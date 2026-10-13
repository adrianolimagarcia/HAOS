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
    s.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, ended_at REAL)")
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
