"""HAOS Standalone WebUI — smoke tests (server real, stores reais em tmp).

Cobre o contrato da UI standalone: servidor sobe, serve a página, /api/state
deriva dos stores canônicos persistentes, Console cria card e despacha,
settings persiste/aplica ao vivo, Ouroboros analyze/decide funcionam com o
ledger real. Nada é mockado — apenas data_dir temporário.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from hermes.platform.webui.settings import (
    apply_to_guard, default_settings, load_settings, reset_settings, save_settings,
)
from hermes.platform.webui.standalone import make_standalone_server
from hermes.platform.execution.backpressure import ConcurrencyGuard


class TestEngineSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_persistence(self):
        cfg = default_settings()
        self.assertEqual(cfg["max_global_concurrency"], 8)
        self.assertIn("a6api", cfg["provider_limits"])
        # Sem arquivo => defaults
        loaded = load_settings(self.dir)
        self.assertEqual(loaded["max_global_concurrency"], 8)

        saved = save_settings(self.dir, {"max_global_concurrency": 14,
                                         "provider_limits": {"openai": 9}})
        self.assertEqual(saved["max_global_concurrency"], 14)
        self.assertEqual(saved["provider_limits"]["openai"], 9)
        reloaded = load_settings(self.dir)
        self.assertEqual(reloaded["max_global_concurrency"], 14)
        # Reset remove o arquivo
        defaults = reset_settings(self.dir)
        self.assertEqual(defaults["max_global_concurrency"], 8)

    def test_apply_live_to_guard(self):
        guard = ConcurrencyGuard()
        self.assertEqual(guard.max_global_concurrency, 8)
        apply_to_guard(guard, {"max_global_concurrency": 6,
                               "provider_limits": {"anthropic": 7},
                               "model_limits": {"gpt-x": 2}})
        self.assertEqual(guard.max_global_concurrency, 6)
        self.assertEqual(guard.max_active_workers, 6)
        self.assertEqual(guard.provider_limits["anthropic"], 7)
        self.assertEqual(guard.model_limits["gpt-x"], 2)

    def test_validate_fail_closed(self):
        saved = save_settings(self.dir, {"max_global_concurrency": "abc"})
        self.assertEqual(saved["max_global_concurrency"], 8)


class TestStandaloneServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        server, self.state, self.base = make_standalone_server(
            self.dir, host="127.0.0.1", port=0)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=15) as resp:
            return resp.read().decode("utf-8")

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_index_and_state(self):
        html = self._get("/")
        self.assertIn("HAOS Standalone", html)
        self.assertIn("Console", html)
        payload = json.loads(self._get("/api/state"))
        self.assertEqual(payload["taskboard"]["view"], "taskboard")
        self.assertEqual(payload["taskboard"]["total"], 0)
        self.assertIn("evolution_pending", payload)
        self.assertIn("settings", payload)
        self.assertEqual(payload["meta"]["mode"], "standalone")
        # ConcurrencyGuard configurado => providers presentes com limites
        self.assertGreaterEqual(len(payload["concurrency"]["providers"]), 1)
        # Team Graph & Control Plane Overview vinculados ao estado canônico
        self.assertIn("team_graph", payload)
        self.assertIn("control_overview", payload)
        self.assertEqual(payload["team_graph"]["role"], "mayor")

    def test_team_graph_and_controlplane_endpoints(self):
        # 1. GET /api/team-graph
        tg = json.loads(self._get("/api/team-graph"))
        self.assertEqual(tg["role"], "mayor")
        self.assertIn("children", tg)
        self.assertGreaterEqual(len(tg["children"]), 1)

        # 2. GET /api/controlplane/team_graph (ADR-006 canonical)
        tg_cp = json.loads(self._get("/api/controlplane/team_graph"))
        self.assertEqual(tg_cp["node_id"], tg["node_id"])

        # 3. GET /api/controlplane/overview (ADR-006 canonical)
        cp_ov = json.loads(self._get("/api/controlplane/overview"))
        self.assertIn("total_missions", cp_ov)
        self.assertIn("active_workers", cp_ov)

        # 4. POST /api/intervene
        res1 = self._post("/api/intervene", {
            "target_id": "specialist-coder-01",
            "action": "steer",
            "reason": "Refactor into clean modules",
        })
        self.assertTrue(res1["success"])
        self.assertEqual(self.state.control_plane.get_pending_intervention("specialist-coder-01"), "steer")

        # 5. POST /api/controlplane/intervene (ADR-006 canonical)
        res2 = self._post("/api/controlplane/intervene", {
            "target_id": "specialist-reviewer-01",
            "action": "pause",
            "reason": "Wait for tests",
        })
        self.assertTrue(res2["success"])
        self.assertEqual(self.state.control_plane.get_pending_intervention("specialist-reviewer-01"), "pause")

    def test_console_creates_task_and_dispatch(self):
        resp = self._post("/api/console", {"message": "Validar ordenação estável do módulo X"})
        self.assertTrue(resp["accepted"])
        self.assertTrue(resp["task_id"].startswith("t_"))
        # Aguarda o tick em background concluir (poll do estado real)
        payload = None
        for _ in range(50):
            payload = json.loads(self._get("/api/state"))
            done = [t for t in payload["taskboard"]["recent"] if t["id"] == resp["task_id"]]
            if done and str(done[0].get("status")) in ("done", "failed", "blocked"):
                break
            import time
            time.sleep(0.2)
        self.assertIsNotNone(payload)
        cards = [t for t in payload["taskboard"]["recent"] if t["id"] == resp["task_id"]]
        self.assertGreaterEqual(len(cards), 1)
        self.assertIn(str(cards[0].get("status")), ("done", "failed", "blocked", "running"))

    def test_task_details_action_and_timeline(self):
        created = self._post("/api/tasks", {"goal": "Testar timeline e acao de tarefas", "priority": 70})
        tid = created["task_id"]
        detail = json.loads(self._get(f"/api/tasks/{tid}"))
        self.assertEqual(detail["id"], tid)
        self.assertIn("tokens", detail)
        self.assertIn("cost", detail)
        self.assertIn("elapsed_seconds", detail)

        # Ação de requeue
        act_res = self._post(f"/api/tasks/{tid}/action", {"action": "requeue"})
        self.assertTrue(act_res["success"])

        # Timeline
        tl = json.loads(self._get("/api/timeline"))
        self.assertIn("timeline", tl)
        self.assertIsInstance(tl["timeline"], list)

    def test_settings_endpoints(self):
        res = self._post("/api/settings", {"max_global_concurrency": 11})
        self.assertTrue(res["ok"])
        self.assertEqual(res["settings"]["max_global_concurrency"], 11)
        payload = json.loads(self._get("/api/state"))
        self.assertEqual(payload["concurrency"]["max_global"], 11)
        res2 = self._post("/api/settings/reset", {})
        self.assertEqual(res2["settings"]["max_global_concurrency"], 8)

    def test_chained_dependencies_surface_cpm(self):
        """Cadeia A->B->C cria grafo e o CPM/PIP aparecem vivos no /api/state."""
        a = self._post("/api/tasks", {"goal": "Tarefa raiz A", "priority": 10})
        b = self._post("/api/tasks", {"goal": "Tarefa B (depende de A)",
                                      "priority": 30,
                                      "requires_tasks": [a["task_id"]]})
        c = self._post("/api/tasks", {"goal": "Tarefa C crítica (depende de B)",
                                      "priority": 95,
                                      "requires_tasks": [b["task_id"]]})
        for _ in range(30):
            payload = json.loads(self._get("/api/state"))
            cp = payload["critical_path"]
            if cp["total_tasks_evaluated"] >= 3:
                break
            import time
            time.sleep(0.2)
        cp = json.loads(self._get("/api/state"))["critical_path"]
        self.assertGreaterEqual(cp["total_tasks_evaluated"], 3)
        # C é folha crítica com prioridade alta: deve estar no caminho crítico
        # (o grafo usa os spec_ids T-...) e receber prioridade efetiva >= base.
        self.assertIn(c["spec_id"], cp["critical_path_ids"])
        self.assertGreaterEqual(cp["inherited_priorities"].get(c["spec_id"], 0), 95.0)

    def test_evolution_analyze_and_decide_requires_approver(self):
        resp = self._post("/api/evolution/analyze", {})
        self.assertIn("submitted", resp)
        # Decide exige approver (nunca inventa identidade)
        import urllib.error
        req = urllib.request.Request(
            f"{self.base}/api/evolution/decide",
            data=json.dumps({"proposal_id": "nope", "verdict": "approved"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=10)


class TestTerminalBridge(unittest.TestCase):
    """Terminal interno: sessão PTY real via HTTP — escreve, lê de volta, kill."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        server, self.state, self.base = make_standalone_server(
            self.dir, host="127.0.0.1", port=0)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post(self, path, payload=None):
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_start_input_drain_kill(self):
        start = self._post("/api/terminal/start", {"cwd": self.tmp.name})
        sid = start["session_id"]
        self.assertTrue(sid)
        # Escreve um comando que ecoa marca única
        ok = self._post(f"/api/terminal/{sid}/input",
                        {"data": "echo HAOS_TERM_MARK_4821 && exit\r"})
        self.assertTrue(ok["ok"])
        seen = ""
        for _ in range(100):
            chunk = self._get(f"/api/terminal/{sid}/drain")
            seen += chunk["data"]
            if not chunk["running"]:
                break
            import time
            time.sleep(0.1)
        self.assertIn("HAOS_TERM_MARK_4821", seen)
        killed = self._post(f"/api/terminal/{sid}/kill", {})
        self.assertTrue(killed["ok"])
        # Sessão morta some da lista
        self.assertEqual(self._get("/api/terminal")["sessions"], [])


