import unittest
import time
from hermes.platform.context.memory.schemas import (
    KnowledgeItem,
    MentalModelBlockSchema,
    MentalModelSectionSchema,
    MentalModelASTSchema,
    DeltaOpSchema,
)


class TestADR022Schemas(unittest.TestCase):
    def test_knowledge_item_extended_fields(self):
        ki = KnowledgeItem(
            id="fact-1",
            title="Uso de Rust",
            kind="fact",
            content="Rust é prioridade para baixa latência",
            proof_count=3,
            supporting_quotes=[
                {"memory_id": "exp-1", "quote": "Rust é rápido", "timestamp": 1000.0}
            ],
            valid_from=1000.0,
            valid_until=2000.0,
        )
        self.assertEqual(ki.proof_count, 3)
        self.assertEqual(len(ki.supporting_quotes), 1)
        self.assertEqual(ki.valid_from, 1000.0)
        self.assertEqual(ki.valid_until, 2000.0)

        d = ki.to_dict()
        self.assertEqual(d["proof_count"], 3)
        self.assertEqual(d["valid_from"], 1000.0)
        self.assertEqual(d["valid_until"], 2000.0)

        restored = KnowledgeItem.from_dict(d)
        self.assertEqual(restored.proof_count, 3)
        self.assertEqual(restored.supporting_quotes[0]["quote"], "Rust é rápido")
        self.assertEqual(restored.digest(), ki.digest())

    def test_knowledge_item_legacy_backwards_compatibility(self):
        legacy_data = {
            "id": "fact-legacy",
            "title": "Legado",
            "content": "Conteúdo sem campos novos",
        }
        item = KnowledgeItem.from_dict(legacy_data)
        self.assertEqual(item.proof_count, 1)
        self.assertEqual(item.supporting_quotes, [])
        self.assertIsNone(item.valid_from)
        self.assertIsNone(item.valid_until)

    def test_mental_model_ast_and_compilation(self):
        b1 = MentalModelBlockSchema(
            block_id="b1",
            content="Appliance local sempre soberano",
            proof_count=5,
        )
        b2 = MentalModelBlockSchema(
            block_id="b2",
            content="Zero dependência de Docker",
            proof_count=2,
        )
        s1 = MentalModelSectionSchema(
            section_id="sec-infra",
            title="Infraestrutura",
            order=1,
            blocks=[b1, b2],
        )
        ast = MentalModelASTSchema(
            model_id="core_directives",
            title="Diretrizes do HAOS",
            version=1,
            sections=[s1],
        )

        md = ast.compile_to_markdown()
        self.assertIn("# Diretrizes do HAOS", md)
        self.assertIn("## Infraestrutura", md)
        self.assertIn("- Appliance local sempre soberano (provas: 5)", md)
        self.assertIn("- Zero dependência de Docker (provas: 2)", md)

        ast_dict = ast.to_dict()
        ast_restored = MentalModelASTSchema.from_dict(ast_dict)
        self.assertEqual(ast_restored.model_id, "core_directives")
        self.assertEqual(len(ast_restored.sections), 1)
        self.assertEqual(len(ast_restored.sections[0].blocks), 2)

    def test_delta_op_schema(self):
        op = DeltaOpSchema(
            op_type="replace_block",
            section_id="sec-infra",
            block_id="b1",
            new_content="Atualizado",
            proof_increment=1,
        )
        d = op.to_dict()
        self.assertEqual(d["type"], "replace_block")
        self.assertEqual(d["new_content"], "Atualizado")
        self.assertEqual(d["proof_increment"], 1)


if __name__ == "__main__":
    unittest.main()
