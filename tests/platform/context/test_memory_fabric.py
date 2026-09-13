"""Testes do Memory Fabric e Retrieval Router (P1).

Valida:
1. RetrievalRouter: roteamento determinístico de consultas por intenção (código -> LSP, arquitetura -> Obsidian, grafo -> GraphRAG, híbrido -> decomposição).
2. ObsidianAdapter: leitura, escrita de notas com frontmatter e busca no vault.
3. DecisionStore: resolução temporal de conflitos e marcação de decisões anteriores como superseded.
4. GraphRAGAdapter: consultas locais por entidade e globais por comunidade.
5. HermesFabricMemoryProvider: integração upstream com ciclo de vida (initialize, prefetch, shutdown).
"""

import tempfile
import unittest
from pathlib import Path

from hermes.platform.context.memory.decisions import DecisionStore
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.provider import HermesFabricMemoryProvider
from hermes.platform.context.primitives.item import AuthorityLevel
from hermes.platform.context.retrieval.router import QueryIntent, RetrievalRouter
from hermes.platform.context.sources.lsp import LSPSource


class TestRetrievalRouter(unittest.TestCase):
    """Testa o roteamento hierárquico e determinístico."""

    def setUp(self):
        self.router = RetrievalRouter()
        self.lsp_src = LSPSource()
        self.lsp_src.register_symbol_context(
            symbol_name="ProtocolAdapter",
            definition_file="hermes/platform/protocols/bus.py",
            definition_line=42,
            references_count=12,
        )
        self.router.register_source(self.lsp_src)

    def test_classify_code_query(self):
        """Consultas sobre definições, classes e métodos vão deterministicamente para CODE_SYMBOL."""
        intent = self.router.classify_intent("where is function ProtocolAdapter defined?")
        self.assertEqual(intent, QueryIntent.CODE_SYMBOL)

    def test_classify_architecture_query(self):
        """Consultas sobre ADRs, decisões e governança vão para ARCHITECTURE."""
        intent = self.router.classify_intent("qual a decisao de arquitetura da ADR-018?")
        self.assertEqual(intent, QueryIntent.ARCHITECTURE)

    def test_classify_relational_query(self):
        """Consultas sobre relações de dependência vão para RELATIONAL."""
        intent = self.router.classify_intent("what components depend on ProviderRouter?")
        self.assertEqual(intent, QueryIntent.RELATIONAL)

    def test_route_code_to_lsp_not_graphrag(self):
        """Consulta sobre símbolo consulta o LSP diretamente sem passar por GraphRAG."""
        items = self.router.route_and_retrieve("where is function ProtocolAdapter defined?", task_id="t1")
        self.assertTrue(len(items) >= 1)
        self.assertEqual(items[0].item_type, "code_symbol")
        self.assertIn("ProtocolAdapter", items[0].content)


class TestObsidianAndDecisionStore(unittest.TestCase):
    """Testa leitura/escrita no Vault e controle temporal de decisões."""

    def test_obsidian_note_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            obs = ObsidianAdapter(vault_path)
            note = obs.write_note(
                relative_path="20-Architecture/ADR-018.md",
                title="ADR-018 Protocol Fabric",
                content="Architecture decision details.",
                doc_type="architecture_decision",
                metadata={"status": "accepted", "author": "core-team"},
            )
            self.assertIsNotNone(note)
            self.assertEqual(note.title, "ADR-018 Protocol Fabric")
            self.assertEqual(note.authority, AuthorityLevel.ARCHITECTURE)

            # Busca no cofre
            results = obs.retrieve("ADR-018")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "obsidian-ADR-018")

    def test_decision_store_temporal_supersession(self):
        """Nova ADR supersede decisão antiga eliminando contradição no contexto."""
        store = DecisionStore()
        d1 = store.record_decision(
            decision_id="ADR-001",
            title="Model Fallback",
            content="Router always falls back to secondary model.",
        )
        self.assertEqual(d1.status, "accepted")

        # Registra ADR-002 supersedendo ADR-001
        d2 = store.record_decision(
            decision_id="ADR-002",
            title="Strict Exact Model Route",
            content="Router never silently falls back to another model.",
            supersedes=["ADR-001"],
        )

        active = store.retrieve()
        active_ids = [it.id for it in active]
        self.assertIn("decision-ADR-002", active_ids)
        self.assertNotIn("decision-ADR-001", active_ids)  # Antiga foi filtrada por supersessão!


class TestGraphRAGAndFabricMemoryProvider(unittest.TestCase):
    """Testa consultas em grafo e o composite MemoryProvider."""

    def test_graphrag_local_and_global(self):
        graph = GraphRAGAdapter()
        graph.register_entity("LaneExecutor", "component", "Executes isolated worker lane", "ExecutionEngine")
        graph.register_relation("Dispatcher", "LaneExecutor", "spawns", "Dispatcher manages lifecycle of lanes")
        graph.register_community_report("ExecutionEngine", "Execution subsystem handling parallel agent lanes.")

        local_item = graph.query_local("LaneExecutor")
        self.assertIsNotNone(local_item)
        self.assertIn("Dispatcher -> [spawns] -> LaneExecutor", local_item.content)

        global_items = graph.query_global("Execution")
        self.assertEqual(len(global_items), 1)
        self.assertIn("COMMUNITY THEME [ExecutionEngine]", global_items[0].content)

    def test_hermes_fabric_memory_provider_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            provider = HermesFabricMemoryProvider()
            self.assertTrue(provider.is_available())

            provider.initialize(session_id="test-sess", hermes_home=str(home_path))
            self.assertTrue(provider._initialized)

            prompt_block = provider.system_prompt_block()
            self.assertIn("Memory Fabric (HAOS Federation)", prompt_block)

            # Teste de prefetch
            prefetch_res = provider.prefetch("architecture")
            self.assertIsInstance(prefetch_res, str)

            provider.shutdown()
            self.assertFalse(provider._initialized)


if __name__ == "__main__":
    unittest.main()
