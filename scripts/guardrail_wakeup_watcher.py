#!/usr/bin/env python3
"""Phase 3 wake-up watcher: revive sessions stalled by a guardrail block
or orphaned by a service restart.

Companion to the wake-up guardrail (agent/tool_guardrails.py, Phases 1-3).

How it works
------------
1. Scans the observability event store for recent ``agent.guardrail.blocked``
   events (emitted by the agent runtime when a tool-call guardrail blocks).
2. For each blocked session, checks whether the session has made progress
   since the block (new messages in state.db) or still has a live turn lease.
3. Sessions that are still quiet past the quiet threshold get ONE wake-up
   message injected through the WebUI API (``POST /api/chat/start``) telling
   the agent to try a different approach \u2014 or, for escalated blocks, to ask
   the user for guidance as instructed.
4. Orphan scan: sessions with no recent block event can still be dead \u2014
   e.g. the agent loop was killed by a service restart (turn lease expired,
   ``ended_at`` NULL, no new messages). A session looks orphaned when ALL hold:

   - ``ended_at`` IS NULL (not formally ended),
   - no live turn lease (missing row or ``expires_at`` <= now),
   - no new messages for longer than ORPHAN_MIN (default 10 min),
   - last activity is fresher than ORPHAN_LOOKBACK_MIN (default 180 min)
     \u2014 older idleness is assumed to be a deliberate stop, not an orphan,
   - the last message is not from the user (waiting on the user is not an
     orphan) and does not look like a normal assistant conclusion.

   Orphaned sessions get ONE ``[Wake-up]`` resume message (\u201cresume where
   you left off\u201d).

Idempotency: a state file records every handled ``(session_id, event_id)``
pair for blocks and ``(session_id, orphan)`` for orphans, so each trigger
fires at most one wake-up per session.

Safety
------
- Read-only opens (``mode=ro``) on events.db and state.db \u2014 never writes.
- Skips ended sessions and sessions with a live (unexpired) turn lease.
- Skips blocks younger than the quiet threshold (default 5 min).
- Caps wake-ups per run (default 5, shared between blocks and orphans).
- Never raises for expected conditions; prints a JSON summary to stdout.

Scheduling (NOT done automatically \u2014 see deploy report)
-------------------------------------------------------
Run every 5 minutes ON cachyosdell (needs localhost access to the WebUI API
and to /root/.haos/*.db). Example root crontab entry::

    */5 * * * * /usr/bin/python3 /usr/local/lib/haos-agent/scripts/guardrail_wakeup_watcher.py >> /var/log/guardrail-wakeup-watcher.log 2>&1

Environment overrides (all optional):

    GUARDRAIL_WATCHER_EVENTS_DB   default /root/.haos/events.db
    GUARDRAIL_WATCHER_STATE_DB    default /root/.haos/state.db
    GUARDRAIL_WATCHER_STATE_FILE  default /root/.haos/guardrail_wakeup_watcher_state.json
    GUARDRAIL_WATCHER_WEBUI       default http://127.0.0.1:8786
    GUARDRAIL_WATCHER_LOOKBACK_MIN default 120 (only consider blocks this fresh)
    GUARDRAIL_WATCHER_QUIET_MIN   default 5 (session quiet this long before wake-up)
    GUARDRAIL_WATCHER_ORPHAN_MIN  default 10 (orphan quiet threshold, minutes)
    GUARDRAIL_WATCHER_ORPHAN_LOOKBACK_MIN default 180 (ignore orphans staler than this)
    GUARDRAIL_WATCHER_MAX_WAKEUPS default 5 (cap per run, shared blocks+orphans)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request


EVENT_NAME = "agent.guardrail.blocked"

DEFAULTS = {
    "EVENTS_DB": "/root/.haos/events.db",
    "STATE_DB": "/root/.haos/state.db",
    "STATE_FILE": "/root/.haos/guardrail_wakeup_watcher_state.json",
    "WEBUI": "http://127.0.0.1:8786",
    "LOOKBACK_MIN": 120,
    "QUIET_MIN": 5,
    "ORPHAN_MIN": 10,
    "ORPHAN_LOOKBACK_MIN": 180,
    "MAX_WAKEUPS": 5,
}

# Case-insensitive phrases marking a normal assistant conclusion. A session
# whose last activity looks like this is never treated as an orphan.
CONCLUSION_SIGNALS = (
    "conclu",
    "veredito",
    "resumo final",
    "causa raiz",
    "diagn\u00f3stico final",
    "diagnostico final",
    "relat\u00f3rio executivo",
    "relatorio executivo",
    "relat\u00f3rio final",
    "relatorio final",
    "finalizado",
    "fixed",
    "corrigido",
)

# Negations that flip a conclusion signal into a FAILURE report (B-i): the raw
# substring signals above also match failure phrasing — "conclu" is inside
# "não consegui concluir", "inconclusivo", etc. A session whose last assistant
# message reports that it could NOT conclude died mid-failure, not mid-conclusion,
# and must stay wakeable as an orphan. Checked before CONCLUSION_SIGNALS.
_CONCLUSION_NEGATIONS = (
    "não consegui concluir",
    "nao consegui concluir",
    "não foi possível concluir",
    "nao foi possivel concluir",
    "não foi possivel concluir",
    "nao foi possível concluir",
    "não conclui",
    "nao conclui",
    "não concluído",
    "nao concluido",
    "inconclusiv",
    "sem concluir",
    "falha ao concluir",
    "falhei ao concluir",
)


def cfg(name: str):
    val = os.environ.get("GUARDRAIL_WATCHER_" + name, DEFAULTS[name])
    if isinstance(DEFAULTS[name], int):
        try:
            return int(val)
        except (TypeError, ValueError):
            return DEFAULTS[name]
    return val


def ro_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def recent_block_events(events_db: str, lookback_min: int, now: float):
    """Return [(event_id, timestamp, payload_dict)] for recent block events."""
    conn = ro_connect(events_db)
    try:
        rows = conn.execute(
            "SELECT event_id, timestamp, payload FROM events "
            "WHERE name = ? AND timestamp >= ? ORDER BY timestamp",
            (EVENT_NAME, now - lookback_min * 60),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else {}
        except (ValueError, TypeError):
            payload = {}
        out.append((r["event_id"], float(r["timestamp"]), payload if isinstance(payload, dict) else {}))
    return out


def session_status(state_db: str, session_id: str, now: float):
    """Return (ended: bool, last_activity_ts: float|None, live_lease: bool)."""
    conn = ro_connect(state_db)
    try:
        row = conn.execute(
            "SELECT ended_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return True, None, False  # unknown session: treat as gone
        if row["ended_at"] is not None:
            return True, None, False
        msg = conn.execute(
            "SELECT MAX(timestamp) AS ts FROM messages WHERE session_id = ? AND active = 1",
            (session_id,),
        ).fetchone()
        last_ts = float(msg["ts"]) if msg and msg["ts"] is not None else None
        live_lease = False
        try:
            lease = conn.execute(
                "SELECT expires_at FROM session_turn_leases WHERE conversation_id = ?",
                (session_id,),
            ).fetchone()
            live_lease = bool(lease and float(lease["expires_at"]) > now)
        except sqlite3.Error:
            pass  # table missing or unreadable: fall back to quiet heuristic
        return False, last_ts, live_lease
    finally:
        conn.close()


def looks_concluded(role, content) -> bool:
    """True when the last activity looks like a normal assistant conclusion.

    Negation-aware (B-i): failure reports that merely mention a conclusion word
    ("não consegui concluir") return False — the session died mid-failure and
    must be woken as an orphan, not filed as concluded.
    """
    if role != "assistant" or not content:
        return False
    text = str(content).lower()
    if any(neg in text for neg in _CONCLUSION_NEGATIONS):
        return False
    return any(sig in text for sig in CONCLUSION_SIGNALS)


def orphan_candidates(state_db: str, orphan_min: int, orphan_lookback_min: int, now: float):
    """Return [(session_id, last_activity_ts)] for sessions that look orphaned.

    A session looks orphaned when ALL hold: ended_at IS NULL, no live turn
    lease, no new messages for longer than orphan_min, last activity fresher
    than orphan_lookback_min, and the last message is neither from the user
    nor a normal assistant conclusion.
    """
    conn = ro_connect(state_db)
    try:
        rows = conn.execute(
            "SELECT s.id AS sid, MAX(m.timestamp) AS last_ts "
            "FROM sessions s LEFT JOIN messages m "
            "ON m.session_id = s.id AND m.active = 1 "
            "WHERE s.ended_at IS NULL GROUP BY s.id"
        ).fetchall()
        out = []
        for r in rows:
            sid = r["sid"]
            if r["last_ts"] is None:
                continue
            last_ts = float(r["last_ts"])
            quiet = now - last_ts
            if quiet < orphan_min * 60:
                continue
            if quiet > orphan_lookback_min * 60:
                continue
            msg = conn.execute(
                "SELECT role, substr(content, 1, 800) AS c FROM messages "
                "WHERE session_id = ? AND active = 1 "
                "ORDER BY timestamp DESC LIMIT 1",
                (sid,),
            ).fetchone()
            role = msg["role"] if msg else None
            content = msg["c"] if msg and msg["c"] else ""
            if role == "user":
                continue  # waiting on the user is not an orphan
            if looks_concluded(role, content):
                continue
            live_lease = False
            try:
                lease = conn.execute(
                    "SELECT expires_at FROM session_turn_leases "
                    "WHERE conversation_id = ?",
                    (sid,),
                ).fetchone()
                live_lease = bool(lease and float(lease["expires_at"]) > now)
            except sqlite3.Error:
                pass
            if live_lease:
                continue
            out.append((sid, last_ts))
        return out
    finally:
        conn.close()


def wakeup_message(payload: dict) -> str:
    tool = payload.get("tool_name") or "the tool"
    code = payload.get("code") or "blocked"
    if payload.get("escalated"):
        return (
            "[Wake-up] The tool guardrail escalated: you were instructed to ask the "
            "user for guidance after trying several approaches without progress. "
            "Please summarize your goal, what you already tried, and ask the user now."
        )
    return (
        f"[Wake-up] The tool guardrail blocked {tool} ({code}). "
        "Try a different approach to your original goal: change the tool, "
        "the parameters, or the search terms \u2014 do not repeat the blocked call unchanged. "
        "If you already tried 3 different approaches without progress, "
        "summarize and ask the user for guidance."
    )


def orphan_wakeup_message() -> str:
    return (
        "[Wake-up] Your previous turn was interrupted by a service restart "
        "(the agent runtime was killed before the turn finished). "
        "Please resume where you left off: re-read your recent tool outputs "
        "and continue toward your original goal. Do not repeat completed work."
    )


def inject_wakeup(webui: str, session_id: str, message: str) -> tuple[bool, str]:
    """POST a wake-up user message via /api/chat/start. Returns (ok, detail)."""
    body = json.dumps({"session_id": session_id, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        webui.rstrip("/") + "/api/chat/start",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(4000).decode("utf-8", "replace")
            return resp.status < 400, f"http={resp.status} {data[:200]}"
    except Exception as exc:  # noqa: BLE001 - best effort; caller logs
        return False, f"{type(exc).__name__}: {exc}"[:200]


def load_handled(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("handled", []))
    except (OSError, ValueError):
        return set()


def save_handled(path: str, handled: set) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"handled": sorted(handled)}, fh)
    os.replace(tmp, path)


def main() -> int:
    now = time.time()
    events_db = cfg("EVENTS_DB")
    state_db = cfg("STATE_DB")
    state_file = cfg("STATE_FILE")
    webui = cfg("WEBUI")
    lookback_min = cfg("LOOKBACK_MIN")
    quiet_s = cfg("QUIET_MIN") * 60
    orphan_min = cfg("ORPHAN_MIN")
    orphan_lookback_min = cfg("ORPHAN_LOOKBACK_MIN")
    max_wakeups = cfg("MAX_WAKEUPS")

    summary = {
        "events_seen": 0,
        "orphans_seen": 0,
        "woken": [],
        "skipped": [],
        "errors": [],
    }

    try:
        events = recent_block_events(events_db, lookback_min, now)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"events_db: {type(exc).__name__}: {exc}"[:200])
        print(json.dumps(summary))
        return 1
    summary["events_seen"] = len(events)

    handled = load_handled(state_file)
    wakeups = 0
    woken_this_run = set()

    for event_id, event_ts, payload in events:
        session_id = payload.get("session_id")
        key = f"{session_id}:{event_id}"
        if not session_id:
            summary["skipped"].append({"event": event_id, "reason": "no session_id"})
            handled.add(key)
            continue
        if key in handled:
            continue
        try:
            ended, last_ts, live_lease = session_status(state_db, session_id, now)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"state_db {session_id}: {type(exc).__name__}"[:200])
            continue
        if ended:
            summary["skipped"].append({"session": session_id, "reason": "ended"})
            handled.add(key)
            continue
        if live_lease:
            # Agent attached; leave it alone this run (not marked handled).
            summary["skipped"].append({"session": session_id, "reason": "live lease"})
            continue
        if last_ts is not None and last_ts > event_ts:
            summary["skipped"].append({"session": session_id, "reason": "progressed"})
            handled.add(key)
            continue
        if now - event_ts < quiet_s:
            summary["skipped"].append({"session": session_id, "reason": "too soon"})
            continue
        if wakeups >= max_wakeups:
            summary["skipped"].append({"session": session_id, "reason": "cap"})
            continue
        ok, detail = inject_wakeup(webui, session_id, wakeup_message(payload))
        if ok:
            wakeups += 1
            handled.add(key)
            woken_this_run.add(session_id)
            summary["woken"].append({"session": session_id, "detail": detail, "kind": "block"})
        else:
            summary["errors"].append(f"wake {session_id}: {detail}")

    try:
        orphans = orphan_candidates(state_db, orphan_min, orphan_lookback_min, now)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"orphan_scan: {type(exc).__name__}: {exc}"[:200])
        orphans = []
    summary["orphans_seen"] = len(orphans)

    for session_id, last_ts in orphans:
        key = f"{session_id}:orphan"
        if key in handled:
            continue
        if session_id in woken_this_run:
            # Already woken via a block event this run; do not double-wake.
            continue
        if wakeups >= max_wakeups:
            summary["skipped"].append({"session": session_id, "reason": "cap", "kind": "orphan"})
            continue
        ok, detail = inject_wakeup(webui, session_id, orphan_wakeup_message())
        if ok:
            wakeups += 1
            handled.add(key)
            woken_this_run.add(session_id)
            summary["woken"].append({"session": session_id, "detail": detail, "kind": "orphan"})
        else:
            summary["errors"].append(f"wake-orphan {session_id}: {detail}")

    try:
        save_handled(state_file, handled)
    except OSError as exc:
        summary["errors"].append(f"state_file: {exc}"[:200])

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
