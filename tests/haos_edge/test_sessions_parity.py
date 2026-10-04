"""Deterministic Python/Rust sessions projection parity fixture."""
import json
from pathlib import Path


def test_sessions_parity_fixture_contract():
    fixture = json.loads((Path(__file__).parent / "fixtures/sessions_parity.json").read_text())
    rows = fixture["rows"]
    q = fixture["query"]
    visible = [r for r in rows if not r["hidden"] and not r["archived"]]
    visible = [r for r in visible if r["source"] not in q["exclude_sources"].split(",")]
    visible = [
        r for r in visible
        if r["parent_session_id"] is None or json.loads(r["model_config"] or "{}").get("_branched_from")
    ]
    visible.sort(key=lambda r: (r["last_activity_at"], r["started_at"], r["id"]), reverse=True)
    assert [r["id"] for r in visible] == fixture["expected_recent_ids"]
    assert all("system_prompt" not in r for r in visible)
