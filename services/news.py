"""
News for the Home page and club pages (GET /api/news?team=).

Guardian headlines (The Guardian Open Platform, GUARDIAN_API_KEY; free for non-commercial use,
1 call a second and 500 a day): headline, thumbnail, trail text and byline, each linking to the
article on theguardian.com and credited to The Guardian (the key was registered for "summary
only" display). Never more than that is shown.

So the site stays within the limits and keeps headlines when the API is down or out of calls:
  - each query (the Premier League feed, or one club) is fetched at most every FEED_TTL /
    CLUB_TTL seconds, and only when a page asks for it;
  - calls are spaced at least a second apart, and stop at DAILY_BUDGET a UTC day;
  - every good answer is written to $LIVE_DATA_DIR/news_cache.json, so a restart doesn't refetch
    and a failed or skipped call serves the last good headlines (marked `stale`), however old;
  - with no key, or nothing cached yet, the Guardian list is empty and the page shows only our
    own FPL news.

FPL news is built from our own data, so it always works: injury and availability updates from the
FPL status file (`news`, `news_added`), and this gameweek's price changes (`cost_change_event`).
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from config.settings import CURRENT_SEASON
from services import season_service as ss

logger = logging.getLogger(__name__)

API_URL = "https://content.guardianapis.com/search"
FEED_TTL = 20 * 60          # the Premier League feed: at most 72 calls a day
CLUB_TTL = 3 * 60 * 60      # a club: at most 8 a day each, and only for clubs people open
DAILY_BUDGET = 400          # of the free key's 500, leaving room for restarts and retries
MIN_INTERVAL = 1.1          # seconds between calls (the key allows 1 a second)
ARTICLES = 12
TIMEOUT = 8.0
CACHE_FILE = "news_cache.json"
FPL_NEWS_DAYS = 10
FPL_NEWS_ITEMS = 30
PRICE_ITEMS = 20

# The clubs' names as the Guardian writes them, where the graph's short name differs
SEARCH_NAMES = {
    "Man City": "Manchester City", "Man Utd": "Manchester United", "Spurs": "Tottenham",
    "Nott'm Forest": "Nottingham Forest", "Wolves": "Wolverhampton", "Newcastle": "Newcastle United",
    "Brighton": "Brighton", "Leeds": "Leeds United", "Leicester": "Leicester City", "Ipswich": "Ipswich Town",
    "West Ham": "West Ham", "Sheffield Utd": "Sheffield United", "Luton": "Luton Town",
    "Coventry": "Coventry City", "Hull": "Hull City",
}

Fetch = Callable[[Dict[str, Any]], Dict[str, Any]]

_lock = threading.Lock()
_memory: Dict[str, Dict[str, Any]] = {}       # query key -> {"at": epoch, "articles": [...]}
_loaded = False
_calls: Dict[str, Any] = {"day": None, "count": 0, "last": 0.0}


def api_key() -> Optional[str]:
    key = os.getenv("GUARDIAN_API_KEY", "").strip()
    return key or None


def _cache_path() -> str:
    return os.path.join(ss.live_dir(), CACHE_FILE)


def _load_disk() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        _memory.update(data.get("queries") or {})
        calls = data.get("calls") or {}
        if calls.get("day"):
            _calls.update(day=calls["day"], count=int(calls.get("count") or 0))
    except (OSError, ValueError):
        pass


def _save_disk() -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"queries": _memory, "calls": {"day": _calls["day"], "count": _calls["count"]}}, f)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Couldn't save the news cache: %s", exc)


def _http_get(params: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    resp = httpx.get(API_URL, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def _strip(text: Any) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(text)))).strip() or None


def parse_articles(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The fields the site shows, from a /search response."""
    out = []
    for r in (payload.get("response") or {}).get("results") or []:
        fields = r.get("fields") or {}
        url = r.get("webUrl")
        if not url or not r.get("webTitle"):
            continue
        thumb = fields.get("thumbnail")
        out.append({
            "id": r.get("id"), "title": _strip(r["webTitle"]), "url": url,
            "summary": _strip(fields.get("trailText")), "byline": _strip(fields.get("byline")),
            "thumbnail": thumb if isinstance(thumb, str) and thumb.startswith("https://") else None,
            "published": r.get("webPublicationDate"), "source": "The Guardian",
        })
    return out


