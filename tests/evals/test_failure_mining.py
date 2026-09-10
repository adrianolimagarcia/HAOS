"""The failure miner runs end-to-end against a REAL synthetic state.db and its cases feed the eval runner.

Two contracts, both behavioural:

  1. every signal is detected from the trajectory itself (never inferred from disk), each candidate
     carries auditable evidence — db path, session, message ids, excerpt — the mining is deterministic,
     and the store is only ever opened read-only;
  2. a mined case is runnable by the existing harness: ``to_eval_suite`` yields a suite the
     ``EvalRunner`` in ``hermes/platform/evals/runner.py`` executes, and ``to_golden_tasks`` yields real
     ``GoldenTaskSpec``s whose fields agree with the case.

The database is built inside the temp ``HERMES_HOME`` the autouse fixture installs, with the repo's own
``SCHEMA_SQL``, and mining is invoked with NO ``--db`` so the production path resolution
(``hermes_constants.get_hermes_home()``) is exercised rather than mocked.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from evals.postmortem.forensics import eval_mining
from hermes_constants import get_hermes_home
from hermes_state_common import SCHEMA_SQL

T0 = 1_800_000_000.0
SIGNALS = ("test_failure_ignored", "repeated_user_correction", "stale_context",
           "repeat_tool_call_no_progress", "hidden_retry_loop", "premature_stop",
           "unrelated_files_touched")


def _tool_calls(call_id: str, name: str, args: dict) -> str:
    return json.dumps([{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}])


class _Seed:
    """Thin writer over the real schema: assistant turns, their tool results, user turns."""

    def __init__(self, conn: sqlite3.Connection, session_id: str, start: float = T0):
        self.conn, self.sid, self.ts = conn, session_id, start

    def session(self, parent=None, source="cli"):
        self.conn.execute(
            "INSERT INTO sessions (id, source, parent_session_id, started_at, ended_at, end_reason, cwd, model,"
            " api_call_count, input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, reasoning_tokens,"
            " estimated_cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.sid, source, parent, self.ts, self.ts + 3600, "cli_close", "/repo", "test-model",
             8, 10_000, 50_000, 5_000, 4_000, 500, 1.25))
        return self

    def user(self, text: str):
        self.ts += 1
        self.conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                          (self.sid, "user", text, self.ts))
        return self

    def call(self, name: str, args: dict, result: object, *, gap: float = 1.0, text: str = "", call_id: str = "c"):
        self.ts += gap
        self.conn.execute("INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?,?,?,?,?)",
                          (self.sid, "assistant", text, _tool_calls(call_id, name, args), self.ts))
        self.ts += 0.5
        self.conn.execute("INSERT INTO messages (session_id, role, content, tool_name, tool_call_id, timestamp)"
                          " VALUES (?,?,?,?,?,?)",
                          (self.sid, "tool", result if isinstance(result, str) else json.dumps(result),
                           name, call_id, self.ts))
        return self

    def final(self, text: str):
        self.ts += 1
        self.conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                          (self.sid, "assistant", text, self.ts))
        return self


def _seed_store() -> Path:
    """One CLI session and one subagent session carrying all seven signals in real trajectory shapes."""
    db = get_hermes_home() / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    root = _Seed(conn, "root").session()
    root.user("Fix the parser bug in /repo/src/parser.py")
    root.call("read_file", {"path": "/repo/src/parser.py", "offset": 0, "limit": 200},
              {"content": "1|def parse(s):\n2|    return s.split(',')"}, call_id="k1")
    # same call, same result, three times: no progress
    for i in range(3):
        root.call("terminal", {"command": "ls -la /repo/build"},
                  {"output": "total 4\n-rw-r--r-- 1 u u 12 build.txt", "exit_code": 0, "error": None},
                  gap=2.0, call_id=f"r{i}")
    # three failures with DIFFERENT calls, spread over 80 s, never mentioned to the user
    root.call("terminal", {"command": "curl http://127.0.0.1:8080/health"},
              {"output": "curl: (7) Failed to connect to 127.0.0.1 port 8080", "exit_code": 7, "error": None},
              gap=2.0, call_id="h0")
    root.call("terminal", {"command": "curl --retry 5 http://127.0.0.1:8080/health"},
              {"output": "curl: (7) Failed to connect to 127.0.0.1 port 8080", "exit_code": 7, "error": None},
              gap=40.0, call_id="h1")
    root.call("terminal", {"command": "curl -k http://127.0.0.1:8080/health"},
              {"output": "curl: (7) Failed to connect to 127.0.0.1 port 8080", "exit_code": 7, "error": None},
              gap=40.0, call_id="h2")
    root.user("no, that's not what I asked for")
    root.call("write_file", {"path": "/opt/elsewhere/scratch.txt", "content": "hello"},
              {"success": True, "path": "/opt/elsewhere/scratch.txt", "verified": True}, call_id="u0")
    root.user("you did not fix it, it's still wrong")
    root.call("patch", {"path": "/repo/src/parser.py", "old_string": "return s.split(',')", "new_string": "return s.split(';')"},
              {"success": False, "error": "Could not find a match for old_string in the file\n\nDid you mean one of these sections?"},
              call_id="s0")
    root.call("terminal", {"command": "python -m pytest tests/test_parser.py"},
              {"output": "FAILED tests/test_parser.py::test_parse - AssertionError\n1 failed, 4 passed in 0.3s",
               "exit_code": 1, "error": None}, call_id="t0")
    root.final("The remaining checklist items are not yet finished; next step is the parser fix.")
    # a resumed child that writes a file its ancestor read and it never read itself, then gives up
    child = _Seed(conn, "child", start=T0 + 100).session(parent="root", source="subagent")
    child.user("Audit /repo/src/parser.py")
    child.call("write_file", {"path": "/repo/src/parser.py", "content": "1|def parse(s):\n2|    return s.split(';')"},
               {"success": True, "path": "/repo/src/parser.py", "verified": True}, call_id="w0")
    child.final("I could not complete the audit; the remaining files are not yet reviewed.")
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def mined(tmp_path):
    db = _seed_store()
    out = tmp_path / "out"
    before = (db.stat().st_mtime_ns, db.stat().st_size)
    assert eval_mining.main(["--out", str(out)]) == 0, "the CLI must mine $HERMES_HOME/state.db with no --db"
    after = (db.stat().st_mtime_ns, db.stat().st_size)
    return db, out, before, after


def test_every_signal_is_mined_from_the_trajectory_with_evidence(mined):
    db, out, before, after = mined
    assert before == after, "mining must not write to the store (mtime/size unchanged)"
    report = json.loads((out / "eval_candidates.json").read_text(encoding="utf-8"))
    cases = report["candidates"]
    assert {c["signal"] for c in cases} >= set(SIGNALS), "a seeded pattern went undetected"
    row_ids = _message_ids(db)
    for case in cases:
        assert case["evidence"], f"{case['case_id']} carries no evidence"
        assert case["occurrences"] == case["evidence_total"] >= len(case["evidence"])
        assert case["prompt"] and case["deterministic_assertions"] and case["priority"] in (1, 2, 3)
        for ev in case["evidence"]:
            assert ev["db_path"] == str(db) and ev["session_id"] in ("root", "child")
            assert ev["message_ids"] and ev["excerpt"].strip() and ev["detail"]
            assert set(ev["message_ids"]) <= row_ids
    # the evidence points at the trajectory itself, not at a re-derivation of it
    stale_edit = next(c for c in cases if c["signal"] == "stale_context" and c["sessions"] == ["root"])
    assert [ev["detail"]["class"] for ev in stale_edit["evidence"]] == ["stale_edit"]
    assert "old_string" in stale_edit["evidence"][0]["excerpt"].lower()
    assert stale_edit["evidence"][0]["detail"]["paths"] == ["/repo/src/parser.py"]
    unverified = next(c for c in cases if c["signal"] == "stale_context" and c["sessions"] == ["child"])
    detail = unverified["evidence"][0]["detail"]
    assert (detail["class"], detail["path"], detail["ancestor_session"]) == \
           ("unverified_write_after_resume", "/repo/src/parser.py", "root")
    assert detail["ancestor_read_message_id"] in row_ids and detail["age_s"] > 0
    # determinism: a second mining pass over the same store yields the same cases in the same order
    again = eval_mining.mine(eval_mining.open_store(db, out=str(out)))
    assert [(c.case_id, c.occurrences, c.evidence[0].message_ids) for c in again] == \
           [(c["case_id"], c["occurrences"], tuple(c["evidence"][0]["message_ids"])) for c in cases]


def _message_ids(db: Path) -> set:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {row[0] for row in conn.execute("SELECT id FROM messages")}
    finally:
        conn.close()


def test_cases_run_through_the_existing_eval_harness(mined):
    from hermes.platform.evals.golden_tasks import GoldenTaskCategory, GoldenTaskSpec
    from hermes.platform.evals.runner import EvalRunner, EvalSuite

    db, out, _before, _after = mined
    cases = eval_mining.mine(eval_mining.open_store(db, out=str(out)))
    assert cases
    suite = eval_mining.to_eval_suite(cases)
    assert isinstance(suite, EvalSuite) and len(suite.cases) == len(cases)
    seen = []

    def run_fn(case):
        seen.append(case.id)
        return {"passed": True, "score": 1.0, "meta": {"signal": case.tags[0]}}

    result = EvalRunner().run_suite(suite, run_fn, label="mined")
    assert seen == [c.case_id for c in cases]
    assert result.pass_rate == 1.0 and [o["case_id"] for o in result.outcomes] == seen
    specs = eval_mining.to_golden_tasks(cases)
    assert len(specs) == len(cases)
    for spec, case in zip(specs, cases):
        assert isinstance(spec, GoldenTaskSpec) and isinstance(spec.category, GoldenTaskCategory)
        assert (spec.id, spec.name, spec.prompt) == (case.case_id, case.title, case.prompt)
        assert spec.deterministic_assertions == list(case.deterministic_assertions)
        assert spec.expected_artifacts == list(case.expected_artifacts)
        assert spec.max_tokens_budget >= 1024
