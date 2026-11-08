from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

# Server name
mcp = FastMCP("dateparser-mcp")


def _normalize_settings(settings: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize settings dict for dateparser, especially RELATIVE_BASE."""
    if not settings:
        return settings
    s = dict(settings)

    # Allow RELATIVE_BASE as:
    # - ISO 8601 string (e.g., "2020-01-02T03:04:05")
    # - date-only ISO string ("2020-01-02")
    # - UNIX timestamp (int/float, seconds)
    # - dict with keys {year, month, day, hour?, minute?, second?, microsecond?}
    rb = s.get("RELATIVE_BASE")
    if rb is not None and not isinstance(rb, _dt.datetime):
        parsed: Optional[_dt.datetime] = None
        if isinstance(rb, str):
            try:
                # Support date-only by adding midnight
                if "T" not in rb and len(rb) in (10,):  # "YYYY-MM-DD"
                    parsed = _dt.datetime.fromisoformat(rb + "T00:00:00")
                else:
                    parsed = _dt.datetime.fromisoformat(rb)
            except Exception:
                parsed = None
        elif isinstance(rb, (int, float)):
            try:
                parsed = _dt.datetime.fromtimestamp(float(rb))
            except Exception:
                parsed = None
        elif isinstance(rb, dict):
            try:
                y = int(rb.get("year"))
                m = int(rb.get("month"))
                d = int(rb.get("day"))
                hh = int(rb.get("hour", 0))
                mm = int(rb.get("minute", 0))
                ss = int(rb.get("second", 0))
                us = int(rb.get("microsecond", 0))
                parsed = _dt.datetime(y, m, d, hh, mm, ss, us)
            except Exception:
                parsed = None

        if parsed is not None:
            s["RELATIVE_BASE"] = parsed
        else:
            # If unrecognized, drop RELATIVE_BASE to avoid errors downstream
            s.pop("RELATIVE_BASE", None)

    return s


def _dt_to_iso(dt: _dt.datetime) -> str:
    """Convert datetime to ISO-8601 string with offset when available."""
    # Ensure timezone-aware objects keep their offset representation
    return dt.isoformat()


@mcp.tool()
def parse_date(
    text: str,
    languages: Optional[List[str]] = None,
    locales: Optional[List[str]] = None,
    date_formats: Optional[List[str]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """
    Parse a human-readable date/time string into a datetime (ISO-8601).
    - text: Input date/time string (any supported language/format).
    - languages: Optional list of ISO language codes to restrict parsing (e.g., ["en", "fr"]).
    - locales: Optional list of locale codes to restrict parsing (e.g., ["en-US", "fr-FR"]).
    - date_formats: Optional list of strptime-like custom formats to try first.
    - settings: Optional dateparser settings dict (e.g., {"DATE_ORDER": "DMY"}).
      RELATIVE_BASE may be provided as ISO string, UNIX timestamp, or a dict.
    Returns: {"datetime": ISO-8601 string or None}
    """
    import dateparser  # Imported lazily to keep module load lightweight

    norm_settings = _normalize_settings(settings)
    dt = dateparser.parse(
        text,
        date_formats=date_formats,
        languages=languages,
        locales=locales,
        settings=norm_settings,
    )
    return {"datetime": _dt_to_iso(dt) if dt is not None else None}


@mcp.tool()
def search_dates(
    text: str,
    languages: Optional[List[str]] = None,
    locales: Optional[List[str]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    Find all date/time expressions in a longer text.
    - text: Input text to search.
    - languages: Optional list of ISO language codes to restrict detection (recommended for short texts).
    - locales: Optional list of locale codes to restrict detection.
    - settings: Optional dateparser settings dict (e.g., {"RETURN_TIME_SPAN": True}).
    Returns: list of {"text": matched_substring, "datetime": ISO-8601 string}
             Empty list if no dates are found.
    """
    from dateparser.search import search_dates as _search_dates  # lazy import

    norm_settings = _normalize_settings(settings)
    pairs: Optional[List[Tuple[str, _dt.datetime]]] = _search_dates(
        text, languages=languages, locales=locales, settings=norm_settings
    )
    if not pairs:
        return []
    return [{"text": s, "datetime": _dt_to_iso(d)} for s, d in pairs]


def _self_test() -> None:
    # Minimal offline smoke test using documented functions
    import dateparser
    from dateparser.search import search_dates as _search_dates

    # Basic parse
    dt = dateparser.parse("2014-10-12")
    if not dt or not dt.isoformat():
        raise RuntimeError("dateparser.parse returned empty result")

    # Basic search_dates
    pairs = _search_dates("We met on 2017-01-02 and again on 2018-03-04.")
    if not pairs or not all(isinstance(p[0], str) and hasattr(p[1], "isoformat") for p in pairs):
        raise RuntimeError("search_dates returned empty or invalid result")

    print("Self-test OK:", dt.isoformat(), " | found:", len(pairs), "dates")


if __name__ == "__main__":
    _self_test()