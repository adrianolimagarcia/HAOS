"""Lane 6: eval-case mining — turn failure patterns OBSERVED in recorded trajectories into eval cases.

    python -m evals.postmortem.forensics.eval_mining [--db state_copy.db] [--root <session_id>]

Where the other lanes COUNT what happened, this one asks what should be REPLAYED. Every candidate case
carries the evidence that produced it — the db path, the session, the message ids, the counts and a
minimal excerpt — so a proposal can be audited instead of believed. Nothing here calls a model, nothing
touches the network, and the database is opened ``mode=ro`` exactly like ``common.Run.open``, so the
store is never written.

The population is the WHOLE store (``--root`` narrows to one run tree, with the same semantics the other
lanes use): a pattern is only worth an eval case when it recurs, and the run tree of one incident cannot
show recurrence. Compression-rollover continuations are excluded, as everywhere else, so a conversation
split by compression is not counted twice.

Signals (one detector per signal in ``DETECTORS``; ``priority`` 1 = correctness, 2 = waste, 3 = hygiene):

    signal                          pri  what the recorded trajectory proves
    test_failure_ignored             1   a test run reported failures and NOTHING afterwards answered it
    repeated_user_correction         1   the user had to correct the same thing twice after the agent acted
    stale_context                    1   a write/edit was built from a copy of the file the agent no
                                         longer had (old_string gone), or a resumed session edited a file
                                         it never read in that session
    repeat_tool_call_no_progress     2   the same call with the same result N times, nothing changed between
    hidden_retry_loop                2   a run of failures retried with varying calls, never surfaced
    premature_stop                   2   the last turn says the work is unfinished and then stops
    unrelated_files_touched          3   writes outside the session's repo/cwd/task scope

Result interpretation is deliberately ASYMMETRIC: the outcome envelope (``error``, ``success``,
``exit_code``, ``status``) is what gets scanned, never ``read_file``/``search_files`` payload — source
code that contains the word "failed" is not a failed command. That distinction is the difference between
a usable lane and a false-positive generator (see ``_interpret``).

Outputs, all evidence-bearing, land in ``postmortem_out/eval_candidates.json``. ``--golden <path>`` and
``--suite <path>`` additionally write the two shapes the existing harness already understands —
``GoldenTaskSpec``-shaped JSON for ``hermes.platform.evals.golden_tasks`` and an ``EvalSuite`` for
``hermes.platform.evals.runner`` — so a mined case is runnable, not a parallel format::

    from evals.postmortem.forensics.eval_mining import mine, open_store, to_eval_suite
    from hermes.platform.evals.runner import EvalRunner
    cases = mine(open_store())                       # in memory, no files written
    result = EvalRunner().run_suite(to_eval_suite(cases), run_fn)

Prerequisites: none beyond a Hermes ``state.db`` (``--db`` defaults to
``get_hermes_home()/state.db``; the other lanes' README still recommends mining a COPY of a live store).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from evals.postmortem.forensics.common import USAGE_COLS, Run

# ── tool taxonomy ─────────────────────────────────────────────────────────────────────────────────
# Command tools: their ``output`` IS the outcome (a failing test run, a dead process).
_CMD_TOOLS = frozenset({"terminal", "process_manage", "execute_code", "code_execution", "bash", "shell"})
# Data tools: their payload is CONTENT, never an outcome. Scanning it for "failed"/"not found" turned a
# read of the test suite into a fake failure 27 times in the reference store.
_DATA_TOOLS = frozenset({"read_file", "search_files", "list_files", "glob", "grep", "grep_files",
                         "skill_view", "tool_describe", "web_search", "web_fetch", "memory_search",
                         "todo_list", "todo_write"})
# Mutation tools: they change files, so they carry paths we can police and re-reads we can expect.
_MUTATION_TOOLS = frozenset({"write_file", "patch", "edit_file", "create_file", "str_replace_editor",
                             "apply_patch", "file_write"})

# Failure classes, first match wins. Each name is what the evidence reports, so a case says WHY the
# call was a failure instead of "it looked broken".
_FAILURE_CLASSES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("hardline_block", re.compile(r"BLOCKED \(hardline\)|malformed executable payload|Foreground (?:timeout|command) use", re.I)),
    ("tool_timeout", re.compile(r"timed out after|thread did not return|deadline exceeded", re.I)),
    ("stale_edit", re.compile(r"Could not find a match for old_string|old_string not found|String to replace "
                              r"not found|no match(?:es)? found for|file has changed|has been modified since|"
                              r"content mismatch|unexpected content", re.I)),
    ("missing_path", re.compile(r"No such file or directory|FileNotFoundError|does not exist\b|no such file", re.I)),
    ("permission", re.compile(r"Permission denied|EACCES|read-only file system", re.I)),
    ("command_failed", re.compile(r"command not found|connection refused|failed to connect|no such host|"
                                  r"unable to resolve host|segmentation fault|core dumped", re.I)),
    ("tool_error", re.compile(r"^Error executing tool|^Tool error|^Error:", re.M)),
)
_TOOL_ERROR_PREFIX = "Error executing tool"

# ── signal patterns ───────────────────────────────────────────────────────────────────────────────
_TEST_RUNNERS = frozenset({"pytest", "run_tests.sh", "vitest", "jest", "mocha", "ctest", "tox", "nose2"})
_TEST_RUNNER_PAIRS = {"cargo": ("test",), "go": ("test",), "dotnet": ("test",), "mvn": ("test",),
                      "gradle": ("test",), "npm": ("test",), "yarn": ("test",), "pnpm": ("test",),
                      "make": ("test",), "node": ("test", "--test")}
_LAUNCHERS = frozenset({"sudo", "time", "env", "uv", "npx", "poetry", "hatch", "rye", "bash", "sh", "zsh"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A red test run. ``\b[1-9]\d* failed\b`` deliberately rejects the "0 failed" of a green run.
_TEST_FAILURE = re.compile(r"(?m)^\s*(?:FAILED|FAIL|not ok|✗|×)\s|\btest result: FAILED\b|\b[1-9]\d* failed\b"
                           r"|\bfailures:\s*[1-9]|\bAssertionError\b|\bpanicked at\b|assertion .{0,40} failed", re.I)
_FAIL_LINE = re.compile(r"(?m)^\s*(?:FAILED|FAIL|not ok|✗|×)\s+([^\n]{1,160})")
# "Nothing answered it": any acknowledgement, edit or re-run of the red suite.
_DISCLOSURE = re.compile(r"\b(?:fail(?:ed|ing|ure|s)?|error(?:ed|s)?|timed out|timeout|could not|couldn't|"
                         r"cannot|can't|unable|retry|retrying|blocked|limitation|problem|broken|red test|"
                         r"falh\w*|erro\w*|não consegui|nao consegui|bloqueado|quebrad\w*)\b", re.I)
_STRONG_UNFINISHED = re.compile(r"\b(?:unable to|not able to|cannot proceed|can't proceed|could not (?:complete|"
                               r"finish|proceed|continue)|couldn't (?:complete|finish|proceed|continue)|gave up|"
                               r"abandoning (?:this|the)|não (?:foi possível|consegui|consigo)|impossível continuar)\b", re.I)
_WEAK_UNFINISHED = re.compile(r"\b(?:remaining|still (?:to be|needs|need|missing)|not yet|haven't|have not|incomplete|"
                             r"unfinished|next step|for now|stopping here|i'll stop|leaving (?:this|it)|pendente|"
                             r"próximos passos|falta(?:m)? (?:ainda|implementar|fazer|corrigir))\b", re.I)
_CORRECTION = re.compile(r"^(?:no|não|nao)\b[,\s]|\b(?:that's not|that is not|not what i|i said|i already|i told you|"
                         r"why did you|you didn't|you did not|still (?:not|failing|broken|wrong)|do it again|"
                         r"try again|redo|wrong|incorrect|errado|não é isso|nao e isso|eu disse|ainda não|"
                         r"ainda nao|não foi isso|refaz|de novo)\b", re.I)
_PATH_TOKEN = re.compile(r"(?<![\w.@-])((?:~|\.{1,2})?/[\w.@%+-]+(?:/[\w.@%+-]+)+)")
_TASK_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{4,}")
_TASK_STOPWORDS = frozenset({"https", "github", "localmente", "analise", "analisar", "projeto", "arquivo",
                             "arquivos", "codigo", "testes", "should", "there", "where", "which", "about",
                             "using", "please", "quando", "porque", "sobre", "para", "with", "that", "this"})
_EXCERPT_LIMIT = 240


# ── observed model ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Call:
    """One recorded tool call with the OUTCOME of its result (never the data it returned)."""
    message_id: int
    ts: float
    tool: str
    args: Dict[str, Any]
    args_text: str
    signature: str
    result_id: Optional[int]
    outcome: str            # failure/command output text, "" when the envelope carried none
    payload: str            # the data the tool returned (content/output/diff), for fingerprints
    ok: bool
    failure_class: str      # "" when ok
    paths: Tuple[str, ...]

    @property
    def command(self) -> str:
        return str(self.args.get("command") or self.args_text or "")

    @property
    def is_mutation(self) -> bool:
        return self.tool in _MUTATION_TOOLS


@dataclass(frozen=True)
class SessionView:
    """Everything the detectors need from one session, extracted once."""
    session_id: str
    depth: int
    parent: Optional[str]
    end_reason: str
    started_at: float
    ended_at: float
    task_text: str
    inherited_task_text: str
    calls: Tuple[Call, ...]
    assistant_texts: Tuple[Tuple[int, float, str], ...]   # (message_id, ts, text) of assistant prose
    user_messages: Tuple[Tuple[int, float, str, bool], ...]  # (id, ts, text, previous turn used tools)
    last_assistant_id: Optional[int]
    last_assistant_text: str
    last_assistant_had_calls: bool


@dataclass(frozen=True)
class Hit:
    """One occurrence of a signal, with the trajectory slice that proves it."""
    session_id: str
    ts: float
    message_ids: Tuple[int, ...]
    detail: Dict[str, Any]
    excerpt: str


@dataclass(frozen=True)
class CaseEvidence:
    db_path: str
    session_id: str
    message_ids: Tuple[int, ...]
    timestamp: float
    detail: Dict[str, Any]
    excerpt: str


@dataclass(frozen=True)
class MinedEvalCase:
    """A candidate eval case: the prompt, the assertions, and the evidence it was mined from."""
    case_id: str
    signal: str
    title: str
    priority: int
    category: str
    occurrences: int
    sessions: Tuple[str, ...]
    evidence: Tuple[CaseEvidence, ...]
    evidence_total: int
    prompt: str
    expected_artifacts: Tuple[str, ...]
    deterministic_assertions: Tuple[str, ...]
    max_tokens_budget: int
    description: str


@dataclass(frozen=True)
class MineOptions:
    min_repeats: int = 3          # identical call+result before "no progress" is claimed
    min_retries: int = 3          # failing calls in a row before "retry loop" is claimed
    retry_window_s: float = 300.0  # a gap this long ends the retry run
    retry_min_span_s: float = 30.0
    max_evidence: int = 5         # evidence entries kept per case (evidence_total keeps the real count)
    excerpt_chars: int = _EXCERPT_LIMIT
    signals: Tuple[str, ...] = ()  # empty = every signal


@dataclass
class MineContext:
    run: Run
    views: Dict[str, SessionView]
    options: MineOptions


DetectorFn = Callable[[SessionView, MineContext], List[Hit]]


@dataclass(frozen=True)
class Detector:
    signal: str
    title: str
    priority: int
    category: str
    artifacts: Tuple[str, ...]
    assertions: Tuple[str, ...]
    prompt_task: str              # one line: what the replayed task must do
    find: DetectorFn


# ── result interpretation ─────────────────────────────────────────────────────────────────────────
def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _json_obj(raw: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _classify(outcome: str) -> str:
    for name, pattern in _FAILURE_CLASSES:
        if pattern.search(outcome):
            return name
    return ""


def _interpret(tool: str, raw: str) -> Tuple[bool, str, str]:
    """``(ok, outcome, payload)`` for one tool result.

    The envelope decides, not the text: ``error``/``success``/``exit_code``/``status`` are the harness's
    own verdict, while a ``read_file``/``search_files`` payload is arbitrary user content — source code
    containing the word "failed" is not a failed command. Only a plain non-JSON string from a non-data
    tool is scanned for failure classes (that is how ``Error executing tool '<name>': timed out after
    420.0s`` and the hardline refusals arrive).
    """
    obj = _json_obj(raw)
    if obj is None:
        text = _normalize(raw)
        if tool in _DATA_TOOLS:
            return True, "", text
        failed = _classify(text)
        if not failed and text.startswith(_TOOL_ERROR_PREFIX):
            failed = "tool_error"
        return (not failed), (text if failed else ""), text
    error = obj.get("error")
    outcome = _normalize(error) if error not in (None, "", "None") else ""
    ok = not outcome
    if obj.get("success") is False:
        ok = False
        outcome = outcome or _normalize(obj.get("message") or "success: false")
    if any(obj.get(key) is False for key in ("ok", "is_error")) or obj.get("failed") is True:
        ok = False
        outcome = outcome or _normalize(obj.get("message") or obj.get("detail") or "reported as failed")
    if obj.get("error_type"):
        ok = False
        outcome = outcome or _normalize(obj.get("error_type"))
    status = str(obj.get("status") or "").lower()
    if status in ("not_found", "failed", "error", "timeout", "dead", "killed"):
        ok = False
        outcome = outcome or f"status={status}"
    if tool in _CMD_TOOLS:
        code = obj.get("exit_code")
        meaning = str(obj.get("exit_code_meaning") or "").lower()
        if code not in (None, 0) and "not an error" not in meaning:
            ok = False
            outcome = outcome or _normalize(obj.get("output")) or f"exit_code={code}"
        elif not outcome:
            outcome = _normalize(obj.get("output"))
    payload = _normalize(obj.get("content") or obj.get("output") or obj.get("diff") or raw)
    if not ok and not outcome:
        outcome = payload
    return ok, outcome, payload


def _canonical(value: Any) -> Any:
    """Whitespace-insensitive view of the arguments: ``cargo  test`` is the same call as ``cargo test``."""
    if isinstance(value, str):
        return _normalize(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _paths_in(tool: str, args: Dict[str, Any]) -> Tuple[str, ...]:
    """Every path the call names: its path arguments, plus path-shaped tokens of a shell command.

    Only command arguments are tokenized — a ``new_string`` full of code is not a write target, and prose
    in a delegated goal is not a path.
    """
    found: List[str] = []
    for key in ("path", "file_path", "filepath", "filename", "target", "file", "workdir", "dir", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    if tool in _CMD_TOOLS:
        for key in ("command", "script", "code", "cmd", "shell"):
            value = args.get(key)
            if isinstance(value, str):
                found.extend(m.group(1) for m in _PATH_TOKEN.finditer(value))
    out: List[str] = []
    for path in found:
        norm = os.path.normpath(path)
        if norm not in out:
            out.append(norm)
    return tuple(out)


def _is_a_test_run(call: Call) -> bool:
    """True only for a COMMAND tool whose shell segment actually invokes a test runner.

    Both halves matter: ``cat tests/x.py`` is not a test run, and a ``tool_call`` wrapper whose JSON
    arguments happen to contain the word "test" is not a shell command at all.
    """
    return call.tool in _CMD_TOOLS and _is_test_run(call.command)


def _is_test_run(command: str) -> bool:
    """True when a shell segment INVOKES a test runner (not when a path merely contains "test")."""
    for segment in re.split(r"[;&|]+", command or ""):
        tokens = [t for t in segment.strip().split() if t]
        while tokens and _ASSIGNMENT.match(tokens[0]):
            tokens.pop(0)
        while tokens and (tokens[0] in _LAUNCHERS or os.path.basename(tokens[0]) in _LAUNCHERS):
            launcher = tokens.pop(0)
            if launcher in ("uv", "npx", "poetry") and tokens and tokens[0] == "run":
                tokens.pop(0)
            if tokens and tokens[0].startswith("-"):
                tokens.pop(0)
        if not tokens:
            continue
        program = os.path.basename(tokens[0])
        if program in _TEST_RUNNERS:
            return True
        if program in ("python", "python3") and len(tokens) > 2 and tokens[1] == "-m" and tokens[2] in _TEST_RUNNERS:
            return True
        if program in ("bash", "sh", "zsh") and len(tokens) > 1 and os.path.basename(tokens[1]) in _TEST_RUNNERS:
            return True
        words = [os.path.basename(t) for t in tokens[1:4]]
        if any(w in _TEST_RUNNER_PAIRS.get(program, ()) or w.startswith("test") for w in words):
            return True
    return False


def _excerpt(text: str, limit: int) -> str:
    flat = _normalize(text)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── session extraction ────────────────────────────────────────────────────────────────────────────
def _session_depth(sessions: Dict[str, Dict[str, Any]], sid: str, rollover: Iterable[str]) -> int:
    depth, seen, cur = 0, {sid}, sessions.get(sid, {}).get("parent_session_id")
    while cur and cur in sessions and cur not in seen:
        if cur not in rollover:
            depth += 1
        seen.add(cur)
        cur = sessions[cur].get("parent_session_id")
    return depth


def _ancestors(sessions: Dict[str, Dict[str, Any]], sid: str) -> List[str]:
    out, seen, cur = [], {sid}, sessions.get(sid, {}).get("parent_session_id")
    while cur and cur in sessions and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = sessions[cur].get("parent_session_id")
    return out


def _view(run: Run, sid: str) -> SessionView:
    """One session's extraction: ordered records, user turns, the trailing turn and the task text."""
    rows = run.messages(sid, "id, role, content, tool_calls, tool_name, tool_call_id, timestamp")
    results = {r["tool_call_id"]: r for r in rows if r["role"] == "tool" and r.get("tool_call_id")}
    # Legacy/scan-built stores may not carry tool_call_id: pair those results positionally instead.
    unkeyed = iter([r for r in rows if r["role"] == "tool" and not r.get("tool_call_id")])
    calls: List[Call] = []
    assistant_texts: List[Tuple[int, float, str]] = []
    user_messages: List[Tuple[int, float, str, bool]] = []
    last_assistant_id: Optional[int] = None
    last_text = ""
    last_had_calls = False
    for row in rows:
        role = row["role"]
        if role == "assistant":
            last_assistant_id = int(row["id"])
            last_text = row.get("content") or ""
            last_had_calls = bool(row.get("tool_calls"))
            if last_text.strip():
                assistant_texts.append((int(row["id"]), float(row.get("timestamp") or 0), last_text))
            for entry in _tool_calls(row.get("tool_calls")):
                name = entry["name"]
                args = entry["args"]
                args_text = entry["args_text"]
                signature = f"{name} {json.dumps(_canonical(args or args_text), sort_keys=True, ensure_ascii=False)}"
                result = results.get(entry["id"]) if entry["id"] else None
                if result is None:
                    result = next(unkeyed, None)
                ok, outcome, payload = True, "", ""
                result_id = None
                if result is not None:
                    result_id = int(result["id"])
                    ok, outcome, payload = _interpret(name, result.get("content") or "")
                calls.append(Call(message_id=int(row["id"]), ts=float(row.get("timestamp") or 0), tool=name, args=args,
                                  args_text=args_text, signature=signature, result_id=result_id, outcome=outcome,
                                  payload=payload, ok=ok, failure_class=_classify(outcome) if not ok else "",
                                  paths=_paths_in(name, args)))
        elif role == "user":
            user_messages.append((int(row["id"]), float(row.get("timestamp") or 0), row.get("content") or "",
                                  bool(last_had_calls)))
    calls.sort(key=lambda c: (c.ts, c.message_id))
    session = run.sessions.get(sid, {})
    task_text = next((t for _i, _ts, t, _u in user_messages if t.strip() and not t.lstrip().startswith("[")), "")
    # A resumed session inherits the task text of the sessions it continues: "unrelated" paths are
    # judged against the whole inherited task, not just this session's own first user message.
    inherited_parts = [task_text]
    for ancestor in _ancestors(run.sessions, sid):
        first_user = next((m["content"] for m in run.messages(ancestor, "role, content")
                           if m["role"] == "user" and (m.get("content") or "").strip()), None)
        if first_user:
            inherited_parts.append(first_user)
    return SessionView(session_id=sid, depth=int(run.depth.get(sid, 0)), parent=session.get("parent_session_id"),
                       end_reason=str(session.get("end_reason") or ""), started_at=float(session.get("started_at") or 0),
                       ended_at=float(session.get("ended_at") or 0), task_text=_normalize(task_text),
                       inherited_task_text=_normalize(" ".join(inherited_parts)), calls=tuple(calls),
                       assistant_texts=tuple(assistant_texts), user_messages=tuple(user_messages),
                       last_assistant_id=last_assistant_id, last_assistant_text=last_text,
                       last_assistant_had_calls=last_had_calls)


