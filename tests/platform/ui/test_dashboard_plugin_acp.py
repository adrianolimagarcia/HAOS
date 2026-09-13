"""Delta 49 — ACP-na-UI: bridge de planejamento + grants_pending no state.

O delta 49 liga o control plane (dashboard) a um agente ACP real de
planejamento e expõe os grants pendentes na view:

(a) ``ui.planner.planning_brief`` deriva um brief textual do MESMO estado
    observável do dashboard (``dashboard_payload`` + evolution pending) —
    nunca segunda fonte; sem store o brief é explícito (nada fabricado).
(b) ``ui.planner.run_planning_session`` executa UMA sessão ACP via o cliente
    canônico do K5 (``ACPSessionClient`` — peer scriptado hermético em
    ``tests/platform/protocols/_mock_acp_server.py``; nunca o pacote ``acp``).
(c) ``plugin_api.configure_acp`` injeta o argv do agente; sem comando
    ``action_acp_plan``/``POST /acp/plan`` respondem fail-closed (409) — o
    plugin nunca spawna um peer que não existe.
(d) ``state_payload`` ganhou ``grants_pending`` (grants ``requires_approval``
    reais do SecretBroker; fail-safe [] sem vault).

Nunca lê código-fonte; exercita comportamento com stores canônicos reais em
HERMES_HOME/KANBAN_HOME temporários (nunca ~/.hermes).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from hermes.platform.evals.suites_platform import platform_root  # noqa: F401
from hermes.platform.observability.event_store import EventStore
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.ui.stats import DashboardStats

try:  # fastapi é dependência do host (Web Dashboard), não do platform
    from fastapi import FastAPI  # type: ignore[import-not-found]
    HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    HAVE_FASTAPI = False

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "hermes/platform/ui/dashboard_plugin"
_MOCK_ACP = (Path(__file__).resolve().parents[1] / "protocols"
             / "_mock_acp_server.py")


def _load_plugin_api() -> Any:
    """Carrega o api exatamente como o upstream: importlib por path + exec."""
    api_path = _PLUGIN_DIR / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("haos_plugin_acp_test", api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _IsolatedHome(unittest.TestCase):
    """HERMES_HOME/KANBAN_HOME temporários + stores canônicos reais."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.root = root
        self.adapter = KanbanAdapter(root / "kanban.db")
        self.store = EventStore(root / "events.db")
        self.mod = _load_plugin_api()
        self.mod.configure_acp(None)  # estado limpo por teste

    def tearDown(self):
        self.mod.configure_stats(None)
        self.mod.configure_acp(None)
        self.adapter.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        if self._env_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env_home
        self._tmp.cleanup()

    def _ready_stats(self) -> DashboardStats:
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        spec = TaskSpec(id="T-A49", title="T-A49", goal="g",
                        workspace_type="scratch",
                        required_capabilities=["git", "code-intelligence"])
        self.adapter.save_task(spec, status="READY")
        return DashboardStats(kanban=self.adapter, event_store=self.store)


class TestPlanningBrief(_IsolatedHome):
    """(a) O brief deriva o estado observável real; fail-safe sem store."""

    def test_brief_derives_real_state_with_kanban(self):
        stats = self._ready_stats()
        from hermes.platform.ui.planner import planning_brief

        brief = planning_brief(stats, instruction="planeje a sprint")
        self.assertIn("total: 1", brief)
        self.assertIn("ready: 1", brief)
        self.assertIn("Operator instruction: planeje a sprint", brief)
        # Evolution pending: sem proposta no EventStore -> 0 explícito.
        self.assertIn("Evolution proposals awaiting decision: 0", brief)
        self.assertIn("HAOS control-plane state", brief)

    def test_brief_fail_closed_without_store(self):
        from hermes.platform.ui.planner import planning_brief

        brief = planning_brief(None)
        self.assertIn("no kanban store configured", brief)
        self.assertIn("total: 0", brief)
        self.assertIn("Evolution proposals awaiting decision: 0", brief)

    def test_brief_reflects_pending_evolution_proposal(self):
        from hermes.platform.evolution.ledger import EvolutionLedger
        from hermes.platform.ui.planner import planning_brief

        ledger = EvolutionLedger(self.store)
        pid = ledger.submit({"target": "adr", "current_profile": "a",
                             "proposed_profile": "b", "mode": "PROPOSAL_ONLY",
                             "rationale": "medido"})
        stats = DashboardStats(kanban=self.adapter, event_store=self.store)
        brief = planning_brief(stats)
        self.assertIn("Evolution proposals awaiting decision: 1", brief)
        self.assertIn(f"- {pid}", brief)


