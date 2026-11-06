"""Link previews and the data sitemap (services/seo.py). Offline: a fake connection only."""
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from config.settings import CURRENT_SEASON
from services import season_service as ss
from services import seo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGIN = "https://pitch.example.com"
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def row(fid, gw, kickoff, home, away, hs=None, as_=None, status=None):
    return {"id": f"{fid}-{CURRENT_SEASON}", "gameweek": gw, "kickoff_time": kickoff,
            "status": status or ("finished" if hs is not None else "scheduled"), "home_team": home,
            "away_team": away, "home_score": hs, "away_score": as_, "home_difficulty": 3, "away_difficulty": 3}


# Status decides played/unplayed, not the clock, so fixed dates are safe here
FIXTURES = [
    row(1, 1, datetime(2026, 8, 22, 11, 30, tzinfo=timezone.utc), "Arsenal", "Everton", 2, 0),
    row(2, 1, datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc), "Brighton & Hove Albion", "Nott'm Forest", 1, 1),
    row(3, 2, datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc), "Everton", "Brighton & Hove Albion", 0, 3),
    row(4, 2, datetime(2026, 8, 29, 16, 30, tzinfo=timezone.utc), "Nott'm Forest", "Arsenal", 1, 2),
    row(85, 11, datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc), "Everton", "Arsenal"),
    row(86, 11, datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc), "Nott'm Forest", "Brighton & Hove Albion",
        1, 0, status="in_progress"),
    row(87, 12, None, "Arsenal", "Nott'm Forest"),
]
PAST_MATCH = {"id": "23-2024-25", "season": "2024-25", "gameweek": 3,
              "kickoff_time": "2024-08-31T14:00:00+00:00", "status": None, "home_team": "Chelsea",
              "away_team": "Arsenal", "home_score": 1, "away_score": 1}


class FakeConn:
    def __init__(self, fixtures=FIXTURES, by_id=None, fail=False):
        self.fixtures, self.by_id, self.fail = fixtures, by_id or {}, fail
        self.queries = []

    def execute_query(self, query, params=None):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("Neo4j is down")
        if "season_id: $season" in query:
            return [dict(r) for r in self.fixtures]
        if "{id: $id}" in query:
            hit = self.by_id.get(params["id"])
            return [dict(hit)] if hit else []
        return []


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.setenv("SITE_ADDRESS", "pitch.example.com, 1-2-3-4.sslip.io")
    ss.clear_cache()
    yield
    ss.clear_cache()


def meta_for(path, conn=None):
    conn = conn if conn is not None else FakeConn()
    return seo.page_meta(path, lambda: conn)


def tag(document, attr, key):
    """content of <meta {attr}="{key}">, or href of the canonical link."""
    found = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', document)
    return found.group(1) if found else None


# ------------------------------------------------------------------ static pages

def _use_page_meta(file):
    with open(os.path.join(ROOT, "src", "pages", file), encoding="utf-8") as f:
        source = f.read()
    found = re.search(r"usePageMeta\(\{\s*(?:title:\s*(['\"])(.*?)\1,\s*)?description:\s*(['\"])(.*?)\3",
                      source, re.S)
    assert found, f"no usePageMeta call in {file}"
    return found.group(2), found.group(4)


PAGE_FILES = {"/": "Home.tsx", "/fixtures": "Fixtures.tsx", "/table": "LeagueTable.tsx", "/teams": "Teams.tsx",
              "/team-of-the-week": "TeamOfTheWeek.tsx", "/players": "PlayerSearch.tsx",
              "/predictions": "Predictions.tsx", "/model": "ModelInsights.tsx", "/assistant": "QAAssistant.tsx",
              "/compare": "PlayerComparison.tsx", "/trivia": "Trivia.tsx", "/settings": "Settings.tsx",
              "/privacy": "Privacy.tsx", "/terms": "Terms.tsx", "/contact": "Contact.tsx", "/stats": "SiteStats.tsx"}


def test_static_pages_reuse_the_pages_own_titles():
    """seo.PAGES must say what each page sets with usePageMeta: update both together."""
    assert set(seo.PAGES) == set(PAGE_FILES)
    for path, file in PAGE_FILES.items():
        assert seo.PAGES[path] == _use_page_meta(file), path


def test_every_preview_page_is_a_caddy_route():
    with open(os.path.join(ROOT, "deploy", "Caddyfile"), encoding="utf-8") as f:
        caddy = f.read()
    snippet = re.search(r"\(page_routes\)\s*\{\s*path ([^\n]+)\n", caddy)
    assert snippet, "the page_routes snippet"
    routes = set(snippet.group(1).split())
    assert set(seo.PAGES) | set(seo.REDIRECTS) | {"/design", "/teams/*", "/matches/*"} == routes


