# HAOS Local MCP Gateway & Aggregator Architecture

## 1. Context and Purpose

As multi-agent systems and tooling scale, connecting agents to independent external capabilities results in an $N \times M$ connection explosion. Rather than managing multiple independent subprocesses and network connections with disparate health lifecycles in the core turn loop, the **HAOS Local MCP Gateway / Aggregator** unifies multiple downstream MCP servers under a single, robust local facade.

## 2. Key Architecture Pillars

- **100% Local & Offline-First**: Does not require Kubernetes, external Docker containers, or third-party cloud infrastructure. Runs in-process via Python `asyncio`.
- **Automatic Namespacing**: Tools are namespaced as `<server_name>_<tool_name>` (e.g. `banking_get_balance`, `sqlite_query`) to completely eliminate naming collisions across different servers.
- **Circuit Breaker & Fault Isolation**: If a downstream server fails 3 consecutive times (`CIRCUIT_BREAKER_THRESHOLD`), the breaker trips into open state. Subsequent calls fail fast without waiting for timeouts or stalling the agent turn loop.
- **Deterministic Timeout Handling**: Configurable per-call timeouts prevent hung subprocesses from deadlocking agent execution.

## 3. Component Architecture

```
                    ┌───────────────────────────────┐
                    │          HAOS Agent           │
                    │      (Tools Registry/CLI)     │
                    └───────────────┬───────────────┘
                                    │ Single Unified Toolset
                                    ▼
                    ┌───────────────────────────────┐
                    │      LocalMCPAggregator       │
                    │ (hermes/platform/mcp/aggregator)
                    └───────┬───────────────┬───────┘
                            │ Routing & Breaker
            ┌───────────────┴────┐     ┌────┴─────────────────┐
            ▼                    ▼     ▼                      ▼
    ┌───────────────┐      ┌───────────┐ ┌───────────────┐ ┌───────────┐
    │  SQLite MCP   │      │ Git MCP   │ │ Local API MCP │ │ Custom    │
    │  (local stdio)│      │  (stdio)  │ │ (HTTP local)  │ │ Tool      │
    └───────────────┘      └───────────┘ └───────────────┘ └───────────┘
```

## 4. Usage & Model Tools

Available through the `mcp_gateway` toolset:

- `mcp_gateway_status`: Inspects overall health, latency, failure counts, and total tool count per server.
- `mcp_gateway_list_tools`: Returns all federated tools with their namespaced IDs and descriptions.
- `mcp_gateway_call`: Invokes a namespaced tool (`tool_name="<server>_<tool>"`, `arguments={...}`) passing through the router, timeout guard, and circuit breaker.

## 5. Zero-Config Kernel Auto-Registration

The gateway hydrates itself from the Hermes kernel's own MCP configuration — no code, no
per-server registration call, CLI or dashboard alike:

1. On first use (`list_tools` / `get_status` / `call_tool`), the aggregator reads
   `mcp_servers` from `config.yaml` (via `tools.mcp_tool_config._load_mcp_config`), honouring
   each server's `enabled` toggle.
2. For every enabled server it mirrors the tools the kernel has already registered under the
   `mcp-<server>` toolset (the kernel runs `discover_mcp_tools()` at startup in CLI, dashboard,
   cron and gateway flows), federating them as `<server>_<tool>`.
3. Federated calls are **delegated back to the kernel registry** (`registry.dispatch` on the
   native `mcp__<server>__<tool>` name), so the kernel's own lazy connect, circuit breaker,
   auth refresh and stdio respawn still apply — the gateway adds namespacing, a unified
   catalog/status and an extra timeout/breaker layer without duplicating the transport.

A server listed in `config.yaml` but not yet connected shows up in `mcp_gateway_status` as
configured with zero tools until the kernel registers it; explicitly registered servers
(programmatic `register_dispatcher`/`register_server_tools`) always take precedence over the
mirror.

