"""
Live points while a gameweek is being played, from FPL's public live feed, so the Model vs actual
review and the ML manager tab update after each match instead of after the pipeline's next run
(twice a day, and the season CSV only has finished matches).

  event/{gw}/live/       every player's points and minutes so far, per fixture (`explain`)
  fixtures/?event={gw}   which of the gameweek's fixtures have started and finished

Both are fetched by the backend (never the browser) and cached for LIVE_TTL_SECONDS, so however
many people watch, FPL sees one request a minute. If FPL can't be reached, the last good answer is
used for up to STALE_SECONDS, then nothing (the pages fall back to the season CSV). Points are
FPL's own provisional totals: bonus appears once FPL adds it, about an hour after the day's last
match. Nothing here writes files; the pipeline still scores a gameweek once FPL has checked it.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

FPL_BASE_URL = "https://fantasy.premierleague.com/api/"
LIVE_TTL_SECONDS = 60
STALE_SECONDS = 15 * 60
TIMEOUT_SECONDS = 6.0
USER_AGENT = "PitchMe/1.0 (+live points for the model review)"

Fetch = Callable[[str], Any]

_lock = threading.Lock()
_cache: Dict[int, Dict[str, Any]] = {}       # gameweek -> {"at": monotonic, "data": parsed}


def _http_get(path: str) -> Any:
    import httpx
    resp = httpx.get(FPL_BASE_URL + path, timeout=TIMEOUT_SECONDS, follow_redirects=True,
                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def parse_live(live: Dict[str, Any], fixtures: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """{actual: {element: {points, minutes}}, started, finished} from the two feeds. A player is
    in `actual` only once one of his team's fixtures has started, so players still to play stay
    undecided (no points) rather than counting as 0 minutes."""
    fixtures = list(fixtures)
    started = {int(f["id"]) for f in fixtures if f.get("started") or f.get("finished") or f.get("finished_provisional")}
    finished = {int(f["id"]) for f in fixtures if f.get("finished") or f.get("finished_provisional")}
    actual: Dict[int, Dict[str, int]] = {}
    # Per fixture, each player's stat values and points in it (match pages; a double gameweek
    # splits by fixture). explain lists {identifier, value, points} per scoring stat.
    by_fixture: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for el in live.get("elements") or []:
        stats = el.get("stats") or {}
        played_fixtures = {int(x["fixture"]) for x in el.get("explain") or [] if x.get("fixture") is not None}
        if not played_fixtures & started:
            continue
        actual[int(el["id"])] = {"points": int(stats.get("total_points") or 0),
                                 "minutes": int(stats.get("minutes") or 0)}
        for x in el.get("explain") or []:
            if x.get("fixture") is None or int(x["fixture"]) not in started:
                continue
            row: Dict[str, Any] = {"points": 0}
            for st in x.get("stats") or []:
                row[st.get("identifier")] = st.get("value")
                row["points"] += int(st.get("points") or 0) + int(st.get("points_modification") or 0)
            if len(played_fixtures) == 1:
                # one match: the element's totals include stats explain leaves out (xG, BPS, starts)
                row = {**{k: v for k, v in stats.items() if k != "total_points"}, **row,
                       "points": int(stats.get("total_points") or row["points"])}
            by_fixture.setdefault(int(x["fixture"]), {})[int(el["id"])] = row
    scores = {int(f["id"]): {"home_score": f.get("team_h_score"), "away_score": f.get("team_a_score"),
                             "started": int(f["id"]) in started, "finished": int(f["id"]) in finished,
                             "minutes": f.get("minutes")} for f in fixtures}
    return {"actual": actual, "started": sorted(started), "finished": sorted(finished),
            "by_fixture": by_fixture, "fixtures": scores,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def gameweek_live(gameweek: int, fetch: Optional[Fetch] = None, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The gameweek's live points (parse_live), cached; None when FPL can't be reached and no
    recent answer is cached."""
    gw = int(gameweek)
    t = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(gw)
        if hit and t - hit["at"] < LIVE_TTL_SECONDS:
            return hit["data"]
    get = fetch or _http_get
    try:
        data = parse_live(get(f"event/{gw}/live/"), get(f"fixtures/?event={gw}"))
    except Exception as exc:  # network, HTTP or shape: the CSV still has finished matches
        logger.warning("FPL live points for GW%s unavailable: %s", gw, exc)
        with _lock:
            hit = _cache.get(gw)
            if hit and t - hit["at"] < STALE_SECONDS:
                return hit["data"]
        return None
    with _lock:
        _cache[gw] = {"at": t, "data": data}
    return data


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def merge(csv_actual: Dict[int, Dict[str, int]], live: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, int]]:
    """The season CSV's finished matches, overridden by the live totals (which include them)."""
    if not live:
        return csv_actual
    return {**csv_actual, **live["actual"]}


def is_live(event: Dict[str, Any], deadline: Optional[str], now: Optional[datetime] = None) -> bool:
    """A gameweek whose deadline has passed and that FPL hasn't finished yet."""
    if event.get("finished"):
        return False
    if not deadline:
        return False
    try:
        when = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= (now or datetime.now(timezone.utc))
