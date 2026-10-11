"""Unit and contract tests for shared temporal research engine."""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch
import pytest

from hermes.platform.research.temporal_engine import (
    CONTRACT_VERSION,
    CoverageItem,
    TemporalFinding,
    TemporalResearchResult,
    TemporalWindow,
    compute_temporal_window,
    execute_temporal_research,
)
from hermes.platform.research.temporal_utils import (
    evaluate_freshness,
    normalize_url,
    parse_timestamp_to_utc,
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
        url="https://example.com/alphago",
        source_channel="web-search",
    )
    assert f.freshness == "in_window"
    assert f.source_ids == [101]
    assert f.url == "https://example.com/alphago"
    assert f.source_channel == "web-search"
    assert f.to_dict()["freshness"] == "in_window"
    assert f.to_dict()["url"] == "https://example.com/alphago"


def test_temporal_utils_date_parsing_and_utc():
    # Epoch timestamp
    dt = parse_timestamp_to_utc(1760000000)
    assert dt is not None
    assert dt.tzinfo == datetime.timezone.utc

    # ISO string with Z
    dt_iso = parse_timestamp_to_utc("2026-10-08T15:30:00Z")
    assert dt_iso is not None
    assert dt_iso.year == 2026
    assert dt_iso.month == 10
    assert dt_iso.day == 8
    assert dt_iso.hour == 15

    # Textual dates
    dt_txt = parse_timestamp_to_utc("Release date: Oct. 7, 2025 # stable release")
    assert dt_txt is not None
    assert dt_txt.year == 2025
    assert dt_txt.month == 10
    assert dt_txt.day == 7

    # None and invalid cases
    assert parse_timestamp_to_utc(None) is None
    assert parse_timestamp_to_utc("") is None
    assert parse_timestamp_to_utc("no date here whatsoever") is None


def test_url_normalization():
    raw = "https://Example.COM/posts/item/?utm_source=twitter&utm_medium=cpc&ref=123"
    norm = normalize_url(raw)
    assert norm == "https://example.com/posts/item"

    raw_trail = "https://example.com/docs/"
    assert normalize_url(raw_trail) == "https://example.com/docs"


