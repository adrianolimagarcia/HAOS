"""B2 — ANP real: did:wba identity + discovery + mensagens E2E (invariantes).

Contratos:
(a) identity: parse/validação de did:wba (domínio, caminho, perfil de chave
    embutido); inválido -> ANPIdentityError; round-trip str(parse(x))==x;
    fingerprint determinístico (mesma chave => mesmo fp; chaves distintas =>
    distintos); DIDDocument build/from_dict valida 'id'.
(b) discovery: fetch via /.well-known/agent-descriptions valida entradas;
    DiscoveryRegistry busca por capability/name (local).
(c) mensagens E2E: digest estável; envio SEM signer falha fechado; peer valida
    DIDs e auth; entrega ok; sender inválido -> -32602; método desconhecido
    -> -32601.
"""

import json
import unittest
import urllib.request

from hermes.platform.protocols.anp.identity import (
    DID, ANPIdentityError,
    public_key_fingerprint, did_with_key,
    DIDDocument, did_wba_document_url,
)
from hermes.platform.protocols.anp.adapter import (
    ANPAdapter, ANPMessage, AgentDescription,
    ANPProtocolError, ANPRemoteError,
    DiscoveryRegistry, fetch_agent_descriptions,
)
from tests.platform.protocols._mock_anp_server import make_anp_http_server

PK1 = "a1" * 32
PK2 = "b2" * 32
DID_ALICE = "did:wba:example.com:alice"
DID_BOB = "did:wba:example.com:bob"


def _map_transport(base: str):
    """Transporte de teste: resolve https://example.com -> servidor local."""

    def transport(method: str, url: str, body: bytes,
                  headers: dict):
        url = url.replace("https://example.com", base)
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()

    return transport


class TestIdentity(unittest.TestCase):
    def test_parse_and_round_trip(self):
        did = DID.parse("did:wba:example.com:alice:pk_e1_" + "0" * 38)
        self.assertEqual(did.domain, "example.com")
        self.assertEqual(did.agent_id, "alice")
        self.assertEqual(did.key_profile, "e1_")
        self.assertEqual(str(did),
                         "did:wba:example.com:alice:pk_e1_" + "0" * 38)

    def test_invalid_dids_fail_closed(self):
        for bad in ("did:web:example.com:a", "did:wba:example.com",
                    "did:wba:Upper.com:a", "did:wba:example.com:",
                    "did:wba:example.com:a:pk_x1_zz"):
            with self.assertRaises(ANPIdentityError, msg=bad):
                DID.parse(bad)

    def test_fingerprint_relationship(self):
        fp1 = public_key_fingerprint("e1_", PK1)
        self.assertEqual(fp1, public_key_fingerprint("e1_", PK1))
        self.assertNotEqual(fp1, public_key_fingerprint("e1_", PK2))
        with self.assertRaises(ANPIdentityError):
            public_key_fingerprint("x9_", PK1)

    def test_did_with_key_embeds_fingerprint(self):
        did = did_with_key("example.com", "bob", "e1_", PK1)
        self.assertTrue(did.did.startswith("did:wba:example.com:bob:pk_e1_"))
        parsed = DID.parse(did.did)
        self.assertEqual(parsed.key_fingerprint, did.key_fingerprint)
        self.assertEqual(parsed.key_profile, "e1_")

    def test_did_document_build_and_from_dict(self):
        did = did_with_key("example.com", "bob", "e1_", PK1)
        doc = DIDDocument.build(did, public_key=PK1,
                                messaging_endpoint="https://example.com/rpc")
        self.assertEqual(doc.id, did.did)
        self.assertEqual(doc.verification_method[0].controller, did.did)
        self.assertEqual(doc.services[0]["type"], "ANPMessageService")
        rebuilt = DIDDocument.from_dict({
            "id": doc.id,
            "verificationMethod": [vm.to_dict() for vm in doc.verification_method],
            "service": doc.services,
        })
        self.assertEqual(rebuilt.id, doc.id)
        self.assertEqual(len(rebuilt.verification_method), 1)

    def test_document_url_shape(self):
        did = DID.parse("did:wba:example.com:alice")
        self.assertEqual(did_wba_document_url(did),
                         "https://example.com/.well-known/did-wba/alice")


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self._descriptions = [
            {"did": DID_ALICE, "name": "Alice",
             "description": "researcher agent",
             "capabilities": ["web-research", "anp.messaging.v1"]},
        ]
        self.server, self.base, self.thread, _peer = make_anp_http_server(
            self._descriptions)
        self.transport = _map_transport(self.base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_fetch_agent_descriptions(self):
        entries = fetch_agent_descriptions("example.com",
                                           transport=self.transport)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Alice")
        self.assertIn("web-research", entries[0].capabilities)

    def test_discovery_registry_search(self):
        reg = DiscoveryRegistry()
        desc = AgentDescription.from_dict(self._descriptions[0])
        reg.register(desc)
        self.assertEqual(len(reg.search(capability="web-research")), 1)
        self.assertEqual(reg.search(capability="git"), [])
        self.assertEqual(len(reg.search(name_query="ali")), 1)
        self.assertTrue(reg.unregister(DID_ALICE))
        self.assertEqual(reg.get(DID_ALICE), None)


class TestMessaging(unittest.TestCase):
    def setUp(self):
        self.server, self.base, self.thread, self.peer = make_anp_http_server(
            [{"did": DID_BOB, "name": "Bob",
              "capabilities": ["anp.messaging.v1"]}])
        self.service_url = f"{self.base}/rpc"
        self.adapter = ANPAdapter()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _msg(self, mid="m-1") -> ANPMessage:
        return ANPMessage(message_id=mid,
                          sender=DID.parse(DID_ALICE),
                          recipient=DID.parse(DID_BOB),
                          content="olá")

    def test_get_capabilities(self):
        caps = self.adapter.service.get_capabilities(self.service_url)
        self.assertEqual(caps, ["anp.messaging.v1"])

    def test_send_requires_signer(self):
        with self.assertRaises(ANPProtocolError):
            self.adapter.service.send_message(self.service_url, self._msg(),
                                              signer=None)

    def test_send_message_e2e(self):
        result = self.adapter.service.send_message(
            self.service_url, self._msg(),
            signer=lambda canonical: f"sig:{canonical.hex()[:8]}",
            request_id="q1")
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["messageId"], "m-1")
        self.assertEqual(len(self.peer.received), 1)
        self.assertEqual(self.peer.received[0]["sender"], DID_ALICE)

    def test_invalid_sender_rejected_by_peer(self):
        raw = self._msg().to_dict()
        raw["sender"] = "did:web:example.com:evil"
        params = {
            "meta": {"profile": raw["profile"]},
            "body": {"message": raw},
            "auth": {"digest": "x", "signature": "sig"},
        }
        request = {"jsonrpc": "2.0", "id": 7,
                   "method": "anp.message.send", "params": params}
        status, body = self.adapter.transport(
            "POST", self.service_url, json.dumps(request).encode("utf-8"),
            {"Content-Type": "application/json"})
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"]["code"], -32602)

    def test_unknown_method_remote_error(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "anp.nope",
                   "params": {}}
        status, body = self.adapter.transport(
            "POST", self.service_url, json.dumps(request).encode("utf-8"),
            {"Content-Type": "application/json"})
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
