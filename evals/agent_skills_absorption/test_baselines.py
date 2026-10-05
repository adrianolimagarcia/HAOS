"""Regression tests for deterministic T0 and CG0 harnesses."""
from __future__ import annotations

import json
from pathlib import Path

from evals.agent_skills_absorption.codegraph import run_cg0
from evals.agent_skills_absorption.temporal import CASES, EVIDENCE, baseline_answers, score


def test_temporal_corpus_is_offline_and_diverse() -> None:
    cases = json.loads(CASES.read_text())
    assert 12 <= len(cases) <= 20
    assert len({case["id"] for case in cases}) == len(cases)
    categories = {case["category"] for case in cases}
    assert {"release", "incident", "price", "conflict", "undated", "degraded"} <= categories


def test_temporal_mechanical_baseline_makes_no_model_claims() -> None:
    cases, evidence = json.loads(CASES.read_text()), json.loads(EVIDENCE.read_text())
    answers = baseline_answers(cases, evidence)
    result = score(cases, evidence, answers)
    assert result["metrics"]["cases"] == len(cases)
    assert result["metrics"]["invented_dates"] == 0
    assert result["metrics"]["supported_recent"] == 0
    assert result["metrics"]["conflicts_present"] == 0
    assert {entry["status"] for entry in result["coverage"]} >= {"ok", "degraded", "bypassed"}


def test_temporal_scorer_detects_fabricated_dates_and_bad_citations() -> None:
    cases, evidence = json.loads(CASES.read_text()), json.loads(EVIDENCE.read_text())
    answers = baseline_answers(cases, evidence)
    answers[0] = {"case_id": "T0-01", "citation_ids": ["NO-SUCH-ID"],
                  "claimed_date": "2027-01-01T00:00:00Z", "conflict_ids": []}
    result = score(cases, evidence, answers)
    assert result["metrics"]["invented_dates"] == 1
    assert result["metrics"]["citation_resolution_rate"] == 0.0


def test_codegraph_indexes_python_and_reports_language_gaps() -> None:
    result = run_cg0()
    assert result["files_indexed"] == 4
    assert result["python_symbol_coverage"] == 1.0
    assert result["typescript_javascript_coverage"] == 0.0
    assert result["architectural_questions_answerable_from_current_graph"] < result["architectural_questions_total"]
