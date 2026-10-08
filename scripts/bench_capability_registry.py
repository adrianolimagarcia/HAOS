#!/usr/bin/env python3
"""Deterministic benchmark for MCP Capability Registry v2 vs v1 schema footprint and runtime overhead."""
import json
import os
import sys
import time
from typing import Any, Dict, List

# Garantir imports locais
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.capability_registry_v2 import CapabilityOperation, CapabilityRegistryV2, _validate_args

# Simulação de ferramentas realistas (baseadas no ferramental real do HAOS e MCP)
SAMPLE_TOOLS = [
    {
        "id": f"tool_{i}",
        "name": f"operation_tool_{i}",
        "tool_ref": f"server__tool_{i}",
        "description": f"Perform complex operational analysis and telemetry dispatch for cluster node #{i}. Provides deep diagnostics, state inspection, and event triggering.",
        "schema": {
            "type": "object",
            "required": ["target_node", "action"],
            "properties": {
                "target_node": {"type": "string", "description": "Canonical identifier of target worker"},
                "action": {"type": "string", "enum": ["inspect", "restart", "drain", "cordon"], "description": "Action type"},
                "force": {"type": "boolean", "description": "Force execution even if drain fails"},
                "timeout_seconds": {"type": "integer", "description": "Maximum seconds to wait"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string"},
                        "caller": {"type": "string"}
                    }
                }
            }
        },
        "read_only": (i % 2 == 0),
        "roles": ["operator", "admin"] if (i % 3 == 0) else ["default", "operator"],
    }
    for i in range(1, 51)
]

def measure_token_footprint(registry: CapabilityRegistryV2) -> Dict[str, Any]:
    # v1: Esquema completo típico do MCP / OpenAI tools format
    v1_full_schemas = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["schema"]
            }
        }
        for t in SAMPLE_TOOLS
    ]
    v1_raw_json = json.dumps(v1_full_schemas, indent=2)
    v1_chars = len(v1_raw_json)
    # Heurística padrão robusta de tokens: ~3.8 chars por token para JSON técnico
    v1_est_tokens = round(v1_chars / 3.8)

    # v2: Manifesto compacto exportado pelo registry
    v2_compact_manifest = registry.export_compact_manifest()
    v2_raw_json = json.dumps(v2_compact_manifest, indent=2)
    v2_chars = len(v2_raw_json)
    v2_est_tokens = round(v2_chars / 3.8)

    token_savings_pct = round(((v1_chars - v2_chars) / v1_chars) * 100, 2)

    return {
        "v1_char_count": v1_chars,
        "v1_estimated_tokens": v1_est_tokens,
        "v2_compact_char_count": v2_chars,
        "v2_compact_estimated_tokens": v2_est_tokens,
        "token_reduction_pct": token_savings_pct,
    }

def measure_search_latency(registry: CapabilityRegistryV2, iterations: int = 5000) -> Dict[str, Any]:
    queries = ["telemetry", "drain", "worker", "cluster", "analysis", "nonexistent"]
    latencies_us: List[float] = []

    for i in range(iterations):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        results = registry.search_capabilities(q, limit=5)
        t1 = time.perf_counter()
        latencies_us.append((t1 - t0) * 1_000_000)

    latencies_us.sort()
    p50 = round(latencies_us[int(len(latencies_us) * 0.50)], 2)
    p95 = round(latencies_us[int(len(latencies_us) * 0.95)], 2)
    p99 = round(latencies_us[int(len(latencies_us) * 0.99)], 2)
    avg = round(sum(latencies_us) / len(latencies_us), 2)
    ops_per_sec = round(iterations / (sum(latencies_us) / 1_000_000), 1)

    return {
        "iterations": iterations,
        "avg_latency_us": avg,
        "p50_latency_us": p50,
        "p95_latency_us": p95,
        "p99_latency_us": p99,
        "ops_per_sec": ops_per_sec,
    }

def measure_validation_latency(registry: CapabilityRegistryV2, iterations: int = 5000) -> Dict[str, Any]:
    schema = SAMPLE_TOOLS[0]["schema"]
    valid_payload = {"target_node": "node-42", "action": "inspect", "force": False, "timeout_seconds": 30}
    invalid_payload = {"target_node": 123, "action": "unknown"}

    latencies_us: List[float] = []
    for i in range(iterations):
        payload = valid_payload if (i % 2 == 0) else invalid_payload
        t0 = time.perf_counter()
        _err = _validate_args(schema, payload)
        t1 = time.perf_counter()
        latencies_us.append((t1 - t0) * 1_000_000)

    latencies_us.sort()
    p50 = round(latencies_us[int(len(latencies_us) * 0.50)], 2)
    p95 = round(latencies_us[int(len(latencies_us) * 0.95)], 2)
    p99 = round(latencies_us[int(len(latencies_us) * 0.99)], 2)
    avg = round(sum(latencies_us) / len(latencies_us), 2)
    ops_per_sec = round(iterations / (sum(latencies_us) / 1_000_000), 1)

    return {
        "iterations": iterations,
        "avg_latency_us": avg,
        "p50_latency_us": p50,
        "p95_latency_us": p95,
        "p99_latency_us": p99,
        "ops_per_sec": ops_per_sec,
    }

def run_all_benchmarks():
    os.environ["HAOS_MCP_CAPABILITY_REGISTRY_V2"] = "true"
    registry = CapabilityRegistryV2()

    for t in SAMPLE_TOOLS:
        op = CapabilityOperation(
            id=t["id"],
            tool_ref=t["tool_ref"],
            name=t["name"],
            description=t["description"],
            parameters_compact={"target_node": "string", "action": "string"},
            parameters_schema=t["schema"],
            read_only=t["read_only"],
            roles=t["roles"],
        )
        registry.register(op, lambda args: {"status": "ok", "echo": args.get("target_node")})

    print("=== EXECUTANDO BENCHMARK REAL MCP CAPABILITY REGISTRY V2 ===")
    
    footprint = measure_token_footprint(registry)
    print(f"[1] Token Footprint:")
    print(f"    v1 full schemas: {footprint['v1_char_count']} chars (~{footprint['v1_estimated_tokens']} tokens)")
    print(f"    v2 compact manifest: {footprint['v2_compact_char_count']} chars (~{footprint['v2_compact_estimated_tokens']} tokens)")
    print(f"    Redução de tokens/espaço: {footprint['token_reduction_pct']}%")

    search_perf = measure_search_latency(registry, iterations=10000)
    print(f"[2] Latência de Busca de Capacidades (10.000 execuções):")
    print(f"    Média: {search_perf['avg_latency_us']} µs | P50: {search_perf['p50_latency_us']} µs | P95: {search_perf['p95_latency_us']} µs | P99: {search_perf['p99_latency_us']} µs")
    print(f"    Throughput: {search_perf['ops_per_sec']} ops/seg")

    valid_perf = measure_validation_latency(registry, iterations=10000)
    print(f"[3] Latência de Validação de Argumentos (10.000 validações):")
    print(f"    Média: {valid_perf['avg_latency_us']} µs | P50: {valid_perf['p50_latency_us']} µs | P95: {valid_perf['p95_latency_us']} µs | P99: {valid_perf['p99_latency_us']} µs")
    print(f"    Throughput: {valid_perf['ops_per_sec']} ops/seg")

    out_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "benchmarks", "capreg_benchmark_results.json"))
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "token_footprint": footprint,
        "search_performance": search_perf,
        "validation_performance": valid_perf,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nResultados gravados em: {out_file}")

if __name__ == "__main__":
    run_all_benchmarks()