def _query(team: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {"section": "football", "order-by": "newest", "page-size": ARTICLES,
                              "show-fields": "thumbnail,trailText,byline"}
    if team:
        params["q"] = f'"{SEARCH_NAMES.get(team, team)}"'
    else:
        params["tag"] = "football/premierleague"
    return params


def guardian_articles(team: Optional[str] = None, *, fetch: Optional[Fetch] = None,
                      now: Optional[float] = None) -> Dict[str, Any]:
    """{articles, stale, fetched_at} for the Premier League (team None) or one club."""
    key = api_key()
    cache_key = f"team:{team}" if team else "premier-league"
    t = time.time() if now is None else now
    ttl = CLUB_TTL if team else FEED_TTL
    with _lock:
        _load_disk()
        hit = _memory.get(cache_key)
        if hit and t - hit["at"] < ttl:
            return {"articles": hit["articles"], "stale": False, "fetched_at": hit["at"]}
        day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
        if _calls["day"] != day:
            _calls.update(day=day, count=0)
        can_call = key is not None and _calls["count"] < DAILY_BUDGET
        if can_call:
            wait = MIN_INTERVAL - (t - _calls["last"])
            if wait > 0 and now is None:
                time.sleep(wait)
            _calls["last"] = time.time() if now is None else t
            _calls["count"] += 1
    if not can_call:
        return _fallback(hit)
    try:
        articles = parse_articles((fetch or _http_get)({**_query(team), "api-key": key}))
    except Exception as exc:  # down, rate-limited or a bad key: the last good headlines stay up
        logger.warning("Guardian news for %s unavailable: %s", team or "the Premier League", exc)
        with _lock:
            _save_disk()
        return _fallback(hit)
    with _lock:
        _memory[cache_key] = {"at": t, "articles": articles}
        _save_disk()
    return {"articles": articles, "stale": False, "fetched_at": t}


def _fallback(hit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if hit:
        return {"articles": hit["articles"], "stale": True, "fetched_at": hit["at"]}
    return {"articles": [], "stale": False, "fetched_at": None}


def reset() -> None:
    """Forget the in-memory cache and call count (tests)."""
    global _loaded
    with _lock:
        _memory.clear()
        _calls.update(day=None, count=0, last=0.0)
        _loaded = False


# ------------------------------------------------------------------ FPL news (our own data)

STATUS_WORDS = {"i": "injured", "d": "doubtful", "s": "suspended", "u": "unavailable", "n": "not in the squad"}


def fpl_news(rows: List[Dict[str, Any]], now: Optional[datetime] = None, team: Optional[str] = None) -> Dict[str, Any]:
    """{updates, prices} from the FPL status file's rows: availability news from the last
    FPL_NEWS_DAYS days (newest first; a cleared flag reads as back in contention) and this
    gameweek's price changes (biggest first, then most owned)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=FPL_NEWS_DAYS)
    updates, prices = [], []
    for r in rows:
        if team and r.get("team") != team:
            continue
        name, web = str(r.get("name")), r.get("web_name") or r.get("name")
        status = str(r.get("status") or "a")
        added = ss.to_datetime(r.get("news_added"))
        news = r.get("news") if isinstance(r.get("news"), str) else None
        chance = r.get("chance_of_playing_next_round")
        chance = int(chance) if isinstance(chance, (int, float)) and chance == chance else None
        if added and added >= since:
            if news:
                kind = {"i": "injury", "s": "suspension", "d": "doubt"}.get(status, "unavailable" if status in ("u", "n") else "doubt")
                updates.append({"kind": kind, "name": name, "web_name": web, "team": r.get("team"),
                                "position": r.get("position"), "status": status, "chance": chance,
                                "text": news, "time": ss.to_iso(added), "_code": r.get("code")})
            elif status == "a":
                updates.append({"kind": "return", "name": name, "web_name": web, "team": r.get("team"),
                                "position": r.get("position"), "status": status, "chance": chance,
                                "text": "No longer flagged: available for selection", "time": ss.to_iso(added),
                                "_code": r.get("code")})
        change = r.get("cost_change_event")
        if isinstance(change, (int, float)) and change == change and change:
            cost = r.get("now_cost")
            prices.append({"name": name, "web_name": web, "team": r.get("team"), "position": r.get("position"),
                           "change": round(float(change) / 10.0, 1),
                           "price": round(float(cost) / 10.0, 1) if isinstance(cost, (int, float)) else None,
                           "selected": r.get("selected_by_percent"), "_code": r.get("code")})
    updates.sort(key=lambda u: u["time"] or "", reverse=True)

    def owned(p: Dict[str, Any]) -> float:
        try:
            return float(p["selected"] or 0)
        except (TypeError, ValueError):
            return 0.0

    prices.sort(key=lambda p: (-abs(p["change"]), -owned(p)))
    return {"updates": updates[:FPL_NEWS_ITEMS], "prices": prices[:PRICE_ITEMS]}


def _status_rows() -> List[Dict[str, Any]]:
    path = ss.status_path(CURRENT_SEASON)

    def load() -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            return []
        import pandas as pd
        return pd.read_csv(path).to_dict("records")

    return ss._cached(("status-rows", path, ss._mtime(path)), load)


def get_news(team: Optional[str] = None, *, photo_for=None, fetch: Optional[Fetch] = None) -> Dict[str, Any]:
    guardian = guardian_articles(team, fetch=fetch)
    fpl = fpl_news(_status_rows(), team=team)
    for key in ("updates", "prices"):
        fpl[key] = [{**{k: v for k, v in p.items() if k != "_code"},
                     "photo": photo_for(p["name"], p.get("team"), _code(p.get("_code"))) if photo_for else None}
                    for p in fpl[key]]
    fetched = guardian["fetched_at"]
    return {
        "team": team,
        "guardian": {"enabled": api_key() is not None, "articles": guardian["articles"], "stale": guardian["stale"],
                     "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(timespec="seconds") if fetched else None},
        "fpl": fpl,
    }


def _code(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
