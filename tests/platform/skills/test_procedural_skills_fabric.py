"""Unit tests for Procedural Intelligence and Skill Lifecycle Fabric (Etapa 2).

Tests:
1. Dynamic skill registration and semantic versioning in SkillRegistry.
2. Automated candidate skill generation from task history in SkillGenerator.
3. Lifecycle pipeline evaluation gate and security checksum validation in SkillLifecyclePipeline.
4. Supply-chain security verification and dependency/capability resolution.
"""

import unittest
from typing import List

from hermes.platform.skills.spec import SkillSpec
from hermes.platform.skills.procedural_engine import (
    SkillRegistry,
    SkillGenerator,
    SkillLifecyclePipeline,
    TaskExecutionRecord,
    EvalTestResult,
    parse_semver,
)


class TestProceduralSkillsFabric(unittest.TestCase):
    """Test suite for procedural engine, registry, generator, and lifecycle."""

    def test_semver_parsing(self):
        self.assertEqual(parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_semver("2.14.3"), (2, 14, 3))
        self.assertEqual(parse_semver("v3.2"), (3, 2, 0))
        self.assertEqual(parse_semver("5"), (5, 0, 0))
        with self.assertRaises(ValueError):
            parse_semver("invalid-semver")

    def test_skill_registry_dynamic_registration_and_semver(self):
        registry = SkillRegistry()

        v1 = SkillSpec(
            name="code-refactor",
            description="Refactor code according to clean code rules",
            version="1.0.0",
            status="active",
        )
        v2 = SkillSpec(
            name="code-refactor",
            description="Refactor code with AST analysis",
            version="1.2.0",
            status="active",
        )
        v3 = SkillSpec(
            name="code-refactor",
            description="Refactor code with multi-file AST graph",
            version="2.0.0",
            status="active",
        )

        registry.register(v1)
        registry.register(v2)
        registry.register(v3)

        # Retrieve highest semver
        latest = registry.get("code-refactor")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.version, "2.0.0")

        # Retrieve specific versions
        got_v1 = registry.get("code-refactor", "1.0.0")
        self.assertIsNotNone(got_v1)
        self.assertEqual(got_v1.version, "1.0.0")

        got_v2 = registry.get("code-refactor", "1.2.0")
        self.assertIsNotNone(got_v2)
        self.assertEqual(got_v2.version, "1.2.0")

        # Check list_versions
        versions = registry.list_versions("code-refactor")
        self.assertEqual(versions, ["1.0.0", "1.2.0", "2.0.0"])

        # Duplicate register without overwrite raises ValueError
        dup = SkillSpec(name="code-refactor", description="duplicate", version="1.0.0")
        with self.assertRaises(ValueError):
            registry.register(dup, overwrite=False)

        # Deprecation
        self.assertTrue(registry.deprecate("code-refactor", "1.0.0"))
        self.assertEqual(registry.get("code-refactor", "1.0.0").status, "deprecated")
        self.assertEqual(registry.get("code-refactor", "2.0.0").status, "active")

    def test_skill_generator_from_task_history(self):
        generator = SkillGenerator(min_pattern_frequency=2)

        # Create task execution records
        records = [
            TaskExecutionRecord(
                task_name="fix-test-failure",
                action_sequence=["read_file", "run_tests", "edit_file", "run_tests"],
                success=True,
                capabilities_used=["fs:read", "exec:terminal", "fs:write"],
            ),
            TaskExecutionRecord(
                task_name="fix-test-failure",
                action_sequence=["read_file", "run_tests", "edit_file", "run_tests"],
                success=True,
                capabilities_used=["fs:read", "exec:terminal", "fs:write"],
            ),
            TaskExecutionRecord(
                task_name="fix-test-failure",
                action_sequence=["read_file", "browse_web"],
                success=False,
                capabilities_used=["fs:read", "web:browse"],
            ),
        ]

        candidate = generator.generate_candidate_from_history(
            records,
            target_task_name="fix-test-failure",
            preferred_posture="coder",
            preferred_model_family="claude-3-5-sonnet",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.status, "candidate")
        self.assertEqual(candidate.preferred_posture, "coder")
        self.assertEqual(candidate.preferred_model_family, "claude-3-5-sonnet")
        self.assertIn("fs:read", candidate.capabilities_required)
        self.assertIn("fs:write", candidate.capabilities_required)
        self.assertIn("exec:terminal", candidate.capabilities_required)
        expected_hash = candidate.calculate_checksum(candidate.entry_script)
        self.assertEqual(candidate.checksum_sha256, expected_hash)
        self.assertIn("execute_skill", candidate.entry_script)
        self.assertEqual(candidate.metadata["synthesized_from_frequency"], 2)

        # Fail to generate if frequency is not met
        single_record = [
            TaskExecutionRecord(
                task_name="deploy",
                action_sequence=["build", "push"],
                success=True,
            )
        ]
        none_candidate = generator.generate_candidate_from_history(single_record, target_task_name="deploy")
        self.assertIsNone(none_candidate)

    def test_skill_lifecycle_pipeline_supply_chain_and_eval_gate(self):
        registry = SkillRegistry()

        # Pre-populate an active dependency
        base_dep = SkillSpec(
            name="base-tooling",
            description="Base tooling dependency",
            version="1.0.0",
            status="active",
        )
        registry.register(base_dep)

        available_caps = {"fs:read", "fs:write", "exec:terminal"}
        pipeline = SkillLifecyclePipeline(
            registry=registry,
            available_capabilities=available_caps,
            min_eval_score=0.85,
        )

        # Add a custom test runner
        def mock_evaluator(spec: SkillSpec) -> EvalTestResult:
            if "fail" in spec.name:
                return EvalTestResult(test_id="t1", passed=False, score=0.4, message="Failed validation")
            return EvalTestResult(test_id="t1", passed=True, score=0.95, message="All checks passed")

        pipeline.register_test_runner(mock_evaluator)

        # 1. Successful lifecycle promotion
        candidate = SkillSpec(
            name="code-patcher",
            description="Automated code patcher",
            version="1.0.0",
            status="candidate",
            capabilities_required=["fs:read", "fs:write"],
            dependencies=["base-tooling@1.0.0"],
            preferred_posture="coder",
            preferred_model_family="claude-sonnet",
        )
        candidate.calculate_checksum("Automated code patcher")

        ok, msg = pipeline.run_full_pipeline(candidate)
        self.assertTrue(ok, f"Pipeline failed: {msg}")
        self.assertEqual(candidate.status, "active")
        self.assertGreaterEqual(candidate.eval_score, 0.85)

        # Verify skill is now active in registry
        stored = registry.get("code-patcher", "1.0.0")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, "active")

        # 2. Checksum tampering detection
        tampered = SkillSpec(
            name="tampered-skill",
            description="Original description",
            version="1.0.0",
            status="candidate",
            capabilities_required=["fs:read"],
        )
        tampered.calculate_checksum("Original description")
        # Tamper content without updating checksum
        tampered.entry_script = "import os; os.system('malicious')"
        
        ok, msg = pipeline.advance_to_sandbox(tampered)
        self.assertFalse(ok)
        self.assertIn("Checksum verification failed", msg)

        # 3. Missing dependency rejection
        missing_dep_skill = SkillSpec(
            name="needs-dep",
            description="Needs dependency",
            version="1.0.0",
            status="candidate",
            dependencies=["non-existent-skill@1.0.0"],
        )
        missing_dep_skill.calculate_checksum("Needs dependency")
        ok, msg = pipeline.advance_to_sandbox(missing_dep_skill)
        self.assertFalse(ok)
        self.assertIn("Dependency resolution failed", msg)

        # 4. Missing capability rejection
        missing_cap_skill = SkillSpec(
            name="needs-gpu",
            description="Needs GPU",
            version="1.0.0",
            status="candidate",
            capabilities_required=["hardware:gpu_cluster"],
        )
        missing_cap_skill.calculate_checksum("Needs GPU")
        ok, msg = pipeline.advance_to_sandbox(missing_cap_skill)
        self.assertFalse(ok)
        self.assertIn("Capability resolution failed", msg)

        # 5. Low eval score gate rejection
        low_score_skill = SkillSpec(
            name="fail-skill",
            description="Skill destined to fail",
            version="1.0.0",
            status="candidate",
            capabilities_required=["fs:read"],
        )
        low_score_skill.calculate_checksum("Skill destined to fail")
        ok, msg = pipeline.run_full_pipeline(low_score_skill)
        self.assertFalse(ok)
        self.assertIn("Sandbox test failed", msg)


if __name__ == "__main__":
    unittest.main()
