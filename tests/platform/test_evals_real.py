"""A6 — suites de evals REAIS do platform (invariantes sobre a árvore real).

Contratos:
(a) structure: compileall rc 0; zero __init__.py (PEP-420); nenhum import de
    topo fora de stdlib ∪ {hermes, hermes_cli}.
(b) seams: módulos canônicos importam em python puro (subprocesso).
(c) model_exact_route: rota escolhida preserva a identity exata do modelo para
    TODOS os perfis declarados no config real.
(d) metrics/BaselineStore: collect_fork_metrics devolve o dicionário estável e
    save→latest devolve o mesmo JSON (round-trip real do store).
"""

import unittest
from pathlib import Path

from hermes.platform.evals.suites_platform import (
    platform_root, resolve_repo_root,
    op_compileall, op_pep420, op_stdlib_lint, op_seams_import,
    op_model_exact_route, collect_fork_metrics, save_fork_metrics,
    build_platform_suites, run_all_real,
)
from hermes.platform.evals.baselines import BaselineStore


class TestRealSuites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]  # HERMES-TURBO
        cls.seams = ["hermes.platform.execution.dispatcher",
                     "hermes.platform.execution.classify",
                     "hermes.platform.capabilities.modality.workers_cli",
                     "hermes.platform.memory.context_engine.engine",
                     "hermes.platform.assembly"]

    def test_platform_root_resolves_under_repo(self):
        self.assertTrue((platform_root(self.root) / "COMPLIANCE.md").exists()
                        or platform_root(self.root).is_dir())

    def test_compileall_passes(self):
        out = op_compileall(self.root)
        self.assertTrue(out["passed"], out["meta"])

    def test_pep420_no_init_files(self):
        out = op_pep420(self.root)
        self.assertTrue(out["passed"], out["meta"])

    def test_module_level_imports_stdlib_only(self):
        out = op_stdlib_lint(self.root)
        self.assertTrue(out["passed"], out["meta"])

    def test_seams_importable(self):
        out = op_seams_import(self.root, self.seams)
        self.assertTrue(out["passed"], out["meta"])
        self.assertEqual(len(out["meta"]["seams"]), len(self.seams))

    def test_exact_model_route_preserves_identity(self):
        out = op_model_exact_route(self.root)
        self.assertTrue(out["passed"], out["meta"])
        self.assertGreaterEqual(out["meta"]["profiles"], 1)

    def test_suites_built_with_cases(self):
        suites = build_platform_suites(self.root)
        self.assertEqual([s.id for s in suites],
                         ["platform.fork.structure",
                          "platform.fork.seams",
                          "platform.fork.model_exact_route"])
        self.assertEqual(len(suites[0].cases), 3)

    def test_run_all_real_passes(self):
        report = run_all_real(self.root, label="haos-fork")
        self.assertEqual(report["label"], "haos-fork")
        self.assertEqual(len(report["suites"]), 3)
        for suite in report["suites"]:
            self.assertEqual(suite["pass_rate"], 1.0, suite)

    def test_metrics_baseline_round_trip(self):
        store = BaselineStore()
        metrics = collect_fork_metrics(self.root)
        row_id = save_fork_metrics(store, self.root, label="haos-fork")
        self.assertIsInstance(row_id, int)
        latest = store.latest("platform.fork.metrics", label="haos-fork")
        self.assertIsNotNone(latest)
        got = latest["metrics"]
        self.assertEqual(got["module_py_files"], metrics["module_py_files"])
        self.assertEqual(got["pep420"], metrics["pep420"])
        self.assertEqual(got["stdlib_lint"], metrics["stdlib_lint"])


if __name__ == "__main__":
    unittest.main()
