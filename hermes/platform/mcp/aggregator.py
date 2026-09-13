"""HAOS Local MCP Aggregator & Gateway.

Federates multiple independent MCP servers under a unified catalog with automatic namespacing
(<server_name>_<tool_name>), timeout isolation, circuit breaking, and call routing.
Operates 100% locally and offline without Kubernetes or external containers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger("haos.mcp.aggregator")

# Aligned with the native Hermes MCP tool-call default (300s): when the federated call is
# delegated to the kernel, a per-server timeout below the kernel's would strangle legitimate
# slow servers. Explicit per-call timeouts still override it.
DEFAULT_CALL_TIMEOUT = 300.0
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SEC = 60.0

# Toolset prefix the Hermes kernel registers configured MCP servers under
# (``tools.mcp_tool_registration``: ``mcp-{server}``).
_KERNEL_TOOLSET_PREFIX = "mcp-"


def _kernel_result_to_mcp_shape(raw: Any) -> Dict[str, Any]:
    """Normalise a ``registry.dispatch`` result (JSON string or multimodal dict) into the
    gateway's MCP-style ``{"content": [...]}`` envelope. Errors surface as ``isError``."""
    if isinstance(raw, dict):
        if raw.get("_multimodal") is True and isinstance(raw.get("content"), list):
            return raw
        payload = raw
    else:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            payload = {"result": str(raw)}
    if not isinstance(payload, dict):
        payload = {"result": str(payload)}
    error = payload.get("error")
    if isinstance(error, str) and error:
        return {"isError": True, "content": [{"type": "text", "text": error}]}
    text = payload.get("result")
    if text is None and payload.get("structuredContent") is not None:
        text = json.dumps(payload["structuredContent"], ensure_ascii=False, default=str)
    if text is None and payload.get("_meta") is not None:
        text = json.dumps(payload["_meta"], ensure_ascii=False, default=str)
    if text is None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": str(text)}]}


class MCPGatewayError(RuntimeError):
    pass


class TargetServerState:
    """Tracks runtime state, circuit breaker and health for a downstream MCP server."""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.consecutive_failures = 0
        self.breaker_opened_at: Optional[float] = None
        self.is_healthy = True
        self.last_latency_ms: float = 0.0

    def record_success(self, latency_ms: float) -> None:
        self.consecutive_failures = 0
        self.breaker_opened_at = None
        self.is_healthy = True
        self.last_latency_ms = latency_ms

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self.breaker_opened_at = time.monotonic()
            self.is_healthy = False

    def is_breaker_open(self) -> bool:
        if self.breaker_opened_at is None:
            return False
        age = time.monotonic() - self.breaker_opened_at
        if age >= CIRCUIT_BREAKER_COOLDOWN_SEC:
            # Half-open: allow probe
            return False
        return True


