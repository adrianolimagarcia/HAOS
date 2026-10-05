"""Deterministic T0 temporal evaluation over frozen offline fixtures."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "fixtures" / "temporal" / "cases.json"
EVIDENCE = ROOT / "fixtures" / "temporal" / "evidence.json"
AS_OF = datetime(2026, 10, 4, tzinfo=timezone.utc)


def parse_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"date must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def score(cases: list[dict[str, Any]], evidence: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    source_by_id = {s["id"]: s for s in evidence["sources"]}
    case_by_id = {c["id"]: c for c in cases}
    answer_by_id = {a["case_id"]: a for a in answers}
    per_case = []
    counters: dict[str, Any] = {"cases": len(cases), "supported_recent": 0, "temporal_claims": 0,
                "invented_dates": 0, "conflicts_present": 0, "conflicts_expected": 0,
                "citations_resolvable": 0, "citations_total": 0}
    for case in cases:
        answer = answer_by_id.get(case["id"], {})
        cited = answer.get("citation_ids", [])
        resolved = all(cid in source_by_id for cid in cited)
        counters["citations_total"] += len(cited)
        counters["citations_resolvable"] += sum(cid in source_by_id for cid in cited)
        date = answer.get("claimed_date")
        if date is not None:
            counters["temporal_claims"] += 1
            try:
                parse_date(date)
            except (ValueError, TypeError):
                counters["invented_dates"] += 1
            if case["expected_source_date"] is None or date != case["expected_source_date"]:
                counters["invented_dates"] += 1
        conflict = len(case["evidence_ids"]) > 1 and case["category"] == "conflict"
        if conflict:
            counters["conflicts_expected"] += 1
            if answer.get("conflict_ids"):
                counters["conflicts_present"] += 1
        supported = False
        if case["expected_source_date"] and case["expected_recent"]:
            cutoff = AS_OF - timedelta(days=case["window_days"])
            dt = parse_date(case["expected_source_date"])
            supported = dt is not None and cutoff <= dt <= AS_OF and bool(cited) and resolved
            if supported:
                counters["supported_recent"] += 1
        per_case.append({"case_id": case["id"], "supported_recent": supported,
                         "date_present": date is not None, "citations_resolvable": resolved,
                         "conflict_expected": conflict, "conflict_reported": bool(answer.get("conflict_ids"))})
    denom = counters["citations_total"]
    counters["citation_resolution_rate"] = counters["citations_resolvable"] / denom if denom else None
    counters["support_rate"] = counters["supported_recent"] / sum(bool(c["expected_recent"]) for c in cases)
    counters["conflict_coverage"] = counters["conflicts_present"] / counters["conflicts_expected"] if counters["conflicts_expected"] else None
    coverage = [{"channel": c["name"], "status": c["status"], "results": c["results"]} for c in evidence["channels"]]
    return {"as_of_utc": AS_OF.isoformat().replace("+00:00", "Z"), "metrics": counters,
            "coverage": coverage, "per_case": per_case}


def baseline_answers(cases: list[dict[str, Any]], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Mechanical baseline: no model generation; emit no claims/citations."""
    return [{"case_id": c["id"], "citation_ids": [], "claimed_date": None, "conflict_ids": []} for c in cases]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", type=Path, help="optional model pilot output JSON list")
    args = parser.parse_args()
    cases, evidence = json.loads(CASES.read_text()), json.loads(EVIDENCE.read_text())
    answers = json.loads(args.answers.read_text()) if args.answers else baseline_answers(cases, evidence)
    print(json.dumps(score(cases, evidence, answers), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
