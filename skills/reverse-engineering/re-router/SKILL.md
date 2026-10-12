---
name: re-router
description: "Route binary reverse engineering to triage, r2, or Ghidra."
version: 1.0.0
author: Zhao Xuya, Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [reverse-engineering, routing, radare2, ghidra, binary-analysis]
    category: security
---

# Reverse Engineering Toolchain Router (HAOS Cherry-Pick)

Hierarchical tool router for binary inspection and analysis. Evaluates binary complexity and maps reverse engineering workflows to the optimal, cost-efficient layer: fast static triage, scriptable radare2 backbone, or deep Ghidra headless decompilation.

## When to Use

- When an unknown binary requires inspection and the appropriate depth is undetermined.
- To prevent heavy analysis (Ghidra/r2) for tasks resolvable via metadata or strings.
- To orchestrate multi-stage reversing: Triage -> Disassembly -> Decompilation.

## Tool Index & Tiering

| Tier | Engine / Capability | Best For | Typical Latency | Context Cost |
|---|---|---|---|---|
| **Tier 1: Triage** | `binary-triage` / `binary_analyzer.py` (Phase 1) | Headers, entropy, security mitigations (PIE/NX), strings | < 1s | Minimal (JSON) |
| **Tier 2: Backbone** | `Radare2MCPClient` (Phase 2) | Function listing, assembly control flow, imports/exports | 1-3s | Moderate (budget/spill) |
| **Tier 3: Deep** | `GhidraMCP` / `analyzeHeadless` (Phase 3) | Full C decompilation, complex call graphs, struct recovery | 5-30s | High (budget/spill) |

## Procedure

1. **Step 1 (Always Start with Tier 1):** Execute static triage on the target binary to verify architecture and check if packed.
2. **Step 2 (Evaluate Findings):**
   - If questions concern compiler flags, sections, or strings, conclude at Tier 1.
   - If control flow or specific assembly functions are needed, route to Tier 2 (`r2`).
   - If high-level logic reconstruction or algorithm understanding is required, route to Tier 3 (`Ghidra`).
3. **Step 3 (Budget Enforcement):** For Tier 2 and Tier 3, ensure character budget enforcement is active; rely on spilled file references for large disassembly listings.

## Pitfalls

- Never jump straight to Tier 3 for simple architecture or string queries.
- If a binary is packed (entropy > 7.0), unpack or dump before attempting Tier 3 decompilation.

## Verification

Confirm appropriate tier selection by logging the chosen analysis level and verifying required artifacts.
