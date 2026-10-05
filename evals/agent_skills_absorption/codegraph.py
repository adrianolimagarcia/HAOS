"""Deterministic CG0 baseline against the existing Python code graph."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from hermes.platform.codebase.runner import run_index

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "fixtures" / "code_corpus"
TRUTH = CORPUS / "ground_truth.json"


def run_cg0() -> dict[str, Any]:
    truth = json.loads(TRUTH.read_text())
    with tempfile.TemporaryDirectory(prefix="cg0-") as tmp:
        report = run_index(CORPUS, Path(tmp) / "wiki", skip=set())
        graph = json.loads((Path(tmp) / "wiki" / "graph.json").read_text())
    node_ids = {node["id"] for node in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    present = {tuple(sorted((edge["source"], edge["target"]))) + (edge["relation"],) for edge in edges}
    observed_imports = [e for e in edges if e["relation"] == "importa"]
    expected_imports = truth["ground_truth"]["imports"]
    # Current graph only indexes .py; imports in Python are resolved and present.
    import_resolved = sum(any(expected["target_module"] in e["target"] for e in observed_imports)
                          for expected in expected_imports if expected["lang"] == "python")
    py_import_total = sum(1 for e in expected_imports if e["lang"] == "python")
    py_symbol_names = [s["name"] for s in truth["ground_truth"]["symbols"] if s["lang"] == "python"]
    py_symbol_coverage = sum(any(name in nid for nid in node_ids) for name in py_symbol_names) / len(py_symbol_names)
    question_results = []
    for question in truth["architectural_questions"]:
        found = [target for target in question["target_symbols"] if target in node_ids]
        question_results.append({"id": question["id"], "target_symbols_present": found,
                                 "answerable_proxy": len(found) == len(question["target_symbols"])})
    unsupported = [item for item in truth["ground_truth"]["imports"] + truth["ground_truth"]["calls"]
                   if item["lang"] in ("typescript", "javascript")]
    return {
        "engine": "HAOS codebase-wiki current Python AST indexer",
        "files_indexed": report.file_count,
        "nodes": report.node_count,
        "edges": report.edge_count,
        "python_symbol_coverage": py_symbol_coverage,
        "python_import_edge_recall_proxy": import_resolved / py_import_total if py_import_total else None,
        "typescript_javascript_ground_truth_edges": len(unsupported),
        "typescript_javascript_edges_observed": 0,
        "typescript_javascript_coverage": 0.0,
        "architectural_questions_answerable_from_current_graph": sum(q["answerable_proxy"] for q in question_results),
        "architectural_questions_total": len(truth["architectural_questions"]),
        "question_results": question_results,
        "gaps": [
            "TS/JS files are not read by the current runner, so their types/imports/calls are absent.",
            "Python-only mini-corpus supports one of three questions; question-level scoring still requires an answer rubric.",
            "Dynamic dispatch and framework semantics are outside this compact lexical ground truth."
        ]
    }


if __name__ == "__main__":
    print(json.dumps(run_cg0(), indent=2))
