"""Test suite — Harness Bindings (seletor de plataforma do Team Graph).

Valida:
1. HarnessBindingStore: default native, set/get persistente, validação de
   papel/harness e warning quando a plataforma não está disponível.
2. ControlPlaneService: o binding escolhido (mayor/sub_orchestrator/coder/
   reviewer) passa a ser o harness dos nós do Team Graph construídos.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes.platform.observability.event_store import EventStore
from hermes.platform.webui.controlplane import ControlPlaneService, NodeStatus
from hermes.platform.webui import harness_bindings as hb
from hermes.platform.webui.harness_bindings import HarnessBindingStore


class TestHarnessBindingStore(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp(prefix="haos-hbind-"))
        self.store_path = self._tmp / "harness_bindings.json"
        self.store = HarnessBindingStore(self.store_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_is_native_and_roundtrip_persists(self):
        self.assertEqual(self.store.get("mayor"), "native")
        res = self.store.set("mayor", "dsh")
        self.assertEqual(res["new"], "dsh")
        self.assertEqual(self.store.get("mayor"), "dsh")
        # recarregar do disco mantém o vínculo
        reloaded = HarnessBindingStore(self.store_path)
        self.assertEqual(reloaded.get("mayor"), "dsh")
        self.assertTrue(self.store_path.is_file())
        data = json.loads(self.store_path.read_text())
        self.assertEqual(data["mayor"], "dsh")

    def test_rejects_unknown_role_and_harness(self):
        with self.assertRaises(ValueError):
            self.store.set("galatico", "dsh")
        with self.assertRaises(ValueError):
            self.store.set("mayor", "turbo-9000")

    def test_warning_when_binding_unavailable_harness(self):
        with patch.object(hb, "detect_cached", return_value={"available": False, "status": "missing"}):
            res = self.store.set("sub_orchestrator", "opencode")
        self.assertEqual(res["new"], "opencode")
        self.assertIsNotNone(res["warning"])
        self.assertIn("fallback native", res["warning"])

    def test_overview_contains_roles_and_catalog(self):
        self.store.set("coder", "agy")
        ov = self.store.overview()
        self.assertEqual([r["key"] for r in ov["roles"]], list(hb.ROLE_KEYS))
        self.assertEqual({r["key"]: r["binding"] for r in ov["roles"]}["coder"], "agy")
        names = [c["name"] for c in ov["catalog"]]
        self.assertIn("native", names)
        self.assertIn("dsh", names)


class TestControlPlaneHarnessBindings(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile

        self._tmp = Path(tempfile.mkdtemp(prefix="haos-cp-hbind-"))
        self.event_store = EventStore(db_path=":memory:")
        self.service = ControlPlaneService(event_store=self.event_store, data_dir=self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _graph(self):
        return self.service.build_team_graph(
            mission_id="m-test",
            goal="g",
            sub_orchestrators=[
                {
                    "id": "so-1",
                    "domain": "software",
                    "workers": [
                        {"id": "w1", "role": "worker", "posture": "coder", "status": "idle"},
                        {"id": "w2", "role": "reviewer", "posture": "reviewer", "status": "idle"},
                    ],
                }
            ],
            root_status=NodeStatus.IDLE,
        )

    def test_default_graph_uses_native_harness(self):
        root = self._graph()
        self.assertEqual(root.harness, "native")
        self.assertEqual(root.children[0].harness, "native")
        self.assertEqual(root.children[0].children[0].harness, "native")

    def test_mayor_binding_flows_into_graph_root(self):
        self.service.set_harness_binding("mayor", "dsh")
        root = self._graph()
        self.assertEqual(root.harness, "dsh")
        self.assertEqual(root.metadata["harness_info"]["name"], "dsh")

    def test_role_bindings_flow_into_suborchestrator_and_workers(self):
        self.service.set_harness_binding("sub_orchestrator", "opencode")
        self.service.set_harness_binding("coder", "agy")
        self.service.set_harness_binding("reviewer", "native")
        root = self._graph()
        so = root.children[0]
        self.assertEqual(so.harness, "opencode")
        by_posture = {w.posture: w.harness for w in so.children}
        self.assertEqual(by_posture["coder"], "agy")
        self.assertEqual(by_posture["reviewer"], "native")

    def test_explicit_worker_harness_overrides_binding(self):
        self.service.set_harness_binding("coder", "agy")
        root = self.service.build_team_graph(
            mission_id="m2",
            goal="g",
            sub_orchestrators=[
                {
                    "id": "so-1",
                    "domain": "software",
                    "workers": [
                        {"id": "w1", "role": "worker", "posture": "coder", "status": "idle", "harness": "dsh"}
                    ],
                }
            ],
            root_status=NodeStatus.IDLE,
        )
        self.assertEqual(root.children[0].children[0].harness, "dsh")

    def test_harness_overview_reports_live_bindings(self):
        self.service.set_harness_binding("mayor", "codex")
        ov = self.service.harness_overview()
        binds = {r["key"]: r["binding"] for r in ov["roles"]}
        self.assertEqual(binds["mayor"], "codex")
        self.assertEqual(ov["bindings"]["mayor"], "codex")


if __name__ == "__main__":
    unittest.main()