def _tool_calls(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") or {}
        name = str(fn.get("name") or "")
        args_text = fn.get("arguments")
        if isinstance(args_text, dict):
            args_text = json.dumps(args_text)
        args_text = args_text if isinstance(args_text, str) else ""
        try:
            parsed = json.loads(args_text)
        except (ValueError, TypeError):
            parsed = {}
        out.append({"id": entry.get("id") or entry.get("call_id"), "name": name,
                    "args": parsed if isinstance(parsed, dict) else {}, "args_text": args_text})
    return out


# ── detectors ─────────────────────────────────────────────────────────────────────────────────────
def _detect_repeat_tool_call_no_progress(view: SessionView, ctx: MineContext) -> List[Hit]:
    by_signature: Dict[str, List[Call]] = collections.defaultdict(list)
    for call in view.calls:
        by_signature[call.signature].append(call)
    hits: List[Hit] = []
    for signature, group in sorted(by_signature.items()):
        if len(group) < ctx.options.min_repeats:
            continue
        fingerprints = {hashlib.sha1(_normalize(c.payload)[:2000].encode("utf-8")).hexdigest() for c in group}
        all_failed = all(not c.ok for c in group)
        if len(fingerprints) > 1 and not all_failed:
            continue
        span = group[-1].ts - group[0].ts
        if _mutated_between(view, group[0].ts, group[-1].ts, {p for c in group for p in c.paths}):
            continue  # something DID change in between: the repeats made progress
        hits.append(Hit(session_id=view.session_id, ts=group[0].ts,
                        message_ids=tuple(c.result_id or c.message_id for c in group[:8]),
                        detail={"tool": group[0].tool, "repeats": len(group), "distinct_results": len(fingerprints),
                                "all_failed": all_failed, "span_s": round(span, 1),
                                "signature": _excerpt(signature, 200),
                                "failure_class": group[0].failure_class},
                        excerpt=_excerpt(group[0].outcome or group[0].payload or signature, ctx.options.excerpt_chars)))
    return hits


def _mutated_between(view: SessionView, start: float, end: float, paths: set) -> bool:
    for call in view.calls:
        if not (start <= call.ts <= end) or not call.is_mutation or not call.ok:
            continue
        if not paths or any(p in paths for p in call.paths):
            return True
    return False


def _detect_test_failure_ignored(view: SessionView, ctx: MineContext) -> List[Hit]:
    hits: List[Hit] = []
    for index, call in enumerate(view.calls):
        if not _is_a_test_run(call) or not _TEST_FAILURE.search(call.outcome):
            continue
        later = view.calls[index + 1:]
        answered = (any(_is_a_test_run(other) for other in later)
                    or any(other.is_mutation and other.ok for other in later)
                    or _disclosed_after(view, call.result_id or call.message_id))
        if answered:
            continue
        failing = [m.group(1) for m in _FAIL_LINE.finditer(call.outcome)][:3]
        hits.append(Hit(session_id=view.session_id, ts=call.ts, message_ids=(call.message_id,) + ((call.result_id,) if call.result_id else ()),
                        detail={"command": _excerpt(call.command, 200), "failing": failing,
                                "failure_class": call.failure_class or "test_failure",
                                "later_calls": len(later), "later_test_runs": sum(1 for o in later if _is_a_test_run(o)),
                                "later_edits": sum(1 for o in later if o.is_mutation)},
                        excerpt=_excerpt(call.outcome, ctx.options.excerpt_chars)))
    return hits


def _disclosed_after(view: SessionView, message_id: int) -> bool:
    return any(mid > message_id and _DISCLOSURE.search(text) for mid, _ts, text in view.assistant_texts)


def _detect_premature_stop(view: SessionView, ctx: MineContext) -> List[Hit]:
    text = view.last_assistant_text or ""
    if not text.strip() or view.last_assistant_had_calls:
        return []
    strong = _STRONG_UNFINISHED.search(text)
    weak = _WEAK_UNFINISHED.search(text)
    if not strong and not weak:
        return []
    failed = _last_failed_call(view, view.last_assistant_id or 0)
    if not strong and (failed is None or text.rstrip().endswith("?")):
        return []
    return [Hit(session_id=view.session_id, ts=view.ended_at,
                message_ids=tuple(m for m in (view.last_assistant_id, failed.result_id if failed else None) if m),
                detail={"marker": (strong or weak).group(0), "marker_strength": "strong" if strong else "weak",
                        "end_reason": view.end_reason, "last_failure_class": failed.failure_class if failed else "",
                        "last_failure_tool": failed.tool if failed else "", "last_message_id": view.last_assistant_id},
                excerpt=_excerpt(text[-600:], ctx.options.excerpt_chars))]


def _last_failed_call(view: SessionView, before_message_id: int) -> Optional[Call]:
    found = [c for c in view.calls if not c.ok and c.message_id < before_message_id]
    return found[-1] if found else None


def _detect_stale_context(view: SessionView, ctx: MineContext) -> List[Hit]:
    hits: List[Hit] = []
    for call in view.calls:
        if call.is_mutation and not call.ok and call.failure_class == "stale_edit":
            earlier_writes = sum(1 for o in view.calls if o.is_mutation and o.ok and o.ts < call.ts and set(o.paths) & set(call.paths))
            hits.append(Hit(session_id=view.session_id, ts=call.ts,
                            message_ids=tuple(m for m in (call.message_id, call.result_id) if m),
                            detail={"class": "stale_edit", "tool": call.tool, "paths": list(call.paths),
                                    "earlier_writes_this_session": earlier_writes,
                                    "failure_class": call.failure_class},
                            excerpt=_excerpt(call.outcome, ctx.options.excerpt_chars)))
    for call in view.calls:
        if not call.is_mutation or not call.ok:
            continue
        read_here = {p for c in view.calls if c.tool in _DATA_TOOLS and c.ts <= call.ts for p in c.paths}
        for path in call.paths:
            if path in read_here:
                continue
            ancestor_read = _ancestor_read(ctx, view, path, call.ts)
            if ancestor_read is None:
                continue
            hits.append(Hit(session_id=view.session_id, ts=call.ts,
                            message_ids=tuple(m for m in (ancestor_read[1], call.message_id) if m),
                            detail={"class": "unverified_write_after_resume", "tool": call.tool, "path": path,
                                    "ancestor_session": ancestor_read[0], "ancestor_read_message_id": ancestor_read[1],
                                    "age_s": round(call.ts - ancestor_read[2], 1)},
                            excerpt=_excerpt(path, ctx.options.excerpt_chars)))
    return hits


def _ancestor_read(ctx: MineContext, view: SessionView, path: str, before_ts: float) -> Optional[Tuple[str, int, float]]:
    """The oldest ancestor read of *path* that predates the write — what the resumed session trusted."""
    for ancestor in _ancestors(ctx.run.sessions, view.session_id):
        ancestor_view = ctx.views.get(ancestor)
        if ancestor_view is None:
            continue
        for call in ancestor_view.calls:
            if call.tool in _DATA_TOOLS and path in call.paths and call.ts < before_ts and call.result_id:
                return ancestor, call.result_id, call.ts
    return None


def _allowed_roots(view: SessionView, ctx: MineContext) -> Tuple[str, ...]:
    session = ctx.run.sessions.get(view.session_id, {})
    roots = [str(session.get("cwd") or ""), str(session.get("git_repo_root") or "")]
    try:
        from hermes_constants import get_hermes_home
        roots.append(str(get_hermes_home()))
    except Exception:
        pass
    roots.extend([tempfile.gettempdir(), "/tmp", "/var/tmp"])
    return tuple(sorted({os.path.normpath(r) for r in roots if r}))


def _under_any(path: str, roots: Iterable[str]) -> bool:
    for root in roots:
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return True
    return False


def _detect_unrelated_files_touched(view: SessionView, ctx: MineContext) -> List[Hit]:
    roots = _allowed_roots(view, ctx)
    tokens = {t.lower() for t in _TASK_TOKEN.findall(view.inherited_task_text)} - _TASK_STOPWORDS
    hits: List[Hit] = []
    seen: set = set()
    for call in view.calls:
        if not call.is_mutation or not call.ok:
            continue
        for path in call.paths:
            if not path.startswith("/") or path in seen:
                continue
            if _under_any(path, roots) or any(tok in path.lower() for tok in tokens):
                continue
            seen.add(path)
            hits.append(Hit(session_id=view.session_id, ts=call.ts,
                            message_ids=tuple(m for m in (call.message_id, call.result_id) if m),
                            detail={"path": path, "tool": call.tool, "allowed_roots": list(roots),
                                    "task_tokens_checked": len(tokens)},
                            excerpt=_excerpt(path, ctx.options.excerpt_chars)))
    return hits


def _detect_repeated_user_correction(view: SessionView, ctx: MineContext) -> List[Hit]:
    corrections = [(mid, ts, text, used_tools) for mid, ts, text, used_tools in view.user_messages
                   if _CORRECTION.search(_normalize(text))]
    acted = [c for c in corrections if c[3]]
    if len(acted) < 2:
        return []
    normalized = [_normalize(text).lower() for _mid, _ts, text, _used in acted]
    repeats = len(normalized) - len(set(normalized))
    return [Hit(session_id=view.session_id, ts=acted[-1][1], message_ids=tuple(c[0] for c in acted[:6]),
                detail={"corrections": len(acted), "after_a_tool_turn": len(acted), "identical_repeats": repeats,
                        "phrases": [_excerpt(c[2], 120) for c in acted[:3]],
                        "total_user_messages": len(view.user_messages)},
                excerpt=_excerpt(acted[-1][2], ctx.options.excerpt_chars))]


def _detect_hidden_retry_loop(view: SessionView, ctx: MineContext) -> List[Hit]:
    hits: List[Hit] = []
    run_calls: List[Call] = []
    for call in view.calls:
        if call.ok:
            hits.extend(_retry_hit(view, run_calls, ctx))
            run_calls = []
            continue
        if run_calls and (call.ts - run_calls[-1].ts) > ctx.options.retry_window_s:
            hits.extend(_retry_hit(view, run_calls, ctx))
            run_calls = []
        run_calls.append(call)
    hits.extend(_retry_hit(view, run_calls, ctx))
    return hits


def _retry_hit(view: SessionView, calls: Sequence[Call], ctx: MineContext) -> List[Hit]:
    if len(calls) < ctx.options.min_retries:
        return []
    signatures = {c.signature for c in calls}
    if len(signatures) < 2:
        return []  # one identical call repeated is the no-progress signal, not this one
    span = calls[-1].ts - calls[0].ts
    if span < ctx.options.retry_min_span_s:
        return []
    if any(_DISCLOSURE.search(text) for mid, _ts, text in view.assistant_texts
           if calls[0].message_id <= mid <= calls[-1].message_id):
        return []  # the user was told: retries were not hidden
    return [Hit(session_id=view.session_id, ts=calls[0].ts,
                message_ids=tuple(m for c in calls[:6] for m in (c.message_id,)),
                detail={"retries": len(calls), "distinct_calls": len(signatures), "span_s": round(span, 1),
                        "tools": sorted({c.tool for c in calls}),
                        "failure_classes": sorted({c.failure_class or "unknown" for c in calls}),
                        "disclosures_between": 0},
                excerpt=_excerpt(calls[0].outcome or calls[0].tool, ctx.options.excerpt_chars))]


DETECTORS: Tuple[Detector, ...] = (
    Detector(signal="test_failure_ignored", priority=1, category="code_modification",
             title="A failing test run was never answered",
             artifacts=("test_run.json",),
             assertions=("failing_test_is_fixed", "final_message_states_test_status"),
             prompt_task="run the failing suite, fix what it reports (or say plainly that it is not fixed)",
             find=_detect_test_failure_ignored),
    Detector(signal="repeated_user_correction", priority=1, category="review_gate",
             title="The user had to correct the same mistake twice",
             artifacts=("answer.md",),
             assertions=("first_answer_satisfies_the_stated_constraint", "correction_is_not_repeated"),
             prompt_task="satisfy the constraint the user states, without needing a second correction",
             find=_detect_repeated_user_correction),
    Detector(signal="stale_context", priority=1, category="context_management",
             title="An edit was built from stale information",
             artifacts=("edit.json",),
             assertions=("re_reads_before_editing_a_file_it_did_not_read_this_session",
                         "recovers_from_a_failed_edit_by_re_reading"),
             prompt_task="edit the file from its CURRENT content, re-reading it instead of trusting memory",
             find=_detect_stale_context),
    Detector(signal="repeat_tool_call_no_progress", priority=2, category="tool_intensive",
             title="The same tool call was repeated with the same result",
             artifacts=("execution_log.json",),
             assertions=("no_identical_retry_of_the_same_call", "stall_is_reported_instead_of_retried"),
             prompt_task="make progress with each call, or report the blocker instead of repeating the call",
             find=_detect_repeat_tool_call_no_progress),
    Detector(signal="hidden_retry_loop", priority=2, category="fault_tolerance",
             title="A run of failures was retried without ever being surfaced",
             artifacts=("failure_report.json",),
             assertions=("retry_loop_is_bounded", "failures_are_surfaced_to_the_user"),
             prompt_task="bound the retries and surface the failure instead of looping silently",
             find=_detect_hidden_retry_loop),
    Detector(signal="premature_stop", priority=2, category="code_modification",
             title="The task was abandoned while work remained",
             artifacts=("completion_report.json",),
             assertions=("task_completed_or_blocker_reported", "no_unfinished_work_claimed_as_done"),
             prompt_task="finish the task or report the concrete blocker, never stop mid-way",
             find=_detect_premature_stop),
    Detector(signal="unrelated_files_touched", priority=3, category="tool_intensive",
             title="Files outside the session's scope were written",
             artifacts=("write_audit.json",),
             assertions=("writes_stay_inside_the_session_repo_root", "out_of_scope_writes_are_justified"),
             prompt_task="change only the files the task owns",
             find=_detect_unrelated_files_touched),
)
_BY_SIGNAL = {d.signal: d for d in DETECTORS}


def _observed_tokens(run: Run, sid: str) -> int:
    """MODELED eval budget: what the source session spent per model call (every usage column over
    ``api_call_count``), rounded up to 512 and floored at 1024. A starting point for the case — the
    trajectory proves what that kind of task CARRIED, not what a correct run must spend."""
    session = run.sessions.get(sid, {})
    total = sum(float(session.get(col) or 0) for col in USAGE_COLS)
    total += float(session.get("reasoning_tokens") or 0)
    per_call = total / max(1, int(session.get("api_call_count") or 0))
    return max(1024, int(math.ceil(per_call / 512.0) * 512))


def _build_case(detector: Detector, run: Run, view: SessionView, hits: Sequence[Hit], options: MineOptions) -> MinedEvalCase:
    kept = list(hits[: options.max_evidence])
    evidence = tuple(CaseEvidence(db_path=str(run.db_path), session_id=hit.session_id, message_ids=hit.message_ids,
                                  timestamp=hit.ts, detail=hit.detail, excerpt=hit.excerpt) for hit in kept)
    first = hits[0]
    prompt = (f"Task: {_excerpt(view.task_text or view.session_id, 200)}\n\n"
              f"{detector.prompt_task[0].upper()}{detector.prompt_task[1:]}.\n"
              f"Recorded pattern ({detector.signal}): {_excerpt(first.excerpt, 200)}\n"
              f"Evidence: session {view.session_id}, message ids {list(first.message_ids)}.")
    return MinedEvalCase(case_id=f"EV-{detector.signal.replace('_', '-')}-{view.session_id}", signal=detector.signal,
                         title=detector.title, priority=detector.priority, category=detector.category,
                         occurrences=len(hits), sessions=(view.session_id,), evidence=evidence,
                         evidence_total=len(hits), prompt=prompt, expected_artifacts=detector.artifacts,
                         deterministic_assertions=detector.assertions, max_tokens_budget=_observed_tokens(run, view.session_id),
                         description=(f"Mined from {len(hits)} observed occurrence(s) in session {view.session_id} of "
                                      f"{run.db_path.name}; first evidence at message ids {list(first.message_ids)}."))


def mine(run: Run, options: Optional[MineOptions] = None) -> List[MinedEvalCase]:
    """Every candidate eval case the store supports, priority-first and deterministic.

    Same store + same options ⇒ same cases in the same order, with the same evidence.
    """
    opts = options or MineOptions()
    selected = [d for d in DETECTORS if not opts.signals or d.signal in opts.signals]
    views = {sid: _view(run, sid) for sid in run.in_run}
    context = MineContext(run=run, views=views, options=opts)
    cases: List[MinedEvalCase] = []
    for detector in selected:
        per_session: Dict[str, List[Hit]] = collections.defaultdict(list)
        for sid in run.in_run:
            for hit in detector.find(views[sid], context):
                per_session[sid].append(hit)
        for sid in sorted(per_session):
            hits = sorted(per_session[sid], key=lambda h: (h.ts, h.message_ids))
            cases.append(_build_case(detector, run, views[sid], hits, opts))
    cases.sort(key=lambda c: (c.priority, c.signal, c.sessions[0]))
    return cases


# ── emitters: the existing harness's shapes ───────────────────────────────────────────────────────
def to_eval_suite(cases: Sequence[MinedEvalCase], suite_id: str = "haos_mined_failures"):
    """The mined cases as the ``hermes.platform.evals.runner`` suite the eval harness already runs."""
    from hermes.platform.evals.runner import EvalCase, EvalSuite
    return EvalSuite(id=suite_id, description="Eval cases mined from recorded trajectories (evals/postmortem/forensics/eval_mining.py)",
                     cases=[EvalCase(id=c.case_id, input={"prompt": c.prompt, "expected_artifacts": list(c.expected_artifacts)},
                                     expected={"deterministic_assertions": list(c.deterministic_assertions)},
                                     tags=[c.signal, f"priority:{c.priority}"] + list(c.sessions)) for c in cases])


def to_golden_tasks(cases: Sequence[MinedEvalCase]):
    """The mined cases as real ``GoldenTaskSpec``s (``hermes.platform.evals.golden_tasks``).

    Not registered in the module-level ``GOLDEN_TASKS`` dict: the reference suite is a fixed catalog,
    and a mined case is a proposal until a human promotes it.
    """
    from hermes.platform.evals.golden_tasks import GoldenTaskCategory, GoldenTaskSpec
    return [GoldenTaskSpec(id=c.case_id, name=c.title, category=GoldenTaskCategory(c.category), prompt=c.prompt,
                           expected_artifacts=list(c.expected_artifacts), max_tokens_budget=c.max_tokens_budget,
                           deterministic_assertions=list(c.deterministic_assertions), description=c.description)
            for c in cases]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # enum
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


def build_report(run: Run, cases: Sequence[MinedEvalCase], options: MineOptions,
                 population: str = "whole store") -> Dict[str, Any]:
    by_signal: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        bucket = by_signal.setdefault(case.signal, {"cases": 0, "occurrences": 0, "priority": case.priority,
                                                    "category": case.category, "sessions": [], "cost_usd": 0.0})
        bucket["cases"] += 1
        bucket["occurrences"] += case.occurrences
        bucket["sessions"].append(case.sessions[0])
        bucket["cost_usd"] = round(bucket["cost_usd"] + run.cost(case.sessions[0]), 2)
    return {
        "source": {"db": str(run.db_path), "root": run.root, "sessions_scanned": len(run.in_run),
                   "population": population,
                   "signals": [d.signal for d in DETECTORS if not options.signals or d.signal in options.signals],
                   "options": _jsonable(options)},
        "observed": {"candidates": len(cases), "occurrences": sum(c.occurrences for c in cases),
                     "by_signal": by_signal},
        "candidates": [_jsonable(case) for case in cases],
        "caveats": [
            "Every case is a PROPOSAL mined from one store: promote it only after reading its evidence.",
            "Excerpts carry a slice of the recorded trajectory (task text, tool output) — treat the JSON as private.",
            "max_tokens_budget is MODELED from what the source session actually consumed (floor 1024), not a proven bound.",
        ],
    }


# ── store opening ─────────────────────────────────────────────────────────────────────────────────
def _default_db() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "state.db"


def open_store(db: Optional[Any] = None, *, root: Optional[str] = None, out: str = "postmortem_out") -> Run:
    """A read-only :class:`Run` whose population is the WHOLE store (or one tree when ``root`` is given).

    ``Run.open`` answers "this run" and needs a root session; mining asks "does this pattern recur?", so
    the default population is every session except compression-rollover continuations. The connection,
    the rollover rule and the fitted pricing all come from ``common`` — this only changes the population.
    """
    path = Path(db) if db else _default_db()
    if root:
        return Run.open(str(path), root=root, out=out)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        sessions = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sessions")}
    except sqlite3.Error as exc:
        raise SystemExit(f"[eval_mining] cannot read {path} read-only: {exc}")
    rollover = Run._rollover_ids(conn, sessions)
    population = [sid for sid in sorted(sessions, key=lambda s: (sessions[s].get("started_at") or 0, s))
                  if sid not in rollover]
    depth = {sid: _session_depth(sessions, sid, rollover) for sid in population}
    roots = [sid for sid in sessions if not sessions[sid].get("parent_session_id")]

    def descendants(sid: str) -> int:
        return sum(1 for other in population if sid in _ancestors(sessions, other))

    root_id = max(roots, key=descendants) if roots else ""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    return Run(path, out_dir, root_id, sessions, depth, population,
               Run._fit_pricing([sessions[s] for s in population]), conn)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--db", default=None, help="state.db to mine READ-ONLY (default: $HERMES_HOME/state.db)")
    ap.add_argument("--root", default=None, help="restrict to one run tree (default: the whole store)")
    ap.add_argument("--out", default="postmortem_out", help="directory for JSON outputs")
    ap.add_argument("--signal", action="append", default=None, choices=sorted(_BY_SIGNAL), help="mine only this signal (repeatable)")
    ap.add_argument("--min-repeats", type=int, default=3, help="identical call+result repeats before 'no progress' is claimed")
    ap.add_argument("--min-retries", type=int, default=3, help="failing calls in a row before a 'retry loop' is claimed")
    ap.add_argument("--retry-window", type=float, default=300.0, help="seconds; a longer gap ends the retry run")
    ap.add_argument("--max-evidence", type=int, default=5, help="evidence entries kept per case")
    ap.add_argument("--excerpt-chars", type=int, default=_EXCERPT_LIMIT, help="length cap of one evidence excerpt")
    ap.add_argument("--top", type=int, default=10, help="cases printed in the summary")
    ap.add_argument("--golden", default=None, help="also write GoldenTaskSpec-shaped JSON here")
    ap.add_argument("--suite", default=None, help="also write an eval-runner EvalSuite JSON here")
    args = ap.parse_args(argv)

    options = MineOptions(min_repeats=args.min_repeats, min_retries=args.min_retries,
                          retry_window_s=args.retry_window, max_evidence=args.max_evidence,
                          excerpt_chars=args.excerpt_chars, signals=tuple(args.signal or ()))
    run = open_store(args.db, root=args.root, out=args.out)
    cases = mine(run, options)
    population = f"tree {args.root}" if args.root else "whole store"
    report = build_report(run, cases, options, population)
    path = run.write("eval_candidates.json", report)

    print(f"[eval_mining] {len(run.in_run):,} sessions scanned from {run.db_path} "
          f"({'tree ' + run.root if args.root else 'whole store'}); {len(cases)} candidate case(s)")
    for signal, bucket in sorted(report["observed"]["by_signal"].items(), key=lambda kv: (kv[1]["priority"], kv[0])):
        print(f"[eval_mining]   p{bucket['priority']} {signal:<30} {bucket['cases']:>3} case(s) "
              f"{bucket['occurrences']:>3} occurrence(s) ${bucket['cost_usd']:,.2f} in the affected sessions")
    for case in cases[: args.top]:
        ev = case.evidence[0] if case.evidence else None
        where = f"{ev.session_id} msg {list(ev.message_ids)}" if ev else "no evidence"
        print(f"[eval_mining]   {case.case_id} · {case.occurrences}× · {where} · {case.title}")
    print(f"[eval_mining] wrote {path}")
    if args.golden:
        specs = _jsonable(to_golden_tasks(cases))
        Path(args.golden).write_text(json.dumps(specs, indent=1), encoding="utf-8")
        print(f"[eval_mining] wrote {len(specs)} GoldenTaskSpec(s) -> {args.golden}")
    if args.suite:
        suite = _jsonable(to_eval_suite(cases))
        Path(args.suite).write_text(json.dumps(suite, indent=1), encoding="utf-8")
        print(f"[eval_mining] wrote EvalSuite ({len(cases)} case(s)) -> {args.suite}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
