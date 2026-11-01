"""
Daily refresh of the current season, run on the VM by a systemd timer
(deploy/install-daily-job.sh) inside the backend container:

    docker compose -f deploy/docker-compose.yml run --rm backend python -m pipeline.daily

Steps:
  1. pipeline.current_season: refresh news/availability and expected points; re-fetch match
     history only when FPL has finalized new results
  2. pipeline.load_current: apply new results, squads and availability to the graph
     (skipped when nothing new was fetched, unless --force)
  3. pipeline.retrain (Mondays): retrain the v4 points model on everything up to the weekend;
     the new model is only used if it passes the guardrail in pipeline/retrain.py
  4. pipeline.predict_next_gw: refresh the bookmaker odds (LIVE_DATA_DIR/odds), regenerate
     next-gameweek predictions (availability changes daily) and, before the deadline, save the
     live model's and the shadow model's (v3) squad picks
  5. pipeline.ml_manager: the ML manager's gameweek cycle (lock plans at the deadline, score
     finished gameweeks and rank them among the managers who started in GW9, plan the next
     gameweek from projections.csv)
  6. POST /api/admin/reload so the running API rebuilds its caches without a restart
  7. Ping HEALTHCHECK_URL (e.g. healthchecks.io) on success, HEALTHCHECK_URL/fail on failure,
     so a silent failure turns into an email. Failures the steps report instead of raising count
     too (run_problems): an ML manager step that failed, a plan the rules rejected or whose solve
     stopped at its time limit, no plan for the next deadline or a captain who is out, an unsaved
     model pick, failed projections, an unreadable v4 input, and odds missing for most of the next
     gameweek after football-data.co.uk has collected them

The timer (06:00 and 18:00 UTC, plus Fridays at 17:15 and Tuesdays at 16:00 UK time, so an evening
deadline gets that day's odds and team news) runs it under one flock with every other pipeline run,
so two runs never overlap (deploy/install-daily-job.sh).

A second timer calls it every hour with --before-deadline, which runs only when the next deadline is
45-105 minutes away (PRE_DEADLINE_WINDOW) and no run started in the 150 minutes before it
(PRE_DEADLINE_RECENT), so late team news reaches the predictions, the saved pick and the ML
manager's plan. Otherwise it exits at once, without a healthcheck ping. Such a run never retrains.
Every run records its start in LIVE_DATA_DIR/daily_runs.json.

Environment: LIVE_DATA_DIR, ODDS_DIR (optional, default LIVE_DATA_DIR/odds), BACKEND_URL (default http://backend:8000), ADMIN_TOKEN,
HEALTHCHECK_URL (optional), plus the usual NEO4J_* settings.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd

from pipeline.current_season import LIVE_DATA_DIR, run as refresh_current_season

logger = logging.getLogger("pipeline.daily")

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "").rstrip("/")

# Odds: the next gameweek's share of upcoming rows with prices, once football-data.co.uk has
# collected them (ml/odds.collected_at of its first kickoff, plus a margin for publishing). Less is
# a failed download or a feed change, not a rearranged match or two.
MIN_ODDS_COVERAGE = 0.5
ODDS_PUBLISH_MARGIN = timedelta(minutes=15)

# --before-deadline: start between 105 and 45 minutes before the deadline (an hourly timer always
# fires once in that hour; a run takes 10-20 minutes), unless a run started within 150 minutes of it
PRE_DEADLINE_WINDOW = (timedelta(minutes=45), timedelta(minutes=105))
PRE_DEADLINE_RECENT = timedelta(minutes=150)
RUNS_FILE = "daily_runs.json"


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def next_deadline(meta: Dict[str, Any], now: datetime) -> Optional[datetime]:
    """The first deadline after `now` in meta.json (its events, or next_deadline)."""
    times = [e.get("deadline_time") for e in meta.get("events") or []] + [meta.get("next_deadline")]
    ahead = [t for t in pd.to_datetime(pd.Series(times, dtype=object), utc=True, errors="coerce").dropna()
             if t > now]
    return min(ahead).to_pydatetime() if ahead else None


def pre_deadline_due(live_dir: str, now: datetime) -> Tuple[bool, str]:
    """(whether a --before-deadline run should go ahead now, why)."""
    deadline = next_deadline(_read_json(os.path.join(live_dir, "meta.json")), now)
    if deadline is None:
        return False, "no deadline ahead in meta.json"
    soonest, latest = PRE_DEADLINE_WINDOW
    if not deadline - latest <= now <= deadline - soonest:
        return False, f"the next deadline ({deadline.isoformat()}) isn't 45-105 minutes away"
    last = pd.to_datetime(_read_json(os.path.join(live_dir, RUNS_FILE)).get("last_started"), utc=True,
                          errors="coerce")
    if pd.notna(last) and last >= deadline - PRE_DEADLINE_RECENT:
        return False, f"a run started at {last.isoformat()}, close enough to the {deadline.isoformat()} deadline"
    return True, f"the deadline is at {deadline.isoformat()}"


def _record_start(live_dir: str, now: datetime, before_deadline: bool) -> None:
    """daily_runs.json: when the last run started (what pre_deadline_due reads). Never raises."""
    path = os.path.join(live_dir, RUNS_FILE)
    try:
        os.makedirs(live_dir, exist_ok=True)
        with open(f"{path}.tmp", "w", encoding="utf-8") as f:
            json.dump({"last_started": now.isoformat(), "before_deadline": before_deadline}, f)
        os.replace(f"{path}.tmp", path)
    except OSError as exc:
        logger.warning("Couldn't record the run's start in %s: %s", path, exc)


def _ping(suffix: str = "", body: str = "") -> None:
    if not HEALTHCHECK_URL:
        return
    try:
        httpx.post(f"{HEALTHCHECK_URL}{suffix}", content=body[-10000:].encode(), timeout=10)
    except Exception as exc:  # monitoring must never break the job
        logger.warning("Healthcheck ping failed: %s", exc)


def _reload_backend() -> str:
    if not ADMIN_TOKEN:
        return "skipped (ADMIN_TOKEN not set)"
    try:
        resp = httpx.post(f"{BACKEND_URL}/api/admin/reload",
                          headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=120)
        resp.raise_for_status()
        return "ok"
    except Exception as exc:
        # Not fatal: the data is already in place; the API picks it up on its next restart
        logger.warning("Backend reload failed: %s", exc)
        return f"failed: {exc}"


def _run_retrain(force: bool = False) -> Dict[str, Any]:
    """pipeline.retrain in a child process: training peaks around 0.8 GB, and a separate
    process guarantees that memory is returned before predictions run on the 4 GB VM."""
    cmd = [sys.executable, "-m", "pipeline.retrain"] + (["--force"] if force else [])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"pipeline.retrain exited {proc.returncode}:\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


def _missing_odds(predictions: Dict[str, Any], now: datetime) -> Optional[str]:
    """The odds problem of a prediction run's summary, if any: the deadline is ahead, the prices
    for its first match have been collected, and most upcoming rows still have none."""
    from ml import odds as od

    coverage = (predictions.get("input_coverage") or {}).get("odds")
    deadline = pd.to_datetime(predictions.get("deadline"), utc=True, errors="coerce")
    if coverage is None or pd.isna(deadline) or now >= deadline:
        return None
    collected = od.collected_at(pd.Series([deadline + timedelta(minutes=od.DEADLINE_MINUTES)])).iloc[0]
    if pd.isna(collected) or now < collected + ODDS_PUBLISH_MARGIN or coverage >= MIN_ODDS_COVERAGE:
        return None
    return (f"predictions: odds for {coverage:.0%} of GW{predictions.get('gameweek')}'s rows, collected by "
            f"{collected.isoformat()} (odds files: {predictions.get('odds')})")


def run_problems(summary: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """What a run's summary shows went wrong without raising (see the module docstring)."""
    now = now or datetime.now(timezone.utc)
    problems = []
    predictions = summary.get("predictions")
    if isinstance(predictions, dict):
        if predictions.get("model_pick") == "failed":
            problems.append("predictions: the model's pick wasn't saved")
        failed = (predictions.get("projections") or {}).get("failed")
        if failed:
            problems.append(f"predictions: projections failed: {failed}")
        if predictions.get("extra_inputs") not in (None, "ok"):
            problems.append(f"predictions: unreadable v4 inputs: {predictions['extra_inputs']}")
        odds = _missing_odds(predictions, now)
        if odds:
            problems.append(odds)
    manager = summary.get("ml_manager")
    if isinstance(manager, dict):
        for step, value in manager.items():
            if isinstance(value, str) and value.startswith("failed"):
                problems.append(f"ml_manager {step}: {value}")
        plan = manager.get("plan")
        if isinstance(plan, dict) and plan.get("status") == "rejected":
            problems.append(f"ml_manager plan: rejected by the rules: {plan.get('errors')}")
        if isinstance(plan, dict) and plan.get("optimal") is False:
            problems.append(f"ml_manager plan: GW{plan.get('gameweek')}'s solve stopped at its time limit")
        check = manager.get("check")
        if isinstance(check, dict):
            problems += [f"ml_manager: {p}" for p in check.get("problems") or []]
    return problems


