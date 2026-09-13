"""Testes do ContextWire sobre o Protocol Fabric (A2A / ANP)."""

import json
import unittest
from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.primitives.package import ContextPackage
from hermes.platform.protocols.context_wire import (
    deserialize_context_item,
    deserialize_context_package,
    serialize_context_item,
    serialize_context_package,
    wrap_context_for_agent,
)


class TestContextWire(unittest.TestCase):
    def test_item_serialization_roundtrip(self):
        item = ContextItem(
            id="item-test",
            item_type="architecture_decision",
            source_uri="obsidian://test.md",
            content="Architecture rule content",
            trust=TrustLevel.ARCHITECTURE_DECISIONS,
            authority=AuthorityLevel.ARCHITECTURE,
            relevance=0.95,
        )
        serialized = serialize_context_item(item)
        deserialized = deserialize_context_item(serialized)

        self.assertEqual(deserialized.id, item.id)
        self.assertEqual(deserialized.trust, TrustLevel.ARCHITECTURE_DECISIONS)
        self.assertEqual(deserialized.authority, AuthorityLevel.ARCHITECTURE)
        self.assertEqual(deserialized.content, item.content)

    def test_clean_context_enforcement_for_reviewer(self):
        """No wire, itens como 'coder_chain_of_thought' DEVEM ser sumariamente expurgados para o Reviewer."""
        task_item = ContextItem(
            id="task-1",
            item_type="task_spec",
            source_uri="task://1",
            content="Task spec",
            trust=TrustLevel.TASK_SPEC,
            authority=AuthorityLevel.TASK,
        )
        diff_item = ContextItem(
            id="diff-1",
            item_type="git_diff",
            source_uri="git://diff",
            content="+ def foo(): pass",
            trust=TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
            authority=AuthorityLevel.ADVISORY,
        )
        cot_item = ContextItem(
            id="cot-1",
            item_type="coder_chain_of_thought",
            source_uri="runtime://coder/cot",
            content="I am not sure about this implementation.",
            trust=TrustLevel.AGENT_MESSAGES,
            authority=AuthorityLevel.NONE,
        )

        package = ContextPackage(
            id="pkg-raw-1",
            task_id="t-1",
            task_revision=1,
            posture_id="coder",
            sections={
                "task": [task_item],
                "code": [diff_item, cot_item],
            },
        )
        package.recalculate_digest_and_tokens()

        # Envelopa para o Reviewer
        enveloped = wrap_context_for_agent(
            task_id="t-1",
            context_package=package,
            recipient_posture="reviewer",
        )

        self.assertEqual(enveloped["status"], "enveloped")
        self.assertEqual(enveloped["stripped_items_count"], 1)

        clean_pkg = deserialize_context_package(enveloped["package"])
        self.assertIn("task", clean_pkg.sections)
        self.assertIn("code", clean_pkg.sections)

        code_item_ids = [it.id for it in clean_pkg.sections["code"]]
        self.assertIn("diff-1", code_item_ids)
        self.assertNotIn("cot-1", code_item_ids)  # Chain of thought expurgado com sucesso!

    def test_anp_message_envelope_context_package(self):
        """Verifica empacotamento em ANPMessage preservando trust, authority e hash."""
        import json
        from hermes.platform.protocols.anp.identity import DID
        from hermes.platform.protocols.anp.adapter import ANPMessage

        item = ContextItem(
            id="spec-1",
            item_type="task_spec",
            source_uri="artifact://tasks/spec",
            content="Spec content",
            trust=TrustLevel.TASK_SPEC,
            authority=AuthorityLevel.TASK,
        )
        pkg = ContextPackage(
            id="pkg-anp",
            task_id="task-42",
            task_revision=1,
            posture_id="coder",
            budget_limit=32000,
            sections={"task": [item]},
        )
        pkg.recalculate_digest_and_tokens()

        sender = DID.parse("did:wba:agent.example:coder")
        recipient = DID.parse("did:wba:agent.example:reviewer")

        # Clean context wrap para destinatário reviewer
        envelope = wrap_context_for_agent("task-42", pkg, recipient_posture="reviewer")
        msg = ANPMessage(
            message_id="msg-001",
            sender=sender,
            recipient=recipient,
            content=json.dumps(envelope),
            content_type="application/vnd.hermes.context-package+json",
        )

        self.assertEqual(msg.content_type, "application/vnd.hermes.context-package+json")
        self.assertTrue(len(msg.digest()) > 0)

        # Receptor deserializa
        received_envelope = json.loads(msg.content)
        reconstructed = deserialize_context_package(received_envelope["package"])

        self.assertEqual(reconstructed.id, f"clean-{pkg.id}")
        self.assertEqual(reconstructed.task_id, "task-42")
        self.assertEqual(reconstructed.posture_id, "reviewer")
        self.assertEqual(reconstructed.digest_hash, envelope["package"]["digest_hash"])
        rec_item = reconstructed.sections["task"][0]
        self.assertEqual(rec_item.trust, TrustLevel.TASK_SPEC)
        self.assertEqual(rec_item.authority, AuthorityLevel.TASK)


if __name__ == "__main__":
    unittest.main()
