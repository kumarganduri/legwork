import os
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

# icalendar library
from icalendar import Calendar, Event, vCalAddress, vText
from dateutil import parser as dateparser


mcp = FastMCP("icalendar_mcp")


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        # Try icalendar decoded value (e.g., bytes to string)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        # icalendar v* objects often have to_ical
        to_ical = getattr(value, "to_ical", None)
        if callable(to_ical):
            ical_bytes = to_ical()
            if isinstance(ical_bytes, bytes):
                return ical_bytes.decode("utf-8", errors="replace")
        return str(value)
    except Exception:
        return str(value)


def _extract_attendees(component: Any) -> List[str]:
    attendees_raw = component.get("attendee")
    attendees: List[str] = []
    if not attendees_raw:
        return attendees
    if not isinstance(attendees_raw, list):
        attendees_raw = [attendees_raw]
    for a in attendees_raw:
        s = str(a)
        if s:
            attendees.append(s)
    return attendees


def _extract_rrule(component: Any) -> Optional[Dict[str, Any]]:
    r = component.get("rrule")
    if not r:
        return None
    # r is usually a dict-like with lists as values
    try:
        # Convert values to lists of strings for JSON friendliness
        out: Dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, (list, tuple)):
                out[str(k)] = [str(x) for x in v]
            else:
                out[str(k)] = str(v)
        return out
    except Exception:
        # Fallback to string form
        s = _to_iso(r)
        return {"raw": s} if s else None


def _parse_calendar(ical_text: str) -> Dict[str, Any]:
    cal = Calendar.from_ical(ical_text)
    cal_info: Dict[str, Any] = {}
    # A few common calendar-level properties if present
    for key in ("prodid", "version", "x-wr-calname", "x-wr-timezone"):
        val = cal.get(key)
        if val is not None:
            cal_info[key] = _to_iso(val)

    events: List[Dict[str, Any]] = []
    for comp in cal.walk("vevent"):
        ev: Dict[str, Any] = {}
        # Common event fields
        for key in (
            "uid",
            "summary",
            "description",
            "location",
            "status",
            "categories",
            "url",
            "organizer",
            "created",
            "last-modified",
            "dtstamp",
            "priority",
            "transp",
            "class",
        ):
            val = comp.get(key)
            if val is not None:
                # dates/times: try decoded then iso
                if key in ("created", "last-modified", "dtstamp"):
                    try:
                        val = comp.decoded(key.replace("-", ""))
                    except Exception:
                        pass
                ev[key.replace("-", "_")] = _to_iso(val)

        # Start/end/tz/rrule/attendees
        for dtkey in ("dtstart", "dtend", "due"):
            if comp.get(dtkey) is not None:
                try:
                    dtv = comp.decoded(dtkey)
                except Exception:
                    dtv = comp.get(dtkey)
                ev[dtkey] = _to_iso(dtv)

        atts = _extract_attendees(comp)
        if atts:
            ev["attendees"] = atts

        rrule = _extract_rrule(comp)
        if rrule:
            ev["rrule"] = rrule

        # Only add non-empty
        if ev:
            events.append(ev)

    return {"calendar": cal_info, "events": events}


def _parse_dt(value: Optional[str]) -> Optional[Any]:
    if not value:
        return None
    # Accept ISO strings (date or datetime); returns datetime or date
    dt = dateparser.parse(value)
    # If it is midnight and has no time info and original had only date, keep date
    return dt


@mcp.tool()
def parse_ics_text(ics_text: str) -> Dict[str, Any]:
    """
    Parse iCalendar (.ics) content and return a summary of calendar-level properties and VEVENTs.
    Returns dict: { "calendar": {...}, "events": [ {summary, dtstart, dtend, ...}, ... ] }
    """
    return _parse_calendar(ics_text)


@mcp.tool()
def parse_ics_file(path: str) -> Dict[str, Any]:
    """
    Parse an iCalendar (.ics) file from disk and return a summary of calendar-level properties and VEVENTs.
    """
    with open(path, "rb") as f:
        data = f.read()
    return _parse_calendar(data.decode("utf-8", errors="replace"))


