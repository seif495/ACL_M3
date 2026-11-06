"""
Numbers-first facts about the players a question names, for the assistant's prompt.

Per player: the next gameweek's predicted points (the predictions file, with his rank), the
projections for the gameweeks after it (LIVE_DATA_DIR/projections.csv, written by
pipeline.predict_next_gw for the ML manager), the current price, FPL's availability flag and
news, the next fixtures with FPL difficulty, and this season's totals (never a career best).

Used when a question weighs several players over the coming gameweek ("Is Isak a better
captain than Haaland this week?": every player's forecast, not just the first one's) and by
services/transfer_advice.py. Building and formatting are pure; load_outlooks() does the
reading (predictions frame passed in, projections file, cached fixtures, one Cypher query).
"""
import logging
import os
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from config.settings import CURRENT_SEASON
from services import answer_cards as ac
from services import season_service as ss
from utils.text import normalize_name

logger = logging.getLogger(__name__)

PROJECTIONS_NAME = "projections.csv"
HORIZON = 4          # gameweeks of projections and fixtures shown, the next one included

SEASON_TOTALS_QUERY = """
UNWIND $names AS name
MATCH (p:Player {name: name})-[st:SEASON_STATS]->(:Season {id: $season})
RETURN p.name AS name, st.team AS team, st.position AS position, st.total_points AS total_points,
       st.goals AS goals, st.assists AS assists, st.clean_sheets AS clean_sheets, st.bonus AS bonus,
       st.minutes AS minutes, coalesce(st.appearances, st.games, 0) AS appearances
"""

# Wording about the coming gameweek(s): the captain is always picked for the next deadline
NEXT_GAMEWEEK_RE = re.compile(
    r"\b(?:captain(?:cy|s|ing)?|armband|triple\s+captain"
    r"|(?:this|next|coming|upcoming)\s+(?:week|weekend|gw|gameweek|match|game|fixture|few|round)s?"
    r"|upcoming|better\s+(?:pick|option|choice|captain|buy|transfer|starter)"
    r"|(?:should\s+i|who\s+to)\s+(?:start|bench|pick|play|captain)"
    r"|predict(?:ed|ion|ions)?|forecast(?:ed|s)?|expected\s+points|xp)\b",
    re.IGNORECASE)

_STATUS_LABELS = {"d": "doubtful", "i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad"}
_POSITION_PLURAL = {"GK": "goalkeepers", "DEF": "defenders", "MID": "midfielders", "FWD": "forwards"}


def is_next_gameweek_question(text: str) -> bool:
    """Whether a question is about the coming gameweek(s): captaincy, "this week", "next
    gameweek", "better pick", predictions."""
    return bool(NEXT_GAMEWEEK_RE.search(text or ""))


# ------------------------------------------------------------------ files

_projections_lock = Lock()
_projections_cache: Optional[Tuple[str, float, pd.DataFrame]] = None


def projections_path() -> str:
    return os.path.join(ss.live_dir(), PROJECTIONS_NAME)


def load_projections(path: Optional[str] = None) -> Optional[pd.DataFrame]:
    """projections.csv (element, name, gameweek, opponents, predicted_points, ...), re-read
    when it changes; None when it is missing or unreadable."""
    global _projections_cache
    path = path or projections_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _projections_lock:
        if _projections_cache is None or _projections_cache[:2] != (path, mtime):
            try:
                frame = pd.read_csv(path)
            except Exception as exc:  # a half-written or corrupt file only drops the projections
                logger.warning("Projections unreadable (%s): %s", path, exc)
                return None
            _projections_cache = (path, mtime, frame)
        return _projections_cache[2]


# ------------------------------------------------------------------ pure: one player

def _clean(value: Any) -> Any:
    return ac._clean(value)


def _float(value: Any) -> Optional[float]:
    return ac._float(value)


def _int(value: Any) -> Optional[int]:
    return ac._int(value)


def find_row(df: Optional[pd.DataFrame], name: str) -> Optional[Dict[str, Any]]:
    """A player's row of the predictions file: exact name (any case), else the same name
    without accents ("Gyokeres" for "Gyökeres"); None when he isn't in it."""
    if df is None or not len(df) or not name:
        return None
    lowered = df["name_lower"] if "name_lower" in df.columns else df["name"].astype(str).str.lower()
    hit = df[lowered == name.lower()]
    if not len(hit):
        key = normalize_name(name)
        hit = df[df["name"].map(lambda n: normalize_name(str(n))) == key]
    return hit.iloc[0].to_dict() if len(hit) else None