def test_freshness_evaluation():
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    start = ref - datetime.timedelta(days=30)  # 2026-09-10

    # In window
    dt_in = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=datetime.timezone.utc)
    assert evaluate_freshness(dt_in, start, ref) == "in_window"

    # Historical context (older than 30 days)
    dt_old = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert evaluate_freshness(dt_old, start, ref) == "historical_context"

    # Future date (clock skew / unverified)
    dt_future = datetime.datetime(2026, 11, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert evaluate_freshness(dt_future, start, ref) == "unverified"

    # None
    assert evaluate_freshness(None, start, ref) == "unverified"


@patch("hermes.platform.research.temporal_engine.search_web")
@patch("hermes.platform.research.temporal_engine.search_reddit")
def test_execute_temporal_research_mocked_connectors(mock_reddit, mock_web):
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

    mock_web.return_value = (
        [
            {
                "claim": "Python 3.14 Released",
                "url": "https://python.org/release/314",
                "published_at": "2026-10-05T00:00:00+00:00",
                "freshness": "in_window",
                "source_channel": "web-search",
            }
        ],
        "ok",
        None,
    )

    mock_reddit.return_value = (
        [
            {
                "claim": "Discussion on Python 3.14: Great features",
                "url": "https://reddit.com/r/python/comments/123",
                "published_at": "2026-10-06T00:00:00+00:00",
                "freshness": "in_window",
                "source_channel": "reddit",
            }
        ],
        "ok",
        None,
    )

    res = execute_temporal_research(
        goal="Python 3.14 features",
        window_spec="last30days",
        channels=["web-search", "reddit"],
        reference_time=ref,
    )

    assert res.contract_version == CONTRACT_VERSION
    assert len(res.coverage) == 2
    assert res.coverage[0].status == "ok"
    assert res.coverage[0].result_count == 1
    assert res.coverage[1].status == "ok"
    assert res.coverage[1].result_count == 1

    assert len(res.findings) == 2
    assert res.findings[0].url == "https://python.org/release/314"
    assert res.findings[0].source_channel == "web-search"
    assert res.findings[0].freshness == "in_window"
    assert res.findings[1].url == "https://reddit.com/r/python/comments/123"


@patch("hermes.platform.research.temporal_engine.search_web")
@patch("hermes.platform.research.temporal_engine.search_reddit")
def test_execute_temporal_research_deduplication(mock_reddit, mock_web):
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

    duplicate_url = "https://example.com/article"

    mock_web.return_value = (
        [
            {
                "claim": "Article One Web",
                "url": duplicate_url,
                "published_at": "2026-10-05T00:00:00+00:00",
                "freshness": "in_window",
                "source_channel": "web-search",
            }
        ],
        "ok",
        None,
    )

    mock_reddit.return_value = (
        [
            {
                "claim": "Article One Reddit Repost",
                "url": f"{duplicate_url}?utm_source=reddit",
                "published_at": "2026-10-05T00:00:00+00:00",
                "freshness": "in_window",
                "source_channel": "reddit",
            }
        ],
        "ok",
        None,
    )

    res = execute_temporal_research(
        goal="Duplicate test",
        window_spec="last30days",
        channels=["web-search", "reddit"],
        reference_time=ref,
    )

    # Even though both returned 1 item, normalized deduplication unifies them into 1 finding
    assert len(res.findings) == 1
    assert res.findings[0].url == duplicate_url
    assert res.findings[0].source_ids == [1]


@patch("hermes.platform.research.temporal_engine.search_web")
@patch("hermes.platform.research.temporal_engine.search_reddit")
def test_execute_temporal_research_partial_fail_closed(mock_reddit, mock_web):
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # Web succeeds
    mock_web.return_value = (
        [
            {
                "claim": "Security advisory published",
                "url": "https://cve.mitre.org/cve-2026-9999",
                "published_at": "2026-10-01T00:00:00+00:00",
                "freshness": "in_window",
                "source_channel": "web-search",
            }
        ],
        "ok",
        None,
    )

    # Reddit fails with HTTP 500 / timeout
    mock_reddit.return_value = (
        [],
        "inaccessible",
        "reddit_http_error_500",
    )

    res = execute_temporal_research(
        goal="CVE-2026-9999",
        channels=["web-search", "reddit"],
        reference_time=ref,
    )

    assert len(res.coverage) == 2
    web_cov = res.coverage[0]
    assert web_cov.channel == "web-search"
    assert web_cov.status == "ok"
    assert web_cov.result_count == 1

    reddit_cov = res.coverage[1]
    assert reddit_cov.channel == "reddit"
    assert reddit_cov.status == "inaccessible"
    assert reddit_cov.result_count == 0
    assert "500" in (reddit_cov.reason or "")

    # Findings from healthy channel are preserved without crashing
    assert len(res.findings) == 1
    assert res.findings[0].url == "https://cve.mitre.org/cve-2026-9999"


def test_execute_temporal_research_gated_and_unsupported_channels():
    ref = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

    res = execute_temporal_research(
        goal="Market predictions",
        channels=["x-twitter", "polymarket", "unsupported_xyz"],
        reference_time=ref,
    )

    assert len(res.coverage) == 3
    assert res.coverage[0].status == "bypassed"
    assert res.coverage[0].reason == "skipped_no_credentials"
    assert res.coverage[1].status == "bypassed"
    assert res.coverage[1].reason == "skipped_no_credentials"
    assert res.coverage[2].status == "bypassed"
    assert "unsupported_channel" in (res.coverage[2].reason or "")

    assert len(res.findings) == 0


@pytest.mark.network
def test_smoke_real_nondestructive_web_search():
    """Non-destructive real smoke test against HAOS web_search_tool."""
    res = execute_temporal_research(
        goal="Python programming language official documentation",
        window_spec="last30days",
        channels=["web-search"],
        limit_per_channel=2,
    )

    assert isinstance(res, TemporalResearchResult)
    assert len(res.coverage) == 1
    cov = res.coverage[0]
    assert cov.channel == "web-search"
    # In active HAOS environment, web_search succeeds
    assert cov.status == "ok"
    assert cov.result_count > 0
    assert len(res.findings) > 0
    assert res.findings[0].url is not None
    assert "http" in res.findings[0].url
