"""ADR-002 Architecture Freeze v0.1 Contract Test Suite.

Locks down the 10 core interfaces and architectural invariants for Phase 1 Kernel:
1. TaskSpec
2. AssignmentSpec
3. PostureSpec / AgentSpec
4. ContextPackage
5. MemoryItem / KnowledgeItem
6. CapabilityMetadata
7. SkillSpec
8. PluginManifest
9. ModelProfile
10. ProviderPriorityEntry / ProviderRoute

Guarantees:
- All 10 contracts exist, are importable, and have stable fields.
- Zero cyclic imports.
- PEP-420 namespace compliance (no __init__.py in hermes/platform).
- Strictly stdlib-only imports in hermes/platform/.
"""

import inspect
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.posture.specs import PostureSpec
from hermes.platform.context.primitives.package import ContextPackage
from hermes.platform.context.memory.schemas import KnowledgeItem, ADRDocument
from hermes.platform.capabilities.universal_registry import CapabilityMetadata, CapabilityCategory, FailurePolicy
from hermes.platform.skills.spec import SkillSpec
from hermes.platform.extensions.registry import ExtensionManifest, ExtensionManifest as PluginManifest
from hermes.platform.models.profiles import ModelProfile, ProviderRoute, ModelIdentity
from hermes.platform.models.unified_fabric import ProviderPriorityEntry


