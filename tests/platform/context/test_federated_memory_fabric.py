"""Testes unitários e de integração para FederatedMemoryCoordinator.

Testa:
1. Ingestão com todos os 4 escopos estritos (`private`, `team`, `project`, `global`) e rejeição de escopo inválido.
2. Deduplicação léxica e semântica com incremento de confiança e mescla de proveniência.
3. Resolução de conflito e rastreamento de supersessão temporal (`supersedes` / `superseded_by`).
4. Sincronização automática multi-store (Coordinator -> Obsidian -> GraphRAG -> Upstream Hermes Memory).
5. Pipeline de background writing / enfileiramento assíncrono.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.decisions import DecisionStore
from hermes.platform.context.memory.events import KnowledgeEventBus
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.provider import HermesFabricMemoryProvider


class TestFederatedMemoryFabric(unittest.TestCase):
    """Bateria de testes para FederatedMemoryCoordinator."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_federated_fabric_")
        self.vault_path = os.path.join(self.temp_dir, "vault")
        os.makedirs(self.vault_path, exist_ok=True)

        self.obsidian = ObsidianAdapter(vault_path=self.vault_path)
        self.graphrag = GraphRAGAdapter()
        self.decisions = DecisionStore()
        self.event_bus = KnowledgeEventBus()
        self.provider = HermesFabricMemoryProvider(
            obsidian_adapter=self.obsidian,
            graphrag_adapter=self.graphrag,
            decision_store=self.decisions,
        )
        self.provider.initialize(session_id="test_session")

        self.coordinator = FederatedMemoryCoordinator(
            vault_path=self.vault_path,
            obsidian_adapter=self.obsidian,
            graphrag_adapter=self.graphrag,
            memory_provider=self.provider,
            decision_store=self.decisions,
            event_bus=self.event_bus,
            auto_start_worker=False,
        )

    def tearDown(self) -> None:
        self.coordinator.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ingest_all_four_scopes(self) -> None:
        """Testa ingestão e filtragem com todos os 4 escopos estritos: private, team, project, global."""
        scopes = ["private", "team", "project", "global"]
        for sc in scopes:
            cand = self.coordinator.ingest_candidate_fact(
                fact=f"Knowledge rule specifically for scope {sc}",
                scope=sc,  # type: ignore
                provenance=f"session://task-{sc}",
                confidence=0.9,
            )
            self.assertEqual(cand.status, "consolidated")
            self.assertEqual(cand.scope, sc)

        # Verifica listagem por escopo
        for sc in scopes:
            records = self.coordinator.list_facts(scope=sc)  # type: ignore
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].scope, sc)
            self.assertIn(sc, records[0].fact)

        # Listagem total
        all_records = self.coordinator.list_facts()
        self.assertEqual(len(all_records), 4)

        # Valida que escopo inválido levanta ValueError
        with self.assertRaises(ValueError):
            self.coordinator.ingest_candidate_fact(
                fact="Illegal scope item",
                scope="universal",  # type: ignore
            )

    def test_lexical_and_semantic_deduplication(self) -> None:
        """Testa deduplicação léxica e semântica com mescla de proveniência e aumento de confiança."""
        fact1 = "PostgreSQL is strictly required for auth storage in cluster A."
        c1 = self.coordinator.ingest_candidate_fact(
            fact=fact1,
            scope="project",
            provenance="repo://docs/setup.md",
            confidence=0.8,
        )
        self.assertEqual(c1.status, "consolidated")

        # Fato quase idêntico (pequena variação de pontuação e case)
        fact2 = "postgresql is strictly required for auth storage in cluster a!"
        c2 = self.coordinator.ingest_candidate_fact(
            fact=fact2,
            scope="project",
            provenance="session://chat-turn-12",
            confidence=0.95,
        )
        self.assertEqual(c2.status, "consolidated")
        self.assertEqual(c1.id, c2.id)

        # Confere que o registro unificado foi atualizado
        rec = self.coordinator.get_fact(c1.id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.confidence, 0.95)
        self.assertIn("repo://docs/setup.md", rec.provenance)
        self.assertIn("session://chat-turn-12", rec.provenance)

        # Não deve haver 2 registros no escopo
        facts = self.coordinator.list_facts(scope="project")
        self.assertEqual(len(facts), 1)

    def test_conflict_resolution_and_temporal_supersession(self) -> None:
        """Testa detecção de conflitos e rastreamento de supersedes / superseded_by."""
        # Registra regra inicial
        cand_old = self.coordinator.ingest_candidate_fact(
            fact="ADR-101: Deployments must use Docker Swarm exclusively.",
            scope="project",
            provenance="docs/adr-101.md",
            confidence=1.0,
            metadata={"title": "ADR-101: Docker Swarm Deployment"},
        )
        old_id = cand_old.id

        # Registra nova decisão contraditória ou com supersedes explícito
        cand_new = self.coordinator.ingest_candidate_fact(
            fact="ADR-102: Deployments must migrate to Kubernetes instead of Docker Swarm.",
            scope="project",
            provenance="docs/adr-102.md",
            confidence=1.0,
            metadata={
                "title": "ADR-102: Kubernetes Migration",
                "supersedes": [old_id],
            },
        )
        new_id = cand_new.id

        # Confere relação de supersessão
        rec_old = self.coordinator.get_fact(old_id)
        rec_new = self.coordinator.get_fact(new_id)
        self.assertIsNotNone(rec_old)
        self.assertIsNotNone(rec_new)

        self.assertEqual(rec_old.superseded_by, new_id)
        self.assertIn(old_id, rec_new.supersedes)

        # Consulta padrão sem include_superseded ignora o fato antigo
        active_facts = self.coordinator.list_facts(scope="project", include_superseded=False)
        active_ids = [f.id for f in active_facts]
        self.assertIn(new_id, active_ids)
        self.assertNotIn(old_id, active_ids)

        # Consulta com include_superseded traz ambos
        all_facts = self.coordinator.list_facts(scope="project", include_superseded=True)
        self.assertEqual(len(all_facts), 2)

        # Confere também no DecisionStore
        dec_old = self.decisions.get_decision(old_id)
        dec_new = self.decisions.get_decision(new_id)
        self.assertIsNotNone(dec_old)
        self.assertIsNotNone(dec_new)
        self.assertEqual(dec_old.status, "superseded")
        self.assertEqual(dec_old.superseded_by, new_id)
        self.assertIn(old_id, dec_new.supersedes)

    def test_multi_store_synchronization(self) -> None:
        """Testa sincronização automática: Coordinator -> Obsidian -> GraphRAG -> Upstream Hermes Memory."""
        decision_text = "ADR-200: Adopt Event-Driven Architecture with Kafka as messaging backbone."
        cand = self.coordinator.ingest_candidate_fact(
            fact=decision_text,
            scope="project",
            provenance="architecture/adr-200.md",
            confidence=0.99,
            metadata={"title": "ADR-200: Kafka Event Backbone"},
            sync=True,
        )

        fact_id = cand.id
        # 1. Verifica no Obsidian Vault se a nota Markdown canônica foi gerada
        obsidian_note_path = os.path.join(self.vault_path, "20-Architecture", f"{fact_id}.md")
        self.assertTrue(os.path.exists(obsidian_note_path), f"Arquivo não encontrado: {obsidian_note_path}")

        note_content = Path(obsidian_note_path).read_text(encoding="utf-8")
        self.assertIn("ADR-200: Adopt Event-Driven Architecture", note_content)
        self.assertIn("Scope:", note_content)
        self.assertIn("type: architecture_decision", note_content)

        # 2. Verifica no GraphRAG: o event bus acionou IncrementalGraphRAGUpdater
        # Deve haver entidades ou itens indexados
        local_or_global = self.graphrag.retrieve(query="Kafka")
        self.assertTrue(len(local_or_global) > 0, "GraphRAG deveria conter informações sobre Kafka")
        self.assertTrue(any("Kafka" in item.content or "Kafka" in item.title for item in local_or_global))

        # 3. Verifica no Provedor Upstream (HermesFabricMemoryProvider)
        prefetched = self.provider.prefetch("Kafka")
        self.assertIn("Kafka", prefetched)

    def test_background_worker_pipeline(self) -> None:
        """Testa ingestão assíncrona (sync=False) via background worker."""
        self.coordinator.start_background_worker()

        cand = self.coordinator.ingest_candidate_fact(
            fact="Team convention: All pull requests must have at least 2 approvals.",
            scope="team",
            provenance="guidelines/review.md",
            confidence=0.9,
            sync=False,
        )
        self.assertEqual(cand.status, "pending")

        # Aguarda esvaziamento da fila de background
        self.coordinator._ingest_queue.join()

        # Confere que o fato foi consolidado pelo worker
        time.sleep(0.05)
        records = self.coordinator.list_facts(scope="team")
        self.assertEqual(len(records), 1)
        self.assertIn("pull requests", records[0].fact)

        self.coordinator.stop_background_worker()


if __name__ == "__main__":
    unittest.main()