class TestAcpPlanAction(_IsolatedHome):
    """(b)+(c) A ação roda uma sessão ACP real; fail-closed sem comando."""

    def test_action_fail_closed_without_configured_agent(self):
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        with self.assertRaises(ValueError):
            self.mod.action_acp_plan()

    def test_action_runs_real_acp_session_with_mock_peer(self):
        stats = self._ready_stats()
        self.mod.configure_acp([sys.executable, str(_MOCK_ACP)])
        cwd = str(self.root / "session-work")
        Path(cwd).mkdir()

        outcome = self.mod.action_acp_plan(cwd=cwd, instruction="planeje")

        # Sessão real com o peer scriptado: identidade + resultado cru do K5.
        self.assertEqual(outcome["agent"]["name"], "mock-acp-agent")
        self.assertEqual(outcome["agent"]["version"], "0.0.1")
        self.assertIn("session_id", outcome)
        self.assertEqual(outcome["result"]["result"][0]["text"], "ok")
        # O peer recebeu o brief derivado (registro do wire em jsonl).
        record = self.root / "acp-record.jsonl"
        os.environ["MOCK_ACP_RECORD"] = str(record)
        try:
            self.mod.configure_acp([sys.executable, str(_MOCK_ACP)])
            self.mod.action_acp_plan(cwd=cwd, instruction="rodada 2")
        finally:
            os.environ.pop("MOCK_ACP_RECORD", None)
        self.assertTrue(record.exists(), "peer não gravou o wire recebido")
        prompts = [json.loads(line).get("params", {}).get("prompt")
                   for line in record.read_text(encoding="utf-8").splitlines()
                   if '"session/prompt"' in line]
        self.assertTrue(prompts, "nenhum session/prompt registrado no wire")
        joined = " ".join(str(p) for p in prompts)
        self.assertIn("HAOS control-plane state", joined)
        self.assertIn("total: 1", joined)

    def test_action_cwd_defaults_to_hermes_home(self):
        self._ready_stats()
        self.mod.configure_acp([sys.executable, str(_MOCK_ACP)])
        outcome = self.mod.action_acp_plan(instruction="sem cwd")
        self.assertEqual(outcome["agent"]["name"], "mock-acp-agent")


class TestAcpPlanRoute(_IsolatedHome):
    """(c) Rota HTTP: 200 com peer real; 409 sem comando; erros do peer."""

    def _mount(self):
        app = FastAPI()
        app.include_router(self.mod.router, prefix="/api/plugins/haos")
        return app

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_acp_plan_409_without_configured_agent(self):
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        self._ready_stats()
        with TestClient(self._mount()) as client:
            resp = client.post("/api/plugins/haos/acp/plan", json={})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("não configurado", resp.json()["detail"])

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_acp_plan_200_runs_real_session(self):
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        self._ready_stats()
        self.mod.configure_acp([sys.executable, str(_MOCK_ACP)])
        cwd = str(self.root / "session-work")
        Path(cwd).mkdir()
        with TestClient(self._mount()) as client:
            resp = client.post("/api/plugins/haos/acp/plan",
                               json={"cwd": cwd, "instruction": "via http"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "planned")
        self.assertEqual(body["agent"]["name"], "mock-acp-agent")


class TestGrantsPendingPayload(_IsolatedHome):
    """(d) state_payload expõe grants pendentes reais; fail-safe sem vault."""

    def test_grants_pending_in_state_payload(self):
        from hermes.platform.auth.vault import SecretBroker

        sb = SecretBroker()
        sb.store_secret("svc:ci", "tok-49")
        sb.grant("ci", "svc:ci", policy="requires_approval", requester="build-bot")
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))

        payload = self.mod.state_payload()
        pending = payload["grants_pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["scope"], "ci")
        self.assertEqual(pending[0]["credential_ref"], "svc:ci")
        # O registro exposto nunca carrega o segredo.
        self.assertNotIn("tok-49", json.dumps(pending))

    def test_grants_pending_empty_without_grants(self):
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        payload = self.mod.state_payload()
        self.assertEqual(payload["grants_pending"], [])

    def test_grants_pending_payload_view_shape(self):
        view = self.mod.grants_pending_payload()
        self.assertEqual(view["view"], "grants")
        self.assertEqual(view["count"], 0)
        self.assertEqual(view["pending"], [])


if __name__ == "__main__":
    unittest.main()
