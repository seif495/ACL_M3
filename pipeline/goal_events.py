"""
Goal minutes and penalties from API-Football (services/goal_events.py) for finished matches.

    python -m pipeline.goal_events --check             # what the key's plan covers (2-3 calls)
    python -m pipeline.goal_events                     # this season's finished matches
    python -m pipeline.goal_events --season 2025-26    # backfill a past season (as the plan allows)

pipeline.daily runs `run` after the graph refresh. It never raises: without API_FOOTBALL_KEY it
does nothing, and an API error (a season the plan doesn't cover, the day's limit) is reported in
its summary while the match pages simply show goals without minutes.
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, Optional

from config.settings import CURRENT_SEASON
from pipeline.current_season import LIVE_DATA_DIR
from services import goal_events as ge

logger = logging.getLogger(__name__)

MAX_CALLS = 30          # per run; the free plan allows 100 a day and the daily job runs 2-5 times


def _fixtures(season: str):
    from config.settings import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
    from graph.connection import Neo4jConnection
    from services.teams import season_fixtures

    conn = Neo4jConnection(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        return season_fixtures(conn, season)
    finally:
        conn.close()


def run(live_dir: str = LIVE_DATA_DIR, season: str = CURRENT_SEASON, max_calls: int = MAX_CALLS,
        fixtures=None) -> Dict[str, Any]:
    if not ge.api_key():
        return {"skipped": "no API_FOOTBALL_KEY"}
    try:
        return ge.update(fixtures if fixtures is not None else _fixtures(season), season, live_dir, max_calls=max_calls)
    except ge.ApiFootballError as exc:
        logger.warning("API-Football: %s", exc)
        return {"season": season, "error": str(exc)}
    except Exception as exc:  # network and the like: try again next run
        logger.warning("Goal events step failed: %s", exc)
        return {"season": season, "error": f"{type(exc).__name__}: {exc}"}


def check(season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """The account's plan and today's usage, then whether `season` is covered and whether a
    finished match comes with goal events (minute, penalty)."""
    out: Dict[str, Any] = {"season": season}
    status = ge.http_get("status", {}).get("response") or {}
    out["plan"] = (status.get("subscription") or {}).get("plan")
    out["requests_today"] = status.get("requests")
    try:
        fixtures = ge.api_fixtures(ge.http_get("fixtures", {"league": ge.PREMIER_LEAGUE, "season": ge.season_year(season)}))
    except ge.ApiFootballError as exc:
        out["season_covered"] = False
        out["error"] = str(exc)
        return out
    done = [f for f in fixtures if f["status"] in ("FT", "AET", "PEN")]
    out.update(season_covered=bool(fixtures), matches=len(fixtures), finished=len(done))
    if done:
        sample = ge.api_fixtures(ge.http_get("fixtures", {"ids": str(done[-1]["api_id"])}))
        if sample:
            goals = ge.goal_rows(sample[0]["events"] or [], [sample[0]["home"], sample[0]["away"]])
            out["sample"] = {"match": f"{sample[0]['home']} v {sample[0]['away']}", "goals": goals}
    return out


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--season", default=CURRENT_SEASON)
    p.add_argument("--check", action="store_true", help="show what the plan covers, fetch nothing to disk")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS)
    args = p.parse_args(argv)
    if not ge.api_key():
        print("API_FOOTBALL_KEY isn't set")
        return 1
    result = check(args.season) if args.check else run(season=args.season, max_calls=args.max_calls)
    print(json.dumps(result, indent=2, default=str))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
