"""
Link previews for social apps and the club/match sitemap (deploy/Caddyfile routes both here).

Previews: WhatsApp, X, Facebook, Slack, Discord, LinkedIn, Telegram and the like read a page's
Open Graph tags without running JavaScript, so every shared link showed index.html's site-wide
title and description. Caddy sends their crawlers' page requests (matched by User-Agent) to
GET /api/seo/preview?path=<page path>, which answers a small HTML document with that page's
title, description, canonical URL, Open Graph and Twitter tags and a plain link. Fixed pages
reuse the title and description they set with usePageMeta (PAGES; tests/test_seo.py checks
they still match src/pages). Club and match pages are described from the season's cached
fixtures. It never fails: an unknown club or match, a missing connection or any data error
gets the site-wide text, with 200.

Sitemap: GET /api/seo/sitemap.xml, served as /sitemap-data.xml, lists every club page and
match page of the current season. The build's sitemap.xml is an index of it and of
sitemap-pages.xml, the fixed routes (vite.config.ts).

Absolute URLs use SITE_URL or SITE_ADDRESS (the backend reads deploy/.env, like Caddy), else
the request's X-Forwarded-Host/Host, mirroring toSiteUrl() in vite.config.ts.
"""
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from config.settings import CURRENT_SEASON
from services import season_service as ss
from services.teams import slugify

logger = logging.getLogger(__name__)

SITE_NAME = "Pitch Me"
DEFAULT_SITE_URL = "https://20-215-33-13.sslip.io"     # DEFAULT_SITE_URL in vite.config.ts
# index.html's site-level text: the fallback for anything we can't describe
SITE_TITLE = "Pitch Me: Fantasy Premier League stats, predictions and AI assistant"
SITE_DESCRIPTION = ("Fixtures, table, player stats and next-gameweek points predictions, plus an AI "
                    "assistant that answers your FPL questions.")
HOME_TITLE = f"{SITE_NAME} · FPL stats, predictions & AI assistant"   # usePageMeta without a title
OG_IMAGE_PATH = "/og-image.png"
OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT = 1200, 630
OG_IMAGE_ALT = "Pitch Me logo with the tagline: Fantasy Premier League stats, predictions and an AI assistant"
PREVIEW_MAX_AGE = 600
SITEMAP_MAX_AGE = 3600
MAX_PATH_LENGTH = 200

# Path -> (title, description), exactly as each page passes them to usePageMeta. A None title
# is the home page (HOME_TITLE). tests/test_seo.py compares these with src/pages.
PAGES: Dict[str, Tuple[Optional[str], str]] = {
    "/": (None, "Gameweek captain pick, fixtures, the league table, team news and an AI assistant for "
                "Fantasy Premier League."),
    "/fixtures": ("Fixtures & results", "Every Premier League fixture and result of the 2026-27 season by "
                                        "gameweek, with kick-off times in your time zone."),
    "/table": ("Premier League table", "The 2026-27 Premier League table calculated from every finished match: "
                                       "points, goal difference, form and next fixture."),
    "/teams": ("Clubs", "Every Premier League club this season: position, form, results, fixtures, squad and news."),
    "/team-of-the-week": ("Team of the Week", "The highest-scoring FPL squad our model predicts for the next "
                                              "gameweek (£100m, 3 per club), how its past picks scored, each "
                                              "week's dream team, and a season-long team the model manages."),
    "/players": ("Players", "Season leaders and stats for every FPL player since 2020-21: points, goals, assists, "
                            "clean sheets and per-game rates."),
    "/predictions": ("Predictions", "Expected FPL points for every player next gameweek from a machine-learning "
                                    "model trained on every season since 2020-21, adjusted for availability."),
    "/model": ("Model insights", "How the points model makes its predictions, explained with SHAP and LIME: what "
                                 "drives this gameweek’s forecasts and why each player got his number."),
    "/assistant": ("Assistant", "Ask questions about FPL players, teams and seasons. Answers come from a knowledge "
                                "graph of every season since 2020-21."),
    "/compare": ("Compare players", "Compare two FPL players head to head across any season: points, goals, "
                                    "assists, bonus, form and more."),
    "/trivia": ("Trivia", "Test your Fantasy Premier League knowledge with quiz questions built from real FPL data."),
    "/settings": ("Settings", "Choose the assistant's language model and retrieval method, and check the system "
                              "status."),
    "/privacy": ("Privacy policy", "What Pitch Me stores (very little), what is sent to third parties, and how to "
                                   "change your choices."),
    "/terms": ("Terms of use", "The terms for using Pitch Me: a free, fan-made FPL project whose predictions and "
                               "answers are for fun and information."),
    "/contact": ("Contact", "Report a bug, suggest a feature or browse the code: Pitch Me is developed in the open "
                            "on GitHub."),
    "/stats": ("Site stats", "Visitor counts for the site owner."),
}
REDIRECTS = {"/qa": "/assistant", "/search": "/players"}     # src/App.tsx's <Redirect>s
NOINDEX = {"/stats", "/design"}

