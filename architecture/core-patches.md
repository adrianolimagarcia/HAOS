# Upstream Core Patch Catalog

This document tracks all intentional, temporary, or permanent modifications made directly to the upstream Hermes core codebase (`gateway/`, `agent/`, `tools/`, `hermes_cli/`).

In accordance with **ADR-001 (Hermes Kernel)** and **ADR-012 (Plugin-first)**, our overarching goal is:
> **Minimum Permanent Core Diff.** Every capability should ideally live under `hermes/platform/` (PEP-420 namespace) or as a plugin.

---

## 1. Active Core Patches

### Patch CP-001: Case-Insensitive Model Deduplication in Gateway `/v1/models`
- **File:** `gateway/platforms/api_server.py`
- **Lines Affected:** `~2210-2230`
- **Reason:** Upstream `/v1/models` concatenated configured `model_routes` aliases and primary models without checking for duplicate bare model keys (e.g. `gpt-5.6-luna` present in both `a6api` and `codex`). This caused duplicate entries in downstream clients (like OpenCode, LibreChat, or WebUI picker).
- **Upstream Alternative:** Upstream issue/PR to add deduplication to `_handle_models`.
- **Size:** +14 lines, -2 lines.
- **Risk:** LOW (purely additive set deduplication maintaining response order and preserved prefixes).
- **Removal Strategy:** Can be cleanly retired when upstream incorporates model route alias deduplication in `api_server.py`.

---

## 2. Platform Extensions (Zero-Patch Namespace)

All other HAOS subsystems run strictly in the non-colliding PEP-420 namespace `hermes/platform/` with zero `__init__.py`:
- `hermes/platform/tasks/`: TaskSpec and TaskRun lifecycle.
- `hermes/platform/execution/`: Dispatcher, GenericWorkerLane, Team Runtime.
- `hermes/platform/models/`: ExactModelRouter, CircuitBreaker, A6API client.
- `hermes/platform/capabilities/`: MCP, LSP, Capability registry.
- `hermes/platform/context/`: Context package builder and byte-stable prompt cache.
- `hermes/platform/memory/`: Obsidian and GraphRAG adapters.
- `hermes/platform/scaling/`: Hardening, chaos injection, crash recovery.
- `hermes/platform/evolution/`: Adaptive Intelligence (Ouroboros) shadow mode.
- `hermes/platform/webui/`: Standalone Control Plane and team graph visualizer.

These modules produce **zero merge conflicts** during upstream rebases.
