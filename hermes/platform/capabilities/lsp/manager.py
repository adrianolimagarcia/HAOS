"""Real LSP client, explicit static stub, and a workspace-scoped manager.

K6 ("LSP real"): ``LSPManager.get_client`` used to hand back a mock that
returned hard-coded literals. Now the mock is an *explicit* stub
(:class:`StaticLSPClient` — it does not speak LSP) returned only when no real
language-server command can be resolved, and a real :class:`LSPClient` speaks
the LSP wire (Content-Length framed JSON-RPC 2.0 over a stdio subprocess) when
one can.

Stdlib only, by design: HAOS capabilities never import the upstream agent
tree (``agent/``, ``hermes_cli/``, ``tools/``) and never add dependencies.

Lifecycle of the real client: the language-server subprocess is spawned
*lazily* — the first method call (or an explicit ``start()``) performs the
``initialize`` handshake and flips ``running`` to ``True``. ``shutdown()``
sends ``shutdown``/``exit`` and reaps the process; the client also works as a
context manager.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from .protocol import (
    LSPProtocolError,
    classify_message,
    encode_message,
    make_notification,
    make_request,
    read_message,
)

# Timeouts (seconds). Bounded so no test or caller ever hangs forever.
_HANDSHAKE_TIMEOUT = 15.0
_REQUEST_TIMEOUT = 10.0
_SHUTDOWN_TIMEOUT = 6.0
_PROCESS_TERMINATE_TIMEOUT = 3.0
_DIAGNOSTICS_SETTLE_SECONDS = 3.0
_MAX_STDERR_KEEP = 64 * 1024

# LSP SymbolKind -> humanized label (spec 3.18, section SymbolKind).
_SYMBOL_KIND_NAMES = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}

# languageId advertised in textDocument/didOpen for the languages we know.
_LANGUAGE_IDS = {"python": "python", "py": "python"}

# Minimal client capabilities sent with initialize (spec 3.18).
_CLIENT_CAPABILITIES = {
    "workspace": {"workspaceFolders": True},
    "textDocument": {
        "publishDiagnostics": {"relatedInformation": True},
        "synchronization": {"didSave": True, "willSave": False, "willSaveWaitUntil": False},
    },
}


class LSPError(RuntimeError):
    """Base error for LSP interactions (handshake, requests, framing)."""


class LSPUnavailableError(LSPError):
    """No usable language server: no command, spawn failure, timeout, or dead pipe."""


def _humanize_kind(kind: Any) -> Any:
    """Map a numeric LSP SymbolKind to its label; leave non-integers alone."""
    if isinstance(kind, int):
        return _SYMBOL_KIND_NAMES.get(kind, kind)
    return kind


def _find_symbol_position(text: str, symbol_name: str) -> Optional[Dict[str, int]]:
    """Naive position lookup: first word-boundary occurrence of ``symbol_name``.

    Returns ``None`` when the symbol is not present in the text (callers then
    report "no references" instead of inventing a position).
    """
    match = re.search(r"\b" + re.escape(symbol_name) + r"\b", text)
    if match is None:
        return None
    line = text.count("\n", 0, match.start())
    line_start = text.rfind("\n", 0, match.start()) + 1
    return {"line": line, "character": match.start() - line_start}


def _uri_to_path(uri: str, known: Dict[str, str]) -> str:
    """Turn a ``file://`` uri into a filesystem path; prefer our open-file cache."""
    if uri in known:
        return known[uri]
    parts = urlparse(uri)
    if parts.scheme == "file":
        return unquote(parts.path)
    return uri


