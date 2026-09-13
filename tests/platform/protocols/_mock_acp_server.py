#!/usr/bin/env python3
"""Scripted ACP agent peer for the HAOS ACP contract tests (K5).

Speaks the ACP 0.9 wire — NEWLINE-delimited JSON-RPC 2.0 over stdio — purely
with the stdlib, mirroring the shapes used by the upstream ``acp`` package /
``acp_adapter``, so ``hermes.platform.protocols.acp.adapter`` can be tested
hermetically without importing either.  Run as ``python3 _mock_acp_server.py``.

The peer answers:

* ``initialize``     -> protocolVersion 1, agentInfo {name: "mock-acp-agent",
                        version: "0.0.1"}, agentCapabilities {...}, authMethods [];
  the version can be overridden with ``MOCK_ACP_PROTOCOL_VERSION`` to test
  handshake mismatch handling;
* ``session/new``    -> {"sessionId": "sess-mock-1", "models": [], "modes": []};
* ``session/prompt`` -> a ``session/update`` notification first, then a text
  result, so the client's reader must tolerate interleaved pushes;
* ``session/close``  -> null;
* anything else      -> null (permissive for a scaffold peer).

Every received request is optionally appended as one JSON line per request to
the file named by the ``MOCK_ACP_RECORD`` environment variable; the tests read
that file back to assert the camelCase wire shapes the client actually sends.
The peer exits cleanly when stdin reaches EOF.
"""

import json
import os
import sys

SESSION_ID = "sess-mock-1"


def _initialize_result() -> dict:
    try:
        protocol_version = int(os.environ.get("MOCK_ACP_PROTOCOL_VERSION", "1"))
    except ValueError:
        protocol_version = 1
    return {
        "protocolVersion": protocol_version,
        "agentInfo": {"name": "mock-acp-agent", "version": "0.0.1"},
        "agentCapabilities": {
            "fs": {},
            "terminal": {},
            "auth": {},
            "mcp": {},
            "tools": {},
        },
        "authMethods": [],
    }


def _response(rid: int, result=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid}
    if result is not None:
        frame["result"] = result
    else:
        frame["result"] = None
    return frame


def handle(request: dict) -> list:
    """Return the frames to send for one request ([] for notifications)."""
    rid = request.get("id")
    if rid is None:
        # A client notification has no response.
        return []
    method = request.get("method")
    params = request.get("params") or {}
    session_id = params.get("sessionId", SESSION_ID)

    frames = []
    if method == "initialize":
        frames.append(_response(rid, result=_initialize_result()))
    elif method == "session/new":
        frames.append(
            _response(
                rid,
                result={
                    "sessionId": SESSION_ID,
                    "models": [],
                    "modes": [],
                },
            )
        )
    elif method == "session/prompt":
        # Push a server notification before answering: the client reader must
        # record it and still correlate the response by id.
        frames.append(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "status": "working"},
            }
        )
        frames.append(
            _response(rid, result={"result": [{"type": "text", "text": "ok"}]})
        )
    elif method == "session/close":
        frames.append(_response(rid, result=None))
    else:
        frames.append(_response(rid, result=None))
    return frames


def main() -> int:
    record_path = os.environ.get("MOCK_ACP_RECORD")
    record_file = None
    if record_path:
        record_file = open(record_path, "a", encoding="utf-8")
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if not isinstance(request, dict):
                continue
            if record_file is not None:
                record_file.write(
                    json.dumps(request, separators=(",", ":")) + "\n"
                )
                record_file.flush()
            for frame in handle(request):
                sys.stdout.write(
                    json.dumps(frame, separators=(",", ":")) + "\n"
                )
                sys.stdout.flush()
    finally:
        if record_file is not None:
            record_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
