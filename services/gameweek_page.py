"""
Gameweek page: one gameweek on one page, in whichever state it is in.

  upcoming   the deadline is ahead: the deadline and little else (the page links on to the
             predictions and the fixture ticker)
  live       the deadline has passed and FPL hasn't finished the gameweek: the top scorers so
             far, bonus points as they stand by BPS (provisional), the model's squad and the
             ML manager's team scored live, and the fixtures with their live scores
  finished   the recap: top 10 scorers, the Dream Team, the biggest hauls by position, the
             model's squad and the ML manager against FPL's average and highest, the model's best
             and worst calls, and the results

Nothing here re-implements a score. It composes the existing services:

  live_points       FPL's live feed (cached a minute by that module)
  model_review      the squad the model picked before the deadline, scored like FPL
  fpl_manager       the ML manager's team, its live result and its history
  season_service    fixtures, the Dream Team (team of the week, actual mode), meta.json events
  track_record      the saved pre-deadline predictions (`_saved_pick`, `prediction_rows`)

Every section is optional: a missing file, a failed lookup or a gameweek without a saved pick
leaves that section empty and adds a line to `notes`; the page never fails on it. An unknown
gameweek is a 404. The payload is cached for 60 seconds while the gameweek is live, 10 minutes
otherwise.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from config.settings import CURRENT_SEASON
from services import live_points as lp
from services import model_review as mr
from services import season_service as ss
from services import track_record as tr

logger = logging.getLogger(__name__)

LIVE_TTL_SECONDS = 60
SETTLED_TTL_SECONDS = 600
TOP_SCORERS = 10
HAULS_PER_POSITION = 3
CALLS_PER_LIST = 5
CALLS_POOL = 20            # "calls" are judged among the model's 20 highest predictions
BONUS_AWARDS = (3, 2, 1)
POSITIONS = ("GK", "DEF", "MID", "FWD")
SEASON_PATTERN = r"^\d{4}-\d{2}$"

Conn = Any
LiveFn = Callable[[int], Optional[Dict[str, Any]]]


# ------------------------------------------------------------------ small helpers

def live_fn() -> Optional[LiveFn]:
    """FPL's live feed for a gameweek, or None when LIVE_POINTS=0 (same switch as the review)."""
    if os.getenv("LIVE_POINTS", "1").strip() == "0":
        return None
    return lp.gameweek_live


def fpl_fixture_id(graph_id: Any) -> Optional[int]:
    """FPL's fixture id from the graph's "{fpl_fixture_id}-2026-27"; None for the offline CSV schedule."""
    head = str(graph_id or "").split("-", 1)[0]
    return int(head) if head.isdigit() else None


def player_directory(season: str = CURRENT_SEASON) -> Dict[int, Dict[str, Any]]:
    """{element: {name, web_name, team, position, code}} from the FPL status file."""
    path = ss.status_path(season)

    def load() -> Dict[int, Dict[str, Any]]:
        if not os.path.exists(path):
            return {}
        import pandas as pd
        df = pd.read_csv(path, usecols=lambda c: c in ("element", "code", "name", "web_name", "team", "position"))
        out: Dict[int, Dict[str, Any]] = {}
        for r in df.to_dict("records"):
            element = ss._int_or_none(r.get("element"))
            if element is None:
                continue
            out[element] = {"name": str(r.get("name") or element), "web_name": ss._str_or_none(r.get("web_name")),
                            "team": ss._str_or_none(r.get("team")), "code": ss._int_or_none(r.get("code")),
                            "position": ss.normalize_position(r.get("position"))}
        return out

    return ss._cached(("gameweek-page-players", path, ss._mtime(path)), load)


def player_row(element: int, directory: Dict[int, Dict[str, Any]], photo_for: Optional[ss.PhotoFn],
               **extra: Any) -> Dict[str, Any]:
    """A player's identity block (name, short name, club, position, photo) plus `extra`."""
    info = directory.get(element) or {}
    name = info.get("name") or f"Player {element}"
    photo = None
    if photo_for is not None and info:
        try:
            photo = photo_for(name, info.get("team"), info.get("code"))
        except Exception:
            photo = None
    return {"element": element, "name": name, "web_name": info.get("web_name"), "team": info.get("team"),
            "position": info.get("position"), "photo": photo, **extra}


