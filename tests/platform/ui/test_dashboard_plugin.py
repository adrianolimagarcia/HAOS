"""Delta 45 — Etapa shell (Fase 3): testes de contrato do plugin de dashboard.

O shell oficial (Hermes Web Dashboard) descobre plugins via
``<plugins root>/*/dashboard/manifest.json`` e monta o backend declarado em
``api`` sob ``/api/plugins/<name>/`` (``exec_module`` + ``getattr(mod,
"router")``). Este arquivo valida o contrato do artefato aditivo:

(a) manifest.json conforma ao schema que o discovery upstream lê
    (name/label/description/icon/version/tab.path/tab.hidden/api) e o ``api``
    aponta para um arquivo irmão existente (regra de relpath segura).
(b) plugin_api.py expõe ``router`` (FastAPI APIRouter) com as rotas
    /state /evolution /health; ``include_router`` sob o prefixo canônico
    produz os paths esperados (requer fastapi; skip honesto quando ausente).
(c) state_payload/evolution_payload derivam 100% das views reais do platform
    com stores canônicos seedados: taskboard conta cards reais; evolution
    reflete propostas do ledger (delta 44). Sem store -> vazio/fail-safe,
    nunca dados de outra base.
(d) configure_stats é o seam de wiring no processo do dashboard (montagem
    final); sem chamada, o plugin serve estado vazio (available False).
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from hermes.platform.evals.suites_platform import platform_root
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.ui.dashboard_plugin import plugin_api
from hermes.platform.ui.stats import DashboardStats

try:  # fastapi é dependência do host (Web Dashboard), não do platform
    from fastapi import FastAPI  # type: ignore[import-not-found]
    HAVE_FASTAPI = True
except Exception:  # pragma: no cover
    HAVE_FASTAPI = False

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "hermes/platform/ui/dashboard_plugin"


def _load_plugin_api() -> Any:
    """Carrega o api exatamente como o upstream: importlib por path + exec."""
    api_path = _PLUGIN_DIR / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("haos_dashboard_plugin_test", api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDashboardPluginManifest(unittest.TestCase):
    """(a) O artefato conforma ao schema que o discovery do upstream lê."""

    def setUp(self):
        self.manifest_path = _PLUGIN_DIR / "manifest.json"
        self.assertTrue(self.manifest_path.exists(),
                        f"manifest ausente: {self.manifest_path}")
        self.raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_manifest_required_fields(self):
        for key in ("name", "label", "description", "icon", "version", "tab", "api"):
            self.assertIn(key, self.raw, f"manifest sem campo {key!r}")
        self.assertEqual(self.raw["name"], "haos")
        self.assertIsInstance(self.raw["label"], str)
        self.assertIsInstance(self.raw["description"], str)
        self.assertIsInstance(self.raw["version"], str)

    def test_tab_path_valid_and_visible(self):
        tab = self.raw["tab"]
        self.assertIsInstance(tab, dict)
        self.assertTrue(str(tab.get("path", "")).startswith("/"))
        # Delta 46 entregou o entry visual: a aba /haos não fica mais oculta.
        self.assertFalse(tab.get("hidden"), "aba /haos deve estar visível (delta 46)")
        self.assertEqual(tab.get("path"), "/haos")

    def test_api_relpath_safe_and_exists(self):
        api = self.raw["api"]
        self.assertIsInstance(api, str)
        candidate = Path(api)
        self.assertFalse(candidate.is_absolute(), "api não pode ser path absoluto")
        # Regra do upstream: resolve() deve permanecer DENTRO do dir do plugin.
        try:
            resolved = (_PLUGIN_DIR / candidate).resolve()
            resolved.relative_to(_PLUGIN_DIR.resolve())
        except (OSError, RuntimeError, ValueError):
            self.fail(f"api {api!r} escapa do dashboard dir (relpath inseguro)")
        self.assertTrue(resolved.exists(), f"arquivo api ausente: {resolved}")


class TestDashboardPluginMount(unittest.TestCase):
    """(b) Contrato de mount: `router` exposto + prefixo canônico."""

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_router_exposed_with_canonical_routes(self):
        mod = _load_plugin_api()
        router = getattr(mod, "router", None)
        self.assertIsNotNone(router, "plugin_api sem atributo 'router'")
        paths = {r.path for r in router.routes}
        self.assertIn("/state", paths)
        self.assertIn("/evolution", paths)
        self.assertIn("/health", paths)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_include_router_under_plugins_prefix(self):
        mod = _load_plugin_api()
        app = FastAPI()
        # Mesmo include que o upstream faz: prefix = /api/plugins/<name>/
        app.include_router(mod.router, prefix="/api/plugins/haos")
        # Contrato de mount via API pública (starlette moderno resolve rotas
        # inclusas de forma lazy; url_path_for materializa o prefixo real).
        self.assertEqual(app.router.url_path_for("get_state"),
                         "/api/plugins/haos/state")
        self.assertEqual(app.router.url_path_for("get_evolution"),
                         "/api/plugins/haos/evolution")
        self.assertEqual(app.router.url_path_for("get_health"),
                         "/api/plugins/haos/health")

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_mount_serves_fail_safe_empty_state_over_http(self):
        """Estado vazio sem store: HTTP 200 com zeros (sem inventar dados)."""
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        mod = _load_plugin_api()
        mod.configure_stats(None)
        app = FastAPI()
        app.include_router(mod.router, prefix="/api/plugins/haos")
        with TestClient(app) as client:
            resp = client.get("/api/plugins/haos/state")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["taskboard"]["total"], 0)
            self.assertEqual(payload["evolution_pending"], [])
            health = client.get("/api/plugins/haos/health").json()
            self.assertFalse(health["available"])


class TestDashboardPluginDerivation(unittest.TestCase):
    """(c) Os payloads derivam 100% dos stores canônicos reais (nunca fabricam)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.adapter = KanbanAdapter(root / "kanban.db")
        self.store = EventStore(root / "events.db")
        plugin_api.configure_stats(DashboardStats(kanban=self.adapter,
                                                  event_store=self.store))

    def tearDown(self):
        plugin_api.configure_stats(None)
        self.adapter.close()
        self._tmp.cleanup()

    def test_state_payload_matches_dashboard_payload_and_evolution(self):
        spec = TaskSpec(id="T-1", title="T-1", goal="g",
                        workspace_type="scratch")
        self.adapter.save_task(spec, status="READY")
        self.store.append(Event(name="task.created", payload={}, trace_id="tr-1"))

        payload = plugin_api.state_payload()
        # taskboard deriva do Kanban canônico real
        self.assertEqual(payload["taskboard"]["total"], 1)
        self.assertEqual(payload["memory_graph"]["nodes"][0]["type"], "task.created")
        # sem proposta submetida, evolution vazio (fail-safe)
        self.assertEqual(payload["evolution_pending"], [])

    def test_evolution_payload_reflects_ledger(self):
        from hermes.platform.evolution.ledger import EvolutionLedger

        ledger = EvolutionLedger(self.store)
        pid = ledger.submit({"target": "suite-adr", "current_profile": "a",
                             "proposed_profile": "b", "mode": "PROPOSAL_ONLY",
                             "rationale": "medido: regressão 0.9 -> 0.6"})
        self.assertTrue(pid)
        view = plugin_api.evolution_payload()
        self.assertEqual(view["view"], "evolution")
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["pending"][0]["proposal_id"], pid)


if __name__ == "__main__":
    unittest.main()
