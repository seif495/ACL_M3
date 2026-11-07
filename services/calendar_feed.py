"""Deadline calendar: GET /api/calendar/deadlines.ics, one event per remaining gameweek deadline
(from meta.json's `events`), each with a 24-hour and a 1-hour alert. Subscribed to by URL
(webcal://), so calendars pick up changed deadlines on their own: UIDs stay stable per gameweek."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from services import season_service as ss
from services.seo import DEFAULT_SITE_URL

PRODID = "-//Pitch Me//FPL deadlines//EN"
EVENT_LENGTH = timedelta(minutes=30)
CACHE_CONTROL = "public, max-age=3600"


def escape_text(value: Any) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma and newlines."""
    s = str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(line: str) -> List[str]:
    """Split a content line into physical lines of at most 75 octets (continuations start with a
    space, which counts), never inside a UTF-8 character."""
    out: List[str] = []
    cur, size, limit = "", 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append(cur)
            cur, size, limit = " ", 1, 75
        cur += ch
        size += n
    out.append(cur)
    return out


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def deadlines_ics(events: Iterable[Dict[str, Any]], site_url: Optional[str] = None, *,
                  season: str = ss.CURRENT_SEASON, now: Optional[datetime] = None) -> str:
    """The calendar text (CRLF line endings). Events without a readable deadline, and deadlines
    already past, are skipped."""
    now = now or datetime.now(timezone.utc)
    url = (site_url or DEFAULT_SITE_URL).rstrip("/")
    lines: List[str] = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text('FPL deadlines (Pitch Me)')}",
        f"X-WR-CALDESC:{escape_text('Fantasy Premier League gameweek deadlines')}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H",
    ]
    rows = []
    for e in events or []:
        start = ss.to_datetime(e.get("deadline_time"))
        try:
            gw = int(e.get("id"))
        except (TypeError, ValueError):
            continue
        if start is not None and start > now:
            rows.append((start, gw))
    for start, gw in sorted(rows):
        lines += [
            "BEGIN:VEVENT",
            f"UID:gw{gw}-{season}@pitchme",
            f"DTSTAMP:{_stamp(now)}",
            f"DTSTART:{_stamp(start)}",
            f"DTEND:{_stamp(start + EVENT_LENGTH)}",
            f"SUMMARY:{escape_text(f'FPL Gameweek {gw} deadline')}",
            f"DESCRIPTION:{escape_text(f'Set your team before the Gameweek {gw} deadline. Predictions: {url}/predictions')}",
            f"URL:{url}/predictions",
            "TRANSP:TRANSPARENT",
        ]
        for trigger, label in (("-P1D", "24 hours"), ("-PT1H", "1 hour")):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      f"DESCRIPTION:{escape_text(f'Gameweek {gw} deadline in {label}')}",
                      f"TRIGGER:{trigger}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(p + "\r\n" for line in lines for p in fold(line))


def register(app) -> None:
    """Adds GET /api/calendar/deadlines.ics (plain def: FastAPI runs it in its threadpool)."""
    from fastapi import Request, Response

    from services.seo import site_origin

    @app.get("/api/calendar/deadlines.ics")
    def calendar_deadlines(request: Request):
        meta = ss.read_meta()
        body = deadlines_ics(meta.get("events") or [], site_origin(request.headers, request.url.scheme))
        return Response(content=body, media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": CACHE_CONTROL,
                                 "Content-Disposition": 'inline; filename="pitchme-deadlines.ics"'})
