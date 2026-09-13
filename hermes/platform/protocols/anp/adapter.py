"""B2 — ANP real: discovery (ADP/ADP listings) + mensagens E2E (stdlib).

Camadas do mapa: identity DID (identity.py) -> discovery (fetch de agent
descriptions via ``/.well-known/agent-descriptions`` e registry local) ->
get_capabilities (JSON-RPC ``anp.get_capabilities``, ANP-06) -> mensagens E2E
(JSON-RPC 2.0 core binding, params meta/body/auth). Assinatura é SEAM
(signer: bytes->str); sem signer o envio falha fechado — nunca mensagem
anônima. O nome do método de envio é ``anp.message.send`` (binding local do
ANP Messaging; a suite 1.1 de sub-profile não está presente na referência).
Transporte: seam injetável igual ao A2A (urllib default).
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes.platform.protocols.anp.identity import (
    ANPIdentityError, DID,
)
from hermes.platform.protocols.a2a.client import (
    Transport, _default_transport, parse_rpc_response,
)

# ---------------------------------------------------------------------- #
# Erros
# ---------------------------------------------------------------------- #
class ANPError(RuntimeError):
    pass


class ANPRemoteError(ANPError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"ANP remote error {code}: {message}")
        self.code = code
        self.remote_message = message
        self.data = data


class ANPProtocolError(ANPError):
    pass


# ---------------------------------------------------------------------- #
# Agent Description (ADP mínimo)
# ---------------------------------------------------------------------- #
@dataclass
class AgentDescription:
    did: DID
    name: str
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    interfaces: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "AgentDescription":
        if not isinstance(raw, dict):
            raise ANPProtocolError("agent description must be an object")
        did_str = raw.get("did")
        if not did_str:
            raise ANPProtocolError("agent description without 'did'")
        did = DID.parse(did_str)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ANPProtocolError(
                f"agent description '{did.did}' without a valid 'name'")
        return AgentDescription(
            did=did, name=name,
            description=raw.get("description", ""),
            capabilities=list(raw.get("capabilities") or []),
            interfaces=list(raw.get("interfaces") or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "did": self.did.did, "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
        }
        if self.interfaces:
            out["interfaces"] = self.interfaces
        return out


def agent_descriptions_url(domain: str) -> str:
    return f"https://{domain}/.well-known/agent-descriptions"


def fetch_agent_descriptions(
    domain: str,
    transport: Optional[Transport] = None,
) -> List[AgentDescription]:
    """Discovery real via well-known: GET devolve {agents: [...]}."""
    http = transport or _default_transport
    status, body = http("GET", agent_descriptions_url(domain), None,
                        {"Accept": "application/json"})
    if status < 200 or status >= 300:
        raise ANPProtocolError(
            f"agent descriptions fetch failed: HTTP {status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ANPProtocolError(f"descriptions not JSON: {exc}") from exc
    entries = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ANPProtocolError(
            "agent descriptions payload must contain an 'agents' array")
    return [AgentDescription.from_dict(e) for e in entries]


class DiscoveryRegistry:
    """Registro local de agentes (registro + busca por capability)."""

    def __init__(self):
        self._records: Dict[str, AgentDescription] = {}

    def register(self, description: AgentDescription) -> None:
        self._records[description.did.did] = description

    def unregister(self, did: str) -> bool:
        return self._records.pop(did, None) is not None

    def get(self, did: str) -> Optional[AgentDescription]:
        return self._records.get(did)

    def search(self, capability: Optional[str] = None,
               name_query: Optional[str] = None) -> List[AgentDescription]:
        out = []
        lowered = (name_query or "").lower()
        for desc in self._records.values():
            if capability and capability not in desc.capabilities:
                continue
            if name_query and lowered not in desc.name.lower() \
                    and lowered not in desc.description.lower():
                continue
            out.append(desc)
        return out


# ---------------------------------------------------------------------- #
# Mensagem E2E (envelope + digest) — ANP Messaging binding local
# ---------------------------------------------------------------------- #
GET_CAPABILITIES_METHOD = "anp.get_capabilities"
SEND_MESSAGE_METHOD = "anp.message.send"
MESSAGING_PROFILE = "anp.messaging.v1"


@dataclass
class ANPMessage:
    message_id: str
    sender: DID
    recipient: DID
    content: str = ""
    content_type: str = "text/plain"
    profile: str = MESSAGING_PROFILE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messageId": self.message_id,
            "sender": self.sender.did,
            "recipient": self.recipient.did,
            "contentType": self.content_type,
            "content": self.content,
            "profile": self.profile,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), sort_keys=True,
                          ensure_ascii=True).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


Signer = Callable[[bytes], str]


def _rpc_request(method: str, params: Dict[str, Any],
                 request_id: Optional[Any] = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params}


# ---------------------------------------------------------------------- #
# Serviço (cliente JSON-RPC)
# ---------------------------------------------------------------------- #
class ANPMessageService:
    """Cliente do ANPMessageService de um agente remoto (JSON-RPC)."""

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or _default_transport

    def get_capabilities(self, service_url: str, *,
                         target_did: Optional[str] = None) -> List[str]:
        params: Dict[str, Any] = {}
        if target_did:
            params["target"] = {"kind": "did", "did": target_did}
        request = _rpc_request(GET_CAPABILITIES_METHOD, params)
        result = self._call(service_url, request)
        if not isinstance(result, list):
            raise ANPProtocolError("anp.get_capabilities result must be a list")
        return [str(item) for item in result]

    def send_message(self, service_url: str, message: ANPMessage, *,
                     signer: Signer, request_id: Optional[Any] = None,
                     ) -> Dict[str, Any]:
        if signer is None:
            raise ANPProtocolError(
                "ANP messaging is fail-closed: a signer is required")
        signature = signer(message.canonical_bytes())
        params = {
            "meta": {"profile": message.profile},
            "body": {"message": message.to_dict()},
            "auth": {"digest": message.digest(), "signature": signature},
        }
        request = _rpc_request(SEND_MESSAGE_METHOD, params,
                               request_id=request_id)
        result = self._call(service_url, request)
        if not isinstance(result, dict):
            raise ANPProtocolError("send result must be an object")
        return result

    def _call(self, service_url: str, request: Dict[str, Any]) -> Any:
        body = json.dumps(request).encode("utf-8")
        status, resp_body = self.transport(
            "POST", service_url, body,
            {"Content-Type": "application/json",
             "Accept": "application/json"},
        )
        try:
            payload = json.loads(resp_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ANPProtocolError(
                f"ANP JSON-RPC response not JSON (HTTP {status}): {exc}"
            ) from exc
        result, remote_error = parse_rpc_response(payload)
        if remote_error is not None:
            raise ANPRemoteError(remote_error.code,
                                 remote_error.remote_message,
                                 remote_error.data)
        return result


# ---------------------------------------------------------------------- #
# Adapter (fachada do mapa) — preserva o nome ANPAdapter do scaffold
# ---------------------------------------------------------------------- #
@dataclass
class ANPIdentity:
    """Visão de identidade usada pelo adapter (compatível com o stub v1)."""

    did: str
    name: str
    capabilities: List[str] = field(default_factory=list)

    @property
    def parsed(self) -> DID:
        return DID.parse(self.did)


class ANPAdapter:
    """Fachada ANP: identidade did:wba + discovery + mensagens E2E."""

    def __init__(self, transport: Optional[Transport] = None,
                 registry: Optional[DiscoveryRegistry] = None):
        self.transport = transport or _default_transport
        self.registry = registry or DiscoveryRegistry()
        self.service = ANPMessageService(transport=self.transport)

    # identity
    def parse_identity(self, did: str) -> DID:
        return DID.parse(did)

    # discovery
    def register(self, description: AgentDescription) -> None:
        self.registry.register(description)

    def discover(self, capability: Optional[str] = None,
                 name_query: Optional[str] = None) -> List[AgentDescription]:
        return self.registry.search(capability, name_query)

    def fetch_remote(self, domain: str) -> List[AgentDescription]:
        return fetch_agent_descriptions(domain, self.transport)

    # messaging
    def send_message(self, service_url: str, message: ANPMessage, *,
                     signer: Signer) -> Dict[str, Any]:
        return self.service.send_message(service_url, message, signer=signer)
