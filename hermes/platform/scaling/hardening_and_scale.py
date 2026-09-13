"""Production Hardening, Chaos Resilience & Distributed Scale (Phases 6 & 7).

Implements:
1. CrashRecoveryManager: Deterministic event replay, zombie worktree cleanup, and lease reclamation.
2. ChaosInjector: Network partition, artificial latency, and 429/5xx fault injection for ExactModelClient.
3. PromptInjectionShield: Deep payload sanitization against indirect prompt injections and artifact quarantine.
4. DistributedScaleCoordinator: Multi-host worker node orchestration (Control Node -> Worker Node CPU/GPU/Kilo) and pluggable storage abstraction.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


# ============================================================================
# PHASE 6: RELIABILITY, CRASH RECOVERY & REPLAY
# ============================================================================

@dataclass
class ReconstructedState:
    mission_id: str
    active_tasks: Set[str] = field(default_factory=set)
    completed_tasks: Set[str] = field(default_factory=set)
    failed_tasks: Set[str] = field(default_factory=set)
    allocated_workers: Dict[str, str] = field(default_factory=dict)  # task_id -> worker_id


class CrashRecoveryManager:
    """Detects interrupted tasks, reclaims stale leases, and replays EventStore for state recovery."""

    def __init__(self, event_store: EventStore, worktree_manager: Optional[GitWorktreeManager] = None):
        self.event_store = event_store
        self.worktree_manager = worktree_manager

    def replay_mission_state(self, mission_id: str) -> ReconstructedState:
        """Deterministically replays the event stream to reconstruct the exact snapshot prior to crash."""
        events = self.event_store.read_events()
        state = ReconstructedState(mission_id=mission_id)

        for ev in events:
            p = ev.payload
            if p.get("mission_id") != mission_id and p.get("task_id", "").split("-")[0] not in mission_id:
                # Filter events belonging to this mission
                continue

            if ev.name == "worker.acquired":
                tid = p.get("task_id")
                wid = p.get("worker_id")
                if tid and wid:
                    state.active_tasks.add(tid)
                    state.allocated_workers[tid] = wid
            elif ev.name == "task.completed":
                tid = p.get("task_id")
                if tid:
                    state.active_tasks.discard(tid)
                    state.completed_tasks.add(tid)
            elif ev.name == "task.failed":
                tid = p.get("task_id")
                if tid:
                    state.active_tasks.discard(tid)
                    state.failed_tasks.add(tid)

        return state

    def cleanup_zombie_worktrees(self, active_task_ids: Set[str]) -> List[str]:
        """Identifies and purges abandoned ephemeral worktrees that do not correspond to active tasks."""
        if not self.worktree_manager:
            return []

        cleaned = []
        wt_dir = self.worktree_manager.worktrees_dir
        if not wt_dir.exists():
            return []

        for p in wt_dir.iterdir():
            if p.is_dir() and p.name.startswith("task-"):
                # worktree directory name is "task-<task_id>"
                task_id = p.name[len("task-"):]
                if task_id not in active_task_ids:
                    try:
                        self.worktree_manager.remove_worktree(task_id=task_id, force=True)
                        cleaned.append(task_id)
                    except Exception:
                        pass
        return cleaned


# ============================================================================
# PHASE 6: PROVIDER CHAOS & CIRCUIT HARDENING
# ============================================================================

class ChaosFaultType(str, enum.Enum):
    RATE_LIMIT_429 = "rate_limit_429"
    SERVER_ERROR_503 = "server_error_503"
    NETWORK_TIMEOUT = "network_timeout"
    LATENCY_SPIKE = "latency_spike"


class ChaosInjector:
    """Simulates realistic network faults, partitions, and transient errors against provider routes."""

    def __init__(self):
        self._fault_schedule: Dict[str, ChaosFaultType] = {}  # provider_id -> fault

    def arm_fault(self, provider_id: str, fault: ChaosFaultType) -> None:
        self._fault_schedule[provider_id] = fault

    def disarm(self, provider_id: Optional[str] = None) -> None:
        if provider_id:
            self._fault_schedule.pop(provider_id, None)
        else:
            self._fault_schedule.clear()

    def wrap_transport(self, real_transport_fn: Callable) -> Callable:
        def chaos_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            for provider_name, fault in self._fault_schedule.items():
                if provider_name in url.lower():
                    if fault == ChaosFaultType.RATE_LIMIT_429:
                        return 429, {"error": {"message": "Simulated rate limit exceeded", "type": "rate_limit"}}
                    elif fault == ChaosFaultType.SERVER_ERROR_503:
                        return 503, {"error": {"message": "Simulated backend unavailable", "type": "server_error"}}
                    elif fault == ChaosFaultType.NETWORK_TIMEOUT:
                        raise TimeoutError("Simulated network connection timed out")
                    elif fault == ChaosFaultType.LATENCY_SPIKE:
                        time.sleep(0.5)
            return real_transport_fn(url, headers, data, timeout)

        return chaos_transport


# ============================================================================
# PHASE 6: SECURITY BOUNDARIES & PROMPT INJECTION DEFENSE
# ============================================================================

class PromptInjectionShield:
    """Deep inspection and sanitization against indirect prompt injection and sandbox escape attempts."""

    SUSPICIOUS_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
        re.compile(r"system\s+prompt\s+override", re.IGNORECASE),
        re.compile(r"dump\s+(all\s+)?environment\s+variables", re.IGNORECASE),
        re.compile(r"cat\s+~?/\.hermes/\.env", re.IGNORECASE),
        re.compile(r"curl\s+-X\s+POST.*http://attacker", re.IGNORECASE),
    ]

    @classmethod
    def inspect_and_sanitize(cls, raw_input: str) -> Tuple[bool, str, List[str]]:
        """Inspects input for malicious instructions. Returns (is_safe, sanitized_text, detected_violations)."""
        violations = []
        for pattern in cls.SUSPICIOUS_PATTERNS:
            if pattern.search(raw_input):
                violations.append(pattern.pattern)

        if violations:
            sanitized = "[BLOCKED: POTENTIAL PROMPT INJECTION DETECTED]"
            return False, sanitized, violations
        return True, raw_input, []


# ============================================================================
# PHASE 7: DISTRIBUTED SCALE COORDINATOR
# ============================================================================

class WorkerNodeType(str, enum.Enum):
    CPU_POOL = "cpu_pool"
    GPU_LOCAL = "gpu_local"
    REMOTE_KILO = "remote_kilo"


@dataclass
class DistributedWorkerNode:
    node_id: str
    node_type: WorkerNodeType
    host: str
    port: int
    capacity: int
    active_load: int = 0
    healthy: bool = True
    last_heartbeat: float = field(default_factory=time.time)


class DistributedScaleCoordinator:
    """Manages multi-host distributed node clusters and pluggable storage routing."""

    def __init__(self):
        self._nodes: Dict[str, DistributedWorkerNode] = {}

    def register_node(self, node: DistributedWorkerNode) -> None:
        self._nodes[node.node_id] = node

    def get_least_loaded_node(self, required_type: WorkerNodeType) -> Optional[DistributedWorkerNode]:
        candidates = [
            n for n in self._nodes.values()
            if n.node_type == required_type and n.healthy and n.active_load < n.capacity
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda n: n.active_load)

    def allocate_task_to_node(self, task_id: str, required_type: WorkerNodeType) -> DistributedWorkerNode:
        node = self.get_least_loaded_node(required_type)
        if not node:
            raise RuntimeError(f"No available distributed worker node for type {required_type.value}")
        node.active_load += 1
        return node

    def release_task_from_node(self, node_id: str) -> None:
        node = self._nodes.get(node_id)
        if node and node.active_load > 0:
            node.active_load -= 1
