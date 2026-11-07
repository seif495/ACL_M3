"""The "Based on: ..." line under an assistant answer: what the pipeline actually used.

e.g. ["2026-27 season stats", "GW10 predictions (updated 6 Nov 08:02)", "your team at the GW9
deadline"]. Built from flags the pipeline sets while it answers (`used`), the predictions file's
modification time and meta.json; pure apart from reading the file's mtime.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

try:  # Windows without tzdata has no zone database: fall back to UTC
    from zoneinfo import ZoneInfo
    _UK = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover - depends on the platform
    _UK = None


def updated_label(timestamp: Optional[float]) -> Optional[str]:
    """"6 Nov 08:02" in UK time (FPL's clock), or with "UTC" when no zone data is available."""
    if not timestamp:
        return None
    moment = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    if _UK is not None:
        local = moment.astimezone(_UK)
        return f"{local.day} {local:%b %H:%M}"
    return f"{moment.day} {moment:%b %H:%M} UTC"


def file_mtime(path: Optional[str]) -> Optional[float]:
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def build_sources(used: Dict[str, Any], *, current_season: str, predictions_gameweek: Optional[int] = None,
                  predictions_mtime: Optional[float] = None, projection_weeks: Sequence[int] = (),
                  team_gameweek: Optional[int] = None) -> List[str]:
    """The answer's sources, in a fixed order.

    `used` keys (all optional): "season" (a season id, "all" for every season, or None),
    "predictions", "projections", "fixtures", "team", "plan", "embeddings" (bools), and
    "team_error" (the team couldn't be loaded, so it isn't listed)."""
    out: List[str] = []
    season = used.get("season")
    if season == "all":
        out.append("every season's stats since 2020-21")
    elif season:
        out.append(f"{season} season stats")
    if used.get("predictions"):
        label = f"GW{predictions_gameweek} predictions" if predictions_gameweek else "next-gameweek predictions"
        when = updated_label(predictions_mtime)
        out.append(f"{label} (updated {when})" if when else label)
    if used.get("projections") and projection_weeks:
        weeks = list(projection_weeks)
        out.append(f"GW{weeks[0]}-{weeks[-1]} projections" if len(weeks) > 1 else f"GW{weeks[0]} projections")
    if used.get("fixtures"):
        out.append("FPL fixture difficulty")
    if used.get("team") and not used.get("team_error"):
        out.append(f"your team at the GW{team_gameweek} deadline" if team_gameweek else "your FPL team")
    if used.get("plan"):
        out.append("the site's transfer planner")
    if used.get("embeddings"):
        out.append("similar-player search")
    return out
