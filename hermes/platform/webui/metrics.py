"""Bounded, dependency-free metrics for the standalone control plane."""
from __future__ import annotations

import os
import threading
from collections import defaultdict, deque
from typing import Any, Dict, Optional


class RouteMetrics:
    """Thread-safe route counters and bounded latency samples."""

    def __init__(self, max_samples: int = 256) -> None:
        self._lock = threading.Lock()
        self._max_samples = max(1, int(max_samples))
        self._counts: Dict[str, int] = defaultdict(int)
        self._latencies: Dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._max_samples)
        )
        self._upstreams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def observe(self, route: str, duration_ms: float, upstream: Optional[str] = None) -> None:
        route = route or "/"
        upstream = upstream if upstream in {"edge", "python", "gateway", "fallback"} else "unknown"
        with self._lock:
            self._counts[route] += 1
            self._latencies[route].append(max(0.0, float(duration_ms)))
            self._upstreams[route][upstream] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            routes: Dict[str, Any] = {}
            for route in sorted(self._counts):
                values = sorted(self._latencies[route])
                # nearest-rank p95; deterministic and meaningful for small samples.
                rank = max(1, int((0.95 * len(values) + 0.999999999)))
                routes[route] = {
                    "count": self._counts[route],
                    "latency_ms": {"p95": values[rank - 1] if values else None},
                    "upstream": dict(sorted(self._upstreams[route].items())),
                }
            return {"routes": routes, "sample_limit": self._max_samples}


def _proc_memory(pid: int) -> Dict[str, Any]:
    """Read RSS/PSS from procfs; unavailable fields are omitted, never fatal."""
    out: Dict[str, Any] = {"pid": pid}
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                if key in {"Name", "VmRSS", "PPid"}:
                    out[{"Name": "name", "VmRSS": "rss_kb", "PPid": "ppid"}[key]] = value.strip()
        out["rss_kb"] = int(str(out.get("rss_kb", "0 kb")).split()[0])
        out["ppid"] = int(out.get("ppid", 0))
    except (OSError, ValueError):
        return {"pid": pid, "unavailable": True}
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    out["pss_kb"] = int(line.split()[1])
                    break
    except (OSError, ValueError):
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            out["cmdline"] = fh.read(4096).replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    return out


def process_memory_status(root_pid: Optional[int] = None) -> Dict[str, Any]:
    """Return this process and live descendants, without spawning commands."""
    root_pid = int(root_pid or os.getpid())
    pids = [root_pid]
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit() and int(entry) != root_pid:
                item = _proc_memory(int(entry))
                if item.get("ppid") in pids:
                    pids.append(int(entry))
    except OSError:
        pass
    processes = [_proc_memory(pid) for pid in pids]
    return {"pid": root_pid, "processes": processes, "procfs": True}
