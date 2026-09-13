"""Tests for HAOS Local MCP Aggregator and Gateway."""

from __future__ import annotations

import asyncio
import json
import pytest
from hermes.platform.mcp.aggregator import (
    LocalMCPAggregator,
    CIRCUIT_BREAKER_THRESHOLD,
    TargetServerState,
)


@pytest.fixture
def sample_tools() -> list[dict]:
    return [
        {
            "name": "say_hello",
            "description": "Say hello to someone",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "get_balance",
            "description": "Get user balance",
            "inputSchema": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}},
                "required": ["account_id"],
            },
        },
    ]


def test_namespacing_and_catalog(sample_tools: list[dict]) -> None:
    aggregator = LocalMCPAggregator()

    # Register tools for server A
    names_a = aggregator.register_server_tools("banking", [sample_tools[1]])
    assert names_a == ["banking_get_balance"]

    # Register tools for server B
    names_b = aggregator.register_server_tools("greeter", [sample_tools[0]])
    assert names_b == ["greeter_say_hello"]

    catalog = aggregator.list_tools()
    assert len(catalog) == 2
    tool_names = {t["name"] for t in catalog}
    assert tool_names == {"banking_get_balance", "greeter_say_hello"}

    # Check namespaced description
    banking_tool = next(t for t in catalog if t["name"] == "banking_get_balance")
    assert "[banking]" in banking_tool["description"]


def test_successful_tool_dispatch(sample_tools: list[dict]) -> None:
    async def run() -> None:
        aggregator = LocalMCPAggregator()
        aggregator.register_server_tools("greeter", [sample_tools[0]])

        async def fake_greeter_dispatcher(tool_name: str, arguments: dict) -> dict:
            assert tool_name == "say_hello"
            return {
                "isError": False,
                "content": [{"type": "text", "text": f"Hello, {arguments.get('name')}!"}],
            }

        aggregator.register_dispatcher("greeter", fake_greeter_dispatcher)

        res = await aggregator.call_tool("greeter_say_hello", {"name": "Adriano"})
        assert not res.get("isError")
        assert "Hello, Adriano!" in res["content"][0]["text"]

        status = aggregator.get_status()
        assert status["total_servers"] == 1
        assert status["servers"]["greeter"]["healthy"] is True
        assert status["servers"]["greeter"]["failures"] == 0

    asyncio.run(run())


def test_tool_timeout_handling(sample_tools: list[dict]) -> None:
    async def run() -> None:
        aggregator = LocalMCPAggregator()
        aggregator.register_server_tools("slow_server", [sample_tools[0]])

        async def slow_dispatcher(tool_name: str, arguments: dict) -> dict:
            await asyncio.sleep(0.5)
            return {"content": [{"type": "text", "text": "done"}]}

        aggregator.register_dispatcher("slow_server", slow_dispatcher)

        # Call with 0.1s timeout
        res = await aggregator.call_tool("slow_server_say_hello", {}, timeout=0.1)
        assert res.get("isError") is True
        assert "timed out" in res["content"][0]["text"]

    asyncio.run(run())


def test_circuit_breaker_tripping(sample_tools: list[dict]) -> None:
    async def run() -> None:
        aggregator = LocalMCPAggregator()
        aggregator.register_server_tools("faulty_server", [sample_tools[0]])

        async def failing_dispatcher(tool_name: str, arguments: dict) -> dict:
            raise RuntimeError("Service unavailable")

        aggregator.register_dispatcher("faulty_server", failing_dispatcher)

        # Trigger failures up to threshold
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            res = await aggregator.call_tool("faulty_server_say_hello", {})
            assert res.get("isError") is True

        # Check status
        status = aggregator.get_status()
        assert status["servers"]["faulty_server"]["circuit_breaker_open"] is True
        assert status["servers"]["faulty_server"]["healthy"] is False

        # Next call should be rejected immediately by the circuit breaker without invoking dispatcher
        res = await aggregator.call_tool("faulty_server_say_hello", {})
        assert res.get("isError") is True
        assert "Circuit breaker open" in res["content"][0]["text"]

    asyncio.run(run())


# --------------------------------------------------------------------------- kernel mirroring

