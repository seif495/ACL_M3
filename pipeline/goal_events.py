"""
Goal minutes and penalties from ESPN's scoreboard (services/goal_events.py) for finished matches.

    python -m pipeline.goal_events --check             # the last 10 days as ESPN shows them, saves nothing
    python -m pipeline.goal_events                     # this season's finished matches
    python -m pipeline.goal_events --season 2025-26    # backfill a past season

pipeline.daily runs `run` after the graph refresh. It never raises: an error (ESPN down or its
format changed) is reported in the summary while match pages show FPL's scorers without minutes.
GOAL_EVENTS=0 turns the step off.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from config.settings import CURRENT_SEASON
from pipeline.current_season import LIVE_DATA_DIR
from services import goal_events as ge

logger = logging.getLogger(__name__)

MAX_CALLS = 15          # a month of matches per call: a whole season's backfill fits in one run


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
    if os.getenv("GOAL_EVENTS", "1").strip() == "0":
        return {"skipped": "GOAL_EVENTS=0"}
    try:
        return ge.update(fixtures if fixtures is not None else _fixtures(season), season, live_dir, max_calls=max_calls)
    except Exception as exc:  # network, a changed format: try again next run
        logger.warning("Goal events step failed: %s", exc)
        return {"season": season, "error": f"{type(exc).__name__}: {exc}"}


def check(days: int = 10) -> Dict[str, Any]:
    """The last `days` days of Premier League matches on ESPN's scoreboard, how each one's goals
    parse, and one match's raw details, to see the feed's format."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    matches = []
    for month in ge.months([start.date(), end.date()]):
        matches += [m for m in ge.espn_matches(ge.http_get({"dates": month}))
                    if m["date"] and start <= ge.ss.to_datetime(m["date"]) <= end]
    done = [m for m in matches if m["completed"]]
    out: Dict[str, Any] = {"matches": len(matches), "finished": len(done), "parsed": []}
    for m in done:
        rows = ge.goal_rows(m, m["home"], m["away"])
        out["parsed"].append({
            "match": f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}",
            "goals": None if rows is None else [
                f"{r['player']} {r['minute']}{'+' + str(r['extra']) if r['extra'] else ''}' {r['kind']} ({r['team']})"
                for r in rows],
        })
    sample = next((m for m in reversed(done) if any(d.get("ownGoal") or d.get("penaltyKick") for d in m["details"])),
                  done[-1] if done else None)
    if sample:
        out["raw_details_sample"] = {"match": f"{sample['home']} v {sample['away']}", "details": sample["details"]}
    return out


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--season", default=CURRENT_SEASON)
    p.add_argument("--check", action="store_true", help="show the last 10 days as ESPN has them, save nothing")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS)
    args = p.parse_args(argv)
    result = check() if args.check else run(season=args.season, max_calls=args.max_calls)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
