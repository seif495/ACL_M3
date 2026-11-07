"""Scorers of a gameweek's finished fixtures in one call (GET /api/fixtures/scorers?gameweek=N),
for the Fixtures page's result rows. The match page (services/matches.py) has them per match;
calling that for every fixture would be 10 requests a gameweek.

Source: PLAYED_IN goals and own goals of the finished fixtures (one query), plus the goal-minute
file (services/goal_events, ESPN's scoreboard via the daily job) when it has the match. With the
file the scorers are in time order with minutes and penalties; without it, one entry per player
and no minute. An own goal is listed under the side it counts for, like the match page.

Payload: {fixture_id: {"home": [scorer], "away": [scorer]}}, a scorer being
{"name", "web_name", "kind": "goal"|"penalty"|"own_goal", "minute", "extra", "count"}
(minute/extra null when unknown; count > 1 only without minutes).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query

from config.settings import CURRENT_SEASON
from services import season_service as ss

logger = logging.getLogger(__name__)

SCORERS_QUERY = """
MATCH (p:Player)-[r:PLAYED_IN]->(f:Fixture)
WHERE f.id IN $ids AND (coalesce(r.goals_scored, 0) > 0 OR coalesce(r.own_goals, 0) > 0)
RETURN f.id AS fixture_id, p.name AS name, r.team AS team,
       coalesce(r.goals_scored, 0) AS goals, coalesce(r.own_goals, 0) AS own_goals
"""

SCORER_KINDS = ("goal", "penalty", "own_goal")
WebNames = Optional[Dict[str, str]]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _entry(name: Any, kind: str, minute: Any = None, extra: Any = None, count: int = 1,
           web_names: WebNames = None) -> Dict[str, Any]:
    name = str(name) if name else "Unknown"
    return {"name": name, "web_name": (web_names or {}).get(name), "kind": kind,
            "minute": minute, "extra": extra, "count": count}


def from_rows(fixture: Dict[str, Any], rows: List[Dict[str, Any]],
              web_names: WebNames = None) -> Dict[str, List[Dict[str, Any]]]:
    """No goal minutes: one entry per scoring player, goals first, an own goal under the other side."""
    home_team, away_team = fixture["home_team"], fixture["away_team"]
    out: Dict[str, List[Dict[str, Any]]] = {"home": [], "away": []}
    for r in sorted(rows, key=lambda r: (-_int(r.get("goals")), str(r.get("name")))):
        side = "home" if r.get("team") == home_team else "away" if r.get("team") == away_team else None
        if side is None:
            continue
        if _int(r.get("goals")):
            out[side].append(_entry(r["name"], "goal", count=_int(r["goals"]), web_names=web_names))
        if _int(r.get("own_goals")):
            other = "away" if side == "home" else "home"
            out[other].append(_entry(r["name"], "own_goal", count=_int(r["own_goals"]), web_names=web_names))
    return out


def from_events(fixture: Dict[str, Any], rows: List[Dict[str, Any]], events: List[Dict[str, Any]],
                web_names: WebNames = None) -> Dict[str, List[Dict[str, Any]]]:
    """Goal events in time order, matched to our players (services/matches.timeline)."""
    from services import matches
    players = [{"name": r["name"], "team": r.get("team")} for r in rows]
    split = matches.timeline(events, fixture, players)
    return {s: [_entry(g.get("player"), g["kind"], g.get("minute"), g.get("extra"), web_names=web_names)
                for g in split[s] if g.get("kind") in SCORER_KINDS]
            for s in ("home", "away")}


def build(fixtures: List[Dict[str, Any]], raw_rows: List[Dict[str, Any]], events_for: Callable[[str], Any],
          web_names: WebNames = None) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """The payload for finished fixtures (rows of season_service.load_fixtures)."""
    by_fixture: Dict[str, List[Dict[str, Any]]] = {}
    for r in raw_rows:
        by_fixture.setdefault(str(r["fixture_id"]), []).append(r)
    out: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for f in fixtures:
        fid = str(f["id"])
        rows = by_fixture.get(fid, [])
        events = events_for(fid)
        out[fid] = from_events(f, rows, events, web_names) if events is not None else from_rows(f, rows, web_names)
    return out


def finished_fixtures(all_fixtures: List[Dict[str, Any]], gameweek: int) -> List[Dict[str, Any]]:
    return [f for f in all_fixtures
            if f.get("gameweek") == gameweek and f.get("status") == "finished" and f.get("home_score") is not None
            and f.get("home_team") and f.get("away_team")]


def get_scorers(conn, gameweek: int, season: str = CURRENT_SEASON) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Cached like season_service (5 minutes, cleared with its cache; the key holds the finished
    fixtures and the goal-minute file's mtime, so a new result or file shows at once)."""
    from services import goal_events as ge
    fixtures = finished_fixtures(ss.load_fixtures(conn, season), gameweek)
    if not fixtures:
        return {}
    ids = sorted(str(f["id"]) for f in fixtures)
    store = ge.store_path(ss.live_dir(), season)

    def load() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        rows = conn.execute_query(SCORERS_QUERY, {"ids": ids})
        return build(fixtures, rows, lambda fid: ge.events_for(fid, season), ss.status_lookup(season)["web_name"])

    return ss._cached(("gw-scorers", season, gameweek, tuple(ids), ss._mtime(store)), load)


def register(app: FastAPI, get_conn: Callable[..., Any]) -> None:
    """Add GET /api/fixtures/scorers. A plain `def` route (blocking driver)."""

    @app.get("/api/fixtures/scorers")
    def fixture_scorers(gameweek: int = Query(..., ge=1, le=38), conn=Depends(get_conn)):
        from neo4j.exceptions import DriverError
        try:
            return get_scorers(conn, gameweek)
        except DriverError as exc:
            logger.error("Neo4j unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Neo4j unavailable, try again shortly")
