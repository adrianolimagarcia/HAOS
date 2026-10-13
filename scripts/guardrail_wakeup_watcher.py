#!/usr/bin/env python3
"""Phase 3 wake-up watcher: revive sessions stalled by a guardrail block.

Companion to the wake-up guardrail (agent/tool_guardrails.py, Phases 1-3).

How it works
------------
1. Scans the observability event store for recent ``agent.guardrail.blocked``
   events (emitted by the agent runtime when a tool-call guardrail blocks).
2. For each blocked session, checks whether the session has made progress
   since the block (new messages in state.db) or still has a live turn lease.
3. Sessions that are still quiet past the quiet threshold get ONE wake-up
   message injected through the WebUI API (``POST /api/chat/start``) telling
   the agent to try a different approach — or, for escalated blocks, to ask
   the user for guidance as instructed.

Idempotency: a state file records every handled ``(session_id, event_id)``
pair, so each block event triggers at most one wake-up.

Safety
------
- Read-only opens (``mode=ro``) on events.db and state.db — never writes.
- Skips ended sessions and sessions with a live (unexpired) turn lease.
- Skips blocks younger than the quiet threshold (default 5 min).
- Caps wake-ups per run (default 5) to avoid storms.
- Never raises for expected conditions; prints a JSON summary to stdout.

Scheduling (NOT done automatically — see deploy report)
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
    GUARDRAIL_WATCHER_MAX_WAKEUPS default 5 (cap per run)
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
    "MAX_WAKEUPS": 5,
}


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
        "the parameters, or the search terms — do not repeat the blocked call unchanged. "
        "If you already tried 3 different approaches without progress, "
        "summarize and ask the user for guidance."
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
    max_wakeups = cfg("MAX_WAKEUPS")

    summary = {
        "events_seen": 0,
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
            summary["woken"].append({"session": session_id, "detail": detail})
        else:
            summary["errors"].append(f"wake {session_id}: {detail}")

    try:
        save_handled(state_file, handled)
    except OSError as exc:
        summary["errors"].append(f"state_file: {exc}"[:200])

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
