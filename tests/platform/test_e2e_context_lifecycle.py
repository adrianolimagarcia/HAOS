"""Teste E2E do ciclo completo de engenharia autônoma:
TaskSpec -> ContextPackage compilation -> Coder execution -> Clean Reviewer isolation -> Context Utilization Evals -> MemoryRouter / Knowledge Events -> Incremental GraphRAG.
"""

import tempfile
import unittest
from pathlib import Path

from hermes.platform.context.budget.budgeter import ContextBudgeter
from hermes.platform.context.evals.utilization import ContextUtilizationEvaluator
from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventBus, KnowledgeEventType
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.router import MemoryRouter
from hermes.platform.context.policies.policy import get_coder_policy, get_reviewer_policy
from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.lsp import LSPSource
from hermes.platform.context.sources.task import TaskSource
from hermes.platform.protocols.context_wire import wrap_context_for_agent
from hermes.platform.tasks.spec import TaskSpec


class TestE2EContextLifecycle(unittest.TestCase):
    def test_full_autonomous_engineering_lifecycle(self):
        # 1. Criação formal da TaskSpec com critérios de aceite
        task_spec = TaskSpec(
            id="task-auth-042",
            title="Implement Token Revocation in AuthManager",
            goal="Add immediate token revocation endpoint",
            description="Users must be able to revoke active session tokens immediately.",
            acceptance_criteria=["Must add revoke() method", "Must pass unit tests"],
        )

        # 2. Compilação de Contexto inicial para o Coder
        task_source = TaskSource(task_spec)
        lsp_source = LSPSource()
        lsp_source.register_symbol_context(
            symbol_name="AuthManager",
            definition_file="hermes/platform/auth/vault.py",
            definition_line=45,
            callers=["SecretBroker", "Dispatcher"],
            references_count=6,
        )

        candidates = {
            "task": task_source.retrieve(),
            "code": lsp_source.retrieve("AuthManager"),
        }

        coder_policy = get_coder_policy(max_tokens=32000)
        budgeter = ContextBudgeter(policy=coder_policy, total_budget=32000)
        coder_package = budgeter.fit_package(
            task_id=task_spec.id,
            task_revision=1,
            candidates_by_section=candidates,
        )

        self.assertIn("task", coder_package.sections)
        self.assertIn("code", coder_package.sections)

        # 3. Execução simulada do Coder: produz diff, testes e rascunho de raciocínio
        coder_diff = ContextItem(
            id="diff-auth-revocation",
            item_type="git_diff",
            source_uri="git://diff/auth",
            title="Git Diff: AuthManager.revoke",
            content="+ def revoke(token_id: str): pass",
            trust=TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
            authority=AuthorityLevel.ADVISORY,
        )
        coder_cot = ContextItem(
            id="cot-coder-uncertainty",
            item_type="coder_chain_of_thought",
            source_uri="runtime://coder/cot",
            title="Coder Reasoning",
            content="I am not sure if we need database locks here, used simple dict for now.",
            trust=TrustLevel.AGENT_MESSAGES,
            authority=AuthorityLevel.NONE,
        )

        coder_output_sections = {
            "task": coder_package.sections["task"],
            "code": [coder_diff, coder_cot],
        }
        coder_package.sections = coder_output_sections
        coder_package.recalculate_digest_and_tokens()

        # 4. Handoff para o Reviewer via Protocol Fabric (Clean Context Enforcement)
        reviewer_enveloped = wrap_context_for_agent(
            task_id=task_spec.id,
            context_package=coder_package,
            recipient_posture="reviewer",
        )

        # Invariante: O chain-of-thought do coder foi estritamente expurgado do contexto do Reviewer!
        self.assertEqual(reviewer_enveloped["stripped_items_count"], 1)
        rev_items_code = reviewer_enveloped["package"]["sections"]["code"]
        self.assertEqual(len(rev_items_code), 1)
        self.assertEqual(rev_items_code[0]["item_type"], "git_diff")
        self.assertNotIn("cot-coder-uncertainty", [it["id"] for it in rev_items_code])

        # 5. Avaliação de Utilização de Contexto (Evals)
        evaluator = ContextUtilizationEvaluator()
        metrics = evaluator.evaluate_usage(
            package=coder_package,
            tool_calls=[{"name": "patch_file", "args": {"symbol": "AuthManager"}}],
            final_response="Implemented token revocation in AuthManager and created unit tests.",
        )
        self.assertGreater(metrics.utilization_ratio, 0.0)

        # 6. Aprendizado e Consolidação via MemoryRouter e Incremental GraphRAG
        bus = KnowledgeEventBus()
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, event_bus=bus)

        router = MemoryRouter()
        candidate = router.route_fact(
            fact="ADR-042 specifies AuthManager must use immediate in-memory revocation cache",
            source_uri=f"task://{task_spec.id}",
        )
        self.assertEqual(candidate.proposed_destination, "obsidian")

        # Publica KnowledgeEvent
        event = KnowledgeEvent.create(
            event_type=KnowledgeEventType.DECISION_RECORDED,
            uri=f"obsidian://20-Architecture/{candidate.id}.md",
            title="ADR-042 AuthManager Revocation Cache",
            content="AuthManager connects to SecretBroker and depends on RevocationCache.",
        )
        updater.process_event(event)

        # Verifica se o GraphRAG capturou a relação de forma incremental
        local_query = graph.query_local("AuthManager")
        self.assertIsNotNone(local_query)
        self.assertIn("SecretBroker", local_query.content)


if __name__ == "__main__":
    unittest.main()