# The same rules api_main applies to /api/teams/{slug} and /api/matches/{fixture_id}
CLUB_PATH = re.compile(r"/teams/([a-z0-9-]{2,40})")
MATCH_PATH = re.compile(r"/matches/([A-Za-z0-9-]{3,40})")

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class PageMeta:
    path: str                 # canonical path ("/teams/arsenal")
    title: str                # the full <title>, "Arsenal 2026-27 · Pitch Me"
    description: str
    noindex: bool = False


# ------------------------------------------------------------------ URLs

def site_url(raw: Optional[str]) -> Optional[str]:
    """toSiteUrl() of vite.config.ts: the first address of a URL or Caddy address list, with
    https:// added; None when unusable (e.g. ":80")."""
    first = next((part for part in re.split(r"[,\s]+", raw or "") if part), None)
    if not first:
        return None
    url = (first if re.match(r"https?://", first, re.I) else f"https://{first}").rstrip("/")
    return url.lower() if re.fullmatch(r"https?://[a-z0-9.-]+(:\d+)?", url, re.I) else None


def site_origin(headers: Mapping[str, str], scheme: str = "https") -> str:
    """The public origin: SITE_URL or SITE_ADDRESS when set (production), else the host the
    request came to (Caddy sets X-Forwarded-Host/-Proto), else the default."""
    for name in ("SITE_URL", "SITE_ADDRESS"):
        url = site_url(os.getenv(name))
        if url:
            return url
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    proto = (headers.get("x-forwarded-proto") or scheme or "https").split(",")[0].strip().lower()
    if proto in ("http", "https") and re.fullmatch(r"[a-z0-9.-]+(:\d+)?", host, re.I):
        return f"{proto}://{host.lower()}"
    return DEFAULT_SITE_URL


def normalize_path(raw: Optional[str]) -> str:
    """The page path a crawler asked for: no query, fragment or trailing slash. Percent-escapes
    are undone (twice over at most): a proxy that escapes the path into the query string, and
    then has the query escaped again, sends "%252Fteams%252Farsenal" for "/teams/arsenal"."""
    path = (raw or "/")[:MAX_PATH_LENGTH]
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.split("?")[0].split("#")[0].strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def page_url(origin: str, path: str) -> str:
    return origin + quote(path, safe="/-._~")


# ------------------------------------------------------------------ wording (pure)

def full_title(title: Optional[str]) -> str:
    return f"{title} · {SITE_NAME}" if title else HOME_TITLE


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def uk_time(dt: datetime) -> Tuple[datetime, str]:
    """Kick-off in UK time (Premier League clocks), or UTC without tz data."""
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/London")), "UK time"
    except Exception:
        return dt.astimezone(timezone.utc), "UTC"


def day_text(dt: datetime, year: bool = True) -> str:
    """"Sat 8 Nov 2026" (English names whatever the server's locale)."""
    text = f"{_DAYS[dt.weekday()]} {dt.day} {_MONTHS[dt.month - 1]}"
    return f"{text} {dt.year}" if year else text


def club_title(team: str, season: str) -> str:
    return full_title(f"{team} {season}")                      # TeamPage's usePageMeta