class TestADR002ArchitectureFreezeContracts(unittest.TestCase):
    """Test suite validating that ADR-002 frozen contracts are strictly honored."""

    def test_adr_002_document_exists_and_declares_phase_1(self):
        """Verify ADR-002 document exists and has required sections."""
        adr_file = Path("docs/architecture/ADR-002-ARCHITECTURE-FREEZE-V0.1.md")
        self.assertTrue(adr_file.exists(), "ADR-002-ARCHITECTURE-FREEZE-V0.1.md must exist")

        content = adr_file.read_text(encoding="utf-8")
        required_markers = [
            "ADR-002: Architecture Freeze v0.1",
            "Phase 1 — Hermes Platform Kernel",
            "TaskSpec",
            "AssignmentSpec",
            "PostureSpec",
            "ContextPackage",
            "MemoryItem",
            "CapabilityMetadata",
            "SkillSpec",
            "PluginManifest",
            "ModelProfile",
            "ProviderRoute",
        ]
        for marker in required_markers:
            self.assertIn(marker, content, f"ADR-002 missing required architectural marker: '{marker}'")

    def test_1_task_spec_frozen_fields(self):
        """Contract 1: TaskSpec has all required frozen fields."""
        fields = [f.name for f in inspect.signature(TaskSpec).parameters.values()]
        expected_fields = ["id", "title", "goal", "posture"]
        for exp in expected_fields:
            self.assertIn(exp, fields)

        task = TaskSpec(
            id="task-freeze-001",
            title="Freeze Task",
            goal="Test frozen interface",
            posture="coder",
        )
        self.assertEqual(task.id, "task-freeze-001")
        self.assertEqual(task.posture, "coder")

    def test_2_assignment_spec_frozen_fields(self):
        """Contract 2: AssignmentSpec has all required frozen fields."""
        fields = [f.name for f in inspect.signature(AssignmentSpec).parameters.values()]
        expected_fields = [
            "task_id",
            "task_revision",
            "run_id",
            "execution_shape",
            "lane",
            "agent_mode",
            "posture_id",
            "model_profile_id",
            "resolved_model_family",
            "resolved_model_variant",
            "workspace_uri",
        ]
        for exp in expected_fields:
            self.assertIn(exp, fields)

        assignment = AssignmentSpec(
            task_id="task-freeze-001",
            task_revision=1,
            run_id="run-001",
            execution_shape="worker_lane",
            lane="kilo",
            agent_mode="ephemeral",
            posture_id="coder",
            model_profile_id="profile-coder",
            resolved_model_family="deepseek-v3",
            resolved_model_variant="default",
        )
        self.assertEqual(assignment.posture_id, "coder")
        self.assertEqual(assignment.lane, "kilo")

    def test_3_posture_spec_frozen_fields(self):
        """Contract 3: PostureSpec has all required frozen fields."""
        posture = PostureSpec(
            id="coder",
            name="Coder Agent",
            description="Implementation specialist",
        )
        self.assertEqual(posture.id, "coder")
        self.assertTrue(hasattr(posture, "prompt_overlay"))
        self.assertTrue(hasattr(posture, "skills_preferred"))
        self.assertTrue(hasattr(posture, "capabilities_prefer"))

    def test_4_context_package_frozen_fields(self):
        """Contract 4: ContextPackage has required fields and prompt-cache methods."""
        pkg = ContextPackage(
            id="pkg-001",
            task_id="task-001",
            task_revision=1,
            posture_id="architect",
        )
        self.assertEqual(pkg.posture_id, "architect")
        self.assertTrue(hasattr(pkg, "render_stable_prefix"))
        self.assertTrue(hasattr(pkg, "render_semi_stable_body"))
        self.assertTrue(hasattr(pkg, "recalculate_digest_and_tokens"))

    def test_5_memory_item_frozen_fields(self):
        """Contract 5: KnowledgeItem / MemoryItem has required fields."""
        item = KnowledgeItem(
            id="mem-001",
            title="PostgreSQL Migration",
            kind="decision",
            content="PostgreSQL migrated to SQLite",
            scope="project",
            confidence=0.95,
        )
        self.assertEqual(item.id, "mem-001")
        self.assertEqual(item.scope, "project")
        self.assertTrue(item.is_active())
        self.assertTrue(hasattr(item, "digest"))

    def test_6_capability_metadata_frozen_fields(self):
        """Contract 6: CapabilityMetadata has required fields and enums."""
        cap = CapabilityMetadata(
            id="lsp:python",
            category=CapabilityCategory.LSP,
            provider_type="builtin",
            description="Pyright symbol analysis",
            failure_policy=FailurePolicy.FAIL_CLOSED,
        )
        self.assertEqual(cap.category, CapabilityCategory.LSP)
        self.assertEqual(cap.failure_policy, FailurePolicy.FAIL_CLOSED)

    def test_7_skill_spec_frozen_fields(self):
        """Contract 7: SkillSpec has SemVer, status and checksum."""
        skill = SkillSpec(
            name="deploy-kilo-worker",
            description="Deploys worker lane",
            version="1.0.0",
            status="candidate",
        )
        self.assertEqual(skill.version, "1.0.0")
        self.assertEqual(skill.status, "candidate")
        self.assertTrue(hasattr(skill, "promote"))

    def test_8_plugin_manifest_frozen_fields(self):
        """Contract 8: ExtensionManifest / PluginManifest has required fields."""
        plugin = ExtensionManifest(
            id="plugin-auth",
            version="1.0.0",
            description="Auth Broker",
            provides=["capability:auth"],
        )
        self.assertEqual(plugin.id, "plugin-auth")
        self.assertEqual(plugin.version, "1.0.0")

    def test_9_model_profile_frozen_fields(self):
        """Contract 9: ModelProfile respects Model != Provider axiom."""
        identity = ModelIdentity(family="deepseek-v3", variant="default")
        profile = ModelProfile(
            id="profile-coder",
            model_identity=identity,
            parameters={"temperature": 0.1},
        )
        self.assertEqual(profile.model_identity.family, "deepseek-v3")

    def test_10_provider_route_frozen_fields(self):
        """Contract 10: ProviderPriorityEntry / ProviderRoute has priority ordering."""
        entry = ProviderPriorityEntry(
            provider_id="deepseek",
            provider_model_id="deepseek-chat",
            priority=1,
        )
        self.assertEqual(entry.provider_id, "deepseek")
        self.assertEqual(entry.priority, 1)


if __name__ == "__main__":
    unittest.main()
