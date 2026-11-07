"""
Forecast routes: the predicted final table and per-match probabilities, from the team-strength
model (services/team_strength.py).

    GET /api/table/forecast                    predicted final table + the model's ratings
    GET /api/matches/{fixture_id}/forecast     win/draw/win, expected goals, likeliest scores,
                                               clean-sheet chances of one unplayed match

Both are plain `def` routes (the Neo4j driver is synchronous). The model is fitted to the
current season's finished fixtures (`season_service.load_fixtures`), with last season's fixtures
(`teams.season_fixtures`) as its prior. The result is cached for ten minutes under a signature
of the fixtures (ids, status, scores), so a new result refits at once and a quiet page costs
nothing. The simulation is seeded: the page is the same between loads until a result changes.

Only results and fixtures are used, never bookmakers' odds (football-data's files may not be
redistributed).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

from config.settings import CURRENT_SEASON, SEASONS
from services import season_service as ss
from services import team_strength as ts
from services import teams as teams_service

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
FIXTURE_ID_PATTERN = r"[A-Za-z0-9-]{3,40}"

_cache: Dict[Tuple, Tuple[float, Any]] = {}
_lock = Lock()


class NotFoundError(LookupError):
    """Unknown fixture, or one that has been played (HTTP 404)."""


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def previous_season(season: str = CURRENT_SEASON) -> Optional[str]:
    i = SEASONS.index(season) if season in SEASONS else -1
    return SEASONS[i - 1] if i > 0 else None


def signature(fixtures: List[Dict[str, Any]], previous: List[Dict[str, Any]]) -> str:
    """Changes whenever a fixture is added, moves to another status or gets a score."""
    parts = [f"{f['id']}:{f['status']}:{f['home_score']}:{f['away_score']}" for f in fixtures]
    parts.append(f"prev:{len(previous)}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _inputs(conn) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    fixtures = ss.load_fixtures(conn, CURRENT_SEASON)
    prev = previous_season()
    previous = teams_service.season_fixtures(conn, prev) if prev else []
    return fixtures, previous


def _cached_build(conn, simulations: Optional[int] = None, seed: int = ts.DEFAULT_SEED) -> Dict[str, Any]:
    """The fitted model for the current fixtures, and (when `simulations` is given) its simulated
    table; both cached for CACHE_TTL_SECONDS under the fixtures' signature."""
    fixtures, previous = _inputs(conn)
    key = signature(fixtures, previous)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        built = hit[1] if hit is not None and now - hit[0] < CACHE_TTL_SECONDS else None
        if built is None:
            for k in [k for k, (t, _) in _cache.items() if now - t >= CACHE_TTL_SECONDS]:
                del _cache[k]
            # compute under the lock: concurrent first loads wait for one fit, not ten
            built = {"model": ts.TeamStrength.fit(fixtures, previous), "fixtures": fixtures, "tables": {},
                     "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            _cache[key] = (now, built)
        if simulations is not None and (simulations, seed) not in built["tables"]:
            built["tables"][(simulations, seed)] = built["model"].simulate_season(simulations, seed)
    return built


# ------------------------------------------------------------------ payloads

SIMULATIONS = ts.DEFAULT_SIMULATIONS     # what the route runs; tests lower it


def table_forecast(conn, simulations: int = ts.DEFAULT_SIMULATIONS, seed: int = ts.DEFAULT_SEED) -> Dict[str, Any]:
    """The predicted final table: one row a club, best expected points first."""
    built = _cached_build(conn, simulations, seed)
    model: ts.TeamStrength = built["model"]
    rows = built["tables"][(simulations, seed)]
    return {
        "season": CURRENT_SEASON,
        "generated_at": built["generated_at"],
        "simulations": simulations,
        "seed": seed,
        "played_matches": sum(1 for f in built["fixtures"] if f["status"] == "finished"),
        "remaining_matches": sum(1 for f in built["fixtures"] if f["status"] != "finished"),
        "zones": {"champions_league": ts.CHAMPIONS_LEAGUE_PLACES, "europe": ts.EUROPE_PLACES,
                  "relegation": ts.RELEGATION_PLACES},
        "model": model.summary(),
        "ratings": model.ratings(),
        "rows": rows,
    }


def match_forecast(conn, fixture_id: str) -> Dict[str, Any]:
    """Probabilities for one unplayed current-season fixture. Played, live and unknown fixtures
    raise NotFoundError."""
    built = _cached_build(conn)
    fixture = next((f for f in built["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise NotFoundError("No such match this season")
    if fixture["status"] != "scheduled":
        raise NotFoundError("This match has been played, so it has no forecast")
    model: ts.TeamStrength = built["model"]
    try:
        probabilities = model.match_probabilities(fixture["home_team"], fixture["away_team"])
    except ValueError:
        raise NotFoundError("No rating for this match's clubs")
    return {
        "fixture_id": fixture["id"], "season": CURRENT_SEASON, "gameweek": fixture["gameweek"],
        "kickoff_time": ss.to_iso(fixture["kickoff_time"]),
        **probabilities,
        "matches_in_model": model.matches,
        "generated_at": built["generated_at"],
    }


# ------------------------------------------------------------------ routes

def register(app, get_conn: Callable[[], Any]) -> None:
    """Add GET /api/table/forecast and GET /api/matches/{fixture_id}/forecast. `get_conn` is
    api_main's get_neo4j_conn (503 without Neo4j)."""
    from fastapi import Depends, HTTPException
    from neo4j.exceptions import DriverError

    def call(fn, *args):
        try:
            return fn(*args)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except DriverError as exc:
            logger.error("Neo4j unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Neo4j unavailable, try again shortly")

    @app.get("/api/table/forecast")
    def table_forecast_route(conn=Depends(get_conn)) -> Dict[str, Any]:
        """Predicted final table: expected points and position, title / top-4 / Europe / relegation
        chances and the position distribution, simulated from a team-strength model."""
        return call(table_forecast, conn, SIMULATIONS)

    @app.get("/api/matches/{fixture_id}/forecast")
    def match_forecast_route(fixture_id: str, conn=Depends(get_conn)) -> Dict[str, Any]:
        """The model's view of one unplayed match: win/draw/win, expected goals, likeliest scores
        and clean-sheet chances. Played or unknown fixtures -> 404."""
        if not re.fullmatch(FIXTURE_ID_PATTERN, fixture_id):
            raise HTTPException(status_code=404, detail="No such match")
        return call(match_forecast, conn, fixture_id)