def club_description(team: str, season: str, row: Optional[Dict[str, Any]]) -> str:
    """From the club's league table row (season_service.compute_table)."""
    lead = f"{team}'s {season} season"
    if not row or not row.get("played"):
        return f"{lead}: position, form, results, fixtures, squad and FPL points."   # TeamPage's text
    parts = [f"{lead}: {ordinal(int(row['position']))} with {_count(int(row['points']), 'point', 'points')} "
             f"after {_count(int(row['played']), 'match', 'matches')}."]
    nxt = row.get("next_fixture") or {}
    if nxt.get("opponent"):
        kickoff = ss.to_datetime(nxt.get("kickoff_time"))
        when = f", {day_text(uk_time(kickoff)[0], year=False)}" if kickoff else ""
        parts.append(f"Next: {nxt['opponent']} ({'H' if nxt.get('home') else 'A'}){when}.")
    parts.append("Results, fixtures, squad, form and news.")
    return " ".join(parts)


def _finished(f: Dict[str, Any]) -> bool:
    return f.get("home_score") is not None and f.get("away_score") is not None and f.get("status") == "finished"


def match_title(f: Dict[str, Any]) -> str:
    """MatchPage's title: the score once played, else "v". A match in progress shows "v": the
    graph's score is from the last pipeline run, not the live one the page shows."""
    if _finished(f):
        return full_title(f"{f['home_team']} {f['home_score']}-{f['away_score']} {f['away_team']}")
    return full_title(f"{f['home_team']} v {f['away_team']}")


def match_description(f: Dict[str, Any], season: str) -> str:
    home, away = f["home_team"], f["away_team"]
    head = f"{home} v {away}, {season}" + (f" gameweek {f['gameweek']}" if f.get("gameweek") else "")
    kickoff = ss.to_datetime(f.get("kickoff_time"))
    local, zone = uk_time(kickoff) if kickoff else (None, "")
    if _finished(f):
        when = f", {day_text(local)}" if local else ""
        return (f"{head}{when}. Full time: {home} {f['home_score']}-{f['away_score']} {away}. "
                "Scorers, line-ups, bonus and FPL points.")
    if f.get("status") == "in_progress":
        when = f", kicked off {day_text(local)} at {local:%H:%M} {zone}" if local else ""
        return f"{head}: in progress{when}. Live score, scorers, line-ups and FPL points."
    when = f"Kick-off {day_text(local)}, {local:%H:%M} {zone}." if local else "Kick-off time to be confirmed."
    return f"{head}. {when} Form, last meetings and predicted FPL points."


# ------------------------------------------------------------------ page descriptions

def generic_meta(path: str) -> PageMeta:
    return PageMeta(path, SITE_TITLE, SITE_DESCRIPTION, noindex=path in NOINDEX)


def static_meta(path: str) -> PageMeta:
    path = REDIRECTS.get(path, path)
    if path not in PAGES:
        return generic_meta(path)
    title, description = PAGES[path]
    return PageMeta(path, full_title(title), description, noindex=path in NOINDEX)


def _club_rows(conn) -> Dict[str, Dict[str, Any]]:
    """slug -> the club's table row, for the current season (cached like the season views)."""
    fixtures = ss.load_fixtures(conn, CURRENT_SEASON)
    return ss._cached(("seo-clubs", CURRENT_SEASON),
                      lambda: {slugify(r["team"]): r for r in ss.compute_table(fixtures)})


def club_meta(conn, slug: str) -> Optional[PageMeta]:
    if conn is None:
        return None
    row = _club_rows(conn).get(slugify(slug))
    if row is None:
        return None
    team = row["team"]
    return PageMeta(f"/teams/{slugify(team)}", club_title(team, CURRENT_SEASON),
                    club_description(team, CURRENT_SEASON, row))


