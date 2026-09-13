"""Hermetic ACP wire contract tests for HAOS integration K5.

``ACPSessionClient`` is driven against the scripted stdlib peer
``_mock_acp_server.py`` (never against the ``acp`` package or the upstream
``acp_adapter``, which are not installed).  All working directories and record
files live in a per-test TemporaryDirectory; every interaction is bounded by
timeouts so a hang fails the test instead of blocking the suite.
"""

import json
import os
import sys
import tempfile
import time
import unittest

from hermes.platform.protocols.acp.adapter import (
    ACPError,
    ACPIdentity,
    ACPProtocolError,
    ACPSession,
    ACPSessionClient,
    ACPUnavailableError,
    PROTOCOL_VERSION,
)

_MOCK_SERVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_mock_acp_server.py"
)


class ACPWireContractTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._client = None

    def tearDown(self):
        if self._client is not None:
            self._client.close()

    def _new_client(self, **kwargs) -> ACPSessionClient:
        client = ACPSessionClient([sys.executable, _MOCK_SERVER], **kwargs)
        self._client = client
        return client

    def _abs_cwd(self) -> str:
        path = os.path.join(self._tmp.name, "session-work")
        os.makedirs(path, exist_ok=True)
        return os.path.abspath(path)

    @staticmethod
    def _restore_env(name: str, value) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    # ------------------------------------------------------------------ happy path

    def test_start_handshake_yields_identity(self):
        client = self._new_client()
        client.start()
        self.assertIsInstance(client.identity, ACPIdentity)
        self.assertEqual(client.identity.name, "mock-acp-agent")
        self.assertEqual(client.identity.version, "0.0.1")
        self.assertEqual(client.identity.protocol_version, PROTOCOL_VERSION)
        self.assertIsInstance(client.identity.agent_capabilities, dict)
        self.assertEqual(client.identity.auth_methods, [])
        self.assertTrue(client.is_running)

    def test_new_session_returns_mock_session(self):
        client = self._new_client()
        client.start()
        cwd = self._abs_cwd()
        session = client.new_session(cwd)
        self.assertIsInstance(session, ACPSession)
        self.assertEqual(session.session_id, "sess-mock-1")
        self.assertEqual(session.cwd, cwd)
        self.assertIs(session.client, client)
        self.assertEqual(
            session.to_dict(), {"session_id": "sess-mock-1", "cwd": cwd}
        )

    def test_send_text_returns_raw_result_and_records_server_push(self):
        client = self._new_client()
        client.start()
        session = client.new_session(self._abs_cwd())
        raw = client.send_text(session, "hello from the test")
        # Raw capture only: the mock's own result shape comes back verbatim.
        self.assertEqual(raw, {"result": [{"type": "text", "text": "ok"}]})
        pushed = [n.get("method") for n in client.notifications]
        self.assertIn("session/update", pushed)

    def test_close_terminates_the_process_within_a_timeout(self):
        client = self._new_client()
        client.start()
        client.new_session(self._abs_cwd())
        client.close()
        self.assertFalse(client.is_running)
        self.assertEqual(client.returncode, 0)
        # Closing twice is a no-op.
        client.close()
        self.assertFalse(client.is_running)

    def test_context_manager_starts_and_closes(self):
        with ACPSessionClient([sys.executable, _MOCK_SERVER]) as client:
            self.assertIsNotNone(client.identity)
            session = client.new_session(self._abs_cwd())
            raw = client.send_text(session, "hi")
            self.assertEqual(raw["result"][0]["text"], "ok")
        self.assertFalse(client.is_running)

    # ------------------------------------------------------------------ wire shapes

    def test_wire_params_are_camel_case_and_cwd_is_absolute(self):
        record = os.path.join(self._tmp.name, "record.jsonl")
        previous = os.environ.get("MOCK_ACP_RECORD")
        os.environ["MOCK_ACP_RECORD"] = record
        self.addCleanup(self._restore_env, "MOCK_ACP_RECORD", previous)

        client = self._new_client()
        cwd = self._abs_cwd()
        client.start()
        client.new_session(cwd)
        client.close()

        with open(record, encoding="utf-8") as handle:
            recorded = [
                json.loads(line) for line in handle if line.strip()
            ]
        methods = [entry["method"] for entry in recorded]
        # close() also sends session/close, so assert the handshake order as a
        # prefix of everything the mock received.
        self.assertEqual(methods[:2], ["initialize", "session/new"])
        self.assertIn("session/close", methods)

        init_request = recorded[0]
        self.assertEqual(init_request["jsonrpc"], "2.0")
        self.assertIsInstance(init_request["id"], int)
        init_params = init_request["params"]
        # The wire uses camelCase, never snake_case.
        self.assertEqual(
            set(init_params),
            {"protocolVersion", "clientCapabilities", "clientInfo"},
        )
        self.assertEqual(init_params["protocolVersion"], PROTOCOL_VERSION)
        self.assertIsInstance(init_params["clientCapabilities"], dict)
        self.assertIsInstance(init_params["clientInfo"], dict)

        new_session_request = recorded[1]
        self.assertEqual(new_session_request["params"], {"cwd": cwd})
        self.assertTrue(os.path.isabs(new_session_request["params"]["cwd"]))

    # ------------------------------------------------------------------ negative paths

    def test_start_raises_unavailable_when_process_exits_immediately(self):
        client = ACPSessionClient(
            [sys.executable, "-c", "import sys; sys.exit(1)"]
        )
        self._client = client
        with self.assertRaises(ACPUnavailableError):
            client.start()
        self.assertFalse(client.is_running)

    def test_start_rejects_protocol_version_mismatch(self):
        previous = os.environ.get("MOCK_ACP_PROTOCOL_VERSION")
        os.environ["MOCK_ACP_PROTOCOL_VERSION"] = "2"
        self.addCleanup(self._restore_env, "MOCK_ACP_PROTOCOL_VERSION", previous)

        client = self._new_client()
        with self.assertRaises(ACPProtocolError):
            client.start()
        self.assertFalse(client.is_running)

    def test_start_times_out_and_cleans_up_when_agent_never_answers(self):
        client = ACPSessionClient(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            request_timeout_s=0.5,
            shutdown_timeout_s=1.0,
        )
        self._client = client
        started = time.monotonic()
        with self.assertRaises(ACPError):
            client.start()
        self.assertLess(time.monotonic() - started, 10.0)
        self.assertFalse(client.is_running)


if __name__ == "__main__":
    unittest.main()
