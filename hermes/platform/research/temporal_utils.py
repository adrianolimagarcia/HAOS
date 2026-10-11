"""Temporal utilities for research engine: parsing, normalization, and deduplication.
"""
from __future__ import annotations

import datetime
import re
import urllib.parse
from typing import Any, List, Optional, Tuple


def parse_timestamp_to_utc(val: Any) -> Optional[datetime.datetime]:
    """Parse integer/float epoch or date/ISO string deterministically into UTC datetime.

    Never invents or hallucinates dates. Returns None if unparseable.
    """
    if val is None or val == "":
        return None

    # Epoch int or float
    if isinstance(val, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(val), tz=datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(val, str):
        val_clean = val.strip()
        # Epoch passed as string digits
        if re.fullmatch(r"^\d+(\.\d+)?$", val_clean):
            try:
                return datetime.datetime.fromtimestamp(float(val_clean), tz=datetime.timezone.utc)
            except (ValueError, OverflowError, OSError):
                return None

        # ISO 8601 parsing (support trailing 'Z')
        iso_str = val_clean.replace("Z", "+00:00")
        try:
            dt = datetime.datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except ValueError:
            pass

        # Regex patterns for common textual dates
        # e.g., '2026-10-09'
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", val_clean)
        if m:
            try:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return datetime.datetime(year, month, day, 0, 0, 0, tzinfo=datetime.timezone.utc)
            except ValueError:
                pass

        # e.g. 'Oct. 7, 2025' or 'October 7, 2025' or 'Oct 7 2025'
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        m_txt = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", val_clean)
        if m_txt:
            m_name = m_txt.group(1).lower()[:3]
            if m_name in month_map:
                try:
                    month = month_map[m_name]
                    day = int(m_txt.group(2))
                    year = int(m_txt.group(3))
                    return datetime.datetime(year, month, day, 0, 0, 0, tzinfo=datetime.timezone.utc)
                except ValueError:
                    pass

    return None


def normalize_url(raw_url: str) -> str:
    """Normalize URL by stripping tracking parameters, normalizing schemes/hosts, and trailing slashes."""
    if not raw_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return raw_url.strip()

        # Filter out common analytics and tracking parameters
        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_params = [
            (k, v) for k, v in query_params
            if not (k.startswith("utm_") or k in ("fbclid", "gclid", "ref", "source"))
        ]
        new_query = urllib.parse.urlencode(filtered_params)

        path = parsed.path
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")

        normalized = urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))
        return normalized
    except Exception:
        return raw_url.strip()


def evaluate_freshness(
    published_dt: Optional[datetime.datetime],
    start_utc_dt: datetime.datetime,
    end_utc_dt: datetime.datetime,
) -> str:
    """Evaluate temporal freshness against absolute UTC boundaries.

    Returns:
      'in_window' if start_utc <= published_dt <= end_utc
      'historical_context' if published_dt < start_utc
      'unverified' if published_dt is None or published_dt > end_utc (future/clock skew)
    """
    if published_dt is None:
        return "unverified"

    if published_dt.tzinfo is None:
        published_dt = published_dt.replace(tzinfo=datetime.timezone.utc)
    else:
        published_dt = published_dt.astimezone(datetime.timezone.utc)

    if start_utc_dt <= published_dt <= end_utc_dt:
        return "in_window"
    elif published_dt < start_utc_dt:
        return "historical_context"
    else:
        # Future timestamp relative to cutoff: marked as unverified to avoid hallucinated future dates
        return "unverified"