def test_static_titles_and_descriptions():
    home = meta_for("/")
    assert home.title == "Pitch Me · FPL stats, predictions & AI assistant" and home.path == "/"
    assert home.description.startswith("Gameweek captain pick")
    fixtures = meta_for("/fixtures/")                       # trailing slash and query dropped
    assert fixtures.path == "/fixtures" and fixtures.title == "Fixtures & results · Pitch Me"
    assert meta_for("/table?x=1").title == "Premier League table · Pitch Me"
    assert meta_for("/team-of-the-week").description.startswith("The highest-scoring FPL squad")
    qa = meta_for("/qa")                                    # redirects: the target's page
    assert qa.path == "/assistant" and qa.title == "Assistant · Pitch Me"
    assert meta_for("/search").path == "/players"
    assert meta_for("/stats").noindex and not meta_for("/players").noindex
    design = meta_for("/design")
    assert design.title == seo.SITE_TITLE and design.noindex
    unknown = meta_for("/nowhere")
    assert (unknown.title, unknown.description, unknown.path) == (seo.SITE_TITLE, seo.SITE_DESCRIPTION, "/nowhere")
    assert meta_for(None).path == "/" and meta_for("").path == "/"


def test_static_pages_never_touch_neo4j():
    def no_conn():
        raise AssertionError("static pages don't need the graph")
    assert seo.page_meta("/predictions", no_conn).title == "Predictions · Pitch Me"


# ------------------------------------------------------------------ clubs

def test_club_from_the_table():
    m = meta_for("/teams/arsenal")
    assert m.path == "/teams/arsenal" and m.title == f"Arsenal {CURRENT_SEASON} · Pitch Me"
    # Arsenal: W 2-0, W 2-1 -> 6 points, top; next match Everton away on Sun 8 Nov
    assert m.description == (f"Arsenal's {CURRENT_SEASON} season: 1st with 6 points after 2 matches. "
                             "Next: Everton (A), Sun 8 Nov. Results, fixtures, squad, form and news.")
    everton = meta_for("/teams/everton").description
    assert "4th with 0 points after 2 matches. Next: Arsenal (H), Sun 8 Nov." in everton


