"""News (services/news.py): Guardian headlines with cache, budget and fallback; FPL news."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from services import news

PAYLOAD = {"response": {"results": [
    {"id": "a", "webTitle": "Arsenal win &amp; go top", "webUrl": "https://www.theguardian.com/a",
     "webPublicationDate": "2026-10-31T18:00:00Z",
     "fields": {"thumbnail": "https://media.guim.co.uk/x.jpg", "trailText": "<p>Saka <b>scores</b></p>", "byline": "A Writer"}},
    {"id": "b", "webTitle": "No link"},
]}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GUARDIAN_API_KEY", "k")
    news.reset()
    yield
    news.reset()


def test_parse_keeps_what_the_site_shows():
    a = news.parse_articles(PAYLOAD)
    assert len(a) == 1 and a[0]["title"] == "Arsenal win & go top" and a[0]["summary"] == "Saka scores"
    assert a[0]["thumbnail"].startswith("https://") and a[0]["source"] == "The Guardian"


def test_cached_then_refetched_after_the_ttl(tmp_path):
    calls = []
    fetch = lambda p: calls.append(p) or PAYLOAD  # noqa: E731
    assert news.guardian_articles(fetch=fetch, now=1000.0)["articles"][0]["id"] == "a"
    news.guardian_articles(fetch=fetch, now=1000.0 + news.FEED_TTL - 1)
    assert len(calls) == 1 and calls[0]["tag"] == "football/premierleague" and calls[0]["api-key"] == "k"
    news.guardian_articles(fetch=fetch, now=1000.0 + news.FEED_TTL + 1)
    assert len(calls) == 2
    assert os.path.exists(tmp_path / news.CACHE_FILE)


def test_club_query_uses_the_full_name():
    calls = []
    news.guardian_articles("Man City", fetch=lambda p: calls.append(p) or PAYLOAD, now=5.0)
    assert calls[0]["q"] == '"Manchester City"' and "tag" not in calls[0]


def test_failure_serves_the_last_good_headlines_and_survives_a_restart():
    news.guardian_articles(fetch=lambda p: PAYLOAD, now=10.0)
    news.reset()                                          # a restart: memory gone, disk kept

    def down(p):
        raise OSError("429")

    out = news.guardian_articles(fetch=down, now=10.0 + news.FEED_TTL + 5)
    assert out["stale"] is True and out["articles"][0]["id"] == "a"


def test_budget_stops_calls_and_falls_back(monkeypatch):
    monkeypatch.setattr(news, "DAILY_BUDGET", 1)
    calls = []
    fetch = lambda p: calls.append(p) or PAYLOAD  # noqa: E731
    news.guardian_articles(fetch=fetch, now=100.0)
    out = news.guardian_articles("Arsenal", fetch=fetch, now=200.0)
    assert len(calls) == 1 and out["articles"] == []      # nothing cached for Arsenal yet
    nxt = 100.0 + 86400                                   # the next UTC day resets the count
    news.guardian_articles("Arsenal", fetch=fetch, now=nxt)
    assert len(calls) == 2


def test_no_key_means_no_calls(monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_KEY")
    out = news.guardian_articles(fetch=lambda p: pytest.fail("called"), now=1.0)
    assert out == {"articles": [], "stale": False, "fetched_at": None}


def test_fpl_news():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    rows = [
        {"name": "Bukayo Saka", "web_name": "Saka", "team": "Arsenal", "status": "d", "news": "Knock - 75% chance",
         "news_added": (now - timedelta(days=1)).isoformat(), "chance_of_playing_next_round": 75, "code": 1},
        {"name": "Back", "web_name": "Back", "team": "Arsenal", "status": "a", "news": float("nan"),
         "news_added": (now - timedelta(hours=2)).isoformat()},
        {"name": "Old", "team": "Chelsea", "status": "i", "news": "Knee", "news_added": (now - timedelta(days=40)).isoformat()},
        {"name": "Riser", "team": "Chelsea", "status": "a", "cost_change_event": 1, "now_cost": 81, "selected_by_percent": "30.1"},
        {"name": "Faller", "team": "Arsenal", "status": "a", "cost_change_event": -2, "now_cost": 60, "selected_by_percent": "2"},
    ]
    out = news.fpl_news(rows, now=now)
    assert [u["name"] for u in out["updates"]] == ["Back", "Bukayo Saka"]
    assert out["updates"][0]["kind"] == "return" and out["updates"][1]["kind"] == "doubt"
    assert [(p["name"], p["change"]) for p in out["prices"]] == [("Faller", -0.2), ("Riser", 0.1)]
    assert [u["name"] for u in news.fpl_news(rows, now=now, team="Chelsea")["updates"]] == []