# ------------------------------------------------------------------ pure: state

def gameweek_state(event: Dict[str, Any], fixtures: List[Dict[str, Any]], now: Optional[datetime] = None) -> str:
    """"finished", "live" or "upcoming". Finished: FPL says so, or every fixture has its final
    score. Live: not finished and the deadline has passed (or a match has started)."""
    if event.get("finished") or event.get("data_checked"):
        return "finished"
    if fixtures and all(f.get("status") == "finished" for f in fixtures):
        return "finished"
    if lp.is_live(event, event.get("deadline_time"), now):
        return "live"
    if any(f.get("status") in ("in_progress", "finished") for f in fixtures):
        return "live"
    return "upcoming"


def default_gameweek(summary: Dict[str, Any], gameweeks: List[int]) -> Optional[int]:
    """The current gameweek, else the next, else the first: what /gameweek shows."""
    for gw in (summary.get("current_gameweek"), summary.get("next_gameweek")):
        if gw is not None and gw in gameweeks:
            return gw
    return gameweeks[0] if gameweeks else None


# ------------------------------------------------------------------ pure: fixtures and scorers

def overlay_live_fixtures(fixtures: List[Dict[str, Any]], feed: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The gameweek's fixtures (season_service.fixture_out rows) with FPL's live scores and
    status laid over the graph's, which lags until the pipeline's next run."""
    out = []
    for f in fixtures:
        row = dict(f)
        live = (feed or {}).get("fixtures", {}).get(fpl_fixture_id(f.get("id")) or -1)
        if live and live.get("started"):
            row["status"] = "finished" if live.get("finished") else "in_progress"
            row["home_score"], row["away_score"] = live.get("home_score"), live.get("away_score")
            row["minutes"] = live.get("minutes")
        out.append(row)
    return out


def top_scorers(actual: Dict[int, Dict[str, int]], directory: Dict[int, Dict[str, Any]],
                photo_for: Optional[ss.PhotoFn], n: int = TOP_SCORERS,
                bps: Optional[Dict[int, int]] = None) -> List[Dict[str, Any]]:
    """The n best scores of the gameweek, most points first, then fewest minutes (same score in
    less time reads better), then element id so the order is stable."""
    ranked = sorted(((e, v) for e, v in actual.items() if v.get("points") is not None),
                    key=lambda kv: (-kv[1]["points"], kv[1].get("minutes") or 0, kv[0]))[:n]
    return [player_row(e, directory, photo_for, points=int(v["points"]), minutes=int(v.get("minutes") or 0),
                       bps=(bps or {}).get(e)) for e, v in ranked]


def hauls_by_position(actual: Dict[int, Dict[str, int]], directory: Dict[int, Dict[str, Any]],
                      photo_for: Optional[ss.PhotoFn], n: int = HAULS_PER_POSITION) -> Dict[str, List[Dict[str, Any]]]:
    """The n best scores of each position."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for position in POSITIONS:
        pool = {e: v for e, v in actual.items() if (directory.get(e) or {}).get("position") == position}
        out[position] = top_scorers(pool, directory, photo_for, n)
    return out


def fixture_bonus(rows: Dict[int, Dict[str, Any]]) -> List[Tuple[int, int, int]]:
    """[(element, bps, bonus)] for one fixture's players with a BPS score, best first. The top
    three BPS get 3, 2, 1; a tie shares the place's award and pushes the next place down:
    two level on top both get 3 and the next gets 1; two level second both get 2 and nobody
    gets 1; two level third both get 1. Players without BPS are left out."""
    scored = sorted(((e, int(r["bps"])) for e, r in rows.items() if r.get("bps") is not None),
                    key=lambda x: (-x[1], x[0]))
    out: List[Tuple[int, int, int]] = []
    place = 0
    i = 0
    while i < len(scored):
        j = i
        while j < len(scored) and scored[j][1] == scored[i][1]:
            j += 1
        award = BONUS_AWARDS[place] if place < len(BONUS_AWARDS) else 0
        out.extend((e, bps, award) for e, bps in scored[i:j])
        place += j - i
        i = j
    return out


def bonus_tables(feed: Optional[Dict[str, Any]], fixtures: List[Dict[str, Any]],
                 directory: Dict[int, Dict[str, Any]], photo_for: Optional[ss.PhotoFn]) -> List[Dict[str, Any]]:
    """Bonus as it stands for each started fixture of the gameweek that has BPS in the live feed.
    `confirmed` once the fixture is over and FPL has added the bonus to its players' points;
    until then it is provisional. Double-gameweek players carry no BPS in the feed and are left out."""
    if not feed:
        return []
    out = []
    for f in fixtures:
        fid = fpl_fixture_id(f.get("id"))
        if fid is None or f.get("status") not in ("in_progress", "finished"):
            continue
        rows = (feed.get("by_fixture") or {}).get(fid) or {}
        awards = [a for a in fixture_bonus(rows) if a[2] > 0]
        if not awards:
            continue
        confirmed = f.get("status") == "finished" and any((rows[e].get("bonus") or 0) > 0 for e, _, _ in awards)
        out.append({
            "fixture_id": f["id"], "home_team": f["home_team"], "away_team": f["away_team"],
            "home_score": f.get("home_score"), "away_score": f.get("away_score"),
            "status": f["status"], "confirmed": confirmed,
            "players": [player_row(e, directory, photo_for, bps=bps, bonus=award) for e, bps, award in awards],
        })
    return out


def bps_lookup(feed: Optional[Dict[str, Any]]) -> Dict[int, int]:
    """{element: BPS} across the feed's fixtures (a double-gameweek player's BPS is summed)."""
    out: Dict[int, int] = {}
    for rows in ((feed or {}).get("by_fixture") or {}).values():
        for e, r in rows.items():
            if r.get("bps") is not None:
                out[e] = out.get(e, 0) + int(r["bps"])
    return out


# ------------------------------------------------------------------ pure: the model's calls

def model_calls(rows: List[Dict[str, Any]], actual: Dict[int, Dict[str, int]],
                directory: Dict[int, Dict[str, Any]], photo_for: Optional[ss.PhotoFn],
                n: int = CALLS_PER_LIST, pool: int = CALLS_POOL) -> Dict[str, List[Dict[str, Any]]]:
    """Predicted against actual, among the players with a fixture, from the predictions saved
    before the deadline (track_record.prediction_rows).
      best    of the model's `pool` highest predictions, those who scored most (it backed them, they delivered)
      worst   of the same pool, the biggest shortfalls against the prediction (backed, flopped)
      missed  players it had low who scored far above their prediction (the hauls it didn't see)
    Each entry has predicted, actual, minutes and gap (actual - predicted)."""
    candidates = [r for r in rows if r["has_fixture"] and r["element"] in actual]
    ranked = sorted(candidates, key=lambda r: (-r["predicted"], r["element"]))

    def entry(r: Dict[str, Any]) -> Dict[str, Any]:
        a = actual[r["element"]]
        info = directory.get(r["element"]) or {}
        row = player_row(r["element"], directory, photo_for, predicted=round(r["predicted"], 2),
                         actual=int(a["points"]), minutes=int(a.get("minutes") or 0),
                         gap=round(a["points"] - r["predicted"], 2))
        if not info:
            row["name"] = r["name"]
        return row

    backed = ranked[:pool]
    best = sorted(backed, key=lambda r: (-actual[r["element"]]["points"], -r["predicted"], r["element"]))[:n]
    worst = sorted(backed, key=lambda r: (actual[r["element"]]["points"] - r["predicted"], r["element"]))[:n]
    worst = [r for r in worst if actual[r["element"]]["points"] < r["predicted"]]
    backed_ids = {r["element"] for r in backed}
    missed = sorted((r for r in candidates if r["element"] not in backed_ids),
                    key=lambda r: (-(actual[r["element"]]["points"] - r["predicted"]), r["element"]))[:n]
    missed = [r for r in missed if actual[r["element"]]["points"] > r["predicted"]]
    return {"best": [entry(r) for r in best], "worst": [entry(r) for r in worst],
            "missed": [entry(r) for r in missed]}


def saved_predictions(season: str, gameweek: int, event: Dict[str, Any]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """(rows, None) for the predictions saved before the deadline, or (None, why not). The same
    no-leakage rule as the track record: the pick must say it was archived before the deadline."""
    picks, csv_path = tr._saved_pick(season, gameweek)
    if picks is None:
        return None, "No prediction was saved before this deadline."
    if not csv_path or not os.path.exists(csv_path):
        return None, "The predictions saved before this deadline are missing."
    deadlines = [t for t in (mr._parse_time(picks.get("deadline")), mr._parse_time(event.get("deadline_time"))) if t]
    archived = mr._parse_time(picks.get("archived_at"))
    if not deadlines or archived is None or archived >= min(deadlines):
        return None, "The saved predictions can't be shown to predate the deadline."
    try:
        import pandas as pd
        rows = tr.prediction_rows(pd.read_csv(csv_path).to_dict("records"))
    except Exception:
        logger.warning("Gameweek page: could not read %s", csv_path, exc_info=True)
        return None, "The saved predictions couldn't be read."
    return (rows, None) if rows else (None, "The saved predictions have no player ids.")


# ------------------------------------------------------------------ pure: squads

def squad_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    """The model's squad for the page, from a model_review payload."""
    def short(p: Dict[str, Any]) -> Dict[str, Any]:
        return {k: p.get(k) for k in ("name", "web_name", "team", "position", "predicted_points", "points",
                                      "minutes", "multiplier", "captain", "vice_captain", "subbed_in", "photo")}

    captain = next((p for p in review["starters"] if p.get("multiplier", 0) > 1), None)
    return {
        "points": review["points"], "bench_points": review["bench_points"],
        "predicted_points": review["predicted_points"], "formation": review["formation"],
        "fpl_average": review.get("fpl_average"), "fpl_highest": review.get("fpl_highest"),
        "status": review.get("status"), "live": review.get("live"),
        "captain": short(captain) if captain else None,
        "starters": [short(p) for p in review["starters"]], "bench": [short(p) for p in review["bench"]],
        "auto_subs": review.get("auto_subs") or [],
    }


def manager_summary(view: Dict[str, Any], gameweek: int) -> Optional[Dict[str, Any]]:
    """The ML manager's gameweek from a get_manager_view payload: the current entry's result
    (provisional while live) or the history row of a scored gameweek. None when it wasn't managed."""
    current = view.get("current") or {}
    if int(current.get("gameweek") or 0) == gameweek and current.get("result"):
        r = current["result"]
        players = [{k: p.get(k) for k in ("name", "web_name", "position", "points", "minutes", "multiplier",
                                          "captain", "vice_captain", "subbed_in", "subbed_out")}
                   for p in r.get("players") or [] if p.get("counted")]
        players.sort(key=lambda p: -((p["points"] or 0) * (p["multiplier"] or 0)))
        moves = [{"out": (t.get("out") or {}).get("web_name") or (t.get("out") or {}).get("name"),
                  "in": (t.get("in") or {}).get("web_name") or (t.get("in") or {}).get("name")}
                 for t in current.get("transfers") or []]
        return {"status": current.get("status"), "points": r["points"], "gross": r.get("gross"),
                "hit_cost": r.get("hit_cost"), "chip": r.get("chip"), "captain": r.get("captain"),
                "gw_average": r.get("gw_average"), "gw_highest": r.get("gw_highest"), "total": r.get("total"),
                "live": r.get("live"), "league": r.get("league"), "transfers": moves, "players": players[:15]}
    row = next((h for h in view.get("history") or [] if int(h.get("gameweek") or 0) == gameweek), None)
    if row is None:
        return None
    return {"status": "scored", "points": row["points"], "gross": row.get("gross"), "hit_cost": row.get("hit_cost"),
            "chip": row.get("chip"), "captain": row.get("captain"), "gw_average": row.get("gw_average"),
            "gw_highest": row.get("gw_highest"), "total": row.get("total"), "live": None,
            "league": {"id": row.get("league_id"), "name": row.get("league_name"), "rank": row.get("league_rank"),
                       "size": row.get("league_size"), "percentile": row.get("percentile")}
            if row.get("league_rank") is not None else None,
            "transfers": [], "transfer_count": row.get("transfers"), "players": []}


# ------------------------------------------------------------------ payload

def build_gameweek(gameweek: int, conn: Optional[Conn], photo_for: Optional[ss.PhotoFn] = None,
                   live: Optional[LiveFn] = None, season: str = CURRENT_SEASON,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """The page's payload. Raises season_service.NotFoundError for a gameweek the season doesn't have."""
    notes: List[str] = []
    meta = ss.read_meta(season)
    events = {e["id"]: e for e in meta.get("events") or [] if isinstance(e, dict) and isinstance(e.get("id"), int)}
    all_fixtures: List[Dict[str, Any]] = []
    if conn is not None:
        try:
            all_fixtures = ss.load_fixtures(conn, season)
        except Exception as exc:
            logger.warning("Gameweek page: fixtures unavailable: %s", exc)
            notes.append("Fixtures couldn't be loaded just now.")
    else:
        notes.append("Fixtures need the database, which isn't connected.")
    gameweeks = sorted(set(ss.gameweeks_of(all_fixtures)) | set(events))
    if gameweek not in gameweeks:
        raise ss.NotFoundError(f"No {season} gameweek {gameweek}")

    event = events.get(gameweek) or {}
    deadline = event.get("deadline_time") or (meta.get("next_deadline") if meta.get("next_gameweek") == gameweek else None)
    event = {**event, "deadline_time": deadline} if deadline else event
    fixtures = ss.fixtures_for_gameweek(all_fixtures, gameweek)
    state = gameweek_state(event, fixtures, now)

    feed = None
    if state == "live" and live is not None:
        try:
            feed = live(gameweek)
        except Exception as exc:
            logger.warning("Gameweek page: live feed failed: %s", exc)
        if not feed:
            notes.append("FPL's live feed isn't answering, so points are from the last full update.")
    fixtures = overlay_live_fixtures(fixtures, feed)

    directory = player_directory(season)
    actual = lp.merge(mr.actual_by_element(gameweek, season), feed) if state != "upcoming" else {}
    if state != "upcoming" and not actual:
        notes.append("No player points for this gameweek yet.")

    payload: Dict[str, Any] = {
        "season": season, "gameweek": gameweek, "gameweeks": gameweeks, "state": state,
        "deadline": deadline, "fpl_average": event.get("average_entry_score") or None,
        "fpl_highest": event.get("highest_score") or None,
        "live": {"fetched_at": feed["fetched_at"]} if feed else None,
        "fixtures": fixtures, "top_scorers": [], "bonus": [], "hauls": {}, "dream_team": None,
        "model_squad": None, "ml_manager": None, "calls": None, "notes": notes,
    }
    if state == "upcoming":
        return payload

    payload["top_scorers"] = top_scorers(actual, directory, photo_for, bps=bps_lookup(feed))
    if state == "live":
        payload["bonus"] = bonus_tables(feed, fixtures, directory, photo_for)
    else:
        payload["hauls"] = hauls_by_position(actual, directory, photo_for)
        if conn is not None:
            try:
                payload["dream_team"] = ss.get_team_of_the_week(conn, "actual", gameweek, photo_for=photo_for, season=season)
            except ss.NotFoundError:
                notes.append("The Dream Team needs the gameweek's player points, which aren't in yet.")
            except Exception as exc:
                logger.warning("Gameweek page: Dream Team failed: %s", exc)
                notes.append("The Dream Team couldn't be worked out just now.")

    try:
        payload["model_squad"] = squad_summary(mr.get_model_review(gameweek, photo_for=photo_for, season=season, live=live))
    except ss.NotFoundError:
        notes.append("The model has no saved squad for this gameweek.")
    except Exception as exc:
        logger.warning("Gameweek page: model review failed: %s", exc)
        notes.append("The model's squad couldn't be scored just now.")

    try:
        from services import fpl_manager
        view = fpl_manager.get_manager_view(season, photo_for=photo_for, live=live, now=now)
        payload["ml_manager"] = manager_summary(view, gameweek)
        if payload["ml_manager"] is None:
            notes.append("The ML manager didn't play this gameweek.")
    except ss.NotFoundError:
        notes.append("The ML manager hasn't started yet.")
    except Exception as exc:
        logger.warning("Gameweek page: ML manager failed: %s", exc)
        notes.append("The ML manager's score couldn't be read just now.")

    if state == "finished":
        rows, why = saved_predictions(season, gameweek, event)
        if rows is None:
            notes.append(why or "No saved predictions.")
        elif actual:
            payload["calls"] = model_calls(rows, actual, directory, photo_for)
    return payload


# ------------------------------------------------------------------ cache and routes

_cache_lock = threading.Lock()
_cache: Dict[Tuple, Tuple[float, float, Dict[str, Any]]] = {}      # key -> (stored at, ttl, payload)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cached_gameweek(gameweek: int, conn: Optional[Conn], photo_for: Optional[ss.PhotoFn] = None,
                    live: Optional[LiveFn] = None, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """build_gameweek, kept 60 s while the gameweek is live and 10 minutes otherwise. Payloads
    built without a database connection aren't kept (they would hide it coming back)."""
    key = (season, gameweek, conn is not None)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < hit[1]:
            return hit[2]
    payload = build_gameweek(gameweek, conn, photo_for, live, season)
    if conn is not None:
        ttl = LIVE_TTL_SECONDS if payload["state"] == "live" else SETTLED_TTL_SECONDS
        with _cache_lock:
            _cache[key] = (time.monotonic(), ttl, payload)
    return payload


def current_gameweek(conn: Optional[Conn], season: str = CURRENT_SEASON) -> Optional[int]:
    """The gameweek /gameweek opens on (see default_gameweek)."""
    meta = ss.read_meta(season)
    fixtures: List[Dict[str, Any]] = []
    if conn is not None:
        try:
            fixtures = ss.load_fixtures(conn, season)
        except Exception as exc:
            logger.warning("Gameweek page: fixtures unavailable: %s", exc)
    gameweeks = sorted(set(ss.gameweeks_of(fixtures)) | {e["id"] for e in meta.get("events") or []
                                                         if isinstance(e, dict) and isinstance(e.get("id"), int)})
    return default_gameweek(ss.summarize_season(fixtures, meta, season), gameweeks)


def register(app: Any, get_conn: Callable[[], Any], photo_for: Optional[ss.PhotoFn] = None) -> None:
    """Add GET /api/gameweek/current and GET /api/gameweek/{gw}. `get_conn` is api_main's
    get_neo4j_conn, called directly (not through Depends) so a missing connection gives a page
    with notes instead of a 503. Plain `def` routes: the Neo4j driver is synchronous."""
    from fastapi import HTTPException, Query

    def optional_conn() -> Any:
        try:
            return get_conn()
        except Exception:
            return None

    @app.get("/api/gameweek/current")
    def gameweek_current(season: str = Query(CURRENT_SEASON, pattern=SEASON_PATTERN)) -> Dict[str, Any]:
        """The page for the current gameweek (else the next one)."""
        conn = optional_conn()
        gw = current_gameweek(conn, season)
        if gw is None:
            raise HTTPException(status_code=404, detail="No gameweeks yet")
        try:
            return cached_gameweek(gw, conn, photo_for, live_fn(), season)
        except ss.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/gameweek/{gw}")
    def gameweek_page(gw: int, season: str = Query(CURRENT_SEASON, pattern=SEASON_PATTERN)) -> Dict[str, Any]:
        """One gameweek: upcoming, live (points so far, provisional bonus, squads scored live) or
        finished (the recap). 404 for a gameweek the season doesn't have."""
        if not 1 <= gw <= 38:
            raise HTTPException(status_code=404, detail="No such gameweek")
        try:
            return cached_gameweek(gw, optional_conn(), photo_for, live_fn(), season)
        except ss.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
