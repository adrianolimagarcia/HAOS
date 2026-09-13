#!/usr/bin/env python3
"""Peer ANP scriptado (tests only): discovery well-known + JSON-RPC /rpc.

Espelho do padrão _mock_lsp_server/_fake_hermes: servidor stdlib em thread que
emula o domínio remoto de um agente ANP para os testes de contrato.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes.platform.protocols.anp.identity import DID
from hermes.platform.protocols.a2a.client import rpc_error

_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_SERVER_ERROR = -32000


class ANPPeer:
    def __init__(self, descriptions: List[Dict[str, Any]],
                 on_message: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None):
        self.descriptions = descriptions
        self.on_message = on_message or (lambda m: {
            "messageId": m.get("messageId"), "status": "delivered",
        })
        self.received: List[Dict[str, Any]] = []

    def handle_rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return rpc_error(_INVALID_PARAMS, "request must be an object")
        rid = payload.get("id")
        if payload.get("jsonrpc") != "2.0":
            return rpc_error(_INVALID_PARAMS, "jsonrpc must be '2.0'", request_id=rid)
        method = payload.get("method")
        params = payload.get("params")
        if method == "anp.get_capabilities":
            return {"jsonrpc": "2.0", "id": rid, "result": ["anp.messaging.v1"]}
        if method == "anp.message.send":
            if not isinstance(params, dict) or not isinstance(
                    params.get("body"), dict):
                return rpc_error(_INVALID_PARAMS, "params.body required", request_id=rid)
            message = params["body"].get("message")
            if not isinstance(message, dict):
                return rpc_error(_INVALID_PARAMS, "message required", request_id=rid)
            try:
                DID.parse(message.get("sender", ""))
                DID.parse(message.get("recipient", ""))
            except ValueError as exc:
                return rpc_error(_INVALID_PARAMS, f"invalid did: {exc}", request_id=rid)
            auth = params.get("auth")
            if not isinstance(auth, dict) or not auth.get("signature") \
                    or not auth.get("digest"):
                return rpc_error(_INVALID_PARAMS,
                                 "auth.signature/auth.digest required (fail-closed)",
                                 request_id=rid)
            self.received.append(message)
            try:
                result = self.on_message(message)
            except Exception as exc:
                return rpc_error(_SERVER_ERROR, str(exc), request_id=rid)
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        return rpc_error(_METHOD_NOT_FOUND, f"unknown {method!r}", request_id=rid)


def make_anp_http_server(
    descriptions: List[Dict[str, Any]],
    on_message: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> Tuple[ThreadingHTTPServer, str, threading.Thread, ANPPeer]:
    peer = ANPPeer(descriptions, on_message)

    class _Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.command == "GET" and self.path == "/.well-known/agent-descriptions":
                payload = json.dumps({"agents": descriptions}).encode("utf-8")
                status = 200
            elif self.command == "POST" and self.path == "/rpc":
                try:
                    req = json.loads(body.decode("utf-8"))
                    response = peer.handle_rpc(req)
                except (ValueError, UnicodeDecodeError) as exc:
                    response = rpc_error(_INVALID_PARAMS, f"bad json: {exc}")
                payload = json.dumps(response).encode("utf-8")
                status = 200
            else:
                payload = b'{"error":"not found"}'
                status = 404
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer((host, port), _Handler)
    base = f"http://{host}:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base, thread, peer