def _register_kernel_tool(server_name: str, tool_name: str, handler) -> str:
    """Register a tool the way the Hermes kernel would (toolset ``mcp-<server>``,
    native name ``mcp__<server>__<tool>``) and return the native name for cleanup."""
    from tools.registry import registry
    native = f"mcp__{server_name}__{tool_name}"
    registry.register(
        name=native,
        toolset=f"mcp-{server_name}",
        schema={
            "name": native,
            "description": f"Demo {tool_name} from {server_name}",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handler,
    )
    return native


def test_kernel_mirror_hydrates_configured_servers(monkeypatch) -> None:
    """A server under config.yaml ``mcp_servers`` whose tools the kernel already registered
    is mirrored into the federated catalog without any explicit registration call."""
    from tools import mcp_tool_config as _cfg
    from tools.registry import registry

    native = _register_kernel_tool(
        "demo", "say_hello",
        lambda args, **kw: json.dumps({"result": f"hello {args.get('name', '')}"}))
    try:
        monkeypatch.setattr(_cfg, "_load_mcp_config",
                            lambda: {"demo": {"enabled": True, "command": "demo"}})
        aggregator = LocalMCPAggregator()
        names = [t["name"] for t in aggregator.list_tools()]
        assert "demo_say_hello" in names
        schema = next(t for t in aggregator.list_tools() if t["name"] == "demo_say_hello")
        assert "[demo]" in schema["description"]
        # the kernel dispatcher is registered for the mirrored server
        assert "demo" in aggregator._dispatchers
    finally:
        registry.deregister(native)


def test_kernel_mirror_ignores_disabled_servers(monkeypatch) -> None:
    from tools import mcp_tool_config as _cfg
    from tools.registry import registry

    native = _register_kernel_tool(
        "demo_off", "ping",
        lambda args, **kw: json.dumps({"result": "pong"}))
    try:
        monkeypatch.setattr(_cfg, "_load_mcp_config",
                            lambda: {"demo_off": {"enabled": False}})
        aggregator = LocalMCPAggregator()
        names = [t["name"] for t in aggregator.list_tools()]
        assert "demo_off_ping" not in names
    finally:
        registry.deregister(native)


def test_kernel_mirror_advertises_configured_server_before_tools(monkeypatch) -> None:
    """A server present in config.yaml but not yet registered by the kernel still shows up
    in status (configured, zero tools) so the operator sees intent rather than absence."""
    from tools import mcp_tool_config as _cfg

    monkeypatch.setattr(_cfg, "_load_mcp_config",
                        lambda: {"pending": {"enabled": True, "command": "sleep"}})
    aggregator = LocalMCPAggregator()
    status = aggregator.get_status()
    assert status["total_servers"] == 1
    assert "pending" in status["servers"]
    assert status["servers"]["pending"]["tools_count"] == 0
    assert aggregator.list_tools() == []



def test_kernel_mirror_delegates_call_to_registry(monkeypatch) -> None:
    """Federated calls on a mirrored server go through the kernel registry dispatch."""
    from tools import mcp_tool_config as _cfg
    from tools.registry import registry

    native = _register_kernel_tool(
        "demo", "greet",
        lambda args, **kw: json.dumps({"result": f"hi {args.get('name', '')}"}))
    try:
        monkeypatch.setattr(_cfg, "_load_mcp_config",
                            lambda: {"demo": {"enabled": True}})
        aggregator = LocalMCPAggregator()
        aggregator.list_tools()  # hydrate

        async def run() -> dict:
            return await aggregator.call_tool("demo_greet", {"name": "Ada"})

        res = asyncio.run(run())
        assert res.get("isError") is not True
        assert "hi Ada" in res["content"][0]["text"]
    finally:
        registry.deregister(native)


def test_call_without_dispatcher_reports_clear_error() -> None:
    """A manually-registered server tool with no dispatcher (and no kernel backing) now
    returns an actionable message instead of the old dead '_dispatch_call_to_session' path."""
    aggregator = LocalMCPAggregator()
    aggregator.register_server_tools("banking", [{
        "name": "get_balance", "description": "Get balance",
        "inputSchema": {"type": "object", "properties": {}},
    }])

    async def run() -> dict:
        return await aggregator.call_tool("banking_get_balance", {})

    res = asyncio.run(run())
    assert res.get("isError") is True
    assert "No dispatcher registered" in res["content"][0]["text"]


def test_gateway_tools_see_kernel_configured_servers(monkeypatch) -> None:
    """E2E through the real mcp_gateway_* entry points (same path the CLI/dashboard tools
    use): a config.yaml mcp_servers server whose kernel tools are registered shows up in
    list_tools and is callable via mcp_gateway_call — zero explicit wiring."""
    from tools import mcp_tool_config as _cfg
    from tools.registry import registry
    from tools.haos_mcp_gateway import mcp_gateway_list_tools, mcp_gateway_call
    import hermes.platform.mcp.aggregator as _agg_mod

    native = _register_kernel_tool(
        "demo", "greet",
        lambda args, **kw: json.dumps({"result": f"hi {args.get('name', '')}"}))
    try:
        monkeypatch.setattr(_cfg, "_load_mcp_config",
                            lambda: {"demo": {"enabled": True}})
        # Fresh singleton so earlier tests in this file can't leak state in.
        monkeypatch.setattr(_agg_mod, "_global_aggregator", None)

        listed = json.loads(mcp_gateway_list_tools())
        names = [t["name"] for t in listed["tools"]]
        assert "demo_greet" in names

        out = json.loads(mcp_gateway_call("demo_greet", {"name": "Ada"}))
        assert out.get("isError") is not True
        assert "hi Ada" in out["content"][0]["text"]
    finally:
        registry.deregister(native)
