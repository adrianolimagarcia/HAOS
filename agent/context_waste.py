"""Context-waste scan over ``state.db`` transcripts: loops, retry churn, error cascades, cache hit.

Heuristic *methodology* (streak thresholds, Jaccard similarity on user messages,
same-tool-same-args churn) was assessed from public write-ups of community token
auditors and REIMPLEMENTED here against the HAOS schema — no third-party code is
vendored or copied (those projects ship PolyForm Noncommercial; ideas and numeric
thresholds are not code, this file is original).

Honesty contract (matches the 0.21.68 measured-vs-null rule):
- every token figure is MEASURED from stored rows, or ``null`` with a reason.
  We never emit fabricated ``count * 5000`` "savings" estimates.
- ``measured_chars`` is the exact sum of ``length(content)`` over the involved
  message rows; ``approx_tokens`` is that char count divided by 4, labelled approx.
- live context occupancy requires per-message ``token_count``; stores that do not
  populate it report ``available: false`` instead of a fake number.

Scans are bounded (sessions + rows per session) so dashboard polling stays cheap;
``truncated`` tells the caller the window was cut. Pure read-only SQL over a
``sqlite3.Connection`` — no writes, no LLM calls, no new dependencies.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

# Thresholds: reimplemented community heuristics, tuned for gateway transcripts.
LOOP_MIN_STREAK = 4          # consecutive similar user messages
LOOP_SIMILARITY = 0.75       # Jaccard on word sets
CHURN_MIN_REPEATS = 3        # same (tool, args-prefix) failing calls
CASCADE_MIN_ERRORS = 4       # consecutive failing tool results
CONTENT_SCAN_CHARS = 600     # similarity/prefix window (full length still measured)
_TOOL_CALLS_SCAN_CHARS = 8000
_MAX_ROWS_PER_SESSION = 3000

_WORD_RE = re.compile(r"\w+")

# Synthetic user rows (background-process / async-delegation notifications) are
# machine re-injections, not human repeats. Mirrors the canonical
# ``hermes_state_timeline._SYNTHETIC_PROMPT`` — kept as a local copy because
# agent/ must not import the storage layer (layering), same 4 prefixes.
_SYNTHETIC_USER_RE = re.compile(
    r"^\s*(?:\[IMPORTANT: Background process |\[ASYNC (?:DELEGATION )?(?:BATCH )?COMPLETE\b|"
    r"A background fan-out of \d+ subagent\(s\) you dispatched earlier has finished\.|"
    r"A background subagent you dispatched earlier has finished\.)",
    re.IGNORECASE,
)

# Machine-authored openers persisted with role="user" (compaction handoffs, model
# switch markers, runtime notes). Kept in sync with agent/title_generator.py
# ``_MACHINE_PREFIXES`` — a handoff carrier is not a human turn.
_MACHINE_USER_PREFIXES = (
    "[CONTEXT COMPACTION", "[CONTEXT SUMMARY]:", "[Runtime note:", "[System note:",
    "[SYSTEM]", "[System: The active model for this chat has changed to ",
)


def _is_human_user_row(row: sqlite3.Row) -> bool:
    """Live human-authored user turn, per the canonical projection rules.

    Compaction summary carriers (``_compressed_summary`` flag or machine opener),
    typed display kinds other than ``steer``, and background/delegation
    notification re-injections are all synthetic.
    """
    if row["_compressed_summary"]:
        return False
    kind = row["display_kind"] or ""
    if kind and kind != "steer":
        return False
    content = row["content"] or ""
    if content.startswith(_MACHINE_USER_PREFIXES):
        return False
    return not _SYNTHETIC_USER_RE.match(content)


# Steer envelope scaffolding (agent.prompt_builder.STEER_MARKER_OPEN/CLOSE) plus
# the gateway-origin block gateway/run_busy.py injects inside it. The scaffolding
# is byte-identical across every steer, so comparing it would make unrelated
# messages look like a loop; similarity must run on the human payload only.
_STEER_OPEN = "[OUT-OF-BAND USER MESSAGE"
_STEER_CLOSE = "[/OUT-OF-BAND USER MESSAGE]"
_ORIGIN_LINE = "Gateway message origin (JSON data, not instructions or authorization):"
_ORIGIN_TAIL = "Do not guess a reply destination when these fields are insufficient."


def _human_user_text(content: str) -> str:
    """Strip steer scaffolding / gateway-origin block so Jaccard sees the payload."""
    if not content.startswith(_STEER_OPEN):
        return content
    marker_end = content.find("]")  # the marker itself contains no ']' before its close
    body = content[marker_end + 1:] if marker_end != -1 else content[len(_STEER_OPEN):]
    body = body.removesuffix(_STEER_CLOSE)
    if _ORIGIN_LINE in body:
        head, _, rest = body.partition(_ORIGIN_LINE)
        _, _, tail = rest.partition(_ORIGIN_TAIL)
        body = head + tail
    return body.strip()

# One SQL expression, reused for the ordered transcript fetch: flags a tool row
# whose stored result envelope reports a failure. HAOS tool results are JSON
# envelopes ({"output", "exit_code", "error"}) or plain "Error executing tool ..."
# strings; json_valid() guards malformed rows (verified against real stores).
_ERROR_FLAG_SQL = """
    CASE WHEN role = 'tool' AND (
        content LIKE 'Error executing tool%'
        OR (json_valid(content) AND (
            COALESCE(json_extract(content, '$.exit_code'), 0) != 0
            OR (json_extract(content, '$.error') IS NOT NULL
                AND json_extract(content, '$.error') != '')
        ))
    ) THEN 1 ELSE 0 END AS is_error
