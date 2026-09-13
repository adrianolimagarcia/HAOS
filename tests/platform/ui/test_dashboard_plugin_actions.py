"""Delta 47 — Actions de control plane no plugin de dashboard do HAOS.

O backend do delta 45 servia estado observável (read-only). O delta 47
adiciona actions reais que chamam os MESMOS seams canônicos do platform que o
runtime usa — nunca duplicam lifecycle nem fabricam efeito:

(a) ``action_dispatch_ready`` -> ``HAOSDispatcher(adapter).claim_tick``: o
    dispatcher canônico (claim atômico upstream + workspace canônico + lane
    real/determinística) roda cards READY. Sem Kanban configurado -> falha
    fechada (ValueError), nunca "dispatch fantasma".
(b) ``action_decide_evolution`` -> ``EvolutionLedger.decide`` (delta 44):
    registra decisão approved/rejected com fail-closed do ledger; sem
    EventStore -> ValueError.
(c) ``action_approve_grant`` / ``action_revoke_grant`` -> ``SecretBroker``
    real (delta 43): aprovar grant inexistente lança VaultError (fail-closed);
    revogar é idempotente.
(d) Rotas HTTP equivalentes (POST /dispatch, /evolution/decide,
    /grants/approve, /grants/revoke) sob o prefixo canônico do plugin — 200
    com efeito real, 409 sem store/estado inválido, 400 com corpo malformado.

Nunca lê código-fonte; exercita comportamento com stores canônicos reais em
HERMES_HOME/KANBAN_HOME temporários (nunca ~/.hermes).
"""

import importlib.util
import os
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