def projection_rows(projections: Optional[pd.DataFrame], row: Dict[str, Any],
                    first_gameweek: Optional[int], horizon: int = HORIZON) -> List[Tuple[int, float, str]]:
    """(gameweek, points, opponents) for the player from `first_gameweek` on, at most
    `horizon` gameweeks. Empty when the file is missing or stale (it doesn't start at the
    predictions file's gameweek, so it was written for an earlier deadline)."""
    if projections is None or not len(projections) or first_gameweek is None:
        return []
    if "gameweek" not in projections.columns or int(projections["gameweek"].min()) != first_gameweek:
        return []
    element = _int(row.get("element"))
    if element is not None and "element" in projections.columns:
        mine = projections[projections["element"] == element]
    else:
        mine = projections[projections["name"].astype(str).str.lower() == str(row.get("name", "")).lower()]
    mine = mine.sort_values("gameweek").head(horizon)
    return [(int(r["gameweek"]), round(float(r["predicted_points"]), 1), str(_clean(r.get("opponents")) or ""))
            for _, r in mine.iterrows()]


@dataclass
class Outlook:
    """What the prompt says about one player. `in_game` is None when there is no predictions
    file at all, False when the file doesn't have him (not in the current FPL game)."""
    name: str
    in_game: Optional[bool]
    position: Optional[str] = None
    team: Optional[str] = None
    price: Optional[float] = None                 # current price, GBP m
    gameweek: Optional[int] = None
    points: Optional[float] = None                # next gameweek's predicted points
    opponents: List[Dict[str, Any]] = field(default_factory=list)
    rank: Optional[int] = None
    pool: Optional[int] = None
    position_rank: Optional[int] = None
    position_pool: Optional[int] = None
    status: str = "a"
    chance: Optional[int] = None
    news: str = ""
    projections: List[Tuple[int, float, str]] = field(default_factory=list)
    fixtures: List[Dict[str, Any]] = field(default_factory=list)
    season: Optional[Dict[str, Any]] = None       # SEASON_TOTALS_QUERY row for this season

    def projected(self, gameweeks: Iterable[int]) -> Optional[float]:
        """Projected points over these gameweeks, or None if one of them is missing."""
        by_gw = {gw: pts for gw, pts, _ in self.projections}
        wanted = list(gameweeks)
        if not wanted or any(gw not in by_gw for gw in wanted):
            return None
        return round(sum(by_gw[gw] for gw in wanted), 1)


def build_outlook(name: str, *, predictions: Optional[pd.DataFrame], projections: Optional[pd.DataFrame],
                  fixtures: Sequence[Dict[str, Any]], season_row: Optional[Dict[str, Any]]) -> Outlook:
    if predictions is None:
        return Outlook(name=name, in_game=None, season=season_row)
    row = find_row(predictions, name)
    if row is None:
        return Outlook(name=name, in_game=False, season=season_row)
    file_name = str(row["name"])
    prediction = ac.prediction_for(predictions, file_name, list(fixtures)) or {}
    team = _clean(row.get("team"))
    value = _float(row.get("value"))
    chance = _float(row.get("chance_of_playing_next_round"))
    gameweek = prediction.get("gameweek") or _int(row.get("gameweek"))
    return Outlook(
        name=file_name, in_game=True,
        position=ss.normalize_position(_clean(row.get("position"))), team=team,
        price=round(value / 10.0, 1) if value is not None else None,
        gameweek=gameweek,
        points=prediction.get("points", round(_float(row.get("predicted_points")) or 0.0, 1)),
        opponents=prediction.get("opponents") or [],
        rank=prediction.get("rank"), pool=prediction.get("pool"),
        position_rank=prediction.get("position_rank"), position_pool=prediction.get("position_pool"),
        status=str(_clean(row.get("status")) or "a"),
        chance=int(chance) if chance is not None else None,
        news=str(_clean(row.get("news")) or "").strip(),
        projections=projection_rows(projections, row, gameweek),
        fixtures=ac.upcoming_fixtures(fixtures, team, HORIZON) if team else [],
        season=season_row,
    )


# ------------------------------------------------------------------ pure: text

def _venue(home: Any) -> str:
    return "H" if home else "A"


def opponents_text(opponents: Sequence[Dict[str, Any]]) -> str:
    if not opponents:
        return "no fixture (blank gameweek)"
    return " and ".join(f"{o.get('team')} ({_venue(o.get('home'))})" for o in opponents)


def availability_text(o: Outlook) -> str:
    if o.status in ("a", "", "nan"):
        return "available"
    label = _STATUS_LABELS.get(o.status, o.status)
    # FPL's news often states the chance already ("Knock - 75% chance of playing")
    chance = f", {o.chance}% chance of playing" if o.chance is not None and "%" not in o.news else ""
    news = f": {o.news}" if o.news else ""
    return f"{label}{chance}{news}"


