"""MCP Unified Fabric — Marco 4.

Dynamic MCP Fabric with Trust Tiers, MCP Packs, Circuit Breaker, and dynamic
tool resolution by (TaskSpec, Posture).

Strict stdlib-only; PEP-420 namespace compliant.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Union


class MCPTrustTier(str, enum.Enum):
    """Trust levels for MCP servers and packs."""
    CORE = "core"
    VERIFIED_PARTNER = "verified_partner"
    VERIFIED = "verified_partner"  # Canonical ADR alias
    COMMUNITY = "community"
    UNTRUSTED = "untrusted"

    @property
    def level(self) -> int:
        """Numeric rank for trust comparison: CORE > VERIFIED_PARTNER > COMMUNITY > UNTRUSTED."""
        _ranks = {
            MCPTrustTier.CORE: 4,
            MCPTrustTier.VERIFIED_PARTNER: 3,
            MCPTrustTier.COMMUNITY: 2,
            MCPTrustTier.UNTRUSTED: 1,
        }
        return _ranks[self]

    def is_at_least(self, other: MCPTrustTier) -> bool:
        return self.level >= other.level


class CircuitBreakerPolicy(str, enum.Enum):
    """Policy when a circuit breaker trips."""
    FAIL_CLOSED = "fail_closed"  # Block tool calls/access when tripped
    FAIL_OPEN = "fail_open"      # Allow fallback or passthrough when tripped


@dataclass
class MCPCircuitBreaker:
    """Circuit breaker for MCP servers and packs."""
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    policy: CircuitBreakerPolicy = CircuitBreakerPolicy.FAIL_CLOSED

    _failures: Dict[str, int] = field(default_factory=dict)
    _open_until: Dict[str, float] = field(default_factory=dict)

    def is_tripped(self, key: str) -> bool:
        """Returns True if the circuit breaker is currently open/tripped."""
        now = time.time()
        if key in self._open_until:
            if now < self._open_until[key]:
                return True
            # Cooldown expired: reset state
            del self._open_until[key]
            self._failures[key] = 0
        return False

    def record_failure(self, key: str) -> bool:
        """Record a failure for key. Returns True if tripped on this call."""
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.failure_threshold:
            self._open_until[key] = time.time() + self.cooldown_seconds
            return True
        return False

    def record_success(self, key: str) -> None:
        """Reset failures and open state upon success."""
        self._failures[key] = 0
        self._open_until.pop(key, None)

    def trip_now(self, key: str, cooldown: Optional[float] = None) -> None:
        """Manually trip the circuit breaker."""
        duration = cooldown if cooldown is not None else self.cooldown_seconds
        self._failures[key] = self.failure_threshold
        self._open_until[key] = time.time() + duration

    def reset(self, key: Optional[str] = None) -> None:
        """Reset one key or all keys."""
        if key is not None:
            self._failures.pop(key, None)
            self._open_until.pop(key, None)
        else:
            self._failures.clear()
            self._open_until.clear()

    def allow_execution(self, key: str) -> bool:
        """Check if execution is allowed under current state and policy."""
        tripped = self.is_tripped(key)
        if not tripped:
            return True
        # If tripped, policy decides: FAIL_OPEN allows, FAIL_CLOSED blocks
        return self.policy == CircuitBreakerPolicy.FAIL_OPEN


@dataclass
class MCPPack:
    """Group of MCP servers under a common trust boundary and posture scope."""
    pack_id: str
    name: str
    servers: List[str] = field(default_factory=list)
    trust_tier: MCPTrustTier = MCPTrustTier.COMMUNITY
    allowed_postures: List[str] = field(default_factory=list)
    circuit_breaker_state: Optional[str] = "closed"  # closed, open, half_open
    metadata: Dict[str, Any] = field(default_factory=dict)

    def permits_posture(self, posture: str) -> bool:
        """Check if this pack is allowed under the given posture.
        If allowed_postures is empty, it permits all postures (open).
        """
        if not self.allowed_postures or "*" in self.allowed_postures:
            return True
        return posture in self.allowed_postures


DEFAULT_MCP_PACKS: Dict[str, MCPPack] = {
    "core": MCPPack(
        pack_id="core",
        name="Core MCP Pack",
        servers=["core", "core_fs", "core_git"],
        trust_tier=MCPTrustTier.CORE,
        allowed_postures=["*"],
    ),
    "dev_tools": MCPPack(
        pack_id="dev_tools",
        name="Developer Tools Pack",
        servers=["git", "terminal", "compiler", "linter", "dev_tools", "lsp"],
        trust_tier=MCPTrustTier.VERIFIED_PARTNER,
        allowed_postures=["coder", "implementer", "debugger"],
    ),
    "review_tools": MCPPack(
        pack_id="review_tools",
        name="Reviewer Tools Pack",
        servers=["git_read", "test_runner", "diff_inspector", "linter", "review_tools"],
        trust_tier=MCPTrustTier.VERIFIED_PARTNER,
        allowed_postures=["reviewer", "auditor"],
    ),
    "arch_tools": MCPPack(
        pack_id="arch_tools",
        name="Architecture Tools Pack",
        servers=["graphrag", "obsidian", "diagram", "adr", "arch_tools"],
        trust_tier=MCPTrustTier.VERIFIED_PARTNER,
        allowed_postures=["architect", "lead"],
    ),
}


class MCPToolFilter:
    """Dynamic tool resolution by (TaskSpec, Posture).

    Ensures agents are NEVER exposed to hundreds of raw tools by scoping
    MCP packs and tools based on trust boundaries, posture permissions,
    and active circuit breakers.
    """

    def __init__(
        self,
        packs: Optional[Dict[str, MCPPack]] = None,
        circuit_breaker: Optional[MCPCircuitBreaker] = None,
        minimum_trust_tier: MCPTrustTier = MCPTrustTier.COMMUNITY,
        server_tools_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ):
        self.packs: Dict[str, MCPPack] = dict(packs or {})
        self.circuit_breaker = circuit_breaker or MCPCircuitBreaker()
        self.minimum_trust_tier = minimum_trust_tier
        # server_name -> list of tool definitions/schemas
        self.server_tools_map: Dict[str, List[Dict[str, Any]]] = dict(server_tools_map or {})

    def register_pack(self, pack: MCPPack) -> None:
        self.packs[pack.pack_id] = pack

    def register_server_tools(self, server_name: str, tools: List[Dict[str, Any]]) -> None:
        self.server_tools_map[server_name] = list(tools)

    def resolve_packs(
        self,
        task: Optional[Any] = None,
        posture: Optional[Union[str, Any]] = None,
        min_trust: Optional[MCPTrustTier] = None,
        required_packs: Optional[Iterable[str]] = None,
    ) -> List[MCPPack]:
        """Resolve eligible MCP packs for given TaskSpec and Posture."""
        effective_trust = min_trust or self.minimum_trust_tier

        # Extract posture string
        posture_id = "default"
        if posture is not None:
            posture_id = getattr(posture, "id", str(posture))
        elif task is not None:
            posture_id = getattr(task, "posture", "default")

        # Explicit packs from argument or task
        requested_pack_ids: Optional[Set[str]] = None
        if required_packs is not None:
            requested_pack_ids = set(required_packs)
        elif task is not None:
            # Check for mcp_packs attribute or tags or dict key
            packs_attr = None
            if isinstance(task, dict):
                packs_attr = task.get("mcp_packs")
            else:
                packs_attr = getattr(task, "mcp_packs", None)
            if packs_attr:
                requested_pack_ids = set(packs_attr)

        resolved: List[MCPPack] = []
        for pack_id, pack in self.packs.items():
            # Filter by explicit request if task or argument requested specific packs
            if requested_pack_ids is not None and pack_id not in requested_pack_ids:
                continue

            # Filter by trust tier
            if not pack.trust_tier.is_at_least(effective_trust):
                continue

            # Filter by posture
            if not pack.permits_posture(posture_id):
                continue

            # Check circuit breaker for pack
            if not self.circuit_breaker.allow_execution(f"pack:{pack.pack_id}"):
                continue

            resolved.append(pack)

        return resolved

    def resolve_allowed_servers(
        self,
        task: Optional[Any] = None,
        posture: Optional[Union[str, Any]] = None,
        min_trust: Optional[MCPTrustTier] = None,
        required_packs: Optional[Iterable[str]] = None,
    ) -> Set[str]:
        """Resolve valid server names across all matching packs, excluding broken servers."""
        packs = self.resolve_packs(
            task=task,
            posture=posture,
            min_trust=min_trust,
            required_packs=required_packs,
        )
        servers: Set[str] = set()

        for pack in packs:
            for server in pack.servers:
                if self.circuit_breaker.allow_execution(f"server:{server}"):
                    servers.add(server)

        return servers

    def filter_tools(
        self,
        task: Optional[Any] = None,
        posture: Optional[Union[str, Any]] = None,
        raw_tools: Optional[List[Dict[str, Any]]] = None,
        min_trust: Optional[MCPTrustTier] = None,
        max_tools: Optional[int] = None,
        required_packs: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return scoped list of tool schemas allowed for the task & posture.

        If raw_tools is passed, tools are filtered by server attribution (e.g. `_server` metadata
        or `server_name` prefix/field). Otherwise, registered tools from `server_tools_map` are retrieved.
        """
        allowed_servers = self.resolve_allowed_servers(
            task=task,
            posture=posture,
            min_trust=min_trust,
            required_packs=required_packs,
        )

        selected: List[Dict[str, Any]] = []

        if raw_tools is not None:
            for tool in raw_tools:
                # Check tool server attribution
                server = tool.get("_server") or tool.get("server") or tool.get("server_name")
                if server is None:
                    # Check prefix convention server__tool_name
                    name = tool.get("name", "")
                    if "__" in name:
                        server = name.split("__", 1)[0]

                # If server is attributed, check if allowed
                if server is not None:
                    if server in allowed_servers:
                        selected.append(tool)
                else:
                    # Unattributed tool: only allow if explicitly accepted or CORE
                    pass
        else:
            for server in allowed_servers:
                tools = self.server_tools_map.get(server, [])
                selected.extend(tools)

        # Apply posture-based tool preferences if task / posture specifies them
        posture_obj = posture
        preferred_tools: List[str] = []
        if hasattr(posture_obj, "skills_preferred"):
            preferred_tools = getattr(posture_obj, "skills_preferred")

        if preferred_tools:
            # Sort with preferred tools prioritized
            def _sort_key(t: Dict[str, Any]) -> int:
                t_name = t.get("name", "")
                for pref in preferred_tools:
                    if pref in t_name:
                        return 0
                return 1
            selected.sort(key=_sort_key)

        if max_tools is not None and len(selected) > max_tools:
            selected = selected[:max_tools]

        return selected

    @classmethod
    def filter_tools_for_task(
        cls,
        tool_schemas: List[Dict[str, Any]],
        task: Optional[Any] = None,
        posture: Optional[Union[str, Any]] = None,
        required_packs: Optional[Iterable[str]] = None,
        circuit_breaker: Optional[MCPCircuitBreaker] = None,
        packs: Optional[Dict[str, MCPPack]] = None,
        min_trust: Optional[MCPTrustTier] = None,
        max_tools: Optional[int] = 50,
    ) -> List[Dict[str, Any]]:
        """Static/class helper to filter tools dynamically for a task and posture.

        Ensures the agent NEVER receives unbounded hundreds of raw tools.
        Respects circuit breaker status so failing MCP servers do not crash the agent turn.
        """
        packs_dict = dict(packs) if packs is not None else dict(DEFAULT_MCP_PACKS)
        tier = min_trust if min_trust is not None else MCPTrustTier.COMMUNITY
        tool_filter = cls(
            packs=packs_dict,
            circuit_breaker=circuit_breaker,
            minimum_trust_tier=tier,
        )
        return tool_filter.filter_tools(
            task=task,
            posture=posture,
            raw_tools=tool_schemas,
            min_trust=min_trust,
            max_tools=max_tools,
            required_packs=required_packs,
        )
