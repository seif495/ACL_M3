"""
Cached access to the precomputed next-gameweek predictions CSV.

Two locations, chosen on every call:
  1. LIVE_DATA_DIR/next_gw_predictions.csv, regenerated from fresh data by
     `python -m pipeline.predict_next_gw` (default data/live);
  2. ml/predictions/next_gw_predictions.csv, the file bundled with the repo, used
     when no live file exists.
Each file is parsed once and re-read only when its path or modification time
changes, so a newly written live file is picked up without a restart.
"""
import json
import os
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import pandas as pd

PREDICTIONS_CSV = "ml/predictions/next_gw_predictions.csv"
LIVE_PREDICTIONS_NAME = "next_gw_predictions.csv"
RUN_NAME = "prediction_run.json"     # written by pipeline.predict_next_gw next to the live file
# Below this share of rows with bookmaker prices, the run was made before they were collected
# (Friday 17:00 or Tuesday 13:00 UK time); a gameweek's rearranged matches alone stay above it
ODDS_PENDING_BELOW = 0.5

_lock = Lock()
_cached: Optional[Tuple[str, float, pd.DataFrame]] = None


def live_predictions_path() -> str:
    return os.path.join(os.getenv("LIVE_DATA_DIR", os.path.join("data", "live")), LIVE_PREDICTIONS_NAME)


def predictions_path() -> str:
    """The live predictions file when it exists, else the bundled one."""
    live = live_predictions_path()
    return live if os.path.exists(live) else PREDICTIONS_CSV


def get_predictions_df(path: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Return the predictions DataFrame, or None if the file doesn't exist.

    `path` defaults to predictions_path(). Callers must not mutate the returned
    frame in place (use .assign/.copy).
    """
    global _cached
    path = path or predictions_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        if _cached is None or _cached[0] != path or _cached[1] != mtime:
            df = pd.read_csv(path)
            df["name_lower"] = df["name"].str.lower()
            _cached = (path, mtime, df)
        return _cached[2]


_run_cached: Optional[Tuple[str, float, Dict[str, Any]]] = None


def run_info() -> Dict[str, Any]:
    """What the last prediction run had (prediction_run.json next to the live predictions):
    gameweek, generated_at, model and input_coverage. {} when the live file is not in use, or
    the run file is missing, unreadable or older than the predictions (a file from before this
    was recorded)."""
    global _run_cached
    live = live_predictions_path()
    path = os.path.join(os.path.dirname(live), RUN_NAME)
    try:
        mtime = os.path.getmtime(path)
        if not os.path.exists(live) or mtime + 300 < os.path.getmtime(live):
            return {}
    except OSError:
        return {}
    with _lock:
        if _run_cached is None or _run_cached[0] != path or _run_cached[1] != mtime:
            try:
                with open(path, encoding="utf-8") as f:
                    info = json.load(f)
                info = info if isinstance(info, dict) else {}
            except (OSError, ValueError):
                info = {}
            _run_cached = (path, mtime, info)
        return _run_cached[2]


def odds_pending(info: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the predictions were made before the bookmakers' prices for the gameweek were
    collected, so they will change once those are in. False when nothing says so."""
    info = run_info() if info is None else info
    coverage = (info.get("input_coverage") or {}).get("odds")
    try:
        return coverage is not None and float(coverage) < ODDS_PENDING_BELOW
    except (TypeError, ValueError):
        return False
