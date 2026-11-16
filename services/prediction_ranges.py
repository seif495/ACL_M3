"""
The spread around a predicted number: what players predicted about that much actually scored.

A prediction is an average. This reads ml/models/points_v4_ranges.json (built out of sample by
scripts/analysis/prediction_ranges.py from a season replay) and gives, for a predicted number,
the usual range (10th to 90th percentile of the actual points of players predicted that much)
and the chance of a return (6+) or a haul (10+). It is the same for every player at that level:
it says how noisy a gameweek is, not anything the model knows about him in particular.

Used by the predictions endpoints (the range under each number on /predictions) and the
assistant (services/player_outlook.py). No file, or an unreadable one: no range, never an error.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

RANGES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml", "models",
                           "points_v4_ranges.json")
MIN_BIN_ROWS = 100      # a bin with fewer players than this says too little to show

_lock = threading.Lock()
_cached: Optional[Tuple[str, float, Dict[str, Any]]] = None


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """The ranges table ({} when missing or malformed), re-read when the file changes."""
    global _cached
    path = path or RANGES_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if _cached is None or _cached[0] != path or _cached[1] != mtime:
            try:
                with open(path, encoding="utf-8") as f:
                    table = json.load(f)
                bins = [b for b in table.get("bins") or [] if isinstance(b, dict) and "from" in b]
                table["bins"] = sorted(bins, key=lambda b: float(b["from"]))
            except (OSError, ValueError, TypeError, AttributeError):
                table = {}
            _cached = (path, mtime, table)
        return _cached[2]


def outcome(predicted: Any, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """{"low", "high", "p_return", "p_haul"} for a predicted number, or None: no table, not a
    number, a prediction of 0 (no fixture or ruled out: nothing to spread), or too few players
    at that level."""
    try:
        value = float(predicted)
    except (TypeError, ValueError):
        return None
    if not value > 0:
        return None
    chosen = None
    for b in load(path).get("bins") or []:
        if value >= float(b["from"]) and (b.get("to") is None or value < float(b["to"])):
            chosen = b
    if chosen is None or int(chosen.get("n") or 0) < MIN_BIN_ROWS:
        return None
    return {"low": int(chosen["low"]), "high": int(chosen["high"]),
            "p_return": float(chosen["p_return"]), "p_haul": float(chosen["p_haul"])}


def describe(predicted: Any, path: Optional[str] = None) -> Optional[str]:
    """One line for the assistant's context: "players predicted about this much usually score
    1-12 (6 or more 44% of the time, 10 or more 18%)"."""
    o = outcome(predicted, path)
    if o is None:
        return None
    table = load(path)
    return (f"players predicted about this much usually score {o['low']}-{o['high']} "
            f"({table.get('return_points', 6)} or more {o['p_return']:.0%} of the time, "
            f"{table.get('haul_points', 10)} or more {o['p_haul']:.0%})")


def legend(levels: Tuple[float, ...] = (2.0, 4.0, 6.0), path: Optional[str] = None) -> List[Dict[str, Any]]:
    """The range at a few predicted levels, for the page's "how to read this" note."""
    out = []
    for level in levels:
        o = outcome(level, path)
        if o is not None:
            out.append({"predicted": level, **o})
    return out
