"""ACP (Agent Client Protocol) wire adapter for HAOS sessions (integration K5).

Wraps the ACP 0.9 wire — NEWLINE-delimited JSON-RPC 2.0 over a child process's
stdio — using only the stdlib.  The third-party ``acp`` PyPI package and the
upstream ``acp_adapter`` server are never imported here (neither is installed
in this tree); the contract tests in ``tests/platform/protocols/`` play the
agent role with a scripted stdlib peer that mirrors the ACP wire shapes.

Wire facts (verified against agent-client-protocol==0.9.0 and the upstream
``acp_adapter``):

* one JSON-RPC 2.0 frame per line —
  ``json.dumps(payload, separators=(",", ":")) + "\\n"`` — never Content-Length;
* client -> agent requests carry an integer ``id`` and a ``method``;
  notifications omit ``id``; responses echo the request ``id`` and carry
  ``result`` (any JSON value, including null) or ``error`` ``{code, message}``;
* client -> agent methods: initialize, authenticate, session/new, session/load,
  session/resume, session/fork, session/list, session/prompt, session/cancel,
  session/close, session/set_config_option, session/set_mode, session/set_model;
* agent -> client pushes (session/update, session/request_permission,
  fs/read_text_file, fs/write_text_file, terminal/*) are notifications; the
  reader records them and never answers, correlating responses purely by id so
  interleaved pushes cannot deadlock a request.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = 1
"""ACP protocol version this client speaks (initialize params are camelCase)."""

_CLIENT_NAME = "hermes-haos-acp"
_CLIENT_VERSION = "0.1.0"
_REQUEST_TIMEOUT_S = 15.0
_SHUTDOWN_TIMEOUT_S = 5.0
_STDERR_TAIL_LINES = 40


class ACPError(RuntimeError):
    """Base failure of the ACP wire client."""


class ACPProtocolError(ACPError):
    """The peer answered in a way that violates the ACP/JSON-RPC contract."""


class ACPUnavailableError(ACPError):
    """The agent process is gone or never completed the handshake."""


@dataclass
class ACPIdentity:
    """Agent identity reported by the ``initialize`` handshake."""

    name: str
    version: str
    protocol_version: int = PROTOCOL_VERSION
    agent_capabilities: Dict[str, Any] = field(default_factory=dict)
    auth_methods: List[str] = field(default_factory=list)


@dataclass
class ACPSession:
    """An open agent session (``session/new`` result) bound to its client."""

    session_id: str
    cwd: str
    client: "ACPSessionClient"

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "cwd": self.cwd}


class ACPSessionClient:
    """Spawn an ACP agent process and speak the ACP wire to it.

    ``server_command`` is the argv of the agent process (the contract tests
    inject the scripted peer); ``cwd`` optionally sets the working directory of
    that process.  Call :meth:`start` — or use the client as a context manager —
    before :meth:`new_session`/:meth:`send_text`; :meth:`close` ends every
    known session, closes stdin (EOF tells the agent to leave), waits for the
    process within ``shutdown_timeout_s`` and escalates to terminate/kill.
    """

    def __init__(
        self,
        server_command: List[str],
        *,
        cwd: Optional[str] = None,
        request_timeout_s: float = _REQUEST_TIMEOUT_S,
        shutdown_timeout_s: float = _SHUTDOWN_TIMEOUT_S,
    ) -> None:
        self.server_command = list(server_command)
        self.cwd = cwd
        self.request_timeout_s = request_timeout_s
        self.shutdown_timeout_s = shutdown_timeout_s

        # Result of the initialize handshake (None until start() succeeds).
        self.identity: Optional[ACPIdentity] = None
        self._sessions: Dict[str, ACPSession] = {}
        self._notifications: List[Dict[str, Any]] = []

        self._proc: Optional[subprocess.Popen] = None
        self._returncode: Optional[int] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_drain: Optional[threading.Thread] = None
        self._stderr_tail: deque = deque(maxlen=_STDERR_TAIL_LINES)

        # One lock guards ids, pending correlation, notifications and the
        # reader-alive flag; a second lock serializes stdin writers.
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._pending: Dict[int, queue.Queue] = {}
        self._reader_alive = False

    # ------------------------------------------------------------------ state

    @property
    def is_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def returncode(self) -> Optional[int]:
        proc = self._proc
        if proc is not None and proc.returncode is not None:
            return proc.returncode
        return self._returncode

    @property
    def stderr_tail(self) -> str:
        """Last agent stderr lines (for failure diagnostics)."""
        with self._lock:
            return "\n".join(self._stderr_tail)

    @property
    def notifications(self) -> tuple:
        """Server pushes recorded so far (snapshot; never answered)."""
        with self._lock:
            return tuple(self._notifications)

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> "ACPSessionClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self.close()
        except ACPError:
            # Never mask an exception already escaping the with-block.
            if exc_type is None:
                raise
        return False

    def start(self) -> None:
        """Spawn the agent and complete the ``initialize`` handshake."""
        if self._proc is not None:
            raise ACPError("ACPSessionClient.start() called twice")
        try:
            self._spawn()
            if self._proc.poll() is not None:
                raise ACPUnavailableError(
                    self._gone_message("agent exited before the handshake")
                )
            result = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": _CLIENT_NAME,
                        "version": _CLIENT_VERSION,
                    },
                },
                timeout_s=self.request_timeout_s,
            )
            self.identity = self._identity_from_initialize(result)
        except BaseException:
            self._kill_process()
            raise

    def new_session(self, cwd: str, mcp_servers: Optional[List[Any]] = None) -> ACPSession:
        """Open an agent session rooted at the absolute directory ``cwd``."""
        self._ensure_running()
        params: Dict[str, Any] = {"cwd": cwd}
        if mcp_servers is not None:
            params["mcpServers"] = mcp_servers
        try:
            result = self._request(
                "session/new",
                params,
                timeout_s=self.request_timeout_s,
            )
        except ACPError as exc:
            if mcp_servers is None and "Invalid params" in str(exc):
                # agent-client-protocol >= 0.9 requer mcpServers na wire
                params["mcpServers"] = []
                result = self._request(
                    "session/new",
                    params,
                    timeout_s=self.request_timeout_s,
                )
            else:
                raise
        if not isinstance(result, dict) or not result.get("sessionId"):
            raise ACPError(f"session/new returned no sessionId: {result!r}")
        session = ACPSession(session_id=result["sessionId"], cwd=cwd, client=self)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def send_text(self, session: ACPSession, text: str) -> Any:
        """Send one text block as ``session/prompt``; return the raw result.

        The result is captured verbatim — this adapter never over-parses the
        agent's answer, so the caller decides what the server's shape means.
        """
        self._ensure_running()
        return self._request(
            "session/prompt",
            {
                "sessionId": session.session_id,
                "prompt": [{"type": "text", "text": text}],
            },
            timeout_s=self.request_timeout_s,
        )

    def close(self) -> None:
        """Close known sessions, then shut the agent process down."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and self.identity is not None:
                self._close_known_sessions()
            if proc.poll() is None:
                # EOF on stdin is the ACP agent's signal to leave.
                stdin = proc.stdin
                if stdin is not None:
                    try:
                        stdin.close()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=self.shutdown_timeout_s)
                except subprocess.TimeoutExpired:
                    self._terminate(proc)
        finally:
            self._finish_process(proc)

    # ------------------------------------------------------------------ internals

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            self.server_command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        with self._lock:
            self._reader_alive = True
        self._reader = threading.Thread(
            target=self._reader_loop, name="acp-reader", daemon=True
        )
        self._reader.start()
        self._stderr_drain = threading.Thread(
            target=self._stderr_loop, name="acp-stderr", daemon=True
        )
        self._stderr_drain.start()

    def _reader_loop(self) -> None:
        proc = self._proc
        try:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    # A non-JSON line on stdout is not ACP wire; skip it.
                    continue
                if isinstance(frame, dict):
                    self._dispatch_frame(frame)
        finally:
            # stdout closed: the agent can no longer answer us.
            with self._lock:
                self._reader_alive = False
                pending = list(self._pending.values())
            if pending:
                gone = ACPUnavailableError(
                    self._gone_message("ACP agent stream ended")
                )
                for response_queue in pending:
                    self._put_nowait(response_queue, gone)

    def _stderr_loop(self) -> None:
        proc = self._proc
        for raw_line in proc.stderr:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            with self._lock:
                self._stderr_tail.append(line)

    def _dispatch_frame(self, frame: Dict[str, Any]) -> None:
        rid = frame.get("id")
        if isinstance(rid, int):
            with self._lock:
                response_queue = self._pending.get(rid)
            if response_queue is not None:
                self._put_nowait(response_queue, frame)
                return
        # Notifications (no id) and stray frames are recorded, never answered.
        with self._lock:
            self._notifications.append(frame)

    @staticmethod
    def _put_nowait(response_queue: queue.Queue, item: Any) -> None:
        try:
            response_queue.put_nowait(item)
        except queue.Full:
            pass

    def _request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: float = _REQUEST_TIMEOUT_S,
    ) -> Any:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            raise ACPUnavailableError(
                f"ACP agent is not running; cannot send '{method}'"
            )
        rid = self._next_request_id()
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = response_queue
            reader_alive = self._reader_alive
        if not reader_alive:
            # The reader already hit EOF; fail fast instead of waiting.
            self._put_nowait(
                response_queue,
                ACPUnavailableError(self._gone_message("ACP agent stream ended")),
            )
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params:
            payload["params"] = params
        try:
            self._write_frame(payload)
            try:
                reply = response_queue.get(timeout=timeout_s)
            except queue.Empty:
                raise ACPError(
                    f"timed out after {timeout_s:g}s waiting for the "
                    f"'{method}' response"
                ) from None
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        if isinstance(reply, BaseException):
            raise reply
        return self._reply_result(method, reply)

    def _next_request_id(self) -> int:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def _write_frame(self, payload: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ACPUnavailableError("ACP agent is not running (stdin closed)")
        raw = json.dumps(payload, separators=(",", ":"))
        try:
            with self._write_lock:
                proc.stdin.write(raw + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise ACPUnavailableError(
                f"cannot write to the ACP agent: {exc}"
            ) from exc

    @staticmethod
    def _reply_result(method: str, reply: Dict[str, Any]) -> Any:
        error = reply.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            message = (
                error.get("message") if isinstance(error, dict) else str(error)
            )
            raise ACPError(
                f"ACP request '{method}' failed (code {code}): {message}"
            )
        if "result" not in reply:
            raise ACPProtocolError(
                f"malformed response to '{method}': neither result nor error "
                f"present: {reply!r}"
            )
        return reply["result"]

    @staticmethod
    def _identity_from_initialize(result: Any) -> ACPIdentity:
        if not isinstance(result, dict):
            raise ACPProtocolError(
                f"initialize result is not an object: {result!r}"
            )
        if "protocolVersion" not in result:
            raise ACPProtocolError(
                "initialize result lacks the protocolVersion field"
            )
        if result["protocolVersion"] != PROTOCOL_VERSION:
            raise ACPProtocolError(
                f"ACP protocol version mismatch: server offers "
                f"{result['protocolVersion']!r}, client requires "
                f"{PROTOCOL_VERSION}"
            )
        agent_info = result.get("agentInfo")
        if not isinstance(agent_info, dict):
            raise ACPProtocolError(
                "initialize result lacks an agentInfo object"
            )
        name = agent_info.get("name")
        if not isinstance(name, str) or not name:
            raise ACPProtocolError(
                "initialize result agentInfo lacks a non-empty name"
            )
        capabilities = result.get("agentCapabilities")
        auth_methods = result.get("authMethods")
        return ACPIdentity(
            name=name,
            version=str(agent_info.get("version") or ""),
            protocol_version=result["protocolVersion"],
            agent_capabilities=(
                dict(capabilities) if isinstance(capabilities, dict) else {}
            ),
            auth_methods=(
                list(auth_methods) if isinstance(auth_methods, list) else []
            ),
        )

    def _ensure_running(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            raise ACPUnavailableError("ACP agent is not running")
        if self.identity is None:
            raise ACPUnavailableError(
                "ACP handshake incomplete; call start() first"
            )

    def _close_known_sessions(self) -> None:
        with self._lock:
            session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                self._request(
                    "session/close",
                    {"sessionId": session_id},
                    timeout_s=min(self.request_timeout_s, 5.0),
                )
            except ACPError:
                # Shutting the process down anyway; never mask here.
                pass

    def _terminate(self, proc: subprocess.Popen) -> None:
        """SIGTERM, escalating to SIGKILL after a short grace period."""
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        except OSError:
            pass

    def _finish_process(self, proc: subprocess.Popen) -> None:
        for thread in (self._reader, self._stderr_drain):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._returncode = proc.returncode
        with self._lock:
            self._sessions.clear()
        self._proc = None

    def _kill_process(self) -> None:
        """Brutal cleanup used when start() fails mid-handshake."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            self._terminate(proc)
        self._finish_process(proc)

    def _gone_message(self, reason: str) -> str:
        proc = self._proc
        rc = proc.poll() if proc is not None else self._returncode
        parts = [reason]
        if rc is not None:
            parts.append(f"exit code {rc}")
        tail = self.stderr_tail
        if tail:
            parts.append(f"stderr: {tail}")
        return "; ".join(parts)
