"""B1 — A2A v1: wire JSON-RPC 2.0 stdlib (protocolos/a2a/).

Escopo mínimo do mapa: enviar/responder Card entre agentes externos via
JSON-RPC 2.0 sobre HTTP, método PascalCase ``SendMessage`` (spec v1 §9;
method-mapping §5.3), agent card público em ``/.well-known/agent-card.json``.

Sem runtime externo no processo: o transporte é um SEAM injetável
(``transport(method, path, body, headers) -> (status, body_bytes)``) com um
default stdlib (urllib). Falha fecha: resposta JSON-RPC com ``error`` =>
``A2ARemoteError``; card sem ``name`` => ``A2AProtocolError``.
"""

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

JSONRPC = "2.0"


# ---------------------------------------------------------------------- #
# Erros
# ---------------------------------------------------------------------- #
class A2AError(RuntimeError):
    pass


class A2AProtocolError(A2AError):
    """Resposta/objeto que viola o contrato A2A (fail-closed)."""


class A2ARemoteError(A2AError):
    """Erro JSON-RPC reportado pelo agente remoto."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"A2A remote error {code}: {message}")
        self.code = code
        self.remote_message = message
        self.data = data


# ---------------------------------------------------------------------- #
# Modelos mínimos (A2A v1: AgentCard, Message, Part)
# ---------------------------------------------------------------------- #
@dataclass
class AgentCard:
    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0"
    capabilities: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "AgentCard":
        if not isinstance(raw, dict):
            raise A2AProtocolError("agent card must be a JSON object")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise A2AProtocolError("agent card without a valid 'name'")
        return AgentCard(
            name=name,
            description=raw.get("description", ""),
            url=raw.get("url", ""),
            version=str(raw.get("version", "1.0")),
            capabilities=raw.get("capabilities") or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
        }
        if self.capabilities:
            out["capabilities"] = self.capabilities
        return out


def _part_dict(part: Any) -> Dict[str, Any]:
    if isinstance(part, dict):
        if "kind" not in part:
            raise A2AProtocolError("A2A part dict requires 'kind'")
        return dict(part)
    if hasattr(part, "to_dict"):
        return part.to_dict()
    raise A2AProtocolError(
        f"unsupported A2A part type: {type(part).__name__} (dict or to_dict())"
    )


@dataclass
class A2AMessage:
    role: str  # user | agent
    parts: List[Any] = field(default_factory=list)  # dicts ou objetos to_dict
    message_id: Optional[str] = None
    kind: str = "message"
    context_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        if self.role not in ("user", "agent"):
            raise A2AProtocolError(f"invalid A2A role: {self.role!r}")
        return {
            "role": self.role,
            "messageId": self.message_id,
            "kind": self.kind,
            "parts": [_part_dict(p) for p in self.parts],
            **({"contextId": self.context_id} if self.context_id else {}),
        }


def text_part(text: str) -> Dict[str, Any]:
    return {"kind": "text", "text": text}


def file_part(uri: str, mime_type: str = "") -> Dict[str, Any]:
    return {"kind": "file", "file": {"uri": uri, "mimeType": mime_type}}


# ---------------------------------------------------------------------- #
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------- #
def rpc_request(method: str, params: Dict[str, Any],
                request_id: Optional[Any] = None) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": request_id,
            "method": method, "params": params}


def rpc_result(result: Any, request_id: Optional[Any] = None) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": request_id, "result": result}


def rpc_error(code: int, message: str,
              request_id: Optional[Any] = None,
              data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC, "id": request_id, "error": error}


class JsonRpcParseError(A2AProtocolError):
    pass


def parse_rpc_response(payload: Dict[str, Any]) -> Tuple[Optional[Any],
                                                         Optional[A2ARemoteError]]:
    """(result, error) — valida jsonrpc 2.0 e a presença de exatamente um."""
    if not isinstance(payload, dict):
        raise JsonRpcParseError("JSON-RPC response must be an object")
    if payload.get("jsonrpc") != JSONRPC:
        raise JsonRpcParseError(f"jsonrpc version must be '{JSONRPC}'")
    has_result = "result" in payload
    has_error = "error" in payload
    if has_result == has_error:
        raise JsonRpcParseError("JSON-RPC response needs exactly one of "
                                "result/error")
    if has_error:
        err = payload["error"]
        code = err.get("code") if isinstance(err, dict) else -32603
        message = err.get("message", "?") if isinstance(err, dict) else "?"
        remote = A2ARemoteError(code, message,
                                err.get("data") if isinstance(err, dict) else None)
        return None, remote
    return payload["result"], None


# ---------------------------------------------------------------------- #
# Transport seam + client
# ---------------------------------------------------------------------- #
Transport = Callable[[str, str, Optional[bytes], Dict[str, str]],
                     Tuple[int, bytes]]

_CARD_PATH = "/.well-known/agent-card.json"
_SEND_PATH = "/message:send"


def _default_transport(method: str, path: str, body: Optional[bytes],
                       headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(path, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise A2AError(f"transport failure: {exc}") from exc


class A2AClient:
    """Cliente A2A: card público + SendMessage (JSON-RPC 2.0, PascalCase)."""

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or _default_transport

    def fetch_agent_card(self, base_url: str) -> AgentCard:
        status, body = self.transport(
            "GET", f"{base_url.rstrip('/')}{_CARD_PATH}", None,
            {"Accept": "application/json"},
        )
        if status < 200 or status >= 300:
            raise A2AProtocolError(
                f"agent card fetch failed: HTTP {status}"
            )
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AProtocolError(f"agent card is not valid JSON: {exc}") from exc
        return AgentCard.from_dict(raw)

    def send_message(self, base_url: str, message: A2AMessage,
                     *, request_id: Optional[Any] = None) -> Dict[str, Any]:
        request = rpc_request("SendMessage", {
            "message": message.to_dict(),
            "configuration": {},
        }, request_id=request_id)
        body = json.dumps(request).encode("utf-8")
        status, resp_body = self.transport(
            "POST", f"{base_url.rstrip('/')}{_SEND_PATH}", body,
            {"Content-Type": "application/json",
             "Accept": "application/json"},
        )
        try:
            payload = json.loads(resp_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AProtocolError(
                f"SendMessage response not JSON (HTTP {status}): {exc}"
            ) from exc
        result, remote_error = parse_rpc_response(payload)
        if remote_error is not None:
            raise remote_error
        if not isinstance(result, dict):
            raise A2AProtocolError("SendMessage result must be an object")
        return {
            "message": result.get("message"),
            "artifacts": result.get("artifacts") or [],
            "task": result.get("task"),
        }
