"""Test suite validating the HAOS Architecture Freeze, Contracts, and Backlog DAG.

Validates:
1. All 21 tasks (TASK-0001 to TASK-0021) in MASTER-BACKLOG-M0-M20 are well-formed.
2. Backlog DAG has NO cycles and all depends_on references exist.
3. Architecture contracts in architecture/contracts/contracts.py serialize/deserialize.
4. All 12 ADRs exist and are non-empty.
"""

import unittest
from pathlib import Path
import yaml

from architecture.contracts.contracts import (
    TaskSpecContract,
    TaskRunContract,
    ArtifactSpecContract,
    ContextPackageContract,
    MemoryItemContract,
    SCHEMA_VERSION,
)


class TestArchitectureAndBacklogDAG(unittest.TestCase):
    """Verifies that the architecture freeze and backlog DAG are strictly sound."""

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent.parent
        self.backlog_path = self.root / "docs" / "architecture" / "MASTER-BACKLOG-M0-M20.yaml"

    def test_all_15_adrs_exist(self):
        adrs_dir = self.root / "architecture" / "ADRs"
        self.assertTrue(adrs_dir.exists())
        for i in range(1, 16):
            matches = list(adrs_dir.glob(f"ADR-{str(i).zfill(3)}*"))
            self.assertGreaterEqual(len(matches), 1, f"Missing ADR-{str(i).zfill(3)}")

    def test_core_patches_catalog_exists(self):
        core_patches_path = self.root / "architecture" / "core-patches.md"
        self.assertTrue(core_patches_path.exists())
        content = core_patches_path.read_text(encoding="utf-8")
        self.assertIn("Upstream Core Patch Catalog", content)

    def test_contracts_schema_version(self):
        task_contract = TaskSpecContract(id="task-test-01", title="Test", goal="Verify freeze")
        d = task_contract.to_dict()
        self.assertEqual(d["schema_version"], "1.0.0")
        self.assertEqual(d["id"], "task-test-01")

    def test_backlog_dag_integrity_and_acyclicity(self):
        self.assertTrue(self.backlog_path.exists())
        with open(self.backlog_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        tasks = data.get("tasks", [])
        self.assertEqual(len(tasks), 21, "Expected exactly 21 tasks for M0-M20")

        task_ids = {t["task_id"] for t in tasks}
        self.assertEqual(len(task_ids), 21, "Duplicate task_id found in backlog")

        # Verify all required keys are present in every task
        required_keys = {
            "task_id", "epic", "objective", "depends_on", "owner",
            "posture", "model_profile", "modules", "interfaces",
            "tests", "risk", "acceptance", "merge_after"
        }

        adj: dict[str, list[str]] = {}
        in_degree: dict[str, int] = {tid: 0 for tid in task_ids}

        for t in tasks:
            for k in required_keys:
                self.assertIn(k, t, f"Task {t['task_id']} is missing required key '{k}'")

            tid = t["task_id"]
            deps = t["depends_on"]
            adj[tid] = []

            for dep in deps:
                self.assertIn(dep, task_ids, f"Dependency {dep} of {tid} does not exist in backlog")
                in_degree[tid] += 1

        # Populate adjacency for topological sort (directed edge: dep -> tid)
        for t in tasks:
            for dep in t["depends_on"]:
                adj[dep].append(t["task_id"])

        # Kahn's algorithm for topological sorting to ensure DAG is acyclic
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        self.assertEqual(
            visited_count,
            len(task_ids),
            "Cycle detected in Backlog DAG! The dependency graph is not a valid DAG."
        )


if __name__ == "__main__":
    unittest.main()
