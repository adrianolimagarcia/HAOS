"""Counters and gauges for the Memory Fabric.

The fabric is a distributed-ish pipeline (one journal, four durable projections,
two retrieval channels), so "is it healthy?" cannot be answered from logs alone.
This module is the single place those numbers are produced, and it is deliberately
dependency-free: counters are in-process and the caller decides where to publish
them.

What it measures, and why each one matters:

* ``outbox_backlog`` / ``outbox_retries`` / ``expired_leases`` — a projection that
  stops draining is silent data loss for that sink until someone looks.
* ``projection_seconds`` — per-projection latency, the first thing to blow up when
  Obsidian or GraphRAG gets slow.
* ``dedupe_hits`` / ``dedupe_misses`` — the dedupe rate is the fabric's main
  defence against unbounded growth.
* ``channel_hits`` — how much each retrieval channel actually contributes; a
  channel that never hits is dead weight in the prompt budget.
* ``dropped_by_budget`` / ``dropped_by_acl`` — recall that was *deliberately*
  withheld. Without this, a scope bug and an empty store look identical.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class _Timing:
    count: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0

    def observe(self, seconds: float) -> None:
        self.count += 1
        self.total_seconds += seconds
        self.max_seconds = max(self.max_seconds, seconds)

    def as_dict(self) -> Dict[str, float]:
        return {
            "count": self.count,
            "total_seconds": round(self.total_seconds, 6),
            "mean_seconds": round(self.total_seconds / self.count, 6) if self.count else 0.0,
            "max_seconds": round(self.max_seconds, 6),
        }


class MemoryFabricMetrics:
    """Thread-safe metric registry for one fabric instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._timings: Dict[str, _Timing] = {}
        self.started_at = time.time()

    # -- counters ------------------------------------------------------------

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    # -- timings -------------------------------------------------------------

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            timing = self._timings.get(name)
            if timing is None:
                timing = self._timings[name] = _Timing()
            timing.observe(seconds)

    def timing(self, name: str) -> _Timing:
        with self._lock:
            return self._timings.get(name, _Timing())

    # -- derived -------------------------------------------------------------

    def dedupe_rate(self) -> float:
        hits = self.counter("dedupe_hits")
        misses = self.counter("dedupe_misses")
        total = hits + misses
        return round(hits / total, 6) if total else 0.0

    def channel_hits(self) -> Dict[str, int]:
        with self._lock:
            return {
                name[len("channel_hits.") :]: value
                for name, value in self._counters.items()
                if name.startswith("channel_hits.")
            }

    def snapshot(self, *, backlog: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            timings = {name: timing.as_dict() for name, timing in self._timings.items()}
        return {
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "counters": counters,
            "timings": timings,
            "dedupe_rate": self.dedupe_rate(),
            "channel_hits": self.channel_hits(),
            "outbox_backlog": dict(backlog or {}),
        }


class Timer:
    """Context manager that records one observation into ``metrics``."""

    def __init__(self, metrics: MemoryFabricMetrics, name: str) -> None:
        self.metrics = metrics
        self.name = name
        self.started = 0.0

    def __enter__(self) -> "Timer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.metrics.observe(self.name, time.perf_counter() - self.started)
