"""Scripted LSP server for contract tests (stdlib only, real subprocess).

Spawned as ``[sys.executable, _mock_lsp_server.py]`` and speaks Content-Length
framed JSON-RPC 2.0 on stdio. It implements just enough LSP to exercise the
real client: initialize/initialized, textDocument/didOpen (stores the text and
pushes ONE publishDiagnostics notification — one error + one warning — after a
short delay), textDocument/documentSymbol (a DocumentSymbol derived from the
stored text, e.g. ``class X`` -> kind 5), textDocument/references (a fixed
Location or an empty array), shutdown/exit.

Reads frames from stdin until EOF and exits cleanly. I/O goes through raw
``os.read``/``os.write`` on the pipe fds: Python's buffered ``stdin.buffer``
reads can stall waiting to fill their internal buffer on some platforms, and
an LSP server must act on the first frame immediately.
"""

import json
import os
import re
import sys
import threading
import time

_STDIN_FD = 0
_STDOUT_FD = 1

_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE)

# Documents known to the server: uri -> text.
_documents = {}
_pushed = set()


def _encode(obj):
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def _write(obj):
    """Write one framed message; os.write is atomic for these small payloads."""
    data = _encode(obj)
    view = memoryview(data)
    while view:
        written = os.write(_STDOUT_FD, view)
        view = view[written:]


def _respond(req_id, result):
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def _respond_error(req_id, code, message):
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _push_diagnostics(uri, delay):
    """Push one publishDiagnostics notification after ``delay`` seconds."""

    def _run():
        try:
            time.sleep(delay)
            diagnostics = [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "severity": 1,
                    "message": "mock error",
                },
                {
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 1},
                    },
                    "severity": 2,
                    "message": "mock warning",
                },
            ]
            _write(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {"uri": uri, "diagnostics": diagnostics},
                }
            )
        except Exception:
            # The client may have gone away (shutdown/exit); that is fine.
            pass

    threading.Thread(target=_run, daemon=True).start()


def _document_symbols(text):
    symbols = []
    for match in _CLASS_RE.finditer(text):
        line = text.count("\n", 0, match.start())
        end_line = line + 1
        symbols.append(
            {
                "name": match.group(1),
                "kind": 5,  # Class
                "range": {
                    "start": {"line": line, "character": 0},
                    "end": {"line": end_line, "character": 0},
                },
                "selectionRange": {
                    "start": {"line": line, "character": 0},
                    "end": {"line": line, "character": len(match.group(1))},
                },
            }
        )
    return symbols


def _first_class_line(text):
    match = _CLASS_RE.search(text)
    if match is None:
        return None
    return text.count("\n", 0, match.start())


def _handle_request(req_id, method, params):
    if method == "initialize":
        _respond(
            req_id,
            {
                "serverInfo": {"name": "mock-lsp", "version": "0.0.1"},
                "capabilities": {
                    "textDocumentSync": 1,  # full sync
                    "documentSymbolProvider": True,
                    "referencesProvider": True,
                    "diagnosticProvider": {
                        "interFileDependencies": False,
                        "workspaceDiagnostics": False,
                    },
                },
            },
        )
    elif method == "shutdown":
        _respond(req_id, None)
    elif method == "textDocument/documentSymbol":
        uri = (params or {}).get("textDocument", {}).get("uri")
        text = _documents.get(uri, "")
        _respond(req_id, _document_symbols(text))
    elif method == "textDocument/references":
        uri = (params or {}).get("textDocument", {}).get("uri")
        text = _documents.get(uri, "")
        line = _first_class_line(text)
        if line is None:
            _respond(req_id, [])
        else:
            _respond(
                req_id,
                [
                    {
                        "uri": uri,
                        "range": {
                            "start": {"line": line, "character": 0},
                            "end": {"line": line, "character": 6},
                        },
                    }
                ],
            )
    else:
        _respond_error(req_id, -32601, f"Method not found: {method}")


def _handle_notification(method, params):
    if method == "initialized":
        return  # nothing to do
    if method == "textDocument/didOpen":
        td = (params or {}).get("textDocument", {})
        uri = td.get("uri")
        text = td.get("text", "")
        if uri is not None:
            _documents[uri] = text
            if uri not in _pushed:
                _pushed.add(uri)
                _push_diagnostics(uri, delay=0.3)
    elif method == "textDocument/didChange":
        # Keep the stored text fresh for later documentSymbol/references requests.
        td = (params or {}).get("textDocument", {})
        uri = td.get("uri")
        changes = (params or {}).get("contentChanges", [])
        if uri is not None and changes:
            _documents[uri] = changes[-1].get("text", _documents.get(uri, ""))
    elif method == "exit":
        os._exit(0)


class _Reader:
    """Frame reader for Content-Length framed LSP messages, unbuffered fds."""

    def __init__(self):
        self._buffer = b""

    def read_frame(self):
        # Returns the parsed JSON object or None on clean EOF.
        while b"\r\n\r\n" not in self._buffer:
            chunk = os.read(_STDIN_FD, 4096)
            if not chunk:
                return None
            self._buffer += chunk
        header, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        content_length = None
        for line in header.split(b"\r\n"):
            key, _, value = line.partition(b":")
            if key.strip().lower() == b"content-length":
                content_length = int(value.strip())
        if content_length is None:
            return None
        while len(self._buffer) < content_length:
            chunk = os.read(_STDIN_FD, 4096)
            if not chunk:
                return None
            self._buffer += chunk
        body = self._buffer[:content_length]
        self._buffer = self._buffer[content_length:]
        return json.loads(body.decode("utf-8"))


def main():
    reader = _Reader()
    while True:
        msg = reader.read_frame()
        if msg is None:  # clean EOF: client went away
            break
        if not isinstance(msg, dict):
            continue
        if "id" in msg and "method" in msg:
            _handle_request(msg["id"], msg["method"], msg.get("params"))
        elif "method" in msg:
            _handle_notification(msg["method"], msg.get("params"))
        # A bare response from the client is not expected; ignore it.


if __name__ == "__main__":
    main()
