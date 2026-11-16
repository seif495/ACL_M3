"""
Which prices are likely to move before the deadline, from this gameweek's transfers.

FPL doesn't publish how it changes prices, only that transfers drive them. Over 2020-21..2025-26
(145,856 player-gameweeks in data/cleaned_merged_seasons_cleaned.csv, 2026-11-16) the net
transfers a player had in a gameweek against whether his price was different by its deadline:

    net transfers in      price rose        net transfers out     price fell
    20k-50k                6%               5k-20k                12%
    50k-100k              17%               20k-50k               18%
    100k-200k             40%               50k-100k              25%
    over 200k             82%               100k-200k             36%
                                            over 200k             60%

(Between -5k and +20k: under 4% either way. A rise almost never comes with net transfers out.)

So this is a rate from history, not FPL's rule, and the status file's counts are "so far this
gameweek": early in the week they understate where a player will end up. A player whose price has
already moved that way this gameweek is left out (the table is about a change by the deadline,
and his has happened). Pure: rows of the FPL status file in, lists out. Used by the Insights
page's Transfers & prices tab (services/insights.py) and the assistant's price facts.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

# (net transfers at least, chance), highest first
RISE: Tuple[Tuple[int, float], ...] = ((200_000, 0.82), (100_000, 0.40), (50_000, 0.17), (20_000, 0.06))
FALL: Tuple[Tuple[int, float], ...] = ((200_000, 0.60), (100_000, 0.36), (50_000, 0.25), (20_000, 0.18), (5_000, 0.12))
MIN_CHANCE = 0.15      # below it a player isn't worth listing
LEFT = ("u",)          # no longer at a Premier League club


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def chances(net: float) -> Tuple[float, float]:
    """(chance the price rises, chance it falls) by the deadline for `net` transfers in minus out."""
    rise = next((p for at_least, p in RISE if net >= at_least), 0.0)
    fall = next((p for at_least, p in FALL if -net >= at_least), 0.0)
    return rise, fall


def watch(status_rows: Iterable[Dict[str, Any]], limit: int = 10, min_chance: float = MIN_CHANCE
          ) -> Dict[str, List[Dict[str, Any]]]:
    """{"risers": [...], "fallers": [...]}: the players most likely to change price before the
    deadline, likeliest first, each the status row plus `net_transfers` and `chance`. Empty when
    the file has no transfer counts."""
    risers, fallers = [], []
    for r in status_rows:
        t_in, t_out = _num(r.get("transfers_in_event")), _num(r.get("transfers_out_event"))
        if t_in is None or t_out is None or str(r.get("status") or "a").strip().lower() in LEFT:
            continue
        net = t_in - t_out
        rise, fall = chances(net)
        moved = _num(r.get("cost_change_event")) or 0.0
        if rise >= min_chance and moved <= 0:
            risers.append({**r, "net_transfers": int(net), "chance": rise})
        if fall >= min_chance and moved >= 0:
            fallers.append({**r, "net_transfers": int(net), "chance": fall})
    risers.sort(key=lambda r: (-r["chance"], -r["net_transfers"], str(r.get("name"))))
    fallers.sort(key=lambda r: (-r["chance"], r["net_transfers"], str(r.get("name"))))
    return {"risers": risers[:limit], "fallers": fallers[:limit]}
