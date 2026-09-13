"""B1 — A2A: lado servidor/responder (stdlib http.server, p/ peers e testes).

``A2AResponder`` implementa o dispatch JSON-RPC do agente: ``SendMessage``
(PascalCase v1) delega num callback; método desconhecido => -32601; params
inválidos => -32602; exceção do callback => -32000 com mensagem (fail-closed,
nunca vaza traceback). ``make_a2a_http_server`` serve o card público em
``/.well-known/agent-card.json`` e ``POST /message:send`` em thread stdlib —
é o PEER scriptado usado pelos testes (mesma filosofia do _mock_lsp_server).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

from hermes.platform.protocols.a2a.client import (
    AgentCard, A2AProtocolError, A2ARemoteError,
    parse_rpc_response, rpc_error, rpc_result,
)

_CARD_PATH = "/.well-known/agent-card.json"
_SEND_PATH = "/message:send"

_SERVER_ERROR = -32000
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


class A2AResponder:
    """Dispatch JSON-RPC de um agente A2A (sem transporte próprio)."""

    def __init__(self, card: AgentCard,
                 on_send_message: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.card = card
        self.on_send_message = on_send_message

    def handle_json_rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return rpc_error(_INVALID_PARAMS, "request must be an object",
                             data={"detail": "not a JSON object"})
        request_id = payload.get("id")
        method = payload.get("method")
        if payload.get("jsonrpc") != "2.0":
            return rpc_error(_INVALID_PARAMS, "jsonrpc must be '2.0'",
                             request_id=request_id)
        if not isinstance(method, str) or not method:
            return rpc_error(_METHOD_NOT_FOUND, "method missing",
                             request_id=request_id)
        params = payload.get("params")
        if not isinstance(params, dict):
            return rpc_error(_INVALID_PARAMS, "params must be an object",
                             request_id=request_id)
        if method == "SendMessage":
            if "message" not in params:
                return rpc_error(_INVALID_PARAMS,
                                 "SendMessage requires 'message'",
                                 request_id=request_id)
            try:
                result = self.on_send_message(params["message"])
            except A2AProtocolError as exc:
                return rpc_error(_INVALID_PARAMS, str(exc),
                                 request_id=request_id)
            except A2ARemoteError as exc:
                # Erro remoto explícito passa com o próprio code (o agente
                # pode querer propagar -32001 etc. de um upstream).
                return rpc_error(exc.code, exc.remote_message,
                                 request_id=request_id)
            except Exception as exc:  # callback quebrou: erro genérico
                return rpc_error(_SERVER_ERROR, str(exc),
                                 request_id=request_id)
            return rpc_result(result, request_id=request_id)
        return rpc_error(_METHOD_NOT_FOUND,
                         f"unknown method {method!r}",
                         request_id=request_id)

    def handle_http(self, method: str, path: str, body: bytes
                    ) -> Tuple[int, bytes]:
        if method == "GET" and path == _CARD_PATH:
            raw = json.dumps(self.card.to_dict()).encode("utf-8")
            return 200, raw
        if method == "POST" and path == _SEND_PATH:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                return 200, json.dumps(
                    rpc_error(_INVALID_PARAMS, f"bad JSON: {exc}")).encode("utf-8")
            response = self.handle_json_rpc(payload)
            return 200, json.dumps(response).encode("utf-8")
        return 404, b'{"error": "not found"}'


def make_a2a_http_server(
    card: AgentCard,
    on_send_message: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> Tuple[ThreadingHTTPServer, str, threading.Thread]:
    """Servidor HTTP stdlib em thread: peer A2A real p/ testes/contrato."""
    responder = A2AResponder(card=card, on_send_message=on_send_message)

    class _Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = responder.handle_http(
                self.command, self.path, body)
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
            pass  # peer scriptado não espalha log

    server = ThreadingHTTPServer((host, port), _Handler)
    base_url = f"http://{host}:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base_url, thread
