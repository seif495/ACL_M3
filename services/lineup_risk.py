"""
How likely a player is to start, from his recent starts.

Line-ups come out about an hour before kickoff, after the FPL deadline, so no prediction can know
them. What can be said beforehand is how often players with the same recent record started their
next match. Over 2022-23..2025-26 (103,757 player-matches with at least three earlier matches that
season; `starts` isn't recorded before 2022-23), by starts in the last six matches and whether he
started the last one:

    last six      started the last one    didn't
    6 of 6               88%                 -
    5 of 6               81%                47%
    3-4 of 6             73%                31%
    2 of 6               63%                23%
    0-1 of 6             54%                 3%

It is separate from FPL's injury flag (a flagged player is handled by the availability factor)
and it is not a model: the points model already has these inputs. It exists so a reader can see
the line-up risk behind a number. The inputs are the model's own (next_gw_features.csv, written
by every prediction run: started_r6 and started_r1), so there is nothing new to fetch.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional, Tuple

FEATURES_NAME = "next_gw_features.csv"

# (share of the last six started, at least) -> (start rate if he started the last one, if not)
START_RATES: Tuple[Tuple[float, float, float], ...] = (
    (0.95, 0.88, 0.88), (0.75, 0.81, 0.47), (0.45, 0.73, 0.31), (0.25, 0.63, 0.23), (0.0, 0.54, 0.03),
)
LABELS: Tuple[Tuple[float, str], ...] = ((0.8, "nailed"), (0.6, "usually starts"), (0.3, "rotation risk"),
                                         (0.0, "unlikely to start"))

_lock = threading.Lock()
_cached: Optional[Tuple[str, float, Dict[int, Dict[str, Any]]]] = None


def features_path() -> str:
    return os.path.join(os.getenv("LIVE_DATA_DIR", os.path.join("data", "live")), FEATURES_NAME)


def start_chance(starts_share: Any, started_last: Any) -> Optional[float]:
    """The start rate of players with this record (see the table above); None without a record."""
    try:
        share, last = float(starts_share), float(started_last)
    except (TypeError, ValueError):
        return None
    if share != share or last != last:
        return None
    for at_least, if_started, if_not in START_RATES:
        if share >= at_least:
            return if_started if last >= 0.5 else if_not
    return None


def label(chance: Optional[float]) -> Optional[str]:
    if chance is None:
        return None
    return next(text for at_least, text in LABELS if chance >= at_least)


def load(path: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
    """{element: {"start_chance", "label", "starts_last6", "started_last"}} from the model's
    inputs for the coming gameweek ({} without the file). Cached until the file changes."""
    global _cached
    path = path or features_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if _cached is None or _cached[0] != path or _cached[1] != mtime:
            out: Dict[int, Dict[str, Any]] = {}
            try:
                import pandas as pd
                df = pd.read_csv(path, usecols=lambda c: c in ("element", "started_r6", "started_r1"))
                if {"element", "started_r6", "started_r1"} <= set(df.columns):
                    for r in df.drop_duplicates("element").to_dict("records"):
                        chance = start_chance(r["started_r6"], r["started_r1"])
                        if chance is not None:
                            out[int(r["element"])] = {
                                "start_chance": chance, "label": label(chance),
                                "starts_last6": round(float(r["started_r6"]), 2),
                                "started_last": bool(float(r["started_r1"]) >= 0.5)}
            except Exception:  # noqa: BLE001 - an unreadable file means no line-up note, never an error
                out = {}
            _cached = (path, mtime, out)
        return _cached[2]


def for_element(element: Any, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        return load(path).get(int(element))
    except (TypeError, ValueError):
        return None


def describe(element: Any, path: Optional[str] = None) -> Optional[str]:
    """One line for the assistant: "started 5 of his last 6 matches and the last one; players
    with that record started their next match 81% of the time (nailed)"."""
    r = for_element(element, path)
    if r is None:
        return None
    starts = round(r["starts_last6"] * 6)
    return (f"started about {starts} of his last 6 matches{' including' if r['started_last'] else ', but not'} "
            f"the last one; players with that record started their next match {r['start_chance']:.0%} of the time "
            f"({r['label']}). Line-ups are only known after the deadline")
