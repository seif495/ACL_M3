"""Deadline calendar feed (services/calendar_feed.py)."""
import os
import sys
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import calendar_feed as cf  # noqa: E402
from services import season_service as ss  # noqa: E402

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def ev(gw, delta_days):
    return {"id": gw, "deadline_time": (NOW + timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_structure_and_crlf():
    text = cf.deadlines_ics([ev(10, 1), ev(11, 8)], "https://example.com", now=NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT") == 2
    assert text.count("BEGIN:VALARM") == 4
    assert "TRIGGER:-PT1H" in text and "TRIGGER:-P1D" in text
    assert "UID:gw10-2026-27@pitchme" in text and "DTSTAMP:20261106T120000Z" in text
    assert "DTSTART:20261107T120000Z" in text


def test_past_and_bad_deadlines_skipped():
    events = [ev(9, -1), {"id": 12, "deadline_time": None}, {"id": "x", "deadline_time": "2027-01-01T00:00:00Z"}, ev(10, 2)]
    text = cf.deadlines_ics(events, None, now=NOW)
    assert text.count("BEGIN:VEVENT") == 1 and "gw10-" in text and "gw9-" not in text


def test_escaping():
    assert cf.escape_text("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_folding_75_octets():
    long = "SUMMARY:" + "é" * 100
    parts = cf.fold(long)
    assert len(parts) > 1
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert all(p.startswith(" ") for p in parts[1:])
    assert "".join(parts[:1] + [p[1:] for p in parts[1:]]) == long
    assert all(len(l.encode("utf-8")) <= 75 for l in cf.deadlines_ics([ev(10, 1)], "https://x.test/" + "a" * 90, now=NOW).split("\r\n"))


def test_route(monkeypatch):
    monkeypatch.setattr(ss, "read_meta", lambda season=ss.CURRENT_SEASON: {"events": [
        {"id": 38, "deadline_time": "2099-05-01T10:00:00Z"}]})
    app = FastAPI()
    cf.register(app)
    r = TestClient(app).get("/api/calendar/deadlines.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert "gw38-" in r.text and "DTSTART:20990501T100000Z" in r.text
