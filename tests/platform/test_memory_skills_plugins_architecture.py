"""Tests for Memory Schemas, SkillSpec Lifecycle, and UnifiedPluginManager."""

import unittest
from hermes.platform.context.memory.schemas import (
    ADRDocument,
    KnowledgeItem,
    classify_experience,
)
from hermes.platform.plugins.unified_manager import PluginManifest, UnifiedPluginManager
from hermes.platform.skills.spec import SkillSpec


class TestMemoryAndSkillsArchitecture(unittest.TestCase):
    # 1. Memory Fabric Schemas & Provenance
    def test_knowledge_item_provenance_and_supersession(self):
        item = KnowledgeItem(
            id="ki-auth-01",
            title="Session Token Invalidation Rule",
            kind="constraint",
            content="Session tokens must expire immediately on logout.",
            scope="project",
            source_uri="obsidian://ADR-042.md",
            version=1,
        )
        self.assertTrue(item.is_active())
        self.assertEqual(len(item.digest()), 64)  # SHA-256

        # Temporal supersession
        item.superseded_by = "ki-auth-02"
        self.assertFalse(item.is_active())

    def test_adr_document_to_markdown(self):
        adr = ADRDocument(
            id="ADR-018",
            title="Protocol Fabric Wire",
            status="accepted",
            context="Multi-agent communication requires isolation.",
            decision="Use ANP message envelope with clean context filtering.",
            consequences="Reviewer agent never receives raw reasoning.",
        )
        md = adr.to_markdown()
        self.assertIn("id: ADR-018", md)
        self.assertIn("## Status\nACCEPTED", md)
        self.assertIn("Use ANP message envelope", md)

    def test_classify_experience_memory_vs_skill(self):
        # Procedimento executável -> Skill
        proc_text = "Passo a passo para rodar deploy de contêineres no cluster"
        self.assertEqual(classify_experience(proc_text), "skill")

        steps = ["git checkout -b fix", "pytest tests/", "git push"]
        self.assertEqual(classify_experience("Correção de bug", action_sequence=steps), "skill")

        # Fato/Decisão -> Memória
        dec_text = "Decidimos utilizar banco SQLite em modo WAL para tarefas locais"
        self.assertEqual(classify_experience(dec_text), "memory")

    # 2. SkillSpec & Lifecycle
    def test_skill_spec_lifecycle_and_security_gate(self):
        skill = SkillSpec(
            name="github-pr-sync",
            description="Synchronize and rebase local branches against remote PRs.",
            capabilities_required=["git", "network"],
        )
        self.assertEqual(skill.status, "candidate")

        # candidate -> sandbox
        skill.promote("sandbox")
        self.assertEqual(skill.status, "sandbox")

        # sandbox -> eval
        skill.promote("eval")
        self.assertEqual(skill.status, "eval")

        # Gate de segurança: ativação sem eval_score mínimo deve falhar
        with self.assertRaises(PermissionError):
            skill.promote("active", min_eval_score=0.90)

        # Atribui score de benchmark e ativa com sucesso
        skill.eval_score = 0.95
        skill.promote("active", min_eval_score=0.90)
        self.assertEqual(skill.status, "active")

        # Checksum de supply-chain
        checksum = skill.calculate_checksum("def run(): pass")
        self.assertEqual(len(checksum), 64)

    # 3. Unified Plugin Manager & Capabilities
    def test_unified_plugin_manager_capabilities_and_hot_reload(self):
        manager = UnifiedPluginManager()
        manifest = PluginManifest(
            id="lsp-python",
            name="Python LSP Intelligence",
            kind="capability",
            capabilities_provided=["lsp:python", "code:symbols"],
            permissions_required=["fs:read"],
        )

        manager.register_plugin(manifest)
        self.assertIn("lsp:python", manager.list_active_capabilities())
        self.assertIn("code:symbols", manager.list_active_capabilities())
        self.assertTrue(manager.check_permissions("lsp-python", "fs:read"))
        self.assertFalse(manager.check_permissions("lsp-python", "net:outbound"))

        # Hot reload com novas capacidades
        updated_manifest = PluginManifest(
            id="lsp-python",
            name="Python LSP Intelligence",
            kind="capability",
            capabilities_provided=["lsp:python", "code:symbols", "code:hierarchy"],
            permissions_required=["fs:read", "fs:write"],
        )
        manager.hot_reload_plugin(updated_manifest)
        self.assertIn("code:hierarchy", manager.list_active_capabilities())
        self.assertTrue(manager.check_permissions("lsp-python", "fs:write"))


if __name__ == "__main__":
    unittest.main()
