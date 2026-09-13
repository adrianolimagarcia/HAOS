import unittest
from hermes.platform.benchmarks.benchmark_engine import HAOSBenchmarkSuite


class TestHAOSBenchmarkSuite(unittest.TestCase):
    def test_benchmark_suite_execution(self):
        suite = HAOSBenchmarkSuite(iterations=50)
        report = suite.run_all()

        self.assertIn("sqlite_wal", report)
        self.assertIn("step_lifecycle_guard", report)
        self.assertGreater(report["total_duration_seconds"], 0.0)
        self.assertLess(report["total_duration_seconds"], 2.0)

        sqlite = report["sqlite_wal"]
        self.assertIn("p50_ms", sqlite)
        self.assertIn("p95_ms", sqlite)
        self.assertGreater(sqlite["ops_per_sec"], 10.0)

        guard = report["step_lifecycle_guard"]
        self.assertIn("p50_ms", guard)
        self.assertLess(guard["p50_ms"], 0.5)  # Overhead do guard deve ser sub-milissegundo


if __name__ == "__main__":
    unittest.main()
