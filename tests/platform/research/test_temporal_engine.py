"""Unit and contract tests for shared temporal research engine."""
import datetime
from hermes.platform.research.temporal_engine import (
    CONTRACT_VERSION,
    CoverageItem,
    TemporalFinding,
    TemporalResearchResult,
    TemporalWindow,
    compute_temporal_window,
    execute_temporal_research,
)


def test_compute_temporal_window_defaults_and_relative_parsing():
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # 30 days
    w30 = compute_temporal_window("last30days", reference_time=ref)
    assert w30.requested == "last30days"
    assert w30.end_utc == ref.isoformat()
    start_expected = (ref - datetime.timedelta(days=30)).isoformat()
    assert w30.start_utc == start_expected

    # 7 days
    w7 = compute_temporal_window("last7days", reference_time=ref)
    assert w7.start_utc == (ref - datetime.timedelta(days=7)).isoformat()

    # 24h
    w1 = compute_temporal_window("24h", reference_time=ref)
    assert w1.start_utc == (ref - datetime.timedelta(days=1)).isoformat()

    # 90 days / quarter
    w90 = compute_temporal_window("quarter", reference_time=ref)
    assert w90.start_utc == (ref - datetime.timedelta(days=90)).isoformat()


def test_execute_temporal_research_structured_result_and_isolation():
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    res = execute_temporal_research(
        goal="Analyze recent algorithmic developments",
        window_spec="last30days",
        channels=["web-search", "reddit"],
        reference_time=ref,
    )

    assert isinstance(res, TemporalResearchResult)
    assert res.contract_version == CONTRACT_VERSION
    assert res.cutoff_utc == ref.isoformat()
    assert res.window.requested == "last30days"
    assert len(res.coverage) == 2
    assert res.coverage[0].channel == "web-search"
    assert res.coverage[0].status == "ok"
    assert res.coverage[1].channel == "reddit"

    # Invariants: no automatic side-effect or mutations
    as_dict = res.to_dict()
    assert "findings" in as_dict
    assert "coverage" in as_dict
    assert "window" in as_dict
    assert as_dict["disagreements"] == []
    assert as_dict["evidence_gaps"] == []


def test_coverage_and_finding_dataclasses():
    c = CoverageItem(channel="x-twitter", status="bypassed", reason="skipped_no_credentials")
    assert c.channel == "x-twitter"
    assert c.status == "bypassed"
    assert c.reason == "skipped_no_credentials"
    assert c.to_dict()["status"] == "bypassed"

    f = TemporalFinding(
        claim="AlphaGo update released",
        source_ids=[101],
        published_at="2026-09-25T00:00:00+00:00",
        freshness="in_window",
    )
    assert f.freshness == "in_window"
    assert f.source_ids == [101]
    assert f.to_dict()["freshness"] == "in_window"