class LSPClient:
    """Real LSP 3.x client: spawns a language-server subprocess and speaks LSP.

    ``server_command`` is an explicit argv (e.g. ``["pyright-langserver",
    "--stdio"]``). Passing ``None`` is a *fail-fast* configuration: no command
    means nothing to spawn, so :meth:`start` raises :class:`LSPUnavailableError`
    instead of silently spawning nothing.

    The subprocess is started lazily by the first method call or by an explicit
    :meth:`start`. All public methods are synchronous facades over an asyncio
    event loop that runs on a private daemon thread.
    """

    is_stub = False

    def __init__(
        self,
        workspace_path: str,
        language: str = "python",
        *,
        server_command: Optional[List[str]] = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.language = language
        self.server_command = list(server_command) if server_command is not None else None
        self.running = False
        self.server_info: Optional[Dict[str, Any]] = None
        self.server_capabilities: Dict[str, Any] = {}

        # Transport state (owned by the loop thread once started).
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._write_lock: Optional[asyncio.Lock] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._stderr_buffer = bytearray()

        # Wire bookkeeping.
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._diagnostics: Dict[str, List[Dict[str, Any]]] = {}  # uri -> LSP diagnostics
        self._opened: Dict[str, Dict[str, Any]] = {}  # relative path -> open info
        self._sent_open: Set[str] = set()  # relative paths didOpened on this connection
        self._primary_relative: Optional[str] = None

        # Startup coordination between the calling thread and the loop thread.
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None

    # ------------------------------------------------------------------ sync API

    def start(self) -> None:
        """Spawn the language server and complete the LSP handshake.

        Raises :class:`LSPUnavailableError` when no ``server_command`` was
        configured or when spawn/handshake fails or times out (the process is
        terminated in those cases).
        """
        if self.running:
            return
        if self.server_command is None:
            raise LSPUnavailableError(
                "cannot start: no language-server command configured "
                "(pass server_command=[...] or use LSPManager)"
            )
        with self._start_lock:
            if self.running:
                return
            if self._thread is not None and self._thread.is_alive():
                # Another caller is mid-handshake; wait for its outcome.
                if not self._ready.wait(_HANDSHAKE_TIMEOUT):
                    raise LSPUnavailableError("language server handshake timed out")
            else:
                self._start_error = None
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._run_loop, name="haos-lsp-client", daemon=True
                )
                self._thread.start()
                if not self._ready.wait(_HANDSHAKE_TIMEOUT):
                    self.running = False
                    raise LSPUnavailableError("language server handshake timed out")
        if self._start_error is not None:
            raise self._start_error  # already an LSPUnavailableError with context

    def shutdown(self) -> None:
        """Send ``shutdown``/``exit`` and reap the subprocess (idempotent).

        Uses bounded waits; if the server does not exit on its own the process
        is force-terminated.
        """
        if self.server_command is None:
            return
        thread = self._thread
        if thread is None or not thread.is_alive():
            self.running = False
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_coro(), loop).result(
                    _SHUTDOWN_TIMEOUT
                )
            except Exception:
                pass  # _force_kill below still guarantees cleanup
        thread.join(_SHUTDOWN_TIMEOUT)
        if thread.is_alive():
            self._force_kill()
            thread.join(_SHUTDOWN_TIMEOUT)
        self.running = False

    def get_document_symbols(self, relative_file_path: str) -> List[Dict[str, Any]]:
        """Return ``[{"name", "kind", "line"}]`` for one file (0-based lines).

        The file is opened (``textDocument/didOpen``) on first request, then a
        ``textDocument/documentSymbol`` request is issued. Both LSP response
        shapes — hierarchical ``DocumentSymbol[]`` and flat ``SymbolInformation[]``
        — are normalized; nested DocumentSymbol children are flattened in.
        """
        self._require_live()
        info = self._open_file(relative_file_path)
        result = self._run(lambda: self._request_symbols(info["uri"]))
        return self._normalize_symbols(result)

    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Return ``[{"file", "line"}]`` for ``symbol_name`` in the primary file.

        The position comes from a naive scan of the currently open (primary)
        file's source for the symbol name; no file open yet raises
        :class:`LSPError`. Returns ``[]`` when the name does not occur.
        """
        self._require_live()
        rel = self._primary_relative
        if rel is None:
            if not self._opened:
                raise LSPError(
                    "no file has been opened yet; call get_document_symbols(<file>) first"
                )
            rel = next(iter(self._opened))
        info = self._opened[rel]
        position = _find_symbol_position(info["text"], symbol_name)
        if position is None:
            return []
        result = self._run(lambda: self._request_references(info["uri"], position))
        return self._normalize_references(result)

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return ``{"new_errors", "warnings", "details"}`` for opened files.

        Diagnostics come from the server-pushed ``textDocument/publishDiagnostics``
        notifications accumulated by the reader (uri -> diagnostics). When at
        least one file is open we wait a bounded settle window for the push to
        arrive; if none arrive (or nothing was opened) the dict still comes back
        with zeros. Never blocks forever.
        """
        self._require_live()
        return self._run(self._collect_diagnostics, timeout=_REQUEST_TIMEOUT)

    # ----------------------------------------------------------- context manager

    def __enter__(self) -> "LSPClient":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.shutdown()

    # ---------------------------------------------------------- internal helpers

    def _require_live(self) -> None:
        """Start lazily on first method call, or fail fast without a command."""
        if self.server_command is None:
            raise LSPUnavailableError(
                "no language-server command configured for this client"
            )
        if not self.running:
            self.start()
        if not self.running:
            raise LSPUnavailableError("language server is not running")

    def _run(self, coro_factory: Any, timeout: float = _REQUEST_TIMEOUT) -> Any:
        """Schedule ``coro_factory()`` on the loop thread and block for the result."""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise LSPUnavailableError("language server loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return future.result(timeout)

    def _open_file(self, relative_file_path: str) -> Dict[str, Any]:
        """Read a workspace file from disk, memoize it, and didOpen it once per connection."""
        rel = relative_file_path.replace("\\", "/")
        info = self._opened.get(rel)
        if info is not None:
            self._primary_relative = rel
            return info
        abs_path = str((Path(self.workspace_path) / rel).resolve())
        try:
            text = Path(abs_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError(
                f"cannot open {abs_path!r} for LSP: {exc}"
            ) from exc
        info = {
            "relative": rel,
            "path": abs_path,
            "uri": Path(abs_path).as_uri(),
            "language_id": _LANGUAGE_IDS.get(self.language, self.language),
            "version": 1,
            "text": text,
        }
        self._opened[rel] = info
        self._primary_relative = rel
        if rel not in self._sent_open:
            self._run(lambda: self._notify_open(info))
            self._sent_open.add(rel)
        return info

    def _normalize_symbols(self, result: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in result or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name is None:
                continue
            if "location" in item and isinstance(item["location"], dict):
                location = item["location"]
                line = (location.get("range") or {}).get("start", {}).get("line", 0)
            else:
                rng = item.get("range") or item.get("selectionRange") or {}
                line = (rng.get("start") or {}).get("line", 0)
            out.append(
                {
                    "name": name,
                    "kind": _humanize_kind(item.get("kind")) if item.get("kind") is not None else "Symbol",
                    "line": line,
                }
            )
            if "children" in item:
                out.extend(self._normalize_symbols(item.get("children")))
        return out

    def _normalize_references(self, result: Any) -> List[Dict[str, Any]]:
        known = {info["uri"]: info["path"] for info in self._opened.values()}
        out: List[Dict[str, Any]] = []
        for loc in result or []:
            if not isinstance(loc, dict):
                continue
            if "uri" in loc:  # Location
                uri = loc["uri"]
                line = (loc.get("range") or {}).get("start", {}).get("line", 0)
            elif "targetUri" in loc:  # LocationLink
                uri = loc["targetUri"]
                line = (loc.get("targetRange") or {}).get("start", {}).get("line", 0)
            else:
                continue
            out.append({"file": _uri_to_path(uri, known), "line": line})
        return out

    # ------------------------------------------------------------- loop thread

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._drive())
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _drive(self) -> None:
        """Transport lifecycle: handshake, then pump messages until the server exits."""
        self._reader_task = None
        self._stderr_task = None
        try:
            await self._connect_and_handshake()
        except Exception as exc:  # LSPUnavailableError / OSError / framing error
            self._start_error = exc
            self.running = False
            await self._teardown()
            self._ready.set()
            return
        self.running = True
        self._ready.set()
        try:
            if self._reader_task is not None:
                await self._reader_task
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            await self._teardown()

    async def _connect_and_handshake(self) -> None:
        cmd = self.server_command or []
        if not cmd:
            raise LSPUnavailableError(
                "refusing to spawn an empty language-server process"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_path,
            )
        except (OSError, ValueError) as exc:
            raise LSPUnavailableError(
                f"failed to spawn language server {cmd!r}: {exc}"
            ) from exc
        self._proc = proc
        self._reader = proc.stdout
        self._writer = proc.stdin
        self._write_lock = asyncio.Lock()
        self._diagnostics = {}  # fresh connection, fresh pushed diagnostics
        self._sent_open = set()
        if proc.stderr is not None:
            self._stderr_task = asyncio.ensure_future(self._drain_stderr(proc.stderr))
        self._reader_task = asyncio.ensure_future(self._read_loop())

        root = Path(self.workspace_path).resolve()
        result = await self._send_request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "haos-platform-lsp", "version": "1.0"},
                "rootUri": root.as_uri(),
                "rootPath": str(root),
                "capabilities": _CLIENT_CAPABILITIES,
            },
            timeout=_HANDSHAKE_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise LSPUnavailableError(
                f"language server returned a non-object initialize result: {result!r}"
            )
        self.server_info = result.get("serverInfo")
        self.server_capabilities = result.get("capabilities") or {}
        await self._send_notification("initialized", {})

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        """Keep a bounded tail of stderr so the server can never block on a full pipe."""
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                self._stderr_buffer.extend(chunk)
                if len(self._stderr_buffer) > _MAX_STDERR_KEEP:
                    del self._stderr_buffer[: len(self._stderr_buffer) - _MAX_STDERR_KEEP]
        except Exception:
            pass

    async def _read_loop(self) -> None:
        while True:
            try:
                msg = await read_message(self._reader)
            except asyncio.CancelledError:
                raise
            except (LSPProtocolError, EOFError, ConnectionError, OSError):
                break  # stream broke: server died or was terminated
            if msg is None:  # clean EOF
                break
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        kind, key = classify_message(msg)
        if kind == "response":
            future = self._pending.pop(key, None)
            if future is not None and not future.done():
                if "error" in msg:
                    err = msg["error"] or {}
                    future.set_exception(
                        LSPError(f"server error {err.get('code')}: {err.get('message')}")
                    )
                else:
                    future.set_result(msg.get("result"))
        elif kind == "notification":
            if key == "textDocument/publishDiagnostics":
                params = msg.get("params") or {}
                uri = params.get("uri")
                if uri:
                    self._diagnostics[uri] = list(params.get("diagnostics") or [])
        elif kind == "request":
            # We register no server->client handlers; answer method-not-found.
            asyncio.ensure_future(self._respond_server_request(key, msg.get("method")))

    async def _respond_server_request(self, req_id: Any, method: Any) -> None:
        try:
            async with self._write_lock:
                self._writer.write(
                    encode_message(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32601, "message": f"Method not found: {method}"},
                        }
                    )
                )
                await self._writer.drain()
        except Exception:
            pass

    async def _send_request(
        self, method: str, params: Any, timeout: float = _REQUEST_TIMEOUT
    ) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        req_id = self._next_id
        self._next_id += 1
        self._pending[req_id] = future
        try:
            try:
                async with self._write_lock:
                    self._writer.write(
                        encode_message(make_request(req_id, method, params))
                    )
                    await self._writer.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                raise LSPUnavailableError(
                    f"language server connection lost while sending {method}"
                ) from exc
            try:
                return await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                raise LSPUnavailableError(
                    f"language server did not respond to {method} within {timeout:g}s"
                ) from None
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification(self, method: str, params: Any) -> None:
        try:
            async with self._write_lock:
                self._writer.write(encode_message(make_notification(method, params)))
                await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise LSPUnavailableError(
                f"language server connection lost while sending {method}"
            ) from exc

    async def _request_symbols(self, uri: str) -> Any:
        return await self._send_request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )

    async def _request_references(self, uri: str, position: Dict[str, int]) -> Any:
        return await self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": position,
                "context": {"includeDeclaration": True},
            },
        )

    async def _notify_open(self, info: Dict[str, Any]) -> None:
        await self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": info["uri"],
                    "languageId": info["language_id"],
                    "version": info["version"],
                    "text": info["text"],
                }
            },
        )

    async def _collect_diagnostics(self) -> Dict[str, Any]:
        # Bounded settle wait: give the server a moment to push diagnostics for
        # opened files before concluding "no diagnostics".
        if self._opened:
            wanted = [info["uri"] for info in self._opened.values()]
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _DIAGNOSTICS_SETTLE_SECONDS
            while loop.time() < deadline:
                if any(uri in self._diagnostics for uri in wanted):
                    break
                await asyncio.sleep(0.05)
        known = {info["uri"]: info["path"] for info in self._opened.values()}
        new_errors = 0
        warnings = 0
        details: List[Dict[str, Any]] = []
        for uri, diags in self._diagnostics.items():
            for diag in diags:
                if not isinstance(diag, dict):
                    continue
                severity = diag.get("severity", 1)
                if severity == 1:
                    new_errors += 1
                elif severity == 2:
                    warnings += 1
                line = (diag.get("range") or {}).get("start", {}).get("line", 0)
                details.append(
                    {
                        "file": _uri_to_path(uri, known),
                        "line": line,
                        "message": diag.get("message", ""),
                        "severity": severity,
                    }
                )
        return {"new_errors": new_errors, "warnings": warnings, "details": details}

    async def _shutdown_coro(self) -> None:
        if not self.running:
            await self._teardown()
            return
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                await self._send_request("shutdown", None, timeout=_REQUEST_TIMEOUT)
            except (LSPError, LSPUnavailableError):
                pass  # error answer to shutdown is still a conversation end
            try:
                await self._send_notification("exit")
            except Exception:
                pass
            # Grace window for a well-behaved server to exit on its own after
            # `exit`; only force-terminate when it does not.
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
        await self._teardown()

    async def _teardown(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), _PROCESS_TERMINATE_TIMEOUT)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), _PROCESS_TERMINATE_TIMEOUT)
                except asyncio.TimeoutError:
                    pass
        for attr in ("_reader_task", "_stderr_task"):
            task = getattr(self, attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        writer = self._writer
        if writer is not None:
            try:
                if not writer.is_closing():
                    writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    LSPUnavailableError("language server closed before responding")
                )
        self._pending.clear()
        self._reader = None
        self._writer = None
        self.running = False

    def _force_kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass


