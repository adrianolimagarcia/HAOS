"""Testes para MemoryCandidate, MemoryConsolidator e MemoryRouter."""

import unittest
from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.consolidation import MemoryConsolidator
from hermes.platform.context.memory.router import MemoryRouter


class TestMemoryCandidatesAndRouting(unittest.TestCase):
    def test_candidate_creation_and_methods(self):
        c1 = MemoryCandidate(
            fact="User prefers dark mode and concise responses",
            source_uri="session://turn-1",
            confidence=0.9,
            scope="private",
            proposed_destination="core_user",
        )
        self.assertTrue(c1.is_high_confidence())
        self.assertEqual(c1.status, "pending")
        
        d = c1.to_dict()
        self.assertEqual(d["fact"], "User prefers dark mode and concise responses")
        self.assertEqual(d["confidence"], 0.9)
        self.assertEqual(d["scope"], "private")
        self.assertEqual(d["proposed_destination"], "core_user")
        self.assertIn("created_at", d)

        c2 = MemoryCandidate(
            fact="Tentative observation",
            source_uri="session://turn-2",
            confidence=0.5,
        )
        self.assertFalse(c2.is_high_confidence())

    def test_router_destination_classification(self):
        router = MemoryRouter()

        # Temporary / execution state -> "working"
        cand_working = router.route_fact(
            "Current execution state is buffer pending verification",
            source_uri="session://1",
        )
        self.assertEqual(cand_working.proposed_destination, "working")

        # Task outcome / residual risks -> "task"
        cand_task = router.route_fact(
            "Task outcome completed with PR merged and residual risk in db migration",
            source_uri="session://2",
        )
        self.assertEqual(cand_task.proposed_destination, "task")

        # Architecture decision / convention -> "obsidian"
        cand_obsidian = router.route_fact(
            "Architecture decision ADR-005: all platform modules use PEP-420 namespace convention",
            source_uri="session://3",
        )
        self.assertEqual(cand_obsidian.proposed_destination, "obsidian")

        # Reusable workflow / procedure -> "skill"
        cand_skill = router.route_fact(
            "Reusable workflow procedure: how to deploy release with step-by-step pipeline",
            source_uri="session://4",
        )
        self.assertEqual(cand_skill.proposed_destination, "skill")

        # User preference / identity -> "core_user"
        cand_user = router.route_fact(
            "User prefers to be addressed as Alex and timezone is America/Sao_Paulo",
            source_uri="session://5",
        )
        self.assertEqual(cand_user.proposed_destination, "core_user")

        # Agent posture / guideline -> "core_agent"
        cand_agent = router.route_fact(
            "Agent posture constraint: agent shall never disclose internal secret broker tokens",
            source_uri="session://6",
        )
        self.assertEqual(cand_agent.proposed_destination, "core_agent")

    def test_consolidator_deduplication(self):
        consolidator = MemoryConsolidator(similarity_threshold=0.85)

        c1 = MemoryCandidate(
            fact="User prefers dark theme in UI",
            confidence=0.88,
            source_uri="turn://1",
            provenance=["turn://1"],
            proposed_destination="core_user",
        )
        c2 = MemoryCandidate(
            fact="user prefers dark theme in ui!",  # Minor punctuation/casing
            confidence=0.95,
            source_uri="turn://2",
            provenance=["turn://2"],
            proposed_destination="core_user",
        )
        c3 = MemoryCandidate(
            fact="Architecture decision: strict typing across platform",
            confidence=0.9,
            source_uri="turn://3",
            provenance=["turn://3"],
            proposed_destination="obsidian",
        )

        deduped = consolidator.deduplicate_candidates([c1, c2, c3])
        self.assertEqual(len(deduped), 2)
        # Should retain c1 with updated confidence 0.95 and merged provenance
        user_cand = next(c for c in deduped if c.proposed_destination == "core_user")
        self.assertEqual(user_cand.confidence, 0.95)
        self.assertIn("turn://1", user_cand.provenance)
        self.assertIn("turn://2", user_cand.provenance)

    def test_consolidator_conflict_detection(self):
        consolidator = MemoryConsolidator()

        cand = MemoryCandidate(
            fact="router falls back to secondary model",
            confidence=0.9,
        )

        # Existing contradictory item with polarity mismatch
        existing = [
            {"id": "adr-1", "content": "router never falls back to secondary model"},
        ]

        conflict = consolidator.detect_conflicts(cand, existing)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["conflicting_item_id"], "adr-1")

    def test_consolidation_and_router_process(self):
        router = MemoryRouter()

        class MockMemoryProvider:
            def __init__(self):
                self.saved = []

            def write_candidate(self, candidate):
                self.saved.append(candidate)
                return True

            def get_existing_facts(self, destination):
                return []

        provider = MockMemoryProvider()

        cand_valid = router.route_fact(
            "User prefers Python 3.11 with strict types",
            source_uri="turn://1",
            confidence=0.9,
        )
        success = router.process_candidate(cand_valid, provider)
        self.assertTrue(success)
        self.assertEqual(cand_valid.status, "consolidated")
        self.assertEqual(len(provider.saved), 1)

        # Low confidence candidate rejection
        cand_low = router.route_fact(
            "User likes maybe blue",
            source_uri="turn://2",
            confidence=0.5,
        )
        success_low = router.process_candidate(cand_low, provider)
        self.assertFalse(success_low)
        self.assertEqual(cand_low.status, "rejected")


if __name__ == "__main__":
    unittest.main()