def test_club_wording():
    assert [seo.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th"]
    one = {"team": "Spurs", "position": 7, "points": 1, "played": 1, "next_fixture": None}
    assert seo.club_description("Spurs", "2026-27", one) == \
        "Spurs's 2026-27 season: 7th with 1 point after 1 match. Results, fixtures, squad, form and news."
    # Before a ball is kicked: the club page's own description
    fresh = {"team": "Spurs", "position": 1, "points": 0, "played": 0,
             "next_fixture": {"opponent": "Arsenal", "home": True, "kickoff_time": "2026-08-22T14:00:00+00:00"}}
    assert seo.club_description("Spurs", "2026-27", fresh) == \
        "Spurs's 2026-27 season: position, form, results, fixtures, squad and FPL points."


def test_unknown_club_and_bad_slugs_fall_back():
    for path in ("/teams/real-madrid", "/teams/Arsenal", "/teams/a", "/teams/arsenal/squad"):
        m = meta_for(path)
        assert (m.title, m.description) == (seo.SITE_TITLE, seo.SITE_DESCRIPTION), path


def test_data_errors_fall_back_with_the_requested_path():
    m = meta_for("/teams/arsenal", FakeConn(fail=True))
    assert (m.title, m.path) == (seo.SITE_TITLE, "/teams/arsenal")
    assert seo.page_meta("/matches/85-2026-27", lambda: None).title == seo.SITE_TITLE

    def not_connected():
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    assert seo.page_meta("/teams/arsenal", not_connected).title == seo.SITE_TITLE


def test_club_names_are_escaped():
    m = meta_for("/teams/brighton-hove-albion")
    assert m.title == f"Brighton & Hove Albion {CURRENT_SEASON} · Pitch Me"
    doc = seo.preview_html(m, ORIGIN)
    assert f"<title>Brighton &amp; Hove Albion {CURRENT_SEASON} · Pitch Me</title>" in doc
    assert "Brighton & Hove" not in doc
    forest = seo.preview_html(meta_for("/teams/nottm-forest"), ORIGIN)
    assert f"Nott&#x27;m Forest {CURRENT_SEASON}" in forest and "Nott'm" not in forest
    assert tag(forest, "property", "og:url") == f"{ORIGIN}/teams/nottm-forest"


# ------------------------------------------------------------------ matches

def test_finished_match():
    m = meta_for(f"/matches/1-{CURRENT_SEASON}")
    assert m.title == "Arsenal 2-0 Everton · Pitch Me"           # MatchPage's title once played
    assert m.description == (f"Arsenal v Everton, {CURRENT_SEASON} gameweek 1, Sat 22 Aug 2026. "
                             "Full time: Arsenal 2-0 Everton. Scorers, line-ups, bonus and FPL points.")


def test_upcoming_match_has_the_kickoff_in_uk_time():
    m = meta_for(f"/matches/85-{CURRENT_SEASON}")
    assert m.path == f"/matches/85-{CURRENT_SEASON}" and m.title == "Everton v Arsenal · Pitch Me"
    assert m.description == (f"Everton v Arsenal, {CURRENT_SEASON} gameweek 11. Kick-off Sun 8 Nov 2026, "
                             "15:00 UK time. Form, last meetings and predicted FPL points.")
    # Summer kick-offs are in BST: 11:30 UTC is 12:30 in London
    summer = dict(FIXTURES[0], status="scheduled", home_score=None, away_score=None)
    assert "Kick-off Sat 22 Aug 2026, 12:30 UK time." in seo.match_description(summer, CURRENT_SEASON)
    tbc = meta_for(f"/matches/87-{CURRENT_SEASON}")
    assert "gameweek 12. Kick-off time to be confirmed." in tbc.description


def test_match_in_progress_shows_no_stale_score():
    m = meta_for(f"/matches/86-{CURRENT_SEASON}")
    assert m.title == "Nott'm Forest v Brighton & Hove Albion · Pitch Me"
    assert ": in progress, kicked off Sun 8 Nov 2026 at 17:30 UK time. Live score" in m.description


def test_past_season_match_by_id():
    conn = FakeConn(by_id={PAST_MATCH["id"]: PAST_MATCH})
    m = meta_for(f"/matches/{PAST_MATCH['id']}", conn)
    assert m.title == "Chelsea 1-1 Arsenal · Pitch Me"
    assert m.description.startswith("Chelsea v Arsenal, 2024-25 gameweek 3, Sat 31 Aug 2024. Full time:")


def test_unknown_match_falls_back():
    for path in ("/matches/999-2026-27", "/matches/x", "/matches/<script>", "/matches/" + "9" * 41):
        m = meta_for(path)
        assert (m.title, m.description) == (seo.SITE_TITLE, seo.SITE_DESCRIPTION), path


# ------------------------------------------------------------------ documents and URLs

def test_preview_document_tags():
    doc = seo.preview_html(meta_for("/teams/arsenal"), ORIGIN)
    url = f"{ORIGIN}/teams/arsenal"
    title = f"Arsenal {CURRENT_SEASON} · Pitch Me"
    assert doc.startswith("<!doctype html>") and f"<title>{title}</title>" in doc
    assert f'<link rel="canonical" href="{url}">' in doc and f'<a href="{url}">' in doc
    assert tag(doc, "property", "og:title") == title and tag(doc, "property", "og:url") == url
    assert tag(doc, "property", "og:type") == "website" and tag(doc, "property", "og:site_name") == "Pitch Me"
    assert tag(doc, "property", "og:image") == f"{ORIGIN}/og-image.png"
    assert (tag(doc, "property", "og:image:width"), tag(doc, "property", "og:image:height")) == ("1200", "630")
    assert tag(doc, "name", "twitter:card") == "summary_large_image"
    assert tag(doc, "name", "twitter:image") == f"{ORIGIN}/og-image.png"
    assert tag(doc, "name", "description") == tag(doc, "property", "og:description")
    assert tag(doc, "name", "description").startswith(f"Arsenal&#x27;s {CURRENT_SEASON} season: 1st")
    assert 'name="robots"' not in doc
    assert '<meta name="robots" content="noindex, nofollow">' in seo.preview_html(meta_for("/stats"), ORIGIN)


def test_site_origin(monkeypatch):
    assert seo.site_url("pitch.example.com, 1-2-3-4.sslip.io") == "https://pitch.example.com"
    assert seo.site_url("http://Localhost:3000/") == "http://localhost:3000"
    assert seo.site_url(":80") is None and seo.site_url("") is None and seo.site_url(None) is None
    assert seo.site_origin({}) == "https://pitch.example.com"                 # SITE_ADDRESS
    monkeypatch.setenv("SITE_URL", "https://other.example.org")
    assert seo.site_origin({}) == "https://other.example.org"                 # SITE_URL wins
    monkeypatch.delenv("SITE_URL")
    monkeypatch.delenv("SITE_ADDRESS")
    assert seo.site_origin({"x-forwarded-host": "a.example.com", "x-forwarded-proto": "https",
                            "host": "backend:8000"}) == "https://a.example.com"
    assert seo.site_origin({"host": "localhost:8000"}, "http") == "http://localhost:8000"
    assert seo.site_origin({"host": "evil.com/<x>"}) == seo.DEFAULT_SITE_URL
    assert seo.site_origin({"host": "a.com", "x-forwarded-proto": "javascript"}) == seo.DEFAULT_SITE_URL


# ------------------------------------------------------------------ sitemap

def test_sitemap_lists_clubs_and_matches():
    xml = seo.data_sitemap(FakeConn(), ORIGIN)
    root = ET.fromstring(xml.encode("utf-8"))
    assert root.tag == f"{SM}urlset"
    urls = {u.find(f"{SM}loc").text: u for u in root.findall(f"{SM}url")}
    locs = list(urls)
    assert all(loc.startswith(ORIGIN + "/") for loc in locs)
    clubs = [loc for loc in locs if "/teams/" in loc]
    assert clubs == [f"{ORIGIN}/teams/{s}" for s in ("arsenal", "brighton-hove-albion", "everton", "nottm-forest")]
    assert [loc for loc in locs if "/matches/" in loc] == [f"{ORIGIN}/matches/{i}-{CURRENT_SEASON}"
                                                          for i in (1, 2, 3, 4, 85, 86, 87)]
    lastmod = lambda loc: (urls[loc].find(f"{SM}lastmod").text if urls[loc].find(f"{SM}lastmod") is not None
                           else None)  # noqa: E731
    assert lastmod(f"{ORIGIN}/teams/arsenal") == "2026-08-29"                 # latest finished match
    assert lastmod(f"{ORIGIN}/matches/1-{CURRENT_SEASON}") == "2026-08-22"
    assert lastmod(f"{ORIGIN}/matches/85-{CURRENT_SEASON}") is None           # not played yet
    assert lastmod(f"{ORIGIN}/matches/86-{CURRENT_SEASON}") is None           # in progress


def test_sitemap_skips_ids_the_match_page_would_refuse():
    odd = FIXTURES[:1] + [dict(FIXTURES[4], id="bad id/1")]
    xml = seo.sitemap_xml(seo.sitemap_entries(odd), ORIGIN)
    assert "bad" not in xml and f"/matches/1-{CURRENT_SEASON}" in xml
    ET.fromstring(xml.encode("utf-8"))


def test_sitemap_is_cached():
    conn = FakeConn()
    first = seo.data_sitemap(conn, ORIGIN)
    queries = len(conn.queries)
    assert seo.data_sitemap(conn, ORIGIN) == first and len(conn.queries) == queries


# ------------------------------------------------------------------ routes

def client_with(get_conn):
    app = FastAPI()
    seo.register(app, get_conn)
    return app, TestClient(app)


def test_register_adds_the_routes():
    app, _ = client_with(lambda: FakeConn())
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/seo/preview", "/api/seo/sitemap.xml"} <= paths


def test_preview_route():
    conn = FakeConn()
    _, client = client_with(lambda: conn)
    r = client.get("/api/seo/preview", params={"path": f"/matches/85-{CURRENT_SEASON}"},
                   headers={"x-forwarded-host": "ignored.example.com"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>Everton v Arsenal · Pitch Me</title>" in r.text
    assert f'href="https://pitch.example.com/matches/85-{CURRENT_SEASON}"' in r.text   # SITE_ADDRESS
    assert r.headers["vary"] == "User-Agent" and "max-age" in r.headers["cache-control"]
    assert client.head("/api/seo/preview", params={"path": "/teams/arsenal"}).status_code == 200
    assert "<title>Pitch Me · FPL" in client.get("/api/seo/preview").text      # no path: home


def test_preview_route_never_fails():
    def not_connected():
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    _, client = client_with(not_connected)
    for path in ("/teams/arsenal", "/matches/85-2026-27", "x" * 5000, "/teams/<b>&'\""):
        r = client.get("/api/seo/preview", params={"path": path})
        assert r.status_code == 200 and f"<title>{seo.SITE_TITLE}</title>" in r.text, path
    assert "<b>" not in client.get("/api/seo/preview", params={"path": "/teams/<b>"}).text
    # Static pages still get their own text without the graph
    assert "<title>Predictions · Pitch Me</title>" in client.get("/api/seo/preview",
                                                                 params={"path": "/predictions"}).text


def test_sitemap_route(monkeypatch):
    monkeypatch.delenv("SITE_ADDRESS")
    _, client = client_with(lambda: FakeConn())
    r = client.get("/api/seo/sitemap.xml", headers={"host": "pitch.test", "x-forwarded-proto": "https"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://pitch.test/teams/arsenal</loc>" in r.text
    ET.fromstring(r.content)

    def not_connected():
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    _, down = client_with(not_connected)
    r = down.get("/api/seo/sitemap.xml")
    assert r.status_code == 503 and r.headers["retry-after"] == "600"
    ss.clear_cache()                        # the fixtures above are cached
    _, failing = client_with(lambda: FakeConn(fail=True))
    assert failing.get("/api/seo/sitemap.xml").status_code == 503