class StaticLSPClient(LSPClient):
    """Explicit static stub — it does NOT speak LSP ("stub explícito, não fala LSP").

    No subprocess, no JSON-RPC, no wire protocol: this is the old hard-coded
    mock made explicit. It returns small, deterministic, disk-free answers so
    callers with no real language server keep the legacy contract (non-empty
    symbols, zero new errors) without ever being passed off as a real client
    (see ``is_stub``).
    """

    is_stub = True

    def __init__(self, workspace_path: str, language: str = "python") -> None:
        # Deliberately NOT calling LSPClient.__init__: there is nothing to spawn.
        self.workspace_path = workspace_path
        self.language = language
        self.running = True
        self.server_command: Optional[List[str]] = None

    def start(self) -> None:
        """No-op: a stub has no language server to start."""

    def shutdown(self) -> None:
        """No-op: a stub has no process to stop."""

    def get_document_symbols(self, relative_file_path: str) -> List[Dict[str, Any]]:
        stem = Path(relative_file_path.replace("\\", "/")).stem
        name = (
            "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", stem) if p)
            or "Symbol"
        )
        return [{"name": name, "kind": "Class", "line": 0}]

    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        return []

    def get_diagnostics(self) -> Dict[str, Any]:
        return {"new_errors": 0, "warnings": 0, "details": []}


