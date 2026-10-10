"""Offline metrics and fail-closed checks for contextual assertion classification."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from hermes.platform.context.memory.classifier import AssertionType, classify_assertion

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_classification_corpus_v2.json"
LABELS = ("FACT_VERIFIED", "FACT_UNVERIFIED", "HYPOTHESIS", "REFUTED")


def load_cases():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 80
    counts = Counter(case["language"] for case in cases)
    assert counts["pt-BR"] == counts["en"]
    assert set(counts) == {"pt-BR", "en"}
    assert len({case["id"] for case in cases}) == len(cases)
    return cases


def evaluate(cases):
    matrix = {label: {pred: 0 for pred in LABELS} for label in LABELS}
    predictions = []
    for case in cases:
        result = classify_assertion(case["text"], evidence_refs=case.get("evidence_refs", []))
        predicted = result.assertion_type.value
        actual = case["expected_type"]
        matrix[actual][predicted] += 1
        predictions.append((case, actual, predicted))
    correct = sum(matrix[label][label] for label in LABELS)
    metrics = {}
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[actual][label] for actual in LABELS if actual != label)
        fn = sum(matrix[label][pred] for pred in LABELS if pred != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        metrics[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        }
    total = len(predictions)
    micro_precision = correct / total if total else 0.0
    total_fn = sum(metrics[label]["fn"] for label in LABELS)
    total_fp = sum(metrics[label]["fp"] for label in LABELS)
    micro_recall = correct / (correct + total_fn) if correct + total_fn else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall)
        if micro_precision + micro_recall
        else 0.0
    )
    overall = {
        "precision": micro_precision,
        "recall": micro_recall,
        "f1": micro_f1,
        "fp": total_fp,
        "fn": total_fn,
    }
    return predictions, matrix, metrics, overall


def report(matrix, metrics, overall, cases):
    lines = [
        f"Cases: {len(cases)}; PT-BR/EN: {sum(c['language']=='pt-BR' for c in cases)}/{sum(c['language']=='en' for c in cases)}",
        f"Micro Precision / Recall / F1: {overall['precision']:.2%} / {overall['recall']:.2%} / {overall['f1']:.2%} | FP/FN: {overall['fp']}/{overall['fn']}",
        "Per-class: class | precision | recall | F1 | TP | FP | FN",
    ]
    for label in LABELS:
        m = metrics[label]
        lines.append(
            f"{label} | {m['precision']:.2%} | {m['recall']:.2%} | {m['f1']:.2%} | {m['tp']} | {m['fp']} | {m['fn']}"
        )
    lines.append("Confusion matrix (true rows, predicted columns):")
    lines.append("true\\pred | " + " | ".join(LABELS))
    for actual in LABELS:
        lines.append(actual + " | " + " | ".join(str(matrix[actual][pred]) for pred in LABELS))
    return "\n".join(lines)


def test_full_corpus_metrics_and_acceptance(capsys):
    cases = load_cases()
    predictions, matrix, metrics, overall = evaluate(cases)
    explicit = [row for row in predictions if row[0]["category"] == "explicit_hypothesis"]
    explicit_recall = sum(actual == predicted == "HYPOTHESIS" for _, actual, predicted in explicit) / len(explicit)
    hypothesis_total = sum(actual == "HYPOTHESIS" for _, actual, _ in predictions)
    promoted = sum(actual == "HYPOTHESIS" and predicted == "FACT_VERIFIED" for _, actual, predicted in predictions)
    print("\n=== Frozen synthetic contextual classification corpus ===")
    print(report(matrix, metrics, overall, cases))
    print(f"Explicit-hypothesis recall: {explicit_recall:.2%} ({len(explicit)} cases)")
    print(f"Hypothesis→FACT_VERIFIED false-positive rate: {promoted / hypothesis_total:.2%} ({promoted}/{hypothesis_total})")
    assert explicit_recall >= 0.98
    assert overall["precision"] >= 0.95
    assert promoted == 0
    assert all(sum(matrix[label].values()) > 0 for label in LABELS)


def test_ambiguous_and_adversarial_cases_never_promote_without_evidence():
    for case in load_cases():
        if case["category"] in {"ambiguous_fail_closed", "adversarial_injection"}:
            prediction = classify_assertion(case["text"], evidence_refs=case.get("evidence_refs", []))
            assert prediction.assertion_type != AssertionType.FACT_VERIFIED, case["id"]


def test_observation_without_evidence_reference_is_not_verified():
    result = classify_assertion("Observed: test completed with exit code 0")
    assert result.assertion_type == AssertionType.FACT_UNVERIFIED


if __name__ == "__main__":
    cases = load_cases()
    predictions, matrix, metrics, overall = evaluate(cases)
    print(report(matrix, metrics, overall, cases))