def find_fixture(conn, fixture_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """(fixture, season): the current season's from the cached fixtures, any other season's
    with matches.FIXTURE_QUERY (one lookup by id)."""
    for f in ss.load_fixtures(conn, CURRENT_SEASON):
        if f["id"] == fixture_id:
            return f, CURRENT_SEASON
    from services import matches
    rows = conn.execute_query(matches.FIXTURE_QUERY, {"id": fixture_id})
    if not rows or not rows[0].get("home_team") or not rows[0].get("away_team"):
        return None
    r = dict(rows[0])
    status = r.get("status") or ("finished" if r.get("home_score") is not None else "scheduled")
    if status not in ("scheduled", "in_progress", "finished"):
        status = "scheduled"
    played = status != "scheduled"
    fixture = {"id": str(r.get("id") or fixture_id), "gameweek": ss._int_or_none(r.get("gameweek")),
               "kickoff_time": ss.to_datetime(r.get("kickoff_time")), "status": status,
               "home_team": r["home_team"], "away_team": r["away_team"],
               "home_score": ss._int_or_none(r.get("home_score")) if played else None,
               "away_score": ss._int_or_none(r.get("away_score")) if played else None}
    return fixture, r.get("season") or CURRENT_SEASON


def match_meta(conn, fixture_id: str) -> Optional[PageMeta]:
    if conn is None:
        return None
    found = find_fixture(conn, fixture_id)
    if found is None:
        return None
    f, season = found
    return PageMeta(f"/matches/{f['id']}", match_title(f), match_description(f, season))


def page_meta(raw_path: Optional[str], get_conn: Callable[[], Any]) -> PageMeta:
    """The page's preview text. Never raises: anything unknown or failing gets the site text.
    `get_conn` is only called for club and match pages."""
    path = normalize_path(raw_path)
    try:
        club = CLUB_PATH.fullmatch(path)
        if club:
            return club_meta(get_conn(), club.group(1)) or generic_meta(path)
        match = MATCH_PATH.fullmatch(path)
        if match:
            return match_meta(get_conn(), match.group(1)) or generic_meta(path)
        return static_meta(path)
    except Exception as exc:          # a crawler must always get a page
        logger.warning("Link preview for %s fell back to the site text: %s", path, exc)
        return generic_meta(path)


# ------------------------------------------------------------------ documents (pure)

def preview_html(meta: PageMeta, origin: str) -> str:
    """A minimal page with the tags link previews read, and a link to the real page."""
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    url = page_url(origin, meta.path)
    image = origin + OG_IMAGE_PATH
    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(meta.title)}</title>",
        f'<meta name="description" content="{e(meta.description)}">',
        f'<link rel="canonical" href="{e(url)}">',
    ]
    if meta.noindex:
        head.append('<meta name="robots" content="noindex, nofollow">')
    og = [("og:type", "website"), ("og:site_name", SITE_NAME), ("og:locale", "en_GB"),
          ("og:title", meta.title), ("og:description", meta.description), ("og:url", url),
          ("og:image", image), ("og:image:type", "image/png"), ("og:image:width", OG_IMAGE_WIDTH),
          ("og:image:height", OG_IMAGE_HEIGHT), ("og:image:alt", OG_IMAGE_ALT)]
    twitter = [("twitter:card", "summary_large_image"), ("twitter:title", meta.title),
               ("twitter:description", meta.description), ("twitter:image", image),
               ("twitter:image:alt", OG_IMAGE_ALT)]
    head += [f'<meta property="{k}" content="{e(v)}">' for k, v in og]
    head += [f'<meta name="{k}" content="{e(v)}">' for k, v in twitter]
    body = [f"<h1>{e(meta.title)}</h1>", f"<p>{e(meta.description)}</p>",
            f'<p><a href="{e(url)}">{e(url)}</a></p>']
    return "\n".join(["<!doctype html>", '<html lang="en">', "<head>", *head, "</head>", "<body>",
                      *body, "</body>", "</html>", ""])


