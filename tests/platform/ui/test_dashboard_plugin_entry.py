"""Delta 46 + 49 — Testes de execução do entry do plugin de dashboard (visual).

O shell oficial carrega o entry (``dist/index.js``) como ``<script>`` e exige
que o bundle chame ``window.__HERMES_PLUGINS__.register("haos", Component)``
consumindo o backend ``/api/plugins/haos/state`` via ``SDK.fetchJSON``. Não
é possível validar o render num React real sem buildar o shell (documentado
como passo de operador); o contrato é verificado EXECUTANDO o bundle num
harness node com stubs do SDK (registro / mount / fetch / fail-closed).
Arquivos JS não são lidos como texto aqui — o harness os executa.

(a) manifest declara entry/css com relpaths seguros e arquivos existentes.
(b) bundle é inerte sem SDK (fail-closed) e registra ``haos`` com o SDK.
(c) o mount do componente chama ``fetchJSON("/api/plugins/haos/state")`` e
    renderiza totais DERIVADOS do payload fake (nada fabricado).
(d) delta 49 — a view de actions é INTERATIVA de verdade: o harness digita um
    operator/approver no input, clica cada botão e exige que o bundle POSTe
    nas rotas reais do backend (dispatch, evolution decide approved/rejected,
    grants approve/revoke, acp plan) com o corpo DERIVADO do payload + do
    operator digitado. O clique é executado (não lido): o stub do SDK grava
    url/method/body de cada fetchJSON e o harness compara com o esperado.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "hermes/platform/ui/dashboard_plugin"
_HARNESS = Path(__file__).resolve().parent / "_dashboard_plugin_harness.mjs"
_NODE = shutil.which("node")


class TestDashboardPluginEntryManifest(unittest.TestCase):
    """(a) O manifest aponta para o bundle real com relpath seguro."""

    def setUp(self):
        self.raw = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_entry_and_css_declared_and_safe(self):
        entry = self.raw["entry"]
        css = self.raw["css"]
        self.assertIsInstance(entry, str)
        self.assertIsInstance(css, str)
        for field in (entry, css):
            candidate = Path(field)
            self.assertFalse(candidate.is_absolute(), f"{field} não pode ser absoluto")
            resolved = (_PLUGIN_DIR / candidate).resolve()
            resolved.relative_to(_PLUGIN_DIR.resolve())  # deve ficar dentro do dir
            self.assertTrue(resolved.exists(), f"arquivo ausente: {resolved}")

    def test_tab_visible_now(self):
        tab = self.raw["tab"]
        self.assertIsInstance(tab, dict)
        self.assertFalse(tab.get("hidden"), "tab não deve estar oculto após o delta 46")
        self.assertEqual(tab["path"], "/haos")


@unittest.skipUnless(_NODE, "node ausente — execução do bundle não disponível")
class TestDashboardPluginEntryExecutes(unittest.TestCase):
    """(b)+(c)+(d) Executa o bundle real; verifica contrato de registro/mount e
    das actions do delta 49 (cliques reais no stub)."""

    def _run_harness(self):
        proc = subprocess.run(
            [_NODE, str(_HARNESS), str(_PLUGIN_DIR / "dist/index.js")],
            capture_output=True, text=True, timeout=60,
        )
        result = None
        for line in proc.stdout.splitlines():
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
        self.assertIsNotNone(result, f"harness sem JSON na saída:\n{proc.stdout}\n{proc.stderr}")
        if not result.get("ok"):
            self.fail(f"harness falhou: {result.get('errors')} "
                      f"(stderr: {proc.stderr.strip()})")
        return result

    def test_bundle_registers_and_renders_derived_state(self):
        result = self._run_harness()
        self.assertEqual(result["checks"].get("registered_name"), "haos")
        self.assertTrue(result["checks"].get("inert_without_sdk"))
        self.assertEqual(result["checks"].get("fetch_url"), "/api/plugins/haos/state")
        rendered = result["checks"].get("rendered_texts", [])
        for expected in ("Total: 2", "Awaiting approval", "Event node kinds: 2",
                         "Proposals awaiting decision: 1", "evo-abc123",
                         "Wire the connector", "Ship the dashboard",
                         "task.created", "task.completed",
                         "trace edges: 3", "correlation edges: 2"):
            self.assertIn(expected, rendered,
                          f"árvore renderizada não contém texto derivado {expected!r}")

    def test_delta49_action_buttons_post_real_backend_routes(self):
        """(d) Os botões existem e, clicados, POSTam nas rotas reais do plugin
        com o corpo derivado do payload + do operator digitado."""
        result = self._run_harness()
        checks = result["checks"]
        buttons = checks.get("action_buttons", [])
        for expected in ("dispatch", "acp-plan", "evolution-approve",
                         "evolution-reject", "grant-approve", "grant-revoke",
                         "review-approve", "review-reject"):
            self.assertIn(expected, buttons,
                          f"botão de action ausente: {expected} (temos {buttons})")
        posted = checks.get("posted_calls", [])

        def has(url, body):
            # Mesma serialização do harness (JSON.stringify é compacto:
            # separators=(",", ":")); o harness posta {"url":..., "body":...}.
            return json.dumps({"url": url, "body": body},
                              separators=(",", ":")) in posted

        # Corpos exatos que o backend recebeu (derivados do payload fake + operator).
        self.assertTrue(
            has("/api/plugins/haos/reviews/decide",
                {"task_id": "t-7c21", "verdict": "approved", "approver": "ops-49"}),
            f"review approve POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/reviews/decide",
                {"task_id": "t-7c21", "verdict": "changes_requested", "approver": "ops-49"}),
            f"review reject POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/dispatch", None),
            f"dispatch POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/evolution/decide",
                {"proposal_id": "evo-abc123", "verdict": "approved",
                 "approver": "ops-49"}),
            f"decide approved POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/evolution/decide",
                {"proposal_id": "evo-abc123", "verdict": "rejected",
                 "approver": "ops-49"}),
            f"decide rejected POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/grants/approve",
                {"scope": "ci", "credential_ref": "svc:ci",
                 "approver": "ops-49"}),
            f"grants approve POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/grants/revoke",
                {"scope": "ci", "credential_ref": "svc:ci"}),
            f"grants revoke POST esperado ausente: {posted}")
        self.assertTrue(
            has("/api/plugins/haos/acp/plan", {"instruction": "ops-49"}),
            f"acp plan POST esperado ausente: {posted}")

    def test_slow_action_shows_immediate_running_feedback(self):
        """(e) Regressão #visual: ação lenta (ACP spawna o peer) precisa mostrar
        progresso IMEDIATO — notice ``running`` — enquanto o POST está em voo,
        em vez de parecer morta (botões desabilitados, nenhuma mensagem até a
        promise resolver). O harness segura o POST do acp e prova o estado
        mid-flight."""
        result = self._run_harness()
        checks = result["checks"]
        self.assertTrue(checks.get("running_notice_midflight"),
                        "notice 'running' não renderizado durante o POST em voo")
        self.assertTrue(checks.get("success_notice_after_release"),
                        "notice de sucesso não substituiu o 'running' após a resposta")

    def test_payload_state_is_visible_not_just_counted(self):
        """(f) Delta 51 — transparência: o estado que o backend deriva precisa
        ser VISÍVEL na view, não só contado. A regressão #visual mostrou a
        seção Approvals com 'Pending reviews: 1' sem dizer QUAL card — o mesmo
        valia para os cards recentes do taskboard, os nós do memory graph e o
        plano devolvido pelo /acp/plan (descartado após o notice 'ok')."""
        result = self._run_harness()
        checks = result["checks"]
        rendered = checks.get("rendered_texts", [])
        for expected in ("Wire the connector", "Ship the dashboard",
                         "task.created", "task.completed",
                         "trace edges: 3", "correlation edges: 2"):
            self.assertIn(expected, rendered,
                          f"texto derivado não visível: {expected!r}")
        self.assertTrue(checks.get("acp_plan_visible"),
                        "plano ACP devolvido pelo backend não ficou visível na view")


if __name__ == "__main__":
    unittest.main()
