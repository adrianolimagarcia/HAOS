"""Testes de extensões visuais e telemetria da UI do Dashboard HAOS (Frente 3).

Valida os 4 Cards de Visibilidade & Resiliência no backend do plugin (plugin_api.py)
e no contrato renderizado pelo index.js (bundle estático).

Invariantes:
1. Model Failover & Resiliência:
   - Presença de rotas mapeadas para posturas ('coder', 'reviewer', 'architect', etc.)
   - Indicação de Claude 3.7 / DeepSeek-V3 / Claude 3.5
   - Enforce de 'zero_degradation_guaranteed' / 'zero_degradation_enforced'
   - Status dos provedores (HEALTHY / OPEN / RATE_LIMITED)

2. MCP Packs & Circuit Breakers:
   - Packs padrão presentes (dev_tools, review_tools, arch_tools, core)
   - Status de saúde dos servers (ex: ONLINE)
   - Posturas permitidas por pack e trust_tier

3. Kilo Worktrees & LSP Blast Radius:
   - Metadados de worktree isolado ativo
   - Modified files list e blast radius risk_score / risk_level
   - Suítes de testes exigidas para AutoMerge

4. Federated Hermes Network:
   - Identificador do nó e status do handshake HMAC mútuo
   - Lista de peer nodes com status ONLINE / mutual HMAC
   - Fluxo de eventos wire ANP / A2A
"""

import unittest
from pathlib import Path
from hermes.platform.ui.stats import DashboardStats
from hermes.platform.models.unified_fabric import ExactModelFailoverRouter
import plugins.haos.dashboard.plugin_api as haos_plugin_api
import hermes.platform.ui.dashboard_plugin.plugin_api as ui_plugin_api


class TestDashboardUiExtensions(unittest.TestCase):
    def setUp(self):
        self.stats = DashboardStats()

    def test_state_payload_includes_all_four_cards_haos_plugin(self):
        payload = haos_plugin_api.state_payload(self.stats)

        # Card 1: Model Failover & Resiliência
        self.assertIn("model_failover", payload)
        mf = payload["model_failover"]
        self.assertIn("routes", mf)
        self.assertTrue(mf.get("zero_degradation_enforced", False))
        routes = mf["routes"]
        # Invariante: rotas mapeadas devem conter posturas padrão
        self.assertTrue(any(p in routes for p in ("coder", "reviewer", "architect")))
        for r in routes.values():
            self.assertTrue(r.get("zero_degradation_guaranteed", False))
            self.assertTrue(len(r.get("providers", [])) > 0)

        # Card 2: MCP Packs & Circuit Breakers
        self.assertIn("mcp_packs", payload)
        packs = payload["mcp_packs"]
        for expected_pack in ("dev_tools", "review_tools", "arch_tools", "core"):
            self.assertIn(expected_pack, packs)
            pack_data = packs[expected_pack]
            self.assertIn("servers", pack_data)
            self.assertIn("allowed_postures", pack_data)
            self.assertIn("trust_tier", pack_data)
            self.assertTrue(len(pack_data["servers"]) > 0)

        # Card 3: Kilo Worktrees & LSP Blast Radius
        self.assertIn("kilo_worktrees", payload)
        kw = payload["kilo_worktrees"]
        self.assertIn("active_worktrees", kw)
        self.assertIn("blast_radius", kw)
        blast = kw["blast_radius"]
        self.assertIn("modified_files", blast)
        self.assertIn("affected_test_suites", blast)
        self.assertIn("risk_score", blast)
        self.assertIn("risk_level", blast)
        self.assertTrue(blast.get("automerge_eligible", False))

        # Card 4: Federated Hermes Network
        self.assertIn("federation", payload)
        fed = payload["federation"]
        self.assertIn("node_id", fed)
        self.assertIn("handshake_status", fed)
        self.assertIn("peer_nodes", fed)
        self.assertIn("wire_events", fed)
        self.assertTrue(len(fed["peer_nodes"]) >= 1)
        self.assertEqual(fed["handshake_status"], "MUTUAL_HMAC_VERIFIED")

    def test_state_payload_includes_all_four_cards_ui_plugin(self):
        payload = ui_plugin_api.state_payload(self.stats)
        self.assertIn("model_failover", payload)
        self.assertIn("mcp_packs", payload)
        self.assertIn("kilo_worktrees", payload)
        self.assertIn("federation", payload)

    def test_static_bundle_contains_card_definitions(self):
        haos_js = Path("plugins/haos/dashboard/dist/index.js").read_text(encoding="utf-8")
        ui_js = Path("hermes/platform/ui/dashboard_plugin/dist/index.js").read_text(encoding="utf-8")

        for js_content in (haos_js, ui_js):
            # Card 1 elements
            self.assertIn("ModelFailoverCard", js_content)
            self.assertIn("Model Failover & Resiliência", js_content)
            self.assertIn("Zero Degradation Guaranteed", js_content)

            # Card 2 elements
            self.assertIn("McpPacksCard", js_content)
            self.assertIn("MCP Packs & Circuit Breakers", js_content)

            # Card 3 elements
            self.assertIn("WorktreesBlastRadiusCard", js_content)
            self.assertIn("Kilo Worktrees & LSP Blast Radius", js_content)
            self.assertIn("AutoMerge", js_content)

            # Card 4 elements
            self.assertIn("FederatedNetworkCard", js_content)
            self.assertIn("Federated Hermes Network", js_content)
            self.assertIn("HMAC", js_content)


if __name__ == "__main__":
    unittest.main()