class TestAgentConfigEndpoints(unittest.TestCase):
    """Config Agente: editor sobre o config.yaml real (HERMES_HOME isolado)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # HERMES_HOME do processo de teste: sobrescrevemos o config.yaml do
        # conftest com conteúdo determinístico deste teste.
        self.home = Path(os.environ.get("HERMES_HOME", str(self.dir)))
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.yaml").write_text(
            "display:\n  skin: midnight\n  language: en\n"
            "model:\n  model: hermes-4-7b\n  provider: a6api\n"
            "  api_key: sk-super-secret-value-abcdefghij123456789\n"
            "memory:\n  memory_enabled: true\n", encoding="utf-8")
        server, self.state, self.base = make_standalone_server(
            self.dir, host="127.0.0.1", port=0)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_read_sections_and_patch_roundtrip(self):
        info = self._get("/api/agent-config")
        self.assertTrue(info["exists"])
        self.assertIn("display", info["sections"])
        skin = [f for f in info["sections"]["display"]["fields"] if f["path"] == "display.skin"]
        self.assertEqual(skin[0]["value"], "midnight")
        # Segredos nunca vazam para a UI (api_key mascarada)
        key = [f for f in info["sections"]["model"]["fields"] if f["path"] == "model.api_key"]
        self.assertEqual(key[0]["kind"], "secret")
        self.assertNotIn("sk-super-secret", str(key[0]["value"]))
        # Patch parcial: muda display.language e preserva as demais chaves
        res = self._post("/api/agent-config", {
            "updates": {"display.language": {"kind": "str", "value": "pt-BR"}},
        })
        self.assertIn("display.language", res["applied"])
        # Backup criado antes da gravação
        backups = list((self.state.data_dir / "config-backups").glob("config-*.yaml"))
        self.assertGreaterEqual(len(backups), 1)
        # Lê de volta do arquivo real
        raw = (self.home / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("language: pt-BR", raw)
        self.assertIn("skin: midnight", raw)  # chave não tocada preservada

    def test_patch_missing_key_fails_closed(self):
        info = self._get("/api/agent-config")
        if not info["exists"]:
            self.skipTest("config.yaml ausente no HERMES_HOME isolado")
        import urllib.error
        req = urllib.request.Request(
            f"{self.base}/api/agent-config",
            data=json.dumps({"updates": {"invalida chave!!": {"kind": "str", "value": "x"}}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=10)


class TestSystemFacts(unittest.TestCase):
    """Sistema: fatos reais das homes + engine; sem vazamento de segredos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        server, self.state, self.base = make_standalone_server(
            self.dir, host="127.0.0.1", port=0)
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()
        # Home isolada com marcador real de uso + segredo p/ provar masking
        home = Path(os.environ.get("HERMES_HOME") or self.dir)
        home.mkdir(parents=True, exist_ok=True)
        (home / "kanban.db").write_bytes(b"x")
        (home / "config.yaml").write_text(
            "model:\n  default: deepseek-v4-flash\n  api_key: sk-super-secret-leak-check\n"
            "delegation:\n  role_models:\n    orchestrator: glm-5.3\n    leaf: deepseek-v4-flash\n",
            encoding="utf-8")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_facts_homes_engine_and_no_secrets(self):
        with urllib.request.urlopen(f"{self.base}/api/system-facts", timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        self.assertIsInstance(payload["homes"], list)
        self.assertGreaterEqual(len(payload["homes"]), 1)
        self.assertEqual(payload["engine"]["kanban_db"],
                         str(self.state.data_dir / "kanban.db"))
        # Segredos nunca saem no payload (masking por nome de chave)
        self.assertNotIn("sk-", raw)
        # Pelo menos uma home com config resumido (modelos visíveis, sem chave)
        self.assertTrue(any("config" in h and "model" in h["config"] for h in payload["homes"]))


class TestStandaloneDefaults(unittest.TestCase):
    def test_default_data_dir_isolated_from_hermes(self):
        with tempfile.TemporaryDirectory() as tmp_haos:
            old_haos_home = os.environ.get("HAOS_HOME")
            old_haos_data = os.environ.get("HAOS_DATA_DIR")
            try:
                os.environ["HAOS_HOME"] = tmp_haos
                os.environ.pop("HAOS_DATA_DIR", None)
                server, state, base = make_standalone_server(port=0)
                try:
                    self.assertEqual(state.data_dir, Path(tmp_haos))
                    self.assertNotIn(".hermes", str(state.data_dir))
                finally:
                    server.server_close()
            finally:
                if old_haos_home is not None:
                    os.environ["HAOS_HOME"] = old_haos_home
                else:
                    os.environ.pop("HAOS_HOME", None)
                if old_haos_data is not None:
                    os.environ["HAOS_DATA_DIR"] = old_haos_data
                else:
                    os.environ.pop("HAOS_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