class LocalMCPAggregator:
    """Local, offline aggregator that federates multiple MCP servers into a single interface."""

    def __init__(self, servers_config: Optional[Dict[str, Dict[str, Any]]] = None):
        self.servers_config = servers_config or {}
        self._states: Dict[str, TargetServerState] = {
            name: TargetServerState(name, cfg)
            for name, cfg in self.servers_config.items()
        }
        # namespace -> (server_name, original_tool_name, tool_schema)
        self._tool_catalog: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
        # server_name -> dispatcher callback for calling tool
        self._dispatchers: Dict[
            str,
            Callable[[str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
        ] = {}
        # Kernel-mirror bookkeeping: one-shot hydration of the Hermes kernel's configured
        # MCP servers (config.yaml ``mcp_servers``) into the federated catalog.
        self._kernel_synced = False
        self._kernel_delegated: set = set()

    def register_dispatcher(
        self,
        server_name: str,
        dispatcher: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> None:
        """Register an async call handler for a specific server name."""
        self._dispatchers[server_name] = dispatcher
        if server_name not in self._states:
            self._states[server_name] = TargetServerState(server_name, {})

    def register_server_tools(
        self, server_name: str, tools: List[Dict[str, Any]]
    ) -> List[str]:
        """Ingests tools from a downstream server and registers them with namespacing.
        
        Tool names are transformed to `<server_name>_<original_name>` to eliminate collisions.
        """
        registered_names: List[str] = []
        for tool in tools:
            orig_name = tool.get("name", "")
            if not orig_name:
                continue

            namespaced_name = f"{server_name}_{orig_name}"
            # Clone and namespace description and schema title
            schema = dict(tool)
            schema["name"] = namespaced_name
            orig_desc = schema.get("description", "")
            schema["description"] = f"[{server_name}] {orig_desc}".strip()

            self._tool_catalog[namespaced_name] = (server_name, orig_name, schema)
            registered_names.append(namespaced_name)
        return registered_names

    # ------------------------------------------------------------------ kernel mirroring

    def _ensure_kernel_sync(self) -> None:
        """One-shot: mirror the Hermes kernel's configured MCP servers into this gateway.

        The kernel already owns discovery/connection/registration for ``config.yaml
        ``mcp_servers`` (CLI, dashboard, cron and gateway all call ``discover_mcp_tools()``
        at startup), so we do not connect or spawn anything here: we mirror the tools the
        kernel has registered under toolset ``mcp-<server>`` and delegate every federated
        call back to the kernel registry (``registry.dispatch``) so the kernel's own lazy
        connect, circuit breaker, auth refresh and stdio respawn all still apply. Never
        raises — a kernel that is unavailable or empty leaves the gateway as-is.
        """
        if self._kernel_synced:
            return
        self._kernel_synced = True
        try:
            from tools.mcp_tool_config import _load_mcp_config
            from tools.registry import registry
        except Exception:
            logger.debug("Kernel MCP mirroring unavailable", exc_info=True)
            return
        try:
            configured = _load_mcp_config() or {}
        except Exception:
            configured = {}
        for server_name, cfg in configured.items():
            if not self._cfg_enabled(cfg):
                continue
            if server_name in self._dispatchers:
                # Explicitly registered (tests/programmatic callers) wins over the mirror.
                continue
            # Advertise the configured server even before the kernel registers its tools.
            self._states.setdefault(server_name, TargetServerState(server_name, cfg))
            try:
                native_names = registry.get_tool_names_for_toolset(
                    f"{_KERNEL_TOOLSET_PREFIX}{server_name}") or []
            except Exception:
                native_names = []
            tools = []
            for native_name in native_names:
                tool_name = self._kernel_tool_name(server_name, native_name)
                if tool_name is None:
                    continue
                tools.append({"native": native_name, "tool": tool_name})
            if not tools:
                continue
            if server_name not in self._dispatchers:
                self.register_dispatcher(server_name, self._kernel_dispatcher(server_name))
            self._ingest_kernel_tools(server_name, tools)

    @staticmethod
    def _cfg_enabled(cfg: Dict[str, Any]) -> bool:
        """Honour the kernel's ``enabled: false`` toggle (default: enabled)."""
        value = cfg.get("enabled", True)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("false", "0", "no", "off")

    @staticmethod
    def _kernel_tool_name(server_name: str, native_name: str) -> Optional[str]:
        """Strip the kernel's ``mcp__<server>__<tool>`` prefix down to the bare tool name."""
        try:
            from tools.mcp_tool_schema import sanitize_mcp_name_component
        except Exception:
            return None
        prefix = f"mcp__{sanitize_mcp_name_component(server_name)}__"
        if not native_name.startswith(prefix):
            return None
        tool = native_name[len(prefix):]
        return tool or None

    def _ingest_kernel_tools(self, server_name: str, tools: List[Dict[str, Any]]) -> None:
        """Federate the kernel-registered tools into the HAOS namespace ``<server>_<tool>``."""
        from tools.registry import registry
        for item in tools:
            native_name = item["native"]
            tool = item["tool"]
            namespaced = f"{server_name}_{tool}"
            if namespaced in self._tool_catalog:
                continue
            try:
                native_schema = registry.get_schema(native_name) or {}
            except Exception:
                native_schema = {}
            description = native_schema.get("description") or f"MCP tool {tool} from {server_name}"
            parameters = native_schema.get("parameters") or {
                "type": "object", "properties": {}}
            self._tool_catalog[namespaced] = (
                server_name, tool,
                {"name": namespaced, "description": f"[{server_name}] {description}".strip(),
                 "parameters": parameters})

    def _kernel_dispatcher(self, server_name: str):
        """Async dispatcher delegating a federated call to the kernel registry by native name."""
        from tools.mcp_tool_schema import mcp_prefixed_tool_name

        async def _dispatcher(orig_tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
            from tools.registry import registry
            native_name = mcp_prefixed_tool_name(server_name, orig_tool_name)
            loop = asyncio.get_running_loop()

            def _sync_call():
                return registry.dispatch(native_name, arguments)

            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _sync_call), timeout=DEFAULT_CALL_TIMEOUT)
            except Exception as exc:
                return {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Kernel dispatch for '{server_name}.{orig_tool_name}' "
                                f"failed: {exc}"
                            ),
                        }
                    ],
                }
            return _kernel_result_to_mcp_shape(raw)

        return _dispatcher

    def list_tools(self) -> List[Dict[str, Any]]:
        """Returns the complete federated tool catalog across all active downstream servers."""
        self._ensure_kernel_sync()
        return [item[2] for item in self._tool_catalog.values()]

    def get_status(self) -> Dict[str, Any]:
        """Provides status, health, and latency statistics for all registered MCP servers."""
        self._ensure_kernel_sync()
        servers_status = {}
        for name, state in self._states.items():
            servers_status[name] = {
                "healthy": state.is_healthy,
                "circuit_breaker_open": state.is_breaker_open(),
                "failures": state.consecutive_failures,
                "latency_ms": round(state.last_latency_ms, 2),
                "tools_count": sum(
                    1 for s_name, _, _ in self._tool_catalog.values() if s_name == name
                ),
            }
        return {
            "total_servers": len(self._states),
            "total_tools": len(self._tool_catalog),
            "servers": servers_status,
        }

    async def call_tool(
        self, namespaced_tool_name: str, arguments: Dict[str, Any], timeout: float = DEFAULT_CALL_TIMEOUT
    ) -> Dict[str, Any]:
        """Routes a namespaced tool call to the corresponding downstream MCP server."""
        self._ensure_kernel_sync()
        if namespaced_tool_name not in self._tool_catalog:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Tool '{namespaced_tool_name}' not found in HAOS federated MCP gateway.",
                    }
                ],
            }

        server_name, orig_tool_name, _ = self._tool_catalog[namespaced_tool_name]
        state = self._states.setdefault(server_name, TargetServerState(server_name, {}))

        if state.is_breaker_open():
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Circuit breaker open for server '{server_name}' ({state.consecutive_failures} failures). Cooldown active.",
                    }
                ],
            }

        dispatcher = self._dispatchers.get(server_name)
        if not dispatcher:
            # A kernel server may have registered its tools after the one-shot sync (slow
            # lazy connect). Re-mirror once; programmatic registrations are never touched.
            self._kernel_synced = False
            self._ensure_kernel_sync()
            dispatcher = self._dispatchers.get(server_name)
        if not dispatcher:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No dispatcher registered for server '{server_name}'. Register one with "
                            "register_dispatcher() or configure the server under 'mcp_servers' "
                            "in config.yaml."
                        ),
                    }
                ],
            }

        # Execute registered async dispatcher
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                dispatcher(orig_tool_name, arguments), timeout=timeout
            )
            latency = (time.monotonic() - t0) * 1000
            state.record_success(latency)
            return result
        except asyncio.TimeoutError:
            state.record_failure()
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Call to '{namespaced_tool_name}' on server '{server_name}' timed out after {timeout}s.",
                    }
                ],
            }
        except Exception as exc:
            state.record_failure()
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Execution error on server '{server_name}': {exc}",
                    }
                ],
            }


# Singleton instance for platform-wide local aggregation
_global_aggregator: Optional[LocalMCPAggregator] = None


def get_local_aggregator() -> LocalMCPAggregator:
    global _global_aggregator
    if _global_aggregator is None:
        _global_aggregator = LocalMCPAggregator()
    return _global_aggregator