def sitemap_entries(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The season's club pages (A-Z) and match pages (by kick-off). lastmod is the day of the
    club's latest finished match, or of the finished match itself; none for upcoming ones."""
    last_played: Dict[str, Optional[str]] = {}
    for f in fixtures:
        day = None
        if _finished(f) and ss.to_datetime(f.get("kickoff_time")):
            day = ss.to_datetime(f["kickoff_time"]).date().isoformat()
        for team in (f["home_team"], f["away_team"]):
            last_played[team] = max(filter(None, (last_played.get(team), day)), default=None)
    entries = [{"path": f"/teams/{slugify(team)}", "lastmod": last_played[team],
                "changefreq": "daily", "priority": 0.7}
               for team in sorted(last_played, key=slugify) if CLUB_PATH.fullmatch(f"/teams/{slugify(team)}")]
    for f in fixtures:
        if not MATCH_PATH.fullmatch(f"/matches/{f['id']}"):
            continue
        finished = _finished(f) and ss.to_datetime(f.get("kickoff_time")) is not None
        entries.append({"path": f"/matches/{f['id']}",
                        "lastmod": ss.to_datetime(f["kickoff_time"]).date().isoformat() if finished else None,
                        "changefreq": "monthly" if finished else "daily", "priority": 0.5})
    return entries


def sitemap_xml(entries: List[Dict[str, Any]], origin: str) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for entry in entries:
        out.append("  <url>")
        out.append(f"    <loc>{xml_escape(page_url(origin, entry['path']))}</loc>")
        if entry.get("lastmod"):
            out.append(f"    <lastmod>{xml_escape(entry['lastmod'])}</lastmod>")
        out.append(f"    <changefreq>{entry['changefreq']}</changefreq>")
        out.append(f"    <priority>{entry['priority']:.1f}</priority>")
        out.append("  </url>")
    out += ["</urlset>", ""]
    return "\n".join(out)


def data_sitemap(conn, origin: str) -> str:
    fixtures = ss.load_fixtures(conn, CURRENT_SEASON)
    return ss._cached(("seo-sitemap", CURRENT_SEASON, origin),
                      lambda: sitemap_xml(sitemap_entries(fixtures), origin))


# ------------------------------------------------------------------ routes

def register(app: FastAPI, get_conn: Callable[[], Any]) -> None:
    """Add GET/HEAD /api/seo/preview?path= and /api/seo/sitemap.xml. `get_conn` is api_main's
    get_neo4j_conn. It's called directly rather than through Depends, so a missing connection
    (its 503) gives the site-wide preview instead of an error. Both are plain `def` routes:
    the Neo4j driver is synchronous, so FastAPI runs them in its threadpool."""

    def optional_conn() -> Any:
        try:
            return get_conn()
        except Exception:
            return None

    def origin_of(request: Request) -> str:
        try:
            return site_origin(request.headers, request.url.scheme)
        except Exception:
            return DEFAULT_SITE_URL

    @app.api_route("/api/seo/preview", methods=["GET", "HEAD"], include_in_schema=False)
    def seo_preview(request: Request, path: str = "/") -> HTMLResponse:
        """Link-preview document for one page (Caddy sends social crawlers here)."""
        try:
            body = preview_html(page_meta(path, optional_conn), origin_of(request))
        except Exception as exc:      # never fail a crawler
            logger.warning("Link preview for %r failed: %s", path, exc)
            body = preview_html(generic_meta("/"), DEFAULT_SITE_URL)
        # The public URL (the page) answers crawlers and people differently
        return HTMLResponse(body, headers={"Cache-Control": f"public, max-age={PREVIEW_MAX_AGE}",
                                           "Vary": "User-Agent"})

    @app.api_route("/api/seo/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
    def seo_sitemap(request: Request) -> Response:
        """The current season's club and match pages (served as /sitemap-data.xml)."""
        conn = optional_conn()
        try:
            if conn is None:
                raise RuntimeError("Neo4j not connected")
            xml = data_sitemap(conn, origin_of(request))
        except Exception as exc:
            logger.warning("Data sitemap unavailable: %s", exc)
            return Response("Sitemap temporarily unavailable\n", status_code=503, media_type="text/plain",
                            headers={"Retry-After": "600", "Cache-Control": "no-store"})
        return Response(xml, media_type="application/xml",
                        headers={"Cache-Control": f"public, max-age={SITEMAP_MAX_AGE}"})
