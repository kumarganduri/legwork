import os
from typing import List, Dict, Optional, Tuple

import phonenumbers
from phonenumbers import geocoder, carrier, timezone
from phonenumbers.phonenumberutil import NumberParseException

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("phonenumbers_mcp")


def _parse(number: str, region: Optional[str]) -> phonenumbers.PhoneNumber:
    try:
        return phonenumbers.parse(number, None if not region else region.upper())
    except NumberParseException as e:
        raise ValueError(f"Could not parse number: {e}")


def _format_all(n: phonenumbers.PhoneNumber) -> Dict[str, str]:
    return {
        "E164": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164),
        "INTERNATIONAL": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
        "NATIONAL": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.NATIONAL),
        "RFC3966": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.RFC3966),
    }


@mcp.tool()
def parse_number(number: str, region: Optional[str] = None) -> Dict:
    """
    Parse a phone number string into structured data, and include common formatted representations.
    - number: The input phone number string (may be E.164 like "+442083661177" or national like "020 8366 1177").
    - region: Optional 2-letter region code for parsing national numbers (e.g., "GB", "US").
    """
    n = _parse(number, region)
    info = {
        "country_code": n.country_code,
        "national_number": n.national_number,
        "extension": getattr(n, "extension", None) or "",
        "italian_leading_zero": bool(getattr(n, "italian_leading_zero", False)),
        "region_code": phonenumbers.region_code_for_number(n) or "",
        "is_possible": phonenumbers.is_possible_number(n),
        "is_valid": phonenumbers.is_valid_number(n),
        "formats": _format_all(n),
    }
    return info


@mcp.tool()
def format_number(number: str, fmt: str, region: Optional[str] = None) -> str:
    """
    Format a phone number into a specified format: one of "E164", "INTERNATIONAL", "NATIONAL", "RFC3966".
    - number: The input phone number string.
    - fmt: Desired output format name.
    - region: Optional region for parsing national numbers.
    """
    n = _parse(number, region)
    fmt_upper = fmt.upper().strip()
    fmt_map = {
        "E164": phonenumbers.PhoneNumberFormat.E164,
        "INTERNATIONAL": phonenumbers.PhoneNumberFormat.INTERNATIONAL,
        "NATIONAL": phonenumbers.PhoneNumberFormat.NATIONAL,
        "RFC3966": phonenumbers.PhoneNumberFormat.RFC3966,
    }
    if fmt_upper not in fmt_map:
        raise ValueError(f"Unknown format '{fmt}'. Use one of {sorted(fmt_map.keys())}.")
    return phonenumbers.format_number(n, fmt_map[fmt_upper])


@mcp.tool()
def validate_number(number: str, region: Optional[str] = None) -> Dict[str, bool]:
    """
    Validate whether a number is possible and/or valid.
    - number: The input phone number string.
    - region: Optional region for parsing national numbers.
    """
    n = _parse(number, region)
    return {
        "is_possible": phonenumbers.is_possible_number(n),
        "is_valid": phonenumbers.is_valid_number(n),
    }


@mcp.tool()
def find_numbers(text: str, region: Optional[str] = None) -> List[Dict]:
    """
    Find phone numbers in a block of text.
    - text: Input text to scan.
    - region: Optional default region for parsing ambiguous/national numbers (e.g., "US").
    """
    matches = []
    matcher = phonenumbers.PhoneNumberMatcher(text, None if not region else region.upper())
    for m in matcher:
        n = m.number
        matches.append({
            "start": m.start,
            "end": m.end,
            "raw_string": getattr(m, "raw_string", text[m.start:m.end]),
            "region_code": phonenumbers.region_code_for_number(n) or "",
            "formats": _format_all(n),
        })
    return matches


@mcp.tool()
def geocode_number(number: str, region: Optional[str] = None, locale: str = "en") -> Dict[str, str]:
    """
    Get a human-readable location description for a number (when available).
    - number: The input phone number string.
    - region: Optional region for parsing national numbers.
    - locale: Output locale code (e.g., "en", "de", "fr", "it").
    """
    n = _parse(number, region)
    desc = geocoder.description_for_number(n, locale)
    return {"description": desc or "", "locale": locale}


@mcp.tool()
def carrier_name(number: str, region: Optional[str] = None, locale: str = "en") -> Dict[str, str]:
    """
    Get the original carrier name for a number (when available).
    - number: The input phone number string.
    - region: Optional region for parsing national numbers.
    - locale: Output locale code (e.g., "en").
    """
    n = _parse(number, region)
    name = carrier.name_for_number(n, locale)
    return {"carrier": name or "", "locale": locale}


@mcp.tool()
def time_zones(number: str, region: Optional[str] = None) -> List[str]:
    """
    Get possible time zone names for a number.
    - number: The input phone number string.
    - region: Optional region for parsing national numbers.
    """
    n = _parse(number, region)
    tzs = timezone.time_zones_for_number(n)
    return list(tzs)


if __name__ == "__main__":
    # Offline self-test: exercise a few simple, documented operations and ensure they return something non-empty.
    res1 = format_number("+442083661177", "E164")
    if not res1:
        raise SystemExit("format_number returned empty")

    res2 = parse_number("+12001230101")
    if not res2:
        raise SystemExit("parse_number returned empty")

    res3 = geocode_number("0431234567", region="CH", locale="en")
    if not res3:
        raise SystemExit("geocode_number returned empty")

    res4 = validate_number("+12001230101")
    if not res4:
        raise SystemExit("validate_number returned empty")

    print("phonenumbers MCP self-test passed.")