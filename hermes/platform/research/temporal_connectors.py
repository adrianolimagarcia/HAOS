"""Connectors for external research sources in HAOS temporal engine.

All connectors enforce:
1. Deterministic auditability: verifiable source URLs and UTC timestamps.
2. Honest reporting: status is 'ok' only on verified retrieval, 'empty' on 0 results,
   'inaccessible' on network/API failure, 'bypassed' on unconfigured/gated channels.
3. Fail-closed: exceptions are caught per-channel, returning typed error reasons
   without taking down the entire research sweep.
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from hermes.platform.research.temporal_utils import (
    evaluate_freshness,
    normalize_url,
    parse_timestamp_to_utc,
)

logger = logging.getLogger("hermes.platform.research.temporal_connectors")


class RawResearchItem:
    """Intermediate container for items retrieved from a source channel."""

    def __init__(
        self,
        title: str,
        url: str,
        snippet: str = "",
        published_at_raw: Any = None,
        source_channel: str = "",
    ):
        self.title = title.strip()
        self.url = normalize_url(url)
        self.snippet = snippet.strip()
        self.published_at_raw = published_at_raw
        self.source_channel = source_channel

    def to_finding_dict(
        self,
        start_utc_dt: datetime.datetime,
        end_utc_dt: datetime.datetime,
    ) -> Dict[str, Any]:
        parsed_dt = parse_timestamp_to_utc(self.published_at_raw)
        published_at_iso = parsed_dt.isoformat() if parsed_dt else None
        freshness = evaluate_freshness(parsed_dt, start_utc_dt, end_utc_dt)

        claim = self.title
        if self.snippet and self.snippet != self.title:
            claim = f"{self.title}: {self.snippet}"

        return {
            "claim": claim,
            "url": self.url,
            "published_at": published_at_iso,
            "freshness": freshness,
            "source_channel": self.source_channel,
        }


def search_web(
    query: str,
    *,
    limit: int = 5,
    start_utc_dt: datetime.datetime,
    end_utc_dt: datetime.datetime,
) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    """Execute web search via HAOS web_search_tool.

    Returns:
        (items, status, error_reason)
    """
    try:
        import importlib
        mod = importlib.import_module("tools.web_tools")
        web_search_tool = getattr(mod, "web_search_tool", None)
        if web_search_tool is None:
            return [], "inaccessible", "web_search_tool_not_found"
    except Exception as ie:
        return [], "inaccessible", f"web_search_tool_import_failed: {ie}"

    try:
        raw_res = web_search_tool(query, limit=limit)
        if isinstance(raw_res, str):
            data = json.loads(raw_res)
        elif isinstance(raw_res, dict):
            data = raw_res
        else:
            return [], "inaccessible", "unexpected_response_format"

        if not data.get("success", False) and "error" in data:
            return [], "inaccessible", f"web_search_error: {data.get('error')}"

        web_items = data.get("data", {}).get("web", [])
        if not web_items:
            return [], "empty", None

        findings: List[Dict[str, Any]] = []
        for item in web_items:
            url = item.get("url") or ""
            title = item.get("title") or ""
            desc = item.get("description") or ""
            published = item.get("published")

            # If published is not explicit, see if description contains date
            if not published:
                published = desc

            raw_item = RawResearchItem(
                title=title,
                url=url,
                snippet=desc,
                published_at_raw=published,
                source_channel="web-search",
            )
            findings.append(raw_item.to_finding_dict(start_utc_dt, end_utc_dt))

        return findings, "ok", None
    except Exception as e:
        logger.warning(f"web-search connector failed: {e}")
        return [], "inaccessible", f"web_search_exception: {str(e)}"


def search_reddit(
    query: str,
    *,
    limit: int = 5,
    start_utc_dt: datetime.datetime,
    end_utc_dt: datetime.datetime,
) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    """Execute search on Reddit using direct HAOS cookies/session if available, or public API.

    Returns:
        (items, status, error_reason)
    """
    # 1. Try local HAOS direct server session if available
    try:
        import importlib
        import sys
        if "/root/.haos/mcp/haos-direct" not in sys.path:
            sys.path.insert(0, "/root/.haos/mcp/haos-direct")
        haos_direct_server = importlib.import_module("server")
        jar, _ = haos_direct_server._session("reddit", "www.reddit.com")
        status, resp_data = haos_direct_server._reddit_get(
            f"/search.json?q={urllib.parse.quote(query)}&limit={limit}&sort=relevance",
            jar,
        )
        if status == 200 and isinstance(resp_data, dict):
            children = resp_data.get("data", {}).get("children", [])
            if not children:
                return [], "empty", None
            findings: List[Dict[str, Any]] = []
            for child in children:
                pdata = child.get("data", {})
                title = pdata.get("title") or ""
                permalink = pdata.get("permalink") or ""
                url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else (pdata.get("url") or "")
                selftext = pdata.get("selftext") or ""
                created_utc = pdata.get("created_utc")

                raw_item = RawResearchItem(
                    title=title,
                    url=url,
                    snippet=selftext[:200] if selftext else f"Subreddit: r/{pdata.get('subreddit')}",
                    published_at_raw=created_utc,
                    source_channel="reddit",
                )
                findings.append(raw_item.to_finding_dict(start_utc_dt, end_utc_dt))
            return findings, "ok", None
    except Exception as e:
        logger.debug(f"HAOS direct reddit_read failed or unavailable, falling back to public Reddit JSON: {e}")

    # 2. Public API fallback with clean headers
    try:
        encoded_q = urllib.parse.quote(query)
        req_url = f"https://www.reddit.com/search.json?q={encoded_q}&sort=relevance&limit={limit}"
        req = urllib.request.Request(
            req_url,
            headers={
                "User-Agent": "HAOS-TemporalEngine/1.0 (Civilization Research Bot)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            children = data.get("data", {}).get("children", [])
            if not children:
                return [], "empty", None
            findings = []
            for child in children:
                pdata = child.get("data", {})
                title = pdata.get("title") or ""
                permalink = pdata.get("permalink") or ""
                url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else (pdata.get("url") or "")
                selftext = pdata.get("selftext") or ""
                created_utc = pdata.get("created_utc")

                raw_item = RawResearchItem(
                    title=title,
                    url=url,
                    snippet=selftext[:200] if selftext else f"Subreddit: r/{pdata.get('subreddit')}",
                    published_at_raw=created_utc,
                    source_channel="reddit",
                )
                findings.append(raw_item.to_finding_dict(start_utc_dt, end_utc_dt))
            return findings, "ok", None
    except urllib.error.HTTPError as he:
        return [], "inaccessible", f"reddit_http_error_{he.code}"
    except Exception as e:
        return [], "inaccessible", f"reddit_exception: {str(e)}"


def search_hackernews(
    query: str,
    *,
    limit: int = 5,
    start_utc_dt: datetime.datetime,
    end_utc_dt: datetime.datetime,
) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    """Execute search on Hacker News via Algolia API.

    Returns:
        (items, status, error_reason)
    """
    try:
        encoded_q = urllib.parse.quote(query)
        req_url = f"https://hn.algolia.com/api/v1/search?query={encoded_q}&hitsPerPage={limit}"
        req = urllib.request.Request(
            req_url,
            headers={
                "User-Agent": "HAOS-TemporalEngine/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            hits = data.get("hits", [])
            if not hits:
                return [], "empty", None

            findings: List[Dict[str, Any]] = []
            for hit in hits:
                title = hit.get("title") or hit.get("story_title") or ""
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                created_at = hit.get("created_at")

                raw_item = RawResearchItem(
                    title=title,
                    url=url,
                    snippet=f"HN Points: {hit.get('points', 0)}",
                    published_at_raw=created_at,
                    source_channel="hackernews",
                )
                findings.append(raw_item.to_finding_dict(start_utc_dt, end_utc_dt))
            return findings, "ok", None
    except Exception as e:
        logger.warning(f"hackernews connector failed: {e}")
        return [], "inaccessible", f"hackernews_exception: {str(e)}"