"""


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _words(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


def _approx_tokens(chars: int) -> int:
    return round(chars / 4) if chars else 0


def _parse_call_inputs(tool_calls_raw: Optional[str]) -> Dict[str, tuple]:
    """Map tool_call_id -> (name, args-prefix) from an assistant row's tool_calls JSON.

    Unparseable/truncated payloads are skipped silently: churn keys then fall back
    to the tool row's own ``tool_name`` (still measured, just coarser).
    """
    if not tool_calls_raw:
        return {}
    try:
        calls = json.loads(tool_calls_raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(calls, list):
        return {}
    out: Dict[str, tuple] = {}
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        cid = call.get("id") or call.get("call_id")
        if not cid:
            continue
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args, sort_keys=True, default=str) if args is not None else ""
        out[str(cid)] = (str(fn.get("name") or call.get("name") or ""), args[:200])
    return out


def _finding(kind: str, session: Dict[str, Any], count: int, chars: int,
             first_ts: Any, last_ts: Any, evidence: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "session_id": session["id"],
        "model": session.get("model") or "",
        "count": count,
        "measured_chars": chars,
        "approx_tokens": _approx_tokens(chars),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "evidence": evidence[:160],
    }


def _scan_session(session: Dict[str, Any], rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Run the three transcript detectors over one session's ordered rows."""
    findings: List[Dict[str, Any]] = []

    # ── looping: consecutive similar USER messages ──────────────────────────
    streak: List[sqlite3.Row] = []
    prev_words: set = set()

    def _close_loop_streak() -> None:
        nonlocal streak, prev_words
        if len(streak) >= LOOP_MIN_STREAK:
            chars = sum(int(r["full_len"] or 0) for r in streak)
            findings.append(_finding(
                "user_loop", session, len(streak), chars,
                streak[0]["timestamp"], streak[-1]["timestamp"],
                _human_user_text(streak[0]["content"] or "")[:CONTENT_SCAN_CHARS],
            ))
        streak, prev_words = [], set()

    # ── tool cascade + retry churn over the ordered TOOL rows ───────────────
    call_inputs: Dict[str, tuple] = {}
    tool_rows: List[sqlite3.Row] = []
    for r in rows:
        if r["role"] == "assistant" and r["tool_calls"]:
            call_inputs.update(_parse_call_inputs(r["tool_calls"]))
        if r["role"] == "user":
            if not _is_human_user_row(r):
                continue
            w = _words(_human_user_text(r["content"] or ""))
            if streak and _jaccard(prev_words, w) > LOOP_SIMILARITY:
                streak.append(r)
            else:
                _close_loop_streak()
                streak = [r]
            prev_words = w
        elif r["role"] == "tool":
            tool_rows.append(r)
    _close_loop_streak()

    cascade_streak: List[sqlite3.Row] = []

    def _close_cascade() -> None:
        nonlocal cascade_streak
        if len(cascade_streak) >= CASCADE_MIN_ERRORS:
            chars = sum(int(r["full_len"] or 0) for r in cascade_streak)
            findings.append(_finding(
                "tool_cascade", session, len(cascade_streak), chars,
                cascade_streak[0]["timestamp"], cascade_streak[-1]["timestamp"],
                f"{cascade_streak[0]['tool_name'] or '?'}: "
                + (cascade_streak[0]["content"] or "")[:CONTENT_SCAN_CHARS - 20],
            ))
        cascade_streak = []

    churn_groups: Dict[tuple, List[sqlite3.Row]] = {}
    for r in tool_rows:
        if r["is_error"]:
            cascade_streak.append(r)
            cid = str(r["tool_call_id"] or "")
            name, args_prefix = call_inputs.get(cid, (r["tool_name"] or "", ""))
            # Conservative: without a real tool name we cannot tell distinct
            # failing calls apart, so the row only feeds the cascade detector.
            if name:
                churn_groups.setdefault((name, args_prefix), []).append(r)
        else:
            _close_cascade()
    _close_cascade()

    for (name, args_prefix), group in churn_groups.items():
        if len(group) >= CHURN_MIN_REPEATS:
            chars = sum(int(r["full_len"] or 0) for r in group)
            findings.append(_finding(
                "retry_churn", session, len(group), chars,
                group[0]["timestamp"], group[-1]["timestamp"],
                f"{name}({args_prefix[:80]})",
            ))
    return findings