def fixtures_text(fixtures: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for f in fixtures:
        difficulty = f", FDR {f['difficulty']}" if f.get("difficulty") is not None else ""
        parts.append(f"GW{f.get('gameweek')} {f.get('team')} ({_venue(f.get('home'))}{difficulty})")
    return "; ".join(parts)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def season_text(row: Optional[Dict[str, Any]], season: str = CURRENT_SEASON) -> Optional[str]:
    """'2026-27 so far: 38 points in 9 appearances (4.2 a game), 4 goals, 2 assists, ...'."""
    if not row:
        return None
    apps = _int(row.get("appearances")) or 0
    points = _int(row.get("total_points")) or 0
    per_game = f" ({points / apps:.1f} a game)" if apps else ""
    parts = [f"{_count(points, 'point')} in {_count(apps, 'appearance')}{per_game}",
             _count(_int(row.get('goals')) or 0, "goal"), _count(_int(row.get('assists')) or 0, "assist")]
    if ss.normalize_position(_clean(row.get("position"))) in ("GK", "DEF"):
        parts.append(_count(_int(row.get('clean_sheets')) or 0, "clean sheet"))
    parts.append(f"{_int(row.get('bonus')) or 0} bonus, {_count(_int(row.get('minutes')) or 0, 'minute')}")
    return f"{season} so far: " + ", ".join(parts)


def outlook_text(o: Outlook, season: str = CURRENT_SEASON) -> str:
    """One player's block for the prompt, prediction first."""
    if o.in_game is False:
        return (f"{o.name}: not in the current FPL game (no longer at a Premier League club), "
                "so there is no prediction.")
    if o.in_game is None:
        lines = [f"{o.name}: no forecast available (the predictions file is missing)."]
        totals = season_text(o.season, season)
        return "\n".join(lines + ([f"- {totals}"] if totals else []))
    head = ", ".join(p for p in (o.position, o.team, f"£{o.price:.1f}m" if o.price is not None else None) if p)
    lines = [f"{o.name} ({head}), {availability_text(o)}"]
    rank = ""
    if o.rank and o.pool:
        rank = f", #{o.rank} of {o.pool} players"
        if o.position_rank and o.position_pool and o.position in _POSITION_PLURAL:
            rank += f" (#{o.position_rank} of {o.position_pool} {_POSITION_PLURAL[o.position]})"
    lines.append(f"- GW{o.gameweek} predicted points: {o.points:.1f} vs {opponents_text(o.opponents)}{rank}")
    if len(o.projections) > 1:
        first, last = o.projections[0][0], o.projections[-1][0]
        total = sum(p for _, p, _ in o.projections)
        lines.append(f"- Projected points GW{first}-GW{last}: "
                     + " + ".join(f"{p:.1f}" for _, p, _ in o.projections) + f" = {total:.1f}")
    if o.fixtures:
        lines.append(f"- Next fixtures (FDR: FPL difficulty, 1 easiest to 5 hardest): {fixtures_text(o.fixtures)}")
    totals = season_text(o.season, season)
    if totals:
        lines.append(f"- {totals}")
    return "\n".join(lines)


def forecast_context(outlooks: Sequence[Outlook], season: str = CURRENT_SEASON) -> str:
    """Every named player's forecast, for a question that weighs them over the coming
    gameweek. Each is listed, so the answer never says one is missing when the file has it."""
    gameweek = next((o.gameweek for o in outlooks if o.gameweek), None)
    when = f"GW{gameweek}, the next gameweek" if gameweek else "the next gameweek"
    lines = [f"ML Predictions for every player in the question ({when}), in the order asked:"]
    lines += [outlook_text(o, season) for o in outlooks]
    lines.append("Each player above has a forecast unless his entry says he is not in the current FPL game.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ reading

def season_rows(conn, names: Sequence[str], season: str = CURRENT_SEASON) -> Dict[str, Dict[str, Any]]:
    """This season's SEASON_STATS row per player name ({} on a driver error)."""
    if conn is None or not names:
        return {}
    try:
        rows = conn.execute_query(SEASON_TOTALS_QUERY, {"names": list(names), "season": season})
    except Exception as exc:
        logger.warning("Season totals unavailable for the prompt: %s", exc)
        return {}
    return {r["name"]: r for r in rows if r.get("name")}


def load_outlooks(conn, names: Sequence[str], predictions: Optional[pd.DataFrame],
                  season: str = CURRENT_SEASON) -> Dict[str, Outlook]:
    """{name: Outlook} for the players asked about (blocking: run it in a thread)."""
    try:
        fixtures = ss.load_fixtures(conn, season) if conn is not None else []
    except Exception as exc:
        logger.warning("Fixtures unavailable for the prompt: %s", exc)
        fixtures = []
    projections = load_projections()
    totals = season_rows(conn, names, season)
    return {name: build_outlook(name, predictions=predictions, projections=projections,
                                fixtures=fixtures, season_row=totals.get(name))
            for name in names}