def _load_plugin_api() -> Any:
    """Carrega o api exatamente como o upstream: importlib por path + exec."""
    api_path = _PLUGIN_DIR / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("haos_plugin_actions_test", api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _IsolatedStoreBase(unittest.TestCase):
    """HERMES_HOME/KANBAN_HOME temporários + Kanban/EventStore canônicos."""

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

    def tearDown(self):
        self.mod.configure_stats(None)
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

    def _save_ready(self, task_id: str = "T-A47", lane: str = "git") -> str:
        spec = TaskSpec(id=task_id, title=task_id, goal="g",
                        workspace_type="scratch",
                        required_capabilities=[lane, "code-intelligence"])
        return self.adapter.save_task(spec, status="READY")


class TestPluginActionsPure(_IsolatedStoreBase):
    """(a)-(c): as action_* chamam os seams canônicos reais; fail-closed."""

    def test_dispatch_ready_runs_card_via_canonical_dispatcher(self):
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        tid = self._save_ready()

        executed = self.mod.action_dispatch_ready()

        # Dispatcher canônico: card READY executado até done com run encerrado.
        self.assertEqual(executed, [tid])
        self.assertEqual(self.adapter.get_task(tid)["status"], "done")
        self.assertEqual(self.adapter.get_run(tid).status, "ended")

    def test_dispatch_accepts_max_spawn_bound(self):
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        tid_a = self._save_ready(task_id="T-A47-A")
        tid_b = self._save_ready(task_id="T-A47-B")

        # max_spawn=1: um tick despacha apenas um card (orçamento do kernel).
        first = self.mod.action_dispatch_ready(max_spawn=1)
        self.assertEqual(first, [tid_a])
        rest = self.mod.action_dispatch_ready(max_spawn=1)
        self.assertEqual(rest, [tid_b])

    def test_dispatch_fail_closed_without_kanban(self):
        self.mod.configure_stats(None)  # sem store -> available False
        with self.assertRaises(ValueError):
            self.mod.action_dispatch_ready()

    def test_decide_evolution_via_ledger_and_fail_closed(self):
        from hermes.platform.evolution.ledger import EvolutionLedger, LedgerError

        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        ledger = EvolutionLedger(self.store)
        pid = ledger.submit({"target": "suite-adr", "current_profile": "a",
                             "proposed_profile": "b", "mode": "PROPOSAL_ONLY",
                             "rationale": "medido"})

        returned = self.mod.action_decide_evolution(
            pid, "approved", "operator-47", rationale="ok")
        self.assertEqual(returned, pid)
        # Decidida: sai da fila de pending (view deriva do stream).
        self.assertEqual(self.mod.evolution_payload()["pending"], [])

        # Fail-closed do ledger: proposta inexistente / já decidida / inválida.
        with self.assertRaises(LedgerError):
            self.mod.action_decide_evolution("nao-existe", "approved", "op")
        with self.assertRaises(LedgerError):
            self.mod.action_decide_evolution(pid, "approved", "op")
        with self.assertRaises(LedgerError):
            self.mod.action_decide_evolution(pid, "maybe", "op")

        # Sem EventStore configurado -> indisponível (fail-closed).
        self.mod.configure_stats(None)
        with self.assertRaises(ValueError):
            self.mod.action_decide_evolution(pid, "rejected", "op")

    def test_grant_approve_revoke_via_real_secret_broker(self):
        from hermes.platform.auth.vault import SecretBroker, VaultError

        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        sb = SecretBroker()
        sb.store_secret("svc:ci", "tok-47")
        sb.grant("ci", "svc:ci", policy="requires_approval", requester="build-bot")
        # Pendente: gate fechado até approve.
        self.assertFalse(sb.check_grant("ci", "svc:ci"))

        self.mod.action_approve_grant("ci", "svc:ci", "operator-47", rationale="ok")
        self.assertTrue(sb.check_grant("ci", "svc:ci"))
        self.assertEqual(sb.resolve_credential_for("ci", "svc:ci"), "tok-47")

        # Fail-closed do vault: aprovar grant inexistente lança VaultError.
        with self.assertRaises(VaultError):
            self.mod.action_approve_grant("ci", "svc:nao-existe", "operator-47")

        # Revogar derruba o grant (idempotente: revogar de novo não lança).
        self.mod.action_revoke_grant("ci", "svc:ci")
        self.assertFalse(sb.check_grant("ci", "svc:ci"))
        self.mod.action_revoke_grant("ci", "svc:ci")


class TestPluginActionRoutes(_IsolatedStoreBase):
    """(d): rotas do plugin sob o prefixo canônico, com efeito real por HTTP.

    O KanbanAdapter cacheia UMA conexão POR THREAD (delta 48): o sqlite3 do
    upstream é thread-bound (``check_same_thread`` ativo) e o TestClient/FastAPI
    rodam handlers sync numa threadpool — exatamente como o plugin kanban do
    próprio upstream, que abre uma conexão por request. Assim o caminho 200 do
    dispatch roda por HTTP REAL (portal thread) com o store canônico, sem o
    workaround in-thread do delta 47. EventStore file-backed já abre conexão
    por chamada (thread-safe); grants usam auth.json (arquivo, thread-safe)."""

    def _mount(self):
        self.mod.configure_stats(DashboardStats(kanban=self.adapter,
                                                event_store=self.store))
        app = FastAPI()
        app.include_router(self.mod.router, prefix="/api/plugins/haos")
        return app

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_dispatch_route_executes_ready_card_over_http(self):
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        tid = self._save_ready()
        app = self._mount()
        # Rota registrada sob o prefixo canônico (url_path_for materializa).
        self.assertEqual(app.router.url_path_for("post_dispatch"),
                         "/api/plugins/haos/dispatch")
        # POST por HTTP REAL: o handler roda na portal thread do TestClient e o
        # adapter abre conexão própria nessa thread (kanban thread-safe).
        with TestClient(app) as client:
            resp = client.post("/api/plugins/haos/dispatch", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"executed": [tid]})
        self.assertEqual(self.adapter.get_task(tid)["status"], "done")
        self.assertEqual(self.adapter.get_run(tid).status, "ended")

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_dispatch_409_without_kanban(self):
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        self.mod.configure_stats(None)
        app = FastAPI()
        app.include_router(self.mod.router, prefix="/api/plugins/haos")
        with TestClient(app) as client:
            resp = client.post("/api/plugins/haos/dispatch", json={"max_spawn": 2})
        self.assertEqual(resp.status_code, 409)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_evolution_decide_and_errors(self):
        from hermes.platform.evolution.ledger import EvolutionLedger

        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        pid = EvolutionLedger(self.store).submit(
            {"target": "x", "current_profile": "a", "proposed_profile": "b",
             "mode": "PROPOSAL_ONLY", "rationale": "r"})
        app = self._mount()
        with TestClient(app) as client:
            ok = client.post("/api/plugins/haos/evolution/decide", json={
                "proposal_id": pid, "verdict": "approved",
                "approver": "operator-47", "rationale": "ok"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["status"], "decided")
            # Decidida: some da fila observável.
            self.assertEqual(self.mod.evolution_payload()["pending"], [])
            # Estado inválido (proposta inexistente) -> 409, sem efeito.
            bad = client.post("/api/plugins/haos/evolution/decide", json={
                "proposal_id": "nao-existe", "verdict": "approved",
                "approver": "operator-47"})
            self.assertEqual(bad.status_code, 409)
            # Corpo malformado (sem campo obrigatório) -> 400.
            missing = client.post("/api/plugins/haos/evolution/decide", json={
                "verdict": "approved", "approver": "operator-47"})
            self.assertEqual(missing.status_code, 400)

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_grants_approve_revoke(self):
        from hermes.platform.auth.vault import SecretBroker

        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        sb = SecretBroker()
        sb.store_secret("svc:ci", "tok-47")
        sb.grant("ci", "svc:ci", policy="requires_approval", requester="build-bot")
        app = self._mount()
        with TestClient(app) as client:
            ok = client.post("/api/plugins/haos/grants/approve", json={
                "scope": "ci", "credential_ref": "svc:ci",
                "approver": "operator-47"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["status"], "approved")
            self.assertTrue(sb.check_grant("ci", "svc:ci"))

            revoked = client.post("/api/plugins/haos/grants/revoke", json={
                "scope": "ci", "credential_ref": "svc:ci"})
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.json()["status"], "revoked")
            self.assertFalse(sb.check_grant("ci", "svc:ci"))

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi ausente (host do dashboard não disponível)")
    def test_post_reviews_decide(self):
        """Delta 52: aprovar/rejeitar resultado de card via POST /reviews/decide."""
        try:
            from starlette.testclient import TestClient  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"TestClient indisponível: {exc}")
        tid = self.adapter.save_task(
            TaskSpec(id="T-REV-1", title="Card de review", goal="g", workspace_type="scratch"),
            status="READY",
        )
        self.mod.configure_stats(DashboardStats(kanban=self.adapter, event_store=self.store))
        app = self._mount()
        with TestClient(app) as client:
            # 1. Aprovação -> grava verdict approved e acceptance passed
            ok = client.post("/api/plugins/haos/reviews/decide", json={
                "task_id": tid, "verdict": "approved", "approver": "operator-52",
            })
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["status"], "decided")
            res = self.adapter.get_result(tid)
            self.assertIsNotNone(res)
            self.assertEqual(res.reviewer_verdict, "approved")
            self.assertTrue(any(a.get("status") == "passed" for a in (res.acceptance or [])))

            # 2. Veredito inválido -> 409
            bad = client.post("/api/plugins/haos/reviews/decide", json={
                "task_id": tid, "verdict": "invalid", "approver": "operator-52",
            })
            self.assertEqual(bad.status_code, 409)

            # 3. Campo obrigatório ausente -> 400
            missing = client.post("/api/plugins/haos/reviews/decide", json={
                "task_id": tid, "verdict": "approved",
            })
            self.assertEqual(missing.status_code, 400)


if __name__ == "__main__":
    unittest.main()
