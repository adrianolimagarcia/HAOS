"""Testes completos das extensões do Context e Memory Fabric:
1. Memory Candidates, Deduplicação e MemoryRouter.
2. Knowledge Events e Incremental GraphRAG Updater.
3. Context Utilization Evals e integração com Evolution Ledger.
"""

import unittest
from hermes.platform.context.evals.utilization import ContextUtilizationEvaluator
from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.consolidation import MemoryConsolidator
from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventBus, KnowledgeEventType
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
from hermes.platform.context.memory.router import MemoryRouter
from hermes.platform.context.primitives.item import ContextItem, TrustLevel
from hermes.platform.context.primitives.package import ContextPackage


class TestMemoryCandidatesAndRouter(unittest.TestCase):
    def test_memory_router_classification(self):
        router = MemoryRouter()

        c_user = router.route_fact("User prefers dark mode and concise answers", source_uri="chat://1")
        self.assertEqual(c_user.proposed_destination, "core_user")

        c_arch = router.route_fact("ADR-019 specifies ANP framing protocol", source_uri="obsidian://adr.md")
        self.assertEqual(c_arch.proposed_destination, "obsidian")

        c_skill = router.route_fact("Procedure to deploy staging with docker compose", source_uri="task://2")
        self.assertEqual(c_skill.proposed_destination, "skill")

    def test_consolidation_deduplication(self):
        c1 = MemoryCandidate(fact="ADR-018 defines adapter architecture", source_uri="uri1", confidence=0.8)
        c2 = MemoryCandidate(fact="adr-018 defines adapter architecture.", source_uri="uri2", confidence=0.95)

        consolidator = MemoryConsolidator()
        deduped = consolidator.deduplicate_candidates([c1, c2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].confidence, 0.95)
        self.assertEqual(len(deduped[0].provenance), 2)


class TestIncrementalGraphRAG(unittest.TestCase):
    def test_event_driven_incremental_update(self):
        bus = KnowledgeEventBus()
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, event_bus=bus)

        event = KnowledgeEvent.create(
            event_type=KnowledgeEventType.NOTE_CREATED,
            uri="obsidian://20-Architecture/ADR-018.md",
            title="ADR-018 Protocol Fabric",
            content="""
# ADR-018: Protocol Fabric
ProtocolAdapter depends on ModelResolver and connects to Dispatcher.
See [[ANP-Spec]] for details.
""",
        )
        count = updater.process_event(event)
        self.assertGreater(count, 0)

        local_item = graph.query_local("ProtocolAdapter")
        self.assertIsNotNone(local_item)
        self.assertIn("ModelResolver", local_item.content)


class TestContextUtilizationEvals(unittest.TestCase):
    def test_utilization_evaluator(self):
        evaluator = ContextUtilizationEvaluator()
        pkg = ContextPackage(
            id="cp-1",
            task_id="t-1",
            task_revision=1,
            posture_id="coder",
            budget_limit=10000,
            sections={
                "task": [
                    ContextItem(
                        id="item-auth",
                        item_type="task_spec",
                        source_uri="task://1",
                        title="AuthManager JWT",
                        content="Fix JWT expiration bug in AuthManager",
                        token_cost=50,
                    )
                ],
                "code": [
                    ContextItem(
                        id="item-unrelated",
                        item_type="code_symbol",
                        source_uri="lsp://foo",
                        content="Unrelated database migration helper",
                        token_cost=200,
                    )
                ],
            },
        )

        metrics = evaluator.evaluate_usage(
            package=pkg,
            tool_calls=[{"name": "patch_code", "args": {"symbol": "AuthManager"}}],
            final_response="Fixed the JWT expiration bug in AuthManager.",
        )

        self.assertEqual(metrics.total_items_count, 2)
        self.assertEqual(metrics.cited_items_count, 1)
        self.assertGreater(metrics.utilization_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
