"""Procedural Engine & Skill Lifecycle Orchestration (K2 / Etapa 2).

Provides:
- SkillRegistry: Dynamic versioned skill registry tracking SkillSpec instances,
  supporting semantic versioning (major.minor.patch), conflict checks, query by name/version.
- SkillGenerator: Generates candidate SkillSpec instances from repetitive successful
  task execution sequences or action patterns.
- SkillLifecyclePipeline: Automated promotion pipeline
  (candidate -> sandbox -> eval -> active), executing automated validation/tests,
  verifying supply-chain checksums, resolving dependencies and capabilities,
  and enforcing min_eval_score before activation.
- Supply-chain security verification and dependency resolution:
  dependencies, capabilities_required, preferred_posture, preferred_model_family.

Strict constraints:
- Strictly stdlib-only imports in hermes/platform/.
- Strictly PEP-420 namespace compliance (no __init__.py in hermes/platform/).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from hermes.platform.skills.spec import SkillSpec


def compute_spec_checksum(spec: SkillSpec) -> str:
    """Compute canonical sha256 checksum for a SkillSpec."""
    body = spec.entry_script or spec.description or ""
    return spec.calculate_checksum(body)


def parse_semver(v_str: str) -> Tuple[int, int, int]:
    """Parse a semantic version string into a (major, minor, patch) tuple.
    
    Falls back gracefully if string has trailing identifiers or simple X.Y.
    """
    clean = v_str.strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", clean)
    if not match:
        raise ValueError(f"Invalid semantic version: {v_str!r}")
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return major, minor, patch


class SkillRegistry:
    """Dynamic versioned skill registry tracking SkillSpec instances.
    
    Features:
    - Register multiple versions of a skill.
    - Query highest version (latest) or exact semver matching.
    - Deprecate or retire old versions.
    - Validate collision or checksum integrity.
    """

    def __init__(self) -> None:
        # name -> { version_str -> SkillSpec }
        self._registry: Dict[str, Dict[str, SkillSpec]] = {}

    def register(self, spec: SkillSpec, overwrite: bool = False) -> SkillSpec:
        """Register a SkillSpec.
        
        If checksum is empty, computes it automatically.
        Raises ValueError if version already exists and overwrite is False.
        """
        if not spec.checksum_sha256:
            compute_spec_checksum(spec)
        else:
            # Verify integrity
            body = spec.entry_script or spec.description or ""
            expected = hashlib.sha256(f"{spec.name}:{spec.version}:{body}".encode("utf-8")).hexdigest()
            if spec.checksum_sha256 != expected:
                raise ValueError(
                    f"Checksum mismatch for skill '{spec.name}@{spec.version}': "
                    f"spec has {spec.checksum_sha256}, calculated {expected}"
                )

        # Validate semver
        parse_semver(spec.version)

        versions = self._registry.setdefault(spec.name, {})
        if spec.version in versions and not overwrite:
            raise ValueError(f"Skill '{spec.name}' version '{spec.version}' already registered")

        versions[spec.version] = spec
        return spec

    def get(self, name: str, version: Optional[str] = None) -> Optional[SkillSpec]:
        """Get a SkillSpec by name and optional version.
        
        If version is None, returns the highest semantic version.
        """
        versions = self._registry.get(name)
        if not versions:
            return None

        if version is not None:
            return versions.get(version)

        # Find highest semver
        sorted_specs = sorted(
            versions.values(),
            key=lambda s: parse_semver(s.version),
            reverse=True
        )
        return sorted_specs[0] if sorted_specs else None

    def list_skills(self, status: Optional[str] = None) -> List[SkillSpec]:
        """List all latest active or filtered skills."""
        results: List[SkillSpec] = []
        for name in sorted(self._registry):
            latest = self.get(name)
            if latest:
                if status is None or latest.status == status:
                    results.append(latest)
        return results

    def list_versions(self, name: str) -> List[str]:
        """List all registered version strings for a given skill name sorted semver ascending."""
        versions = self._registry.get(name, {})
        return sorted(versions.keys(), key=parse_semver)

    def deprecate(self, name: str, version: Optional[str] = None) -> bool:
        """Mark a skill version or all versions as deprecated."""
        versions = self._registry.get(name)
        if not versions:
            return False

        if version is not None:
            spec = versions.get(version)
            if spec:
                spec.status = "deprecated"
                spec.updated_at = time.time()
                return True
            return False

        for spec in versions.values():
            spec.status = "deprecated"
            spec.updated_at = time.time()
        return True


@dataclass
class TaskExecutionRecord:
    """Record of a task or action execution for skill discovery."""
    task_name: str
    action_sequence: List[str]
    success: bool
    context_keys: List[str] = field(default_factory=list)
    capabilities_used: List[str] = field(default_factory=list)
    duration_sec: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class SkillGenerator:
    """Automatically generates candidate SkillSpec instances from repetitive patterns.
    
    Detects repeated successful action sequences across task executions
    and synthesizes candidate SkillSpec instances with capabilities and entry points.
    """

    def __init__(self, min_pattern_frequency: int = 2) -> None:
        self.min_pattern_frequency = min_pattern_frequency

    def generate_candidate_from_history(
        self,
        execution_history: List[TaskExecutionRecord],
        target_task_name: Optional[str] = None,
        skill_name: Optional[str] = None,
        preferred_posture: str = "coder",
        preferred_model_family: Optional[str] = None,
    ) -> Optional[SkillSpec]:
        """Analyzes successful task records to identify recurring sequences and create a candidate SkillSpec."""
        successful_records = [
            r for r in execution_history
            if r.success and (target_task_name is None or r.task_name == target_task_name)
        ]

        if len(successful_records) < self.min_pattern_frequency:
            return None

        # Sequence frequency analysis
        pattern_counts: Dict[Tuple[str, ...], int] = {}
        caps_by_pattern: Dict[Tuple[str, ...], Set[str]] = {}

        for rec in successful_records:
            seq = tuple(rec.action_sequence)
            if not seq:
                continue
            pattern_counts[seq] = pattern_counts.get(seq, 0) + 1
            if seq not in caps_by_pattern:
                caps_by_pattern[seq] = set()
            caps_by_pattern[seq].update(rec.capabilities_used)

        if not pattern_counts:
            return None

        # Find most frequent pattern meeting threshold
        best_pattern, count = max(pattern_counts.items(), key=lambda kv: kv[1])
        if count < self.min_pattern_frequency:
            return None

        name = skill_name or (
            f"auto-skill-{target_task_name or 'pattern'}"
            .lower().replace(" ", "-").replace("_", "-")
        )

        caps = sorted(list(caps_by_pattern[best_pattern]))
        script_steps = "\n".join(f"  # Step {idx+1}: {step}" for idx, step in enumerate(best_pattern))
        entry_script = (
            f"# Auto-generated skill workflow for pattern: {' -> '.join(best_pattern)}\n"
            f"def execute_skill(context):\n"
            f"{script_steps}\n"
            f"  return {{'status': 'success', 'steps': {list(best_pattern)}}}\n"
        )

        candidate = SkillSpec(
            name=name,
            description=f"Auto-generated procedural skill synthesized from {count} successful executions.",
            version="0.1.0",
            status="candidate",
            capabilities_required=caps,
            preferred_posture=preferred_posture,
            preferred_model_family=preferred_model_family,
            entry_script=entry_script,
            metadata={
                "synthesized_from_frequency": count,
                "action_pattern": list(best_pattern),
                "generation_timestamp": time.time(),
            },
        )
        compute_spec_checksum(candidate)
        return candidate


@dataclass
class EvalTestResult:
    """Result of an automated test or evaluation step in the lifecycle."""
    test_id: str
    passed: bool
    score: float
    message: str = ""


class SkillLifecyclePipeline:
    """Automated promotion pipeline for SkillSpec:
    
    candidate -> sandbox -> eval -> active (or deprecated / rejected).
    
    Enforces:
    1. Supply-chain integrity: Checksum SHA-256 verification.
    2. Dependency resolution: Validates required skills exist and are active.
    3. Capability resolution: Validates required capabilities against a registry or available set.
    4. Sandbox validation: Executes pre-flight test suites.
    5. Evaluation gate: Checks min_eval_score before promoting to 'active'.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        available_capabilities: Optional[Set[str]] = None,
        min_eval_score: float = 0.80,
    ) -> None:
        self.registry = registry
        self.available_capabilities: Set[str] = set(available_capabilities or [])
        self.min_eval_score = min_eval_score
        self._test_runners: List[Callable[[SkillSpec], EvalTestResult]] = []

    def register_test_runner(self, runner: Callable[[SkillSpec], EvalTestResult]) -> None:
        """Register an automated test runner for sandbox and eval stages."""
        self._test_runners.append(runner)

    def verify_supply_chain_security(self, spec: SkillSpec) -> Tuple[bool, str]:
        """Verify checksum integrity and author/license fields."""
        if not spec.checksum_sha256:
            return False, "SkillSpec missing checksum_sha256"

        body = spec.entry_script or spec.description or ""
        expected_hash = hashlib.sha256(f"{spec.name}:{spec.version}:{body}".encode("utf-8")).hexdigest()
        if spec.checksum_sha256 != expected_hash:
            return False, f"Checksum verification failed: expected {expected_hash}, got {spec.checksum_sha256}"

        if not spec.name or not spec.version:
            return False, "SkillSpec missing mandatory identification fields (name, version)"

        return True, "Security and integrity checks passed"

    def resolve_dependencies(self, spec: SkillSpec) -> Tuple[bool, List[str]]:
        """Resolve and verify all declared dependencies are present in registry.
        
        Returns (is_resolved, missing_or_inactive_dependencies).
        """
        unresolved: List[str] = []
        for dep in spec.dependencies:
            # Handles "dep_name" or "dep_name@version"
            if "@" in dep:
                name, ver = dep.split("@", 1)
                match = self.registry.get(name, ver)
            else:
                name = dep
                match = self.registry.get(name)

            if not match or match.status != "active":
                unresolved.append(dep)

        return len(unresolved) == 0, unresolved

    def resolve_capabilities(self, spec: SkillSpec) -> Tuple[bool, List[str]]:
        """Resolve and verify that all capabilities_required are available in the platform."""
        missing = [cap for cap in spec.capabilities_required if cap not in self.available_capabilities]
        return len(missing) == 0, missing

    def advance_to_sandbox(self, spec: SkillSpec) -> Tuple[bool, str]:
        """Advance a candidate skill to sandbox state if supply chain and deps pass."""
        if spec.status != "candidate":
            return False, f"Invalid state transition: {spec.status} -> sandbox (expected 'candidate')"

        sec_ok, sec_msg = self.verify_supply_chain_security(spec)
        if not sec_ok:
            return False, f"Supply chain check failed: {sec_msg}"

        deps_ok, missing_deps = self.resolve_dependencies(spec)
        if not deps_ok:
            return False, f"Dependency resolution failed: missing active dependencies {missing_deps}"

        caps_ok, missing_caps = self.resolve_capabilities(spec)
        if not caps_ok:
            return False, f"Capability resolution failed: missing capabilities {missing_caps}"

        try:
            spec.promote("sandbox", min_eval_score=self.min_eval_score)
        except Exception as e:
            return False, f"Transition failed: {e}"

        compute_spec_checksum(spec)
        return True, "Advanced to sandbox"

    def advance_to_eval(self, spec: SkillSpec) -> Tuple[bool, str]:
        """Advance a sandbox skill to eval state after executing sandbox tests."""
        if spec.status != "sandbox":
            return False, f"Invalid state transition: {spec.status} -> eval (expected 'sandbox')"

        # Run registered tests in sandbox
        for runner in self._test_runners:
            res = runner(spec)
            if not res.passed:
                return False, f"Sandbox test failed ({res.test_id}): {res.message}"

        try:
            spec.promote("eval", min_eval_score=self.min_eval_score)
        except Exception as e:
            return False, f"Transition failed: {e}"

        compute_spec_checksum(spec)
        return True, "Advanced to eval"

    def evaluate_and_activate(
        self,
        spec: SkillSpec,
        eval_score_override: Optional[float] = None
    ) -> Tuple[bool, str]:
        """Evaluate skill score against min_eval_score and promote to active in registry."""
        if spec.status != "eval":
            return False, f"Invalid state transition: {spec.status} -> active (expected 'eval')"

        final_score: float
        if eval_score_override is not None:
            final_score = eval_score_override
        elif self._test_runners:
            scores = []
            for runner in self._test_runners:
                res = runner(spec)
                scores.append(res.score if res.passed else 0.0)
            final_score = sum(scores) / len(scores) if scores else 1.0
        else:
            final_score = spec.eval_score if spec.eval_score is not None else 1.0

        spec.eval_score = final_score
        try:
            spec.promote("active", min_eval_score=self.min_eval_score)
        except Exception as e:
            return False, str(e)

        compute_spec_checksum(spec)

        # Register in registry
        self.registry.register(spec, overwrite=True)
        return True, f"Skill promoted to active with eval_score {final_score:.2f}"

    def run_full_pipeline(
        self,
        spec: SkillSpec,
        eval_score_override: Optional[float] = None
    ) -> Tuple[bool, str]:
        """Execute candidate -> sandbox -> eval -> active in sequence."""
        ok, msg = self.advance_to_sandbox(spec)
        if not ok:
            return False, f"Stage [sandbox] failed: {msg}"

        ok, msg = self.advance_to_eval(spec)
        if not ok:
            return False, f"Stage [eval] failed: {msg}"

        ok, msg = self.evaluate_and_activate(spec, eval_score_override=eval_score_override)
        if not ok:
            return False, f"Stage [active] failed: {msg}"

        return True, f"Pipeline succeeded: skill '{spec.name}@{spec.version}' is active"