class LSPManager:
    """Hands out per-workspace LSP clients, memoized by ``workspace_path``.

    Resolution order for :meth:`get_client`: an explicit ``server_command``
    argument wins; otherwise :meth:`_default_server_command` resolves one for
    the language. When a command resolves, a real :class:`LSPClient` is
    returned (NOT auto-started — spawning is lazy, see :meth:`LSPClient.start`);
    when none does, the explicit :class:`StaticLSPClient` stub is returned.
    Like the legacy manager, memoization is keyed on ``workspace_path`` alone
    (first resolution wins), so repeated calls for the same workspace return
    the same instance regardless of the ``language`` argument.
    """

    def __init__(self) -> None:
        self._instances: Dict[str, LSPClient] = {}

    def get_client(
        self,
        workspace_path: str,
        language: str = "python",
        *,
        server_command: Optional[List[str]] = None,
    ) -> LSPClient:
        existing = self._instances.get(workspace_path)
        if existing is not None:
            return existing
        resolved = (
            server_command
            if server_command is not None
            else self._default_server_command(language)
        )
        if resolved:
            client: LSPClient = LSPClient(workspace_path, language, server_command=resolved)
        else:
            client = StaticLSPClient(workspace_path, language)
        self._instances[workspace_path] = client
        return client

    @staticmethod
    def _default_server_command(language: str) -> Optional[List[str]]:
        """Resolve a language-server argv for ``language``, or ``None`` (stub).

        No real server is auto-spawned: pyright/gopls/... are not assumed to be
        installed in this environment. An explicit opt-in env var
        ``HAOS_LSP_SERVER_<LANG>`` (e.g. ``HAOS_LSP_SERVER_PYTHON="pyright-langserver
        --stdio"``) resolves when present; otherwise ``None`` means "use the
        static stub".
        """
        var = "HAOS_LSP_SERVER_" + language.upper().replace("-", "_")
        raw = os.environ.get(var)
        if raw:
            return shlex.split(raw)
        return None