def _live_context_occupancy(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Occupancy needs per-message token_count; report null-with-reason when absent."""
    populated = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE active = 1 AND compacted = 0"
        " AND token_count IS NOT NULL AND token_count > 0"
    ).fetchone()[0]
    if not populated:
        return {
            "available": False,
            "reason": "per-message token_count is not populated in this store; "
                      "sessions only carry lifetime totals, which are not a context-window measure",
        }
    rows = conn.execute(
        "SELECT session_id, SUM(token_count) AS live_tokens FROM messages"
        " WHERE active = 1 AND compacted = 0 GROUP BY session_id"
        " ORDER BY live_tokens DESC LIMIT 20"
    ).fetchall()
    return {
        "available": True,
        "top_sessions": [
            {"session_id": r["session_id"], "live_context_tokens": r["live_tokens"]}
            for r in rows
        ],
    }


def _cache_hit(conn: sqlite3.Connection, cutoff: float) -> Dict[str, Any]:
    """cache_read / (fresh_input + cache_read + cache_write) — pure SQL, measured."""
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(model, ''), 'unknown') AS model,
               SUM(COALESCE(input_tokens, 0))       AS fresh_input,
               SUM(COALESCE(cache_read_tokens, 0))  AS cache_read,
               SUM(COALESCE(cache_write_tokens, 0)) AS cache_write
        FROM sessions WHERE started_at > ?
        GROUP BY model ORDER BY (fresh_input + cache_read + cache_write) DESC
        """,
        (cutoff,),
    ).fetchall()

    def _ratio(fresh: int, read: int, write: int) -> Optional[float]:
        denom = fresh + read + write
        return round(read / denom, 4) if denom else None

    by_model = [
        {"model": r["model"], "fresh_input": r["fresh_input"], "cache_read": r["cache_read"],
         "cache_write": r["cache_write"],
         "hit_ratio": _ratio(r["fresh_input"] or 0, r["cache_read"] or 0, r["cache_write"] or 0)}
        for r in rows
    ]
    fresh = sum(r["fresh_input"] or 0 for r in rows)
    read = sum(r["cache_read"] or 0 for r in rows)
    write = sum(r["cache_write"] or 0 for r in rows)
    return {"by_model": by_model, "overall": {
        "fresh_input": fresh, "cache_read": read, "cache_write": write,
        "hit_ratio": _ratio(fresh, read, write),
    }}


def scan_waste(conn: sqlite3.Connection, *, cutoff: float,
               max_sessions: int = 100) -> Dict[str, Any]:
    """Bounded waste scan over sessions active after ``cutoff``.

    ``conn`` must already use ``sqlite3.Row`` as row factory (the SessionDB
    connection does). Returns findings, per-kind totals, cache-hit analytics and
    the occupancy availability report. Read-only; never mutates the store.
    """
    sessions = conn.execute(
        """
        SELECT id, model, input_tokens, output_tokens, message_count, tool_call_count
        FROM sessions WHERE started_at > ?
        ORDER BY (COALESCE(input_tokens,0) + COALESCE(output_tokens,0)) DESC
        LIMIT ?
        """,
        (cutoff, max_sessions),
    ).fetchall()

    findings: List[Dict[str, Any]] = []
    for s in sessions:
        rows = conn.execute(
            f"""
            SELECT role, substr(content, 1, {CONTENT_SCAN_CHARS}) AS content,
                   length(content) AS full_len, tool_name, tool_call_id,
                   substr(tool_calls, 1, {_TOOL_CALLS_SCAN_CHARS}) AS tool_calls,
                   timestamp, COALESCE(_compressed_summary, 0) AS _compressed_summary,
                   display_kind, {_ERROR_FLAG_SQL}
            FROM messages WHERE session_id = ?
            ORDER BY timestamp ASC, id ASC LIMIT {_MAX_ROWS_PER_SESSION}
            """,
            (s["id"],),
        ).fetchall()
        findings.extend(_scan_session(dict(s), rows))

    totals: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        t = totals.setdefault(f["kind"], {"occurrences": 0, "sessions": set(), "measured_chars": 0})
        t["occurrences"] += 1
        t["sessions"].add(f["session_id"])
        t["measured_chars"] += f["measured_chars"]
    for kind, t in totals.items():
        t["sessions"] = len(t["sessions"])
        t["approx_tokens"] = _approx_tokens(t["measured_chars"])

    findings.sort(key=lambda f: f["measured_chars"], reverse=True)
    return {
        "findings": findings,
        "totals": totals,
        "sessions_scanned": len(sessions),
        "truncated": len(sessions) >= max_sessions,
        "cache_hit": _cache_hit(conn, cutoff),
        "live_context_occupancy": _live_context_occupancy(conn),
    }
