"""B1 — A2A wire JSON-RPC stdlib (invariantes).

Contratos:
(a) JSON-RPC 2.0: helper request/result/error válidos; parse exige jsonrpc
    "2.0" e exatamente um de result/error; erro remoto vira A2ARemoteError.
(b) AgentCard: from_dict valida name (fail-closed); round-trip to_dict.
(c) A2AMessage: role user|agent validado; parts aceitam dict (kind) ou
    to_dict(); text/file parts helpers.
(d) Peer scriptado (http.server stdlib em thread): GET /.well-known/agent-card
    devolve o card; POST /message:send responde JSON-RPC com resultado do
    callback; A2AClient bate no peer real (sem rede externa) e devolve
    {message, artifacts, task}.
(e) Responder: método desconhecido -32601; params inválidos -32602; erro do
    callback -> -32000; card sem name -> A2AProtocolError.
"""

import json
import threading
import unittest

from hermes.platform.protocols.a2a.client import (
    AgentCard, A2AClient, A2AMessage, A2AProtocolError, A2ARemoteError,
    text_part, file_part,
    rpc_request, rpc_result, rpc_error, parse_rpc_response,
    JsonRpcParseError,
)
from hermes.platform.protocols.a2a.server import (
    A2AResponder, make_a2a_http_server,
)

CARD = AgentCard(name="peer-agent", description="test peer",
                 url="http://127.0.0.1", capabilities={"streaming": False})


class TestJsonRpcWire(unittest.TestCase):
    def test_request_result_error_shapes(self):
        req = rpc_request("SendMessage", {"message": {}}, request_id="r1")
        self.assertEqual(req["jsonrpc"], "2.0")
        self.assertEqual(req["method"], "SendMessage")
        ok = parse_rpc_response(rpc_result({"message": {}}, "r1"))
        self.assertEqual(ok[0]["message"], {})
        self.assertIsNone(ok[1])
        _, err = parse_rpc_response(rpc_error(-32001, "boom", "r1"))
        self.assertIsInstance(err, A2ARemoteError)
        self.assertEqual(err.code, -32001)

    def test_parse_rejects_malformed(self):
        with self.assertRaises(JsonRpcParseError):
            parse_rpc_response({"jsonrpc": "1.0", "result": {}})
        with self.assertRaises(JsonRpcParseError):
            parse_rpc_response({"jsonrpc": "2.0", "result": {}, "error": {}})
        with self.assertRaises(JsonRpcParseError):
            parse_rpc_response([])


class TestModels(unittest.TestCase):
    def test_agent_card_validation_and_round_trip(self):
        with self.assertRaises(A2AProtocolError):
            AgentCard.from_dict({"description": "sem nome"})
        card = AgentCard.from_dict(CARD.to_dict())
        self.assertEqual(card.name, "peer-agent")
        self.assertEqual(card.capabilities["streaming"], False)

    def test_message_roles_and_parts(self):
        msg = A2AMessage(role="user", parts=[text_part("oi"),
                                             file_part("file:///x.txt", "text/plain")])
        raw = msg.to_dict()
        self.assertEqual(raw["role"], "user")
        self.assertEqual(raw["parts"][0]["kind"], "text")
        self.assertEqual(raw["parts"][1]["kind"], "file")
        with self.assertRaises(A2AProtocolError):
            A2AMessage(role="system").to_dict()
        with self.assertRaises(A2AProtocolError):
            A2AMessage(role="user", parts=[object()]).to_dict()


class TestPeerRoundTrip(unittest.TestCase):
    def setUp(self):
        self._received = {}

        def on_send(message: dict) -> dict:
            self._received = message
            return {"message": message, "artifacts": [],
                    "task": {"id": "task-1", "status": {"state": "completed"}}}

        self.server, self.base, self.thread = make_a2a_http_server(
            CARD, on_send)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_fetch_agent_card_over_http(self):
        client = A2AClient()
        card = client.fetch_agent_card(self.base)
        self.assertEqual(card.name, "peer-agent")
        self.assertFalse(card.capabilities["streaming"])

    def test_send_message_round_trip(self):
        client = A2AClient()
        result = client.send_message(
            self.base,
            A2AMessage(role="user",
                       parts=[text_part("quanto custa?")],
                       message_id="m-1"),
            request_id="q-1",
        )
        self.assertEqual(result["message"]["messageId"], "m-1")
        self.assertEqual(self._received["role"], "user")
        self.assertEqual(self._received["parts"][0]["text"], "quanto custa?")
        self.assertEqual(result["task"]["status"]["state"], "completed")

    def test_remote_error_propagates(self):
        responder = A2AResponder(
            CARD,
            on_send_message=lambda m: (_ for _ in ()).throw(
                A2ARemoteError(-32001, "busy")))
        payload = responder.handle_json_rpc(
            rpc_request("SendMessage", {"message": {"role": "user"}}))
        _, err = parse_rpc_response(payload)
        self.assertIsInstance(err, A2ARemoteError)
        self.assertEqual(err.code, -32001)


class TestResponderDispatch(unittest.TestCase):
    def test_unknown_method_and_bad_params(self):
        resp = A2AResponder(CARD, on_send_message=lambda m: {"message": m})
        _, err = parse_rpc_response(
            resp.handle_json_rpc(rpc_request("Boom", {})))
        self.assertEqual(err.code, -32601)
        _, err = parse_rpc_response(
            resp.handle_json_rpc(rpc_request("SendMessage", [])))
        self.assertEqual(err.code, -32602)

    def test_callback_exception_is_generic(self):
        def boom(message: dict) -> dict:
            raise ValueError("segredo interno")

        resp = A2AResponder(CARD, on_send_message=boom)
        _, err = parse_rpc_response(
            resp.handle_json_rpc(
                rpc_request("SendMessage", {"message": {"role": "user"}})))
        self.assertEqual(err.code, -32000)


if __name__ == "__main__":
    unittest.main()