def run_daily(live_dir: str = LIVE_DATA_DIR, force: bool = False, retrain: bool = False,
              allow_retrain: bool = True) -> Dict[str, Any]:
    """One run (the module docstring's steps). `allow_retrain` False skips the retrain step
    (a run right before a deadline mustn't spend half an hour training)."""
    summary: Dict[str, Any] = {}
    started = time.time()

    meta = refresh_current_season(live_dir, full=force)
    summary["fetch"] = {k: meta.get(k) for k in ("season", "rows", "gameweeks", "next_gameweek", "refreshed")}

    if meta.get("refreshed") or force:
        from graph.connection import Neo4jConnection
        from config.settings import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
        from pipeline.load_current import load_current_season

        conn = Neo4jConnection(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        try:
            summary["graph"] = load_current_season(conn, live_dir=live_dir)
        finally:
            conn.close()
    else:
        summary["graph"] = "skipped (no newly finished fixtures)"

    # Neither step may undo the refresh: on failure the active model / previous predictions
    # stay in place, the API still reloads, and the run is reported as failed so the
    # healthcheck alerts.
    errors = []
    try:
        if allow_retrain or retrain:
            summary["retrain"] = _run_retrain(force=retrain)
        else:
            summary["retrain"] = "skipped (pre-deadline run)"
    except Exception:
        errors.append(("retrain", traceback.format_exc()))
        summary["retrain"] = "failed"
        logger.error("Retrain step failed:\n%s", errors[-1][1])

    try:
        from pipeline.predict_next_gw import predict_next_gameweek
        summary["predictions"] = predict_next_gameweek(live_dir=live_dir)
    except Exception:
        errors.append(("predictions", traceback.format_exc()))
        summary["predictions"] = "failed"
        logger.error("Prediction step failed:\n%s", errors[-1][1])

    # After the predictions: it plans from their projections.csv. It never raises; a failed step
    # is reported in its own summary.
    try:
        from pipeline.ml_manager import run as run_ml_manager
        summary["ml_manager"] = run_ml_manager(live_dir)
    except Exception:
        errors.append(("ml_manager", traceback.format_exc()))
        summary["ml_manager"] = "failed"
        logger.error("ML manager step failed:\n%s", errors[-1][1])

    # Goal minutes and penalties for match pages (ESPN's scoreboard). It never raises and isn't a
    # problem the healthcheck reports: pages show FPL's scorers without minutes.
    try:
        from pipeline.goal_events import run as run_goal_events
        summary["goal_events"] = run_goal_events(live_dir)
    except Exception as exc:
        summary["goal_events"] = f"failed: {exc}"
        logger.warning("Goal events step failed: %s", exc)

    summary["backend_reload"] = _reload_backend()
    summary["seconds"] = round(time.time() - started, 1)
    problems = run_problems(summary)
    if problems:
        summary["problems"] = problems
        errors.append(("problems", "\n".join(problems)))
        logger.error("Problems:\n%s", errors[-1][1])
    if errors:
        failed = ", ".join(step for step, _ in errors)
        details = "\n".join(tb for _, tb in errors)
        raise RuntimeError(f"{failed} failed (data refresh succeeded): {json.dumps(summary, default=str)}\n{details}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily current-season refresh")
    parser.add_argument("--live-dir", default=LIVE_DATA_DIR)
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch all history and reload the graph even if nothing changed")
    parser.add_argument("--retrain", action="store_true", help="Retrain the model now, not just on Mondays")
    parser.add_argument("--before-deadline", action="store_true",
                        help="Run only when the next deadline is 45-105 minutes away and no run started within "
                             "150 minutes of it, and don't retrain (the hourly fpl-deadline timer)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    now = datetime.now(timezone.utc)
    if args.before_deadline:
        due, why = pre_deadline_due(args.live_dir, now)
        logger.info("%s: %s", "Pre-deadline run" if due else "No pre-deadline run", why)
        if not due:
            return 0
    _record_start(args.live_dir, now, args.before_deadline)
    _ping("/start")
    try:
        summary = run_daily(args.live_dir, force=args.force, retrain=args.retrain,
                            allow_retrain=not args.before_deadline)
    except Exception:
        details = traceback.format_exc()
        logger.error("Daily refresh failed:\n%s", details)
        _ping("/fail", details)
        return 1
    report = json.dumps(summary, indent=2, default=str)
    print(report)
    _ping("", report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