def _ensure_calendar_base(cal: Calendar, prodid: Optional[str] = None, version: Optional[str] = None) -> None:
    if prodid is None:
        prodid = "-//icalendar_mcp//EN"
    if version is None:
        version = "2.0"
    if cal.get("prodid") is None:
        cal.add("prodid", prodid)
    if cal.get("version") is None:
        cal.add("version", version)


def _build_event(
    summary: str,
    dtstart: str,
    dtend: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    organizer: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    uid: Optional[str] = None,
) -> Event:
    ev = Event()
    ev.add("summary", vText(summary))

    start_parsed = _parse_dt(dtstart)
    if start_parsed is None:
        raise ValueError("dtstart could not be parsed")
    ev.add("dtstart", start_parsed)

    if dtend:
        end_parsed = _parse_dt(dtend)
        if end_parsed is None:
            raise ValueError("dtend could not be parsed")
        ev.add("dtend", end_parsed)

    if description:
        ev.add("description", vText(description))
    if location:
        ev.add("location", vText(location))
    if organizer:
        # Organizer should be a URI, e.g., MAILTO:host@example.com
        org = organizer if organizer.lower().startswith("mailto:") else f"MAILTO:{organizer}"
        ev.add("organizer", vCalAddress(org))

    if attendees:
        for a in attendees:
            addr = a if a.lower().startswith("mailto:") else f"MAILTO:{a}"
            vaddr = vCalAddress(addr)
            ev.add("attendee", vaddr, encode=0)

    if uid:
        ev.add("uid", uid)
    else:
        ev.add("uid", str(uuid.uuid4()))
    return ev


@mcp.tool()
def create_ics_file(
    summary: str,
    dtstart: str,
    dtend: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    organizer: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    prodid: Optional[str] = None,
    version: Optional[str] = None,
    out_path: Optional[str] = None,
) -> str:
    """
    Create a new iCalendar (.ics) file with a single VEVENT.
    - dtstart/dtend: ISO 8601 strings (date or datetime), e.g. "2026-12-25" or "2026-12-25T14:00:00"
    - attendees: list of emails or mailto: URIs
    Returns the absolute path to the written .ics file.
    """
    cal = Calendar()
    _ensure_calendar_base(cal, prodid=prodid, version=version)

    ev = _build_event(
        summary=summary,
        dtstart=dtstart,
        dtend=dtend,
        description=description,
        location=location,
        organizer=organizer,
        attendees=attendees,
    )
    cal.add_component(ev)

    if not out_path:
        out_path = f"./event_{uuid.uuid4().hex}.ics"
    with open(out_path, "wb") as f:
        f.write(cal.to_ical())
    return os.path.abspath(out_path)


@mcp.tool()
def append_event_to_ics_file(
    ics_path: str,
    summary: str,
    dtstart: str,
    dtend: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    organizer: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    backup: bool = True,
) -> str:
    """
    Append a VEVENT to an existing .ics file. If file doesn't exist, it is created.
    Returns the absolute path to the updated .ics file.
    """
    cal: Calendar
    if os.path.exists(ics_path):
        with open(ics_path, "rb") as f:
            data = f.read()
        try:
            cal = Calendar.from_ical(data)
        except Exception:
            # If parsing fails, start a new calendar
            cal = Calendar()
    else:
        cal = Calendar()

    _ensure_calendar_base(cal)

    ev = _build_event(
        summary=summary,
        dtstart=dtstart,
        dtend=dtend,
        description=description,
        location=location,
        organizer=organizer,
        attendees=attendees,
    )
    cal.add_component(ev)

    if backup and os.path.exists(ics_path):
        bak = f"{ics_path}.bak"
        with open(bak, "wb") as f:
            f.write(data)

    with open(ics_path, "wb") as f:
        f.write(cal.to_ical())

    return os.path.abspath(ics_path)


if __name__ == "__main__":
    # Self-test: create a simple calendar and ensure .to_ical() returns non-empty bytes.
    from icalendar import Calendar, Event
    from datetime import datetime

    cal = Calendar()
    cal.add("prodid", "-//icalendar_mcp self-test//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("summary", "Self-test event")
    ev.add("dtstart", datetime.now())
    cal.add_component(ev)
    data = cal.to_ical()
    if not data:
        raise SystemExit("Self-test failed: empty iCalendar data")
    print("Self-test OK:", len(data), "bytes")