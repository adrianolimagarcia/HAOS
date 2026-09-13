"""HAOS Core & Memory Latency Benchmarking Engine.

Measures p50, p95 latencies and throughput for SQLite, RAGFlow, and StepLifecycleGuard.
"""

from __future__ import annotations

import time
import tempfile
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
from hermes.platform.execution.step_lifecycle_guard import StepLifecycleGuard, StepContext


class HAOSBenchmarkSuite:
    def __init__(self, iterations: int = 200):
        self.iterations = iterations

    @staticmethod
    def _percentile(values: List[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        d = k - f
        return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * d

    def benchmark_sqlite_wal(self) -> Dict[str, float]:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            conn = sqlite3.connect(tmp.name)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT);")
            conn.commit()

            latencies = []
            for i in range(self.iterations):
                t0 = time.perf_counter()
                conn.execute("INSERT INTO test (val) VALUES (?)", (f"data_{i}",))
                conn.commit()
                latencies.append((time.perf_counter() - t0) * 1000.0)

            conn.close()

            return {
                "iterations": self.iterations,
                "p50_ms": round(self._percentile(latencies, 0.50), 3),
                "p95_ms": round(self._percentile(latencies, 0.95), 3),
                "ops_per_sec": round(self.iterations / (sum(latencies) / 1000.0), 1),
            }

    def benchmark_step_lifecycle_guard(self) -> Dict[str, float]:
        ctx = StepContext(tool_name="terminal", tool_args={"command": "ls"}, step_index=1, total_steps=2)
        res_ok = {"exit_code": 0, "output": "file1\nfile2"}

        latencies = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            StepLifecycleGuard.before_step(ctx)
            StepLifecycleGuard.after_step(ctx, res_ok)
            latencies.append((time.perf_counter() - t0) * 1000.0)

        return {
            "iterations": self.iterations,
            "p50_ms": round(self._percentile(latencies, 0.50), 4),
            "p95_ms": round(self._percentile(latencies, 0.95), 4),
            "ops_per_sec": round(self.iterations / (sum(latencies) / 1000.0), 1),
        }

    def run_all(self) -> Dict[str, Any]:
        t_start = time.perf_counter()
        sqlite_stats = self.benchmark_sqlite_wal()
        guard_stats = self.benchmark_step_lifecycle_guard()
        total_time_s = round(time.perf_counter() - t_start, 3)

        return {
            "timestamp": time.time(),
            "total_duration_seconds": total_time_s,
            "sqlite_wal": sqlite_stats,
            "step_lifecycle_guard": guard_stats,
        }
