"""
Rich answer cards for the assistant: the `cards` and `follow_ups` fields of POST /api/query.

The LLM writes the text; these typed blocks are what the frontend draws with it: player
profiles, comparisons, leaderboards, next-gameweek predictions, team, results and season
summaries, "notable stats" highlights and follow-up questions. They are built from
structured data only (the pipeline's query rows, a few small Cypher lookups, the predictions
file and the cached current-season fixtures), never parsed from the answer text. No LLM or
network call is made: photos come from `photo_for` (the avatar cache and FPL photo codes).

Layout, as in services/season_service.py:
  * pure functions that shape rows into cards and compute highlights (tested without Neo4j);
  * the Cypher for the extra lookups;
  * build_cards(), which picks the cards for an intent and runs their lookups concurrently.
A card that fails to build is logged and skipped; it never breaks the answer.

Every card is a dict with a `type` ("player", "comparison", "leaderboard", "prediction",
"team", "fixtures", "season" or "pitch") and an `id`; src/services/api.ts has the TypeScript
shapes.
"""
import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config.settings import CURRENT_SEASON
from services import season_service as ss
from utils.text import normalize_name

logger = logging.getLogger(__name__)

PhotoFn = Callable[[str, Optional[str], Optional[int]], Optional[str]]

MAX_CARDS = 3
MAX_TRANSFER_CARDS = 3   # comparison cards for a transfer question, one per (sold, bought) move
MAX_FOLLOW_UPS = 4
MAX_HIGHLIGHTS = 4
FORM_LENGTH = 8          # recent matches in a player's sparkline
COMPARE_FORM_LENGTH = 6
LEADERBOARD_ROWS = 10
GROUP_ROWS = 5           # per position when the ranking is grouped by position
PREDICTION_ROWS = 5
TEAM_PLAYERS = 3
TEAM_FIXTURES = 5
NEXT_FIXTURES = 5        # a player card's fixture-difficulty strip
COMPARE_FIXTURES = 3
TEAM_FORM = 5
MATCH_ROWS = 8
HAUL_POINTS = 10         # a "double-digit haul"
BLANK_POINTS = 2         # 2 points or fewer is a blank

POSITION_WORDS = {
    "GK": ("goalkeeper", "goalkeepers"),
    "DEF": ("defender", "defenders"),
    "MID": ("midfielder", "midfielders"),
    "FWD": ("forward", "forwards"),
}

# ------------------------------------------------------------------ small helpers


def _clean(value: Any) -> Any:
    """Plain Python value: numpy scalars unwrapped; None for NaN and blank strings."""
    if value is None:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes, dict, list)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _float(value: Any) -> Optional[float]:
    value = _clean(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _int(value: Any) -> Optional[int]:
    number = _float(value)
    return None if number is None else int(round(number))


def _num(value: Any, decimals: int = 2) -> Optional[float]:
    """JSON-safe number: whole values as int, others rounded."""
    number = _float(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else round(number, decimals)


def _bool(value: Any) -> Optional[bool]:
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _fmt(value: float, decimals: int = 1) -> str:
    """'22' for whole numbers, '6.2' otherwise."""
    return str(int(value)) if float(value).is_integer() else f"{value:.{decimals}f}"


def _compact(value: float) -> str:
    """1234567 -> '1.2M', 34500 -> '34.5k'."""
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(value) >= size:
            return f"{value / size:.1f}".rstrip("0").rstrip(".") + suffix
    return _fmt(value)


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def season_label(season: Optional[str], current: str = CURRENT_SEASON) -> str:
    if not season:
        return "All seasons"
    return f"{season} so far" if season == current else season


def season_phrase(season: Optional[str], current: str = CURRENT_SEASON) -> str:
    """How a follow-up question names a season ('' for all seasons)."""
    if not season:
        return ""
    return "this season" if season == current else f"in {season}"


_NAME_PARTICLES = {"de", "da", "das", "do", "dos", "di", "del", "della", "van", "von", "der", "den", "le", "la", "ter", "ten"}


def short_name(name: str, web_names: Optional[Dict[str, str]] = None) -> str:
    """FPL's web name when known, else the surname with its particles ('De Bruyne', 'van Dijk')."""
    if web_names and web_names.get(name):
        return web_names[name]
    words = name.split()
    start = len(words) - 1
    while start > 1 and words[start - 1].lower() in _NAME_PARTICLES:
        start -= 1
    return " ".join(words[start:]) if len(words) > 1 else name


def venue(home: Optional[bool]) -> str:
    return "" if home is None else (" (H)" if home else " (A)")


def highlight(label: str, value: str, detail: Optional[str] = None,
              tone: str = "neutral", icon: str = "star") -> Dict[str, Any]:
    """One "notable stat". tone: accent|info|warning|danger|neutral; icon: a name the
    frontend maps to an icon (trophy, flame, medal, star, zap, clock, home, trend-up,
    trend-down, shield, target, calendar, alert)."""
    return {"label": label, "value": value, "detail": detail, "tone": tone, "icon": icon}


def player_ref(name: str, team: Any = None, position: Any = None, photo: Optional[str] = None,
               status: Any = None, chance: Any = None, news: Any = None) -> Dict[str, Any]:
    status = _clean(status)
    return {
        "name": name,
        "team": _clean(team),
        "position": ss.normalize_position(_clean(position)),
        "photo": photo,
        "status": str(status) if status is not None else None,
        "chance": _int(chance),
        "news": _clean(news),
    }


def competition_ranks(values: Sequence[Any]) -> List[int]:
    """1, 1, 3, ... for values already sorted best-first."""
    ranks: List[int] = []
    previous: Any = object()
    for i, value in enumerate(values):
        if value != previous:
            rank, previous = i + 1, value
        ranks.append(rank)
    return ranks


def _streak(items: Sequence[Dict[str, Any]], test: Callable[[Dict[str, Any]], bool]) -> int:
    """How many of the most recent items in a row pass `test`."""
    count = 0
    for item in reversed(items):
        if not test(item):
            break
        count += 1
    return count


# ------------------------------------------------------------------ pure: matches and totals

def match_rows(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PLAYER_MATCHES_QUERY rows -> one dict per match, oldest first, with the opponent,
    venue, score and result from the player's side."""
    out = []
    for r in raw:
        home_team, away_team, team = _clean(r.get("home_team")), _clean(r.get("away_team")), _clean(r.get("team"))
        home = _bool(r.get("was_home"))
        if home is None and team and (home_team or away_team):
            home = team == home_team
        opponent = None if home is None else (away_team if home else home_team)
        home_score, away_score = _int(r.get("home_score")), _int(r.get("away_score"))
        team_score = opponent_score = result = None
        if home is not None and home_score is not None and away_score is not None:
            team_score, opponent_score = (home_score, away_score) if home else (away_score, home_score)
            result = "W" if team_score > opponent_score else ("D" if team_score == opponent_score else "L")
        kickoff = ss.to_datetime(r.get("kickoff_time"))
        out.append({
            "season": _clean(r.get("season")),
            "gameweek": _int(r.get("gameweek")),
            "kickoff": kickoff,
            "points": _int(r.get("points")) or 0,
            "minutes": _int(r.get("minutes")) or 0,
            "goals": _int(r.get("goals")) or 0,
            "assists": _int(r.get("assists")) or 0,
            "clean_sheets": _int(r.get("clean_sheets")) or 0,
            "bonus": _int(r.get("bonus")) or 0,
            "goals_conceded": _int(r.get("goals_conceded")) or 0,
            "opponent": opponent,
            "home": home,
            "team_score": team_score,
            "opponent_score": opponent_score,
            "result": result,
            # Price in tenths of £m and ownership (managers) at the time; expected stats exist
            # only in newer seasons (None before)
            "value": _float(r.get("value")),
            "selected": _int(r.get("selected")),
            "xg": _float(r.get("xg")),
            "xa": _float(r.get("xa")),
            "xgi": _float(r.get("xgi")),
            "xgc": _float(r.get("xgc")),
        })
    out.sort(key=lambda m: (m["season"] or "", m["gameweek"] or 0,
                            m["kickoff"].timestamp() if m["kickoff"] else 0))
    return out


def price_trend(matches: Iterable[Dict[str, Any]], season: Optional[str]) -> Optional[Dict[str, Any]]:
    """Price (£m) and ownership per gameweek of one season, from the player's match rows
    (a double gameweek counts once, at its last match). None with fewer than 2 gameweeks."""
    by_gameweek: Dict[int, Dict[str, Any]] = {}
    for m in matches:
        if m["season"] == season and m["gameweek"] is not None and m["value"] is not None:
            by_gameweek[m["gameweek"]] = m
    if not season or len(by_gameweek) < 2:
        return None
    points = [{"gameweek": gw, "price": round(m["value"] / 10.0, 1), "selected": m["selected"]}
              for gw, m in sorted(by_gameweek.items())]
    first, last = points[0], points[-1]
    owned = first["selected"] is not None and last["selected"] is not None
    return {
        "season": season,
        "points": points,
        "price_change": round(last["price"] - first["price"], 1),
        "selected_change": last["selected"] - first["selected"] if owned else None,
    }


def underlying_stats(matches: Iterable[Dict[str, Any]], season: Optional[str]) -> Optional[Dict[str, Any]]:
    """Expected goals (xG), assists (xA) and involvements (xGI) over one season, next to the
    real goals and assists, with per-90 rates. None when that season has no expected stats
    (FPL publishes them for recent seasons only)."""
    rows = [m for m in matches if m["season"] == season]
    if not season or not any(m["xg"] is not None or m["xa"] is not None for m in rows):
        return None
    minutes = sum(m["minutes"] for m in rows)
    xg = sum(m["xg"] or 0.0 for m in rows)
    xa = sum(m["xa"] or 0.0 for m in rows)
    xgi = sum(m["xgi"] if m["xgi"] is not None else (m["xg"] or 0.0) + (m["xa"] or 0.0) for m in rows)
    played = [m for m in rows if m["minutes"] > 0]

    def per90(total: float) -> Optional[float]:
        return round(total * 90 / minutes, 2) if minutes >= 90 else None

    return {
        "season": season,
        "minutes": minutes,
        "xg": round(xg, 2), "xa": round(xa, 2), "xgi": round(xgi, 2),
        "goals": sum(m["goals"] for m in rows), "assists": sum(m["assists"] for m in rows),
        "xg_per90": per90(xg), "xa_per90": per90(xa), "xgi_per90": per90(xgi),
        "xgc": round(sum(m["xgc"] or 0.0 for m in played), 2),
        "goals_conceded": sum(m["goals_conceded"] for m in played),
    }


def form_point(match: Dict[str, Any]) -> Dict[str, Any]:
    """A match as the frontend's sparkline/match views read it."""
    score = None
    if match["team_score"] is not None and match["opponent_score"] is not None:
        score = f"{match['team_score']}-{match['opponent_score']}"
    return {
        "season": match["season"], "gameweek": match["gameweek"], "points": match["points"],
        "minutes": match["minutes"], "goals": match["goals"], "assists": match["assists"],
        "opponent": match["opponent"], "home": match["home"], "score": score, "result": match["result"],
    }


SUM_KEYS = ("total_points", "goals", "assists", "clean_sheets", "bonus", "minutes", "saves",
            "games", "ict_sum", "value_sum")


def season_totals(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Sums of SEASON_STATS rows. `appearances` falls back to games for older graphs."""
    totals: Dict[str, float] = {k: 0.0 for k in SUM_KEYS}
    totals["appearances"] = 0
    for r in rows:
        for key in SUM_KEYS:
            totals[key] += _float(r.get(key)) or 0.0
        apps = _int(r.get("appearances"))
        totals["appearances"] += apps if apps is not None else (_int(r.get("games")) or 0)
    return totals


def per_game(totals: Optional[Dict[str, float]]) -> Optional[float]:
    """Points per appearance, FPL's basis for points per game."""
    if not totals or not totals.get("appearances"):
        return None
    return round(totals["total_points"] / totals["appearances"], 2)


STAT_LABELS = {
    "total_points": "Points", "goals": "Goals", "assists": "Assists", "clean_sheets": "Clean sheets",
    "bonus": "Bonus", "saves": "Saves", "minutes": "Minutes", "points_per_game": "Pts/game",
}
POSITION_STATS = {
    "GK": ("total_points", "clean_sheets", "saves", "bonus", "points_per_game", "minutes"),
    "DEF": ("total_points", "clean_sheets", "goals", "assists", "points_per_game", "minutes"),
    "MID": ("total_points", "goals", "assists", "bonus", "points_per_game", "minutes"),
    "FWD": ("total_points", "goals", "assists", "bonus", "points_per_game", "minutes"),
}


def stat_items(totals: Dict[str, float], position: Optional[str]) -> List[Dict[str, Any]]:
    values = dict(totals, points_per_game=per_game(totals))
    return [
        {"key": key, "label": STAT_LABELS[key], "value": _num(values.get(key)),
         "format": "dec1" if key == "points_per_game" else "int"}
        for key in POSITION_STATS.get(position or "", POSITION_STATS["MID"])
    ]


def position_rank(pool: Iterable[Dict[str, Any]], name: str) -> Optional[Tuple[int, int]]:
    """(rank by points, pool size) of `name` among POSITION_POOL_QUERY rows."""
    points = {r["name"]: _float(r.get("total_points")) or 0.0 for r in pool if r.get("name")}
    if name not in points:
        return None
    mine = points[name]
    return sum(1 for v in points.values() if v > mine) + 1, len(points)


def rival_in_pool(pool: Iterable[Dict[str, Any]], name: str) -> Optional[str]:
    """The benchmark to compare `name` with: the pool's leader by points (the runner-up
    when `name` leads)."""
    ranked = sorted((r for r in pool if r.get("name")),
                    key=lambda r: (-(_float(r.get("total_points")) or 0.0), r["name"]))
    names = [r["name"] for r in ranked]
    if name not in names or len(names) < 2:
        return None
    return names[1] if names[0] == name else names[0]


# ------------------------------------------------------------------ pure: fixtures

def team_fixture(fixture: Dict[str, Any], team: str) -> Dict[str, Any]:
    """A cleaned fixture (season_service.clean_fixture_rows) from `team`'s side. FPL's
    home/away difficulty is the difficulty for that side, i.e. how hard the opponent is."""
    home = fixture["home_team"] == team
    return {
        "team": fixture["away_team"] if home else fixture["home_team"],
        "home": home,
        "difficulty": fixture["home_difficulty"] if home else fixture["away_difficulty"],
        "kickoff_time": ss.to_iso(fixture["kickoff_time"]),
        "gameweek": fixture["gameweek"],
    }


def gameweek_opponents(fixtures: Iterable[Dict[str, Any]], team: Optional[str],
                       gameweek: Optional[int]) -> List[Dict[str, Any]]:
    if not team or gameweek is None:
        return []
    return [team_fixture(f, team) for f in fixtures
            if f["gameweek"] == gameweek and team in (f["home_team"], f["away_team"])]


def upcoming_fixtures(fixtures: Iterable[Dict[str, Any]], team: str, limit: int = TEAM_FIXTURES) -> List[Dict[str, Any]]:
    rows = [team_fixture(f, team) for f in fixtures
            if f["status"] == "scheduled" and team in (f["home_team"], f["away_team"])]
    return rows[:limit]


def team_results(fixtures: Iterable[Dict[str, Any]], team: str) -> List[Dict[str, Any]]:
    """Finished matches of `team`, oldest first, with the result from its side."""
    out = []
    for f in fixtures:
        if f["status"] != "finished" or team not in (f["home_team"], f["away_team"]):
            continue
        if f["home_score"] is None or f["away_score"] is None:
            continue
        home = f["home_team"] == team
        gf, ga = (f["home_score"], f["away_score"]) if home else (f["away_score"], f["home_score"])
        out.append({
            "opponent": f["away_team"] if home else f["home_team"], "home": home,
            "goals_for": gf, "goals_against": ga, "score": f"{gf}-{ga}",
            "result": "W" if gf > ga else ("D" if gf == ga else "L"),
            "gameweek": f["gameweek"], "kickoff_time": ss.to_iso(f["kickoff_time"]),
        })
    return out


def _prediction_opponents(row: Any, fixtures: List[Dict[str, Any]], team: Optional[str],
                          gameweek: Optional[int]) -> List[Dict[str, Any]]:
    """Exact opponents with FDR from the fixtures, else the predictions file's text."""
    exact = gameweek_opponents(fixtures, team, gameweek)
    if exact:
        return exact
    return [{**o, "difficulty": None, "kickoff_time": None, "gameweek": gameweek}
            for o in ss.opponents_from_prediction(row)]


# ------------------------------------------------------------------ pure: predictions

def _name_lower(df: Any) -> Any:
    return df["name_lower"] if "name_lower" in df.columns else df["name"].str.lower()


def model_label(df: Any) -> Optional[str]:
    """'xgboost_v3' -> 'XGBoost v3'."""
    if df is None or "prediction_method" not in df.columns or not len(df):
        return None
    method = str(df["prediction_method"].mode().iat[0])
    return method.replace("xgboost", "XGBoost").replace("_", " ")


def prediction_for(df: Any, name: str, fixtures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Next-gameweek prediction for one player with overall and position ranks, or None
    when the player isn't in the predictions file (not in the current FPL game)."""
    if df is None or not len(df) or not name:
        return None
    hit = df[_name_lower(df) == name.lower()]
    if not len(hit):
        return None
    row = hit.iloc[0]
    points, gameweek = _float(row.get("predicted_points")), _int(row.get("gameweek"))
    if points is None or gameweek is None:
        return None
    team = _clean(row.get("team"))
    position = ss.normalize_position(_clean(row.get("position")))
    all_points = df["predicted_points"].astype(float)
    same_position = all_points[df["position"].map(lambda p: ss.normalize_position(_clean(p))) == position]
    return {
        "gameweek": gameweek,
        "points": round(points, 1),
        "opponents": _prediction_opponents(row, fixtures, team, gameweek),
        "rank": int((all_points > points).sum()) + 1,
        "pool": int(len(all_points)),
        "position_rank": int((same_position > points).sum()) + 1,
        "position_pool": int(len(same_position)),
    }


# ------------------------------------------------------------------ pure: highlights

def player_highlights(*, name: str, position: Optional[str], seasons: List[Dict[str, Any]],
                      matches: List[Dict[str, Any]], scope: Optional[str],
                      rank: Optional[Tuple[int, int]], rank_season: Optional[str],
                      totals: Dict[str, float], current: str = CURRENT_SEASON,
                      underlying: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Notable stats for a player card, most interesting first: streaks, position rank,
    biggest haul, finishing against xG, best season, recent form, double-digit hauls,
    minutes share."""
    out: List[Dict[str, Any]] = []
    scope_matches = [m for m in matches if scope is None or m["season"] == scope]
    apps = [m for m in scope_matches if m["minutes"] > 0]
    where = season_label(scope, current) if scope else "all seasons"

    goal_run = _streak(apps, lambda m: m["goals"] > 0)
    return_run = _streak(apps, lambda m: m["goals"] + m["assists"] > 0)
    clean_run = _streak(apps, lambda m: m["clean_sheets"] > 0) if position in ("GK", "DEF") else 0
    blank_run = _streak(apps, lambda m: m["points"] <= BLANK_POINTS)
    if goal_run >= 2:
        out.append(highlight("Scoring streak", _plural(goal_run, "game"),
                             f"Scored in each of the last {goal_run} appearances", "accent", "flame"))
    elif return_run >= 3:
        out.append(highlight("Returns streak", _plural(return_run, "game"),
                             f"A goal or assist in each of the last {return_run} appearances", "accent", "flame"))
    if clean_run >= 2:
        out.append(highlight("Clean sheet run", f"{clean_run} in a row",
                             f"Clean sheets in each of the last {clean_run} appearances", "accent", "shield"))
    if blank_run >= 3:
        out.append(highlight("Blank run", _plural(blank_run, "game"),
                             f"{BLANK_POINTS} points or fewer in each of the last {blank_run} appearances",
                             "warning", "trend-down"))

    if rank and rank_season:
        place, pool = rank
        word = POSITION_WORDS.get(position or "", ("player", "players"))[1]
        tone = "accent" if place <= 5 else ("info" if place <= 20 else "neutral")
        out.append(highlight(f"{position or 'Player'} rank", f"#{place}",
                             f"By points among {pool} {word}, {season_label(rank_season, current)}", tone, "medal"))

    if scope_matches:
        best = max(reversed(scope_matches), key=lambda m: m["points"])  # ties: most recent
        if best["points"] >= 8:
            opponent = f"v {best['opponent']}{venue(best['home'])}, " if best["opponent"] else ""
            out.append(highlight("Biggest haul", f"{best['points']} pts",
                                 f"{opponent}GW {best['gameweek']} {best['season']}",
                                 "accent" if best["points"] >= 15 else "info", "trophy"))

    if underlying and underlying["minutes"] >= 270:
        diff = underlying["goals"] - underlying["xg"]
        if abs(diff) >= 1.5:
            above = diff > 0
            out.append(highlight("Finishing", f"{'+' if above else '-'}{abs(diff):.1f} v xG",
                                 f"{_plural(underlying['goals'], 'goal')} from {underlying['xg']:.1f} expected, "
                                 f"{season_label(underlying['season'], current)}",
                                 "accent" if above else "warning", "target"))

    if scope is None and len(seasons) >= 2:
        top = max(seasons, key=lambda r: _float(r.get("total_points")) or 0.0)
        out.append(highlight("Best season", str(top["season"]),
                             f"{_int(top.get('total_points')) or 0} pts, {_plural(_int(top.get('goals')) or 0, 'goal')}",
                             "info", "calendar"))

    season_ppg = per_game(totals)
    if len(apps) >= 8 and season_ppg is not None:
        last5 = apps[-5:]
        recent = sum(m["points"] for m in last5) / len(last5)
        if abs(recent - season_ppg) >= 1.5:
            up = recent > season_ppg
            out.append(highlight("Last 5", f"{recent:.1f} pts/app",
                                 f"{'Up' if up else 'Down'} from {season_ppg:.1f} across {where}",
                                 "accent" if up else "warning", "trend-up" if up else "trend-down"))

    hauls = sum(1 for m in scope_matches if m["points"] >= HAUL_POINTS)
    if hauls >= 2:
        out.append(highlight("Double digits", f"{hauls}×", f"{HAUL_POINTS}+ point hauls, {where}", "info", "zap"))

    if scope and totals.get("games", 0) >= 5:
        share = totals["minutes"] / (totals["games"] * 90)
        if share >= 0.9:
            out.append(highlight("Minutes", f"{share:.0%}", f"Of the available minutes, {where}", "info", "clock"))
        elif share < 0.5 and totals.get("appearances", 0) >= 3:
            out.append(highlight("Minutes", f"{share:.0%}", f"Of the available minutes, {where}", "warning", "clock"))

    return out[:MAX_HIGHLIGHTS]


def comparison_highlights(names: Sequence[str], metrics: List[Dict[str, Any]],
                          predictions: Sequence[Optional[Dict[str, Any]]],
                          forms: Sequence[List[Dict[str, Any]]], short: Callable[[str], str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    by_key = {m["key"]: m["values"] for m in metrics}
    a, b = short(names[0]), short(names[1])

    points = by_key.get("total_points")
    if points and None not in points and points[0] != points[1]:
        lead = 0 if points[0] > points[1] else 1
        out.append(highlight("Points edge", f"+{_fmt(abs(points[0] - points[1]))}",
                             f"{(a, b)[lead]} over {(b, a)[lead]}", "accent", "trophy"))
    ppg = by_key.get("points_per_game")
    if ppg and None not in ppg and ppg[0] != ppg[1]:
        lead = 0 if ppg[0] > ppg[1] else 1
        points_lead = None if not points or None in points or points[0] == points[1] else (0 if points[0] > points[1] else 1)
        if points_lead is not None and lead != points_lead:
            out.append(highlight("Per appearance", f"{ppg[lead]:.1f} v {ppg[1 - lead]:.1f}",
                                 f"{(a, b)[lead]} scores more per game", "info", "trend-up"))
    if all(predictions):
        pa, pb = predictions[0]["points"], predictions[1]["points"]
        if pa != pb:
            lead = 0 if pa > pb else 1
            out.append(highlight("Next GW", f"{pa:.1f} v {pb:.1f}",
                                 f"The model favours {(a, b)[lead]} for GW {predictions[0]['gameweek']}", "accent", "star"))
    recent = []
    for form in forms:
        played = [m for m in form if m["minutes"] > 0][-5:]
        recent.append(sum(m["points"] for m in played) / len(played) if len(played) >= 3 else None)
    if None not in recent and abs(recent[0] - recent[1]) >= 1:
        lead = 0 if recent[0] > recent[1] else 1
        out.append(highlight("Recent form", f"{recent[0]:.1f} v {recent[1]:.1f}",
                             f"Points per appearance over the last 5; {(a, b)[lead]} in better form", "info", "flame"))
    return out[:MAX_HIGHLIGHTS]


def team_highlights(team: str, results: List[Dict[str, Any]], standing: Optional[Dict[str, Any]],
                    table: List[Dict[str, Any]], upcoming: List[Dict[str, Any]],
                    top_players: List[Dict[str, Any]], short: Callable[[str], str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    letters = [r["result"] for r in results]
    won, unbeaten = _streak(letters, lambda r: r == "W"), _streak(letters, lambda r: r != "L")
    lost, winless = _streak(letters, lambda r: r == "L"), _streak(letters, lambda r: r != "W")
    if won >= 2:
        out.append(highlight("Form", f"Won {won} in a row", "Most recent league matches", "accent", "flame"))
    elif unbeaten >= 3:
        out.append(highlight("Form", f"Unbeaten in {unbeaten}", "Most recent league matches", "accent", "shield"))
    elif lost >= 2:
        out.append(highlight("Form", f"Lost {lost} in a row", "Most recent league matches", "danger", "trend-down"))
    elif winless >= 3:
        out.append(highlight("Form", f"No win in {winless}", "Most recent league matches", "warning", "trend-down"))

    played = [r for r in table if r["played"] > 0]
    if standing and standing["played"] > 0 and len(played) >= 4:
        if standing["goals_for"] == max(r["goals_for"] for r in played):
            out.append(highlight("Best attack", _plural(standing["goals_for"], "goal"),
                                 "The most in the league", "accent", "target"))
        elif standing["goals_against"] == min(r["goals_against"] for r in played):
            out.append(highlight("Best defence", f"{standing['goals_against']} conceded",
                                 "The fewest in the league", "accent", "shield"))

    clean = sum(1 for r in results if r["goals_against"] == 0)
    if clean >= 2:
        out.append(highlight("Clean sheets", f"{clean} in {len(results)}", "This season", "info", "shield"))

    next3 = [f["difficulty"] for f in upcoming[:3] if f.get("difficulty") is not None]
    if len(next3) == 3:
        avg = sum(next3) / 3
        if avg <= 2.5 or avg >= 3.7:
            kind = avg <= 2.5
            out.append(highlight("Next 3 fixtures", f"FDR {avg:.1f}", "A kind run" if kind else "A tough run",
                                 "accent" if kind else "warning", "calendar"))

    scorer = max(top_players, key=lambda p: p.get("goals") or 0, default=None)
    if scorer and (scorer.get("goals") or 0) >= 2:
        out.append(highlight("Top scorer", short(scorer["player"]["name"]), _plural(scorer["goals"], "goal"),
                             "info", "target"))
    return out[:MAX_HIGHLIGHTS]


def results_record(team: str, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    won = sum(1 for m in matches if m["result"] == "W")
    drawn = sum(1 for m in matches if m["result"] == "D")
    lost = sum(1 for m in matches if m["result"] == "L")
    goals_for = sum(m["home_score"] if m["home_team"] == team else m["away_score"] for m in matches)
    goals_against = sum(m["away_score"] if m["home_team"] == team else m["home_score"] for m in matches)
    return {"team": team, "played": len(matches), "won": won, "drawn": drawn, "lost": lost,
            "goals_for": goals_for, "goals_against": goals_against}


def _scoreline(m: Dict[str, Any]) -> str:
    return f"{m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}"


def results_highlights(team: str, matches: List[Dict[str, Any]], opponent: Optional[str] = None) -> List[Dict[str, Any]]:
    """matches oldest first, `result` from `team`'s side; `opponent` set for head to head."""
    out: List[Dict[str, Any]] = []
    if not matches:
        return out

    def margin(m: Dict[str, Any]) -> int:
        diff = m["home_score"] - m["away_score"]
        return diff if m["home_team"] == team else -diff

    wins = [m for m in matches if margin(m) > 0]
    if wins:
        best = max(reversed(wins), key=margin)
        out.append(highlight("Biggest win", _scoreline(best), f"GW {best['gameweek']}, {best['season']}", "accent", "trophy"))
    if opponent:
        losses = [m for m in matches if margin(m) < 0]
        if losses:
            worst = min(reversed(losses), key=margin)
            out.append(highlight(f"Best {opponent} win", _scoreline(worst),
                                 f"GW {worst['gameweek']}, {worst['season']}", "info", "trophy"))
        last = matches[-1]
        out.append(highlight("Last meeting", _scoreline(last), f"GW {last['gameweek']}, {last['season']}", "neutral", "calendar"))
        unbeaten = _streak(matches, lambda m: m["result"] != "L")
        winless = _streak(matches, lambda m: m["result"] != "W")
        if unbeaten >= 3:
            out.append(highlight("Unbeaten run", f"{unbeaten} meetings", f"{team} without defeat", "accent", "shield"))
        elif winless >= 3:
            out.append(highlight("Unbeaten run", f"{winless} meetings", f"{opponent} without defeat", "accent", "shield"))
    else:
        clean = sum(1 for m in matches if (m["away_score"] if m["home_team"] == team else m["home_score"]) == 0)
        if clean >= 2:
            out.append(highlight("Clean sheets", f"{clean} in {len(matches)}", "In these matches", "info", "shield"))
        letters = [m["result"] for m in matches]
        won, unbeaten = _streak(letters, lambda r: r == "W"), _streak(letters, lambda r: r != "L")
        if won >= 2:
            out.append(highlight("Form", f"Won {won} in a row", "Most recent results", "accent", "flame"))
        elif unbeaten >= 3:
            out.append(highlight("Form", f"Unbeaten in {unbeaten}", "Most recent results", "accent", "shield"))
    goals = sum(m["home_score"] + m["away_score"] for m in matches)
    out.append(highlight("Goals per game", f"{goals / len(matches):.1f}", f"{_plural(goals, 'goal')} in {_plural(len(matches), 'match', 'matches')}",
                         "info", "target"))
    return out[:MAX_HIGHLIGHTS]


# ------------------------------------------------------------------ pure: leaderboards

# Ranking queries the leaderboard card understands: the metric column (key, label, format,
# unit), extra columns and a title. Formats: int, dec1, dec2, money (£m), compact (1.2M).
_SORTS = {"goals": "goals", "goals_scored": "goals", "assists": "assists", "bonus": "bonus", "bps": "bonus",
          "points_per_game": "points_per_game", "total_points": "total_points", "points": "total_points"}
_TOP_BY_SORT = {
    "goals": (("goals", "Goals", "int", "goals"), [("total_points", "Pts", "int"), ("assists", "Ast", "int")], "Top scorers", "target"),
    "assists": (("assists", "Assists", "int", "assists"), [("total_points", "Pts", "int"), ("goals", "Gls", "int")], "Top assisters", "zap"),
    "bonus": (("bonus", "Bonus", "int", "bonus points"), [("total_points", "Pts", "int"), ("goals", "Gls", "int")], "Bonus point leaders", "star"),
    "points_per_game": (("points_per_game", "Pts/game", "dec2", "points per game"), [("total_points", "Pts", "int"), ("goals", "Gls", "int")], "Best points per game", "trend-up"),
    "total_points": (("total_points", "Points", "int", "points"), [("goals", "Gls", "int"), ("assists", "Ast", "int")], "Top points scorers", "trophy"),
}
_OTHER_LEADERBOARDS = {
    "get_top_players_all_positions": (("total_points", "Points", "int", "points"), [("goals", "Gls", "int"), ("assists", "Ast", "int")], "Top players by position", "trophy"),
    "get_best_value_players": (("points_per_million", "Pts/£m", "dec1", "points per £m"), [("total_points", "Pts", "int"), ("value_millions", "Price", "money")], "Best value", "zap"),
    "get_clean_sheet_leaders": (("total_clean_sheets", "Clean sheets", "int", "clean sheets"), [("total_points", "Pts", "int"), ("clean_sheets_per_game", "CS/game", "dec2")], "Clean sheet leaders", "shield"),
    "get_bonus_point_leaders": (("total_bonus", "Bonus", "int", "bonus points"), [("total_bps", "BPS", "int"), ("bonus_per_game", "Per game", "dec2")], "Bonus point leaders", "star"),
    "get_ict_index_leaders": (("avg_ict_index", "ICT/app", "dec1", "ICT per appearance"), [("total_points", "Pts", "int"), ("games", "Apps", "int")], "ICT index leaders", "zap"),
    "get_most_selected_players": (("selected", "Selected by", "compact", "managers"), [("value_millions", "Price", "money"), ("points", "GW pts", "int")], "Most selected", "shield"),
    "get_team_top_performers": (("total_points", "Points", "int", "points"), [("goals", "Gls", "int"), ("assists", "Ast", "int")], "Top performers", "trophy"),
    "get_team_squad": (("total_points", "Points", "int", "points"), [("games", "Apps", "int"), ("minutes", "Mins", "int")], "Squad", "shield"),
    "get_highest_single_gameweek_score": (("points", "Points", "int", "points"), [("goals", "Gls", "int"), ("assists", "Ast", "int"), ("bonus", "Bonus", "int")], "Highest gameweek score", "trophy"),
    "get_top_scorers_by_season": (("total_goals", "Goals", "int", "goals"), [("total_points", "Pts", "int")], "Top scorers", "target"),
    "get_top_assisters_by_season": (("total_assists", "Assists", "int", "assists"), [("total_points", "Pts", "int")], "Top assisters", "zap"),
}
LEADERBOARD_METHODS = {"get_top_points_by_position", "get_most_transferred_players", *_OTHER_LEADERBOARDS}


def leaderboard_spec(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if method == "get_top_points_by_position":
        metric, columns, title, icon = _TOP_BY_SORT[_SORTS.get(params.get("sort_by") or "", "total_points")]
        position = params.get("position")
        if metric[0] == "total_points" and position in POSITION_WORDS:
            title = f"Top {POSITION_WORDS[position][1]}"
    elif method == "get_most_transferred_players":
        direction = "out" if params.get("direction") == "out" else "in"
        metric = ("transfers", f"Transfers {direction}", "compact", "transfers")
        columns, title, icon = [("points", "GW pts", "int")], f"Most transferred {direction}", "trend-up" if direction == "in" else "trend-down"
    elif method in _OTHER_LEADERBOARDS:
        metric, columns, title, icon = _OTHER_LEADERBOARDS[method]
        if method == "get_team_top_performers" and params.get("team_name"):
            title = f"{params['team_name']} top performers"
        if method == "get_team_squad" and params.get("team_name"):
            position = params.get("position")
            title = f"{params['team_name']} {POSITION_WORDS[position][1] if position in POSITION_WORDS else 'squad'}"
    else:
        return None
    key, label, fmt, unit = metric
    return {"metric": {"key": key, "label": label, "format": fmt, "unit": unit},
            "columns": [{"key": k, "label": lab, "format": f} for k, lab, f in columns],
            "title": title, "icon": icon,
            "grouped": method == "get_top_players_all_positions"
            or (method == "get_team_squad" and params.get("position") not in POSITION_WORDS)}


_SINGULAR_UNITS = {"goals": "goal", "assists": "assist", "points": "point", "bonus points": "bonus point",
                   "clean sheets": "clean sheet", "transfers": "transfer", "managers": "manager"}


def _unit(value: float, unit: str) -> str:
    return _SINGULAR_UNITS.get(unit, unit) if value == 1 else unit


def _format_value(value: float, fmt: str) -> str:
    if fmt == "money":
        return f"£{value:.1f}m"
    if fmt == "compact":
        return _compact(value)
    if fmt == "dec2":
        return f"{value:.2f}"
    if fmt == "dec1":
        return f"{value:.1f}"
    return _fmt(value)


def leaderboard_subtitle(method: str, params: Dict[str, Any], current: str = CURRENT_SEASON) -> str:
    parts = []
    position = params.get("position")
    if position in POSITION_WORDS and method != "get_team_squad" and not (
            method == "get_top_points_by_position"
            and _SORTS.get(params.get("sort_by") or "", "total_points") == "total_points"):
        parts.append(POSITION_WORDS[position][1].capitalize())
    if method == "get_top_points_by_position" and _SORTS.get(params.get("sort_by") or "", "total_points") == "total_points":
        parts.append("By points")
    if params.get("gameweek") and method in ("get_top_points_by_position", "get_top_players_all_positions",
                                             "get_most_transferred_players", "get_most_selected_players"):
        parts.append(f"GW {params['gameweek']}")
    parts.append(season_label(params.get("season"), current))
    return " · ".join(parts)


def leaderboard_highlights(rows: List[Dict[str, Any]], metric: Dict[str, Any], grouped: bool,
                           has_position: bool, short: Callable[[str], str],
                           one_club: bool = False) -> List[Dict[str, Any]]:
    """Ties or the leader's margin, a club with 3+ players in the list (unless the list is
    one club's), and a dominant position when the ranking isn't for one position."""
    out: List[Dict[str, Any]] = []
    values = [r["value"] for r in rows]
    if grouped or len(rows) < 2 or not values[0]:
        return out
    unit, fmt = metric["unit"], metric["format"]
    top = values[0]
    tied = [r for r in rows if r["value"] == top]
    if len(tied) >= 2:
        names = ", ".join(short(r["player"]["name"]) for r in tied[:4]) + (" and more" if len(tied) > 4 else "")
        out.append(highlight("Joint leaders", _plural(len(tied), "player"),
                             f"{names} on {_format_value(top, fmt)} {_unit(top, unit)}", "accent", "trophy"))
    elif values[1] is not None and top > values[1]:
        gap = top - values[1]
        out.append(highlight("Clear at the top", f"+{_format_value(gap, fmt)}",
                             f"{short(rows[0]['player']['name'])} leads {short(rows[1]['player']['name'])} "
                             f"by {_format_value(gap, fmt)} {_unit(gap, unit)}", "accent", "trophy"))
    if len(rows) >= 6:
        clubs = Counter(r["player"]["team"] for r in rows if r["player"]["team"]).most_common(1)
        if clubs and clubs[0][1] >= 3 and not one_club:
            club, count = clubs[0]
            out.append(highlight("Club cluster", f"{count} of {len(rows)}", f"{club} players in this list", "info", "shield"))
        positions = Counter(r["player"]["position"] for r in rows if r["player"]["position"]).most_common(1)
        if not has_position and positions and positions[0][1] >= max(4, len(rows) // 2):
            pos, count = positions[0]
            out.append(highlight("Position mix", f"{count} {POSITION_WORDS[pos][1]}", f"In the top {len(rows)}", "info", "target"))
    return out[:3]


def prediction_highlights(rows: List[Dict[str, Any]], short: Callable[[str], str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if len(rows) >= 2 and rows[0]["points"] - rows[1]["points"] >= 0.1:
        out.append(highlight("Top pick", f"+{rows[0]['points'] - rows[1]['points']:.1f} pts",
                             f"{short(rows[0]['player']['name'])} ahead of {short(rows[1]['player']['name'])}",
                             "accent", "star"))
    doubles = [r for r in rows if len(r["opponents"]) >= 2]
    if doubles:
        out.append(highlight("Double gameweek", ", ".join(short(r["player"]["name"]) for r in doubles[:3]),
                             "Two fixtures this gameweek", "info", "calendar"))
    rated = [(r["opponents"][0]["difficulty"], r) for r in rows
             if r["opponents"] and r["opponents"][0].get("difficulty") is not None]
    if rated:
        difficulty, row = min(rated, key=lambda x: x[0])
        if difficulty <= 2:
            opp = row["opponents"][0]
            out.append(highlight("Kindest fixture", f"FDR {difficulty}",
                                 f"{short(row['player']['name'])} v {opp['team']}{venue(opp['home'])}", "accent", "target"))
    doubts = [r for r in rows if r["player"]["status"] in ("d", "i", "s", "u", "n")]
    if doubts:
        r = doubts[0]
        chance = f" ({r['player']['chance']}%)" if r["player"]["chance"] is not None else ""
        out.append(highlight("Fitness doubt", f"{short(r['player']['name'])}{chance}",
                             r["player"]["news"] or "Flagged by FPL", "warning", "alert"))
    return out[:3]


# ------------------------------------------------------------------ Cypher

PLAYERS_QUERY = """
UNWIND $names AS name
MATCH (p:Player {name: name})
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
OPTIONAL MATCH (p)-[:PLAYS_FOR]->(t:Team)
OPTIONAL MATCH (p)-[st:SEASON_STATS]->(:Season {id: $season})
RETURN p.name AS name, pos.code AS position, t.name AS current_team, p.last_team AS last_team,
       st.team AS season_team, st.position AS season_position, p.fpl_code AS fpl_code,
       p.status AS status, p.chance_of_playing_next_round AS chance, p.news AS news
"""

PLAYER_SEASONS_QUERY = """
UNWIND $names AS name
MATCH (p:Player {name: name})-[st:SEASON_STATS]->(s:Season)
RETURN p.name AS name, s.id AS season, st.team AS team, st.position AS position,
       st.total_points AS total_points, st.goals AS goals, st.assists AS assists,
       st.clean_sheets AS clean_sheets, st.bonus AS bonus, st.minutes AS minutes, st.saves AS saves,
       st.games AS games, st.appearances AS appearances, st.ict_sum AS ict_sum, st.value_sum AS value_sum
ORDER BY name, season
"""

PLAYER_MATCHES_QUERY = """
UNWIND $names AS name
MATCH (p:Player {name: name})-[r:PLAYED_IN]->(f:Fixture)
OPTIONAL MATCH (f)-[:HOME_TEAM]->(h:Team)
OPTIONAL MATCH (f)-[:AWAY_TEAM]->(a:Team)
RETURN p.name AS name, coalesce(r.season, f.season_id) AS season, coalesce(r.gameweek, f.gameweek) AS gameweek,
       f.kickoff_time AS kickoff_time, r.total_points AS points, r.minutes AS minutes,
       r.goals_scored AS goals, r.assists AS assists, r.clean_sheets AS clean_sheets, r.bonus AS bonus,
       r.goals_conceded AS goals_conceded, r.team AS team, r.was_home AS was_home,
       h.name AS home_team, a.name AS away_team, f.home_score AS home_score, f.away_score AS away_score,
       r.value AS value, r.selected AS selected, r.expected_goals AS xg, r.expected_assists AS xa,
       r.expected_goal_involvements AS xgi, r.expected_goals_conceded AS xgc
"""

# Everyone who played in the player's position that season (the position they had then)
POSITION_POOL_QUERY = """
MATCH (:Player {name: $name})-[mine:SEASON_STATS]->(s:Season {id: $season})
MATCH (p:Player)-[st:SEASON_STATS]->(s)
WHERE st.position = mine.position AND coalesce(st.appearances, st.games, 0) > 0
RETURN p.name AS name, st.total_points AS total_points
"""

TEAM_PLAYERS_QUERY = """
MATCH (p:Player)-[st:SEASON_STATS]->(:Season {id: $season})
WHERE st.team = $team
OPTIONAL MATCH (p)-[:PLAYS_FOR]->(t:Team)
RETURN p.name AS name, st.position AS position, st.total_points AS total_points, st.goals AS goals,
       st.assists AS assists, st.clean_sheets AS clean_sheets, coalesce(st.appearances, st.games, 0) AS games,
       t.name AS current_team, p.last_team AS last_team, p.fpl_code AS fpl_code
ORDER BY total_points DESC, name
LIMIT $limit
"""


# ------------------------------------------------------------------ context

@dataclass
class PlayerData:
    """One player's graph data for player and comparison cards."""
    name: str
    info: Dict[str, Any]
    seasons: List[Dict[str, Any]] = field(default_factory=list)   # oldest first
    matches: List[Dict[str, Any]] = field(default_factory=list)   # match_rows(), oldest first

    @property
    def position(self) -> Optional[str]:
        return ss.normalize_position(_clean(self.info.get("position")))

    @property
    def team(self) -> Optional[str]:
        """Current club, else the last one."""
        return _clean(self.info.get("current_team")) or _clean(self.info.get("last_team"))

    def scope_rows(self, season: Optional[str]) -> List[Dict[str, Any]]:
        return [r for r in self.seasons if season is None or r["season"] == season]


@dataclass
class CardContext:
    """Everything the card builders need about one answered question."""
    question: str
    intent: str                               # preprocessing.intent_classifier.Intent value
    method: Optional[str]                     # CypherQueries method that ran (None when none did)
    params: Dict[str, Any]
    entities: Dict[str, Any]                  # ExtractedEntities.to_dict()
    results: List[Dict[str, Any]]
    conn: Any
    current_season: str = CURRENT_SEASON
    photo_for: Optional[PhotoFn] = None
    predictions: Any = None                   # DataFrame from services.predictions, or None
    photo_codes: Dict[int, int] = field(default_factory=dict)    # FPL element -> photo code
    web_names: Dict[str, str] = field(default_factory=dict)      # player name -> FPL web name
    meta: Dict[str, Any] = field(default_factory=dict)           # data/live/meta.json
    _fixtures: Optional["asyncio.Future"] = field(default=None, repr=False)

    async def query(self, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.conn.execute_query, cypher, params)

    async def fixtures(self) -> List[Dict[str, Any]]:
        """The current season's cleaned fixtures (season_service's cache); [] if unavailable."""
        if self._fixtures is None:
            self._fixtures = asyncio.ensure_future(self._load_fixtures())
        return await self._fixtures

    async def _load_fixtures(self) -> List[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(ss.load_fixtures, self.conn, self.current_season)
        except Exception as exc:
            logger.warning("Fixtures unavailable for answer cards: %s", exc)
            return []

    def photo(self, name: str, team: Optional[str], code: Optional[int] = None) -> Optional[str]:
        if self.photo_for is None or not name:
            return None
        try:
            return self.photo_for(name, team, code)
        except Exception:
            return None

    def short(self, name: str) -> str:
        return short_name(name, self.web_names)


async def load_players(ctx: CardContext, names: List[str], season: Optional[str]) -> Dict[str, PlayerData]:
    info, seasons, matches = await asyncio.gather(
        ctx.query(PLAYERS_QUERY, {"names": names, "season": season}),
        ctx.query(PLAYER_SEASONS_QUERY, {"names": names}),
        ctx.query(PLAYER_MATCHES_QUERY, {"names": names}),
    )
    players = {r["name"]: PlayerData(r["name"], r) for r in info if r.get("name")}
    for r in seasons:
        if r.get("name") in players:
            players[r["name"]].seasons.append(r)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in matches:
        grouped.setdefault(r.get("name"), []).append(r)
    for name, data in players.items():
        data.seasons.sort(key=lambda r: r["season"])
        data.matches = match_rows(grouped.get(name, []))
    return players


def _follow(question: str, intent: str) -> Tuple[str, str]:
    return question, intent


# ------------------------------------------------------------------ player card

def player_card(data: PlayerData, *, season: Optional[str], gameweek: Optional[int],
                pool: List[Dict[str, Any]], pool_season: Optional[str],
                prediction: Optional[Dict[str, Any]], photo: Optional[str], emphasis: str,
                current: str = CURRENT_SEASON, short: Callable[[str], str] = short_name,
                fixtures: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    rows = data.scope_rows(season)
    totals = season_totals(rows)
    position = data.position or (ss.normalize_position(_clean(rows[-1].get("position"))) if rows else None)
    team = (_clean(rows[-1].get("team")) if season and rows else None) or data.team
    scope_matches = [m for m in data.matches if season is None or m["season"] == season]
    match = next((m for m in scope_matches if m["gameweek"] == gameweek), None) if gameweek else None
    rank = position_rank(pool, data.name)
    info = data.info
    # Price, ownership and expected stats are per season: the one asked about, else the latest
    focus = season or (data.seasons[-1]["season"] if data.seasons else None)
    underlying = underlying_stats(data.matches, focus)
    current_team = _clean(info.get("current_team"))

    follow_ups = []
    if prediction and emphasis != "prediction":
        follow_ups.append(_follow(f"Predicted points for {data.name} next gameweek", "prediction"))
    if emphasis == "prediction" or season != current:
        if any(r["season"] == current for r in data.seasons):
            follow_ups.append(_follow(f"{data.name} stats this season", "player_stats"))
    rival = rival_in_pool(pool, data.name)
    if rival:
        follow_ups.append(_follow(f"Compare {data.name} and {rival} {season_phrase(pool_season, current)}".strip(),
                                  "player_comparison"))
    earlier = [r["season"] for r in data.seasons if season and r["season"] < season]
    if earlier:
        follow_ups.append(_follow(f"{data.name} stats in {earlier[-1]}", "player_stats"))
    if position in POSITION_WORDS:
        follow_ups.append(_follow(f"Top 5 {POSITION_WORDS[position][1]} by points this season", "top_points"))

    return {
        "type": "player",
        "id": f"player:{data.name}:{season or 'all'}",
        "emphasis": emphasis,
        "player": player_ref(data.name, team, position, photo, info.get("status"), info.get("chance"), info.get("news")),
        "season": season,
        "season_label": season_label(season, current),
        "stats": stat_items(totals, position),
        "seasons": [
            {"season": r["season"], "team": _clean(r.get("team")), "points": _int(r.get("total_points")) or 0,
             "goals": _int(r.get("goals")) or 0, "assists": _int(r.get("assists")) or 0,
             "games": _int(r.get("appearances")) if _int(r.get("appearances")) is not None else (_int(r.get("games")) or 0)}
            for r in data.seasons
        ] if season is None and len(data.seasons) > 1 else [],
        "form": [form_point(m) for m in scope_matches[-FORM_LENGTH:]],
        "match": form_point(match) if match else None,
        "prediction": prediction,
        # The club's next five matches, with FPL difficulty (current players only)
        "next_fixtures": upcoming_fixtures(fixtures, current_team, NEXT_FIXTURES) if current_team else [],
        "trend": price_trend(data.matches, focus),
        "underlying": underlying,
        "highlights": player_highlights(name=data.name, position=position, seasons=data.seasons,
                                        matches=data.matches, scope=season, rank=rank,
                                        rank_season=pool_season, totals=totals, current=current,
                                        underlying=underlying),
        "_follow_ups": follow_ups,
    }


async def build_player_card(ctx: CardContext, name: str, season: Optional[str] = None,
                            gameweek: Optional[int] = None, emphasis: str = "stats") -> Optional[Dict[str, Any]]:
    data = (await load_players(ctx, [name], season)).get(name)
    if data is None or not (data.seasons or data.matches):
        return None
    if season and not data.scope_rows(season):
        season = None  # nothing that season: show the career instead
    if emphasis == "prediction" and season is None and data.seasons:
        season = data.seasons[-1]["season"]
    pool_season = season or (data.seasons[-1]["season"] if data.seasons else None)

    async def pool() -> List[Dict[str, Any]]:
        if not pool_season:
            return []
        return await ctx.query(POSITION_POOL_QUERY, {"name": name, "season": pool_season})

    pool_rows, fixtures = await asyncio.gather(pool(), ctx.fixtures())
    return player_card(data, season=season, gameweek=gameweek, pool=pool_rows, pool_season=pool_season,
                       prediction=prediction_for(ctx.predictions, name, fixtures),
                       photo=ctx.photo(name, data.team, _int(data.info.get("fpl_code"))),
                       emphasis=emphasis, current=ctx.current_season, short=ctx.short, fixtures=fixtures)


# ------------------------------------------------------------------ comparison card

COMPARE_METRICS = (
    ("total_points", "Points", "int", True),
    ("points_per_game", "Pts/game", "dec1", True),
    ("goals", "Goals", "int", True),
    ("assists", "Assists", "int", True),
    ("clean_sheets", "Clean sheets", "int", True),
    ("bonus", "Bonus", "int", True),
    ("minutes", "Minutes", "int", True),
    ("appearances", "Appearances", "int", True),
    ("avg_price", "Avg price", "money", False),
)


def _metric_value(totals: Optional[Dict[str, float]], key: str) -> Optional[float]:
    if totals is None:
        return None
    if key == "points_per_game":
        return per_game(totals)
    if key == "avg_price":
        return round(totals["value_sum"] / totals["games"] / 10.0, 1) if totals["games"] and totals["value_sum"] else None
    return _num(totals.get(key))


EXPECTED_METRICS = (("xg", "xG"), ("xa", "xA"))


def _metric_row(key: str, label: str, fmt: str, values: List[Optional[float]], scored: bool) -> Dict[str, Any]:
    better = None
    if scored and None not in values and values[0] != values[1]:
        better = 0 if values[0] > values[1] else 1
    return {"key": key, "label": label, "format": fmt, "values": values, "better": better, "scored": scored}


def comparison_metrics(totals: Sequence[Optional[Dict[str, float]]], positions: Sequence[Optional[str]],
                       underlying: Sequence[Optional[Dict[str, Any]]] = ()) -> List[Dict[str, Any]]:
    """Stat rows for two players. xG and xA follow assists when both players have expected
    stats for the season compared (single-season comparisons only)."""
    rows = []
    for key, label, fmt, scored in COMPARE_METRICS:
        values = [_metric_value(t, key) for t in totals]
        skip = key == "clean_sheets" and not {"GK", "DEF", "MID"} & set(p for p in positions if p)
        if not skip and any(v is not None for v in values):
            rows.append(_metric_row(key, label, fmt, values, scored))
        if key == "assists" and len(underlying) == 2 and all(underlying):
            rows += [_metric_row(k, lab, "dec1", [u[k] for u in underlying], True) for k, lab in EXPECTED_METRICS]
    return rows


def comparison_card(players: Sequence[PlayerData], *, season: Optional[str],
                    predictions: Sequence[Optional[Dict[str, Any]]], photos: Sequence[Optional[str]],
                    emphasis: str, current: str = CURRENT_SEASON,
                    short: Callable[[str], str] = short_name,
                    fixtures: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    totals, refs, forms, underlying, upcoming = [], [], [], [], []
    for data, photo in zip(players, photos):
        rows = data.scope_rows(season)
        totals.append(season_totals(rows) if rows else None)
        team = (_clean(rows[-1].get("team")) if season and rows else None) or data.team
        position = data.position or (ss.normalize_position(_clean(rows[-1].get("position"))) if rows else None)
        refs.append(player_ref(data.name, team, position, photo, data.info.get("status"),
                               data.info.get("chance"), data.info.get("news")))
        scope_matches = [m for m in data.matches if season is None or m["season"] == season]
        forms.append(scope_matches[-COMPARE_FORM_LENGTH:])
        underlying.append(underlying_stats(data.matches, season) if season else None)
        current_team = _clean(data.info.get("current_team"))
        upcoming.append(upcoming_fixtures(fixtures, current_team, COMPARE_FIXTURES) if current_team else [])
    metrics = comparison_metrics(totals, [r["position"] for r in refs], underlying)
    names = [d.name for d in players]
    phrase = season_phrase(season, current)
    follow_ups = [_follow(f"{name} stats {phrase}".strip(), "player_stats") for name in names]
    earlier = sorted({r["season"] for d in players for r in d.seasons if season and r["season"] < season})
    if earlier:
        follow_ups.append(_follow(f"Compare {names[0]} and {names[1]} in {earlier[-1]}", "player_comparison"))
    return {
        "type": "comparison",
        "id": f"comparison:{names[0]}:{names[1]}:{season or 'all'}",
        "emphasis": emphasis,
        "season": season,
        "season_label": season_label(season, current),
        "players": refs,
        "metrics": metrics,
        "leads": [sum(1 for m in metrics if m["better"] == side) for side in (0, 1)],
        "predictions": list(predictions),
        "next_fixtures": upcoming,
        "form": [[form_point(m) for m in form] for form in forms],
        "highlights": comparison_highlights(names, metrics, predictions, forms, short),
        "_follow_ups": follow_ups,
    }


async def build_comparison_card(ctx: CardContext, names: List[str], season: Optional[str] = None,
                                emphasis: str = "stats") -> Optional[Dict[str, Any]]:
    data = await load_players(ctx, names[:2], season)
    found = [data[n] for n in names[:2] if n in data and (data[n].seasons or data[n].matches)]
    if len(found) < 2:
        if found:  # only one of them exists: show that player
            return await build_player_card(ctx, found[0].name, season, emphasis=emphasis)
        return None
    if season and not any(d.scope_rows(season) for d in found):
        season = None
    fixtures = await ctx.fixtures()
    return comparison_card(
        found, season=season, emphasis=emphasis, current=ctx.current_season, short=ctx.short,
        predictions=[prediction_for(ctx.predictions, d.name, fixtures) for d in found],
        photos=[ctx.photo(d.name, d.team, _int(d.info.get("fpl_code"))) for d in found],
        fixtures=fixtures,
    )


# ------------------------------------------------------------------ leaderboard card

def leaderboard_card(method: str, params: Dict[str, Any], results: List[Dict[str, Any]],
                     infos: Dict[str, Dict[str, Any]], *, photo: Callable[[str, Optional[str], Optional[int]], Optional[str]],
                     current: str = CURRENT_SEASON, short: Callable[[str], str] = short_name) -> Optional[Dict[str, Any]]:
    spec = leaderboard_spec(method, params)
    if spec is None:
        return None
    metric, grouped = spec["metric"], spec["grouped"]
    season = params.get("season")
    show_availability = season == current
    rows_in: List[Dict[str, Any]] = []
    if grouped:
        seen: Counter = Counter()
        for r in results:
            group = ss.normalize_position(_clean(r.get("position")))
            if seen[group] < GROUP_ROWS:
                seen[group] += 1
                rows_in.append(r)
    else:
        rows_in = list(results[:LEADERBOARD_ROWS])
    rows_in = [r for r in rows_in if r.get("player_name")]
    if not rows_in:
        return None

    values = [_num(r.get(metric["key"])) for r in rows_in]
    if not any(values):
        return None  # nothing to rank (e.g. a gameweek with no transfers recorded)
    ranks = competition_ranks(values) if not grouped else []
    rows = []
    for i, r in enumerate(rows_in):
        name = r["player_name"]
        info = infos.get(name, {})
        home_team = _clean(info.get("current_team")) or _clean(info.get("last_team"))
        team = _clean(r.get("team")) or _clean(info.get("season_team")) or home_team
        position = _clean(r.get("position")) or _clean(info.get("season_position")) or _clean(info.get("position"))
        rows.append({
            "rank": ranks[i] if ranks else None,
            "player": player_ref(
                name, team, position, photo(name, home_team or team, _int(info.get("fpl_code"))),
                *((info.get("status"), info.get("chance"), info.get("news")) if show_availability else ()),
            ),
            "value": values[i],
            "values": {c["key"]: _num(r.get(c["key"])) for c in spec["columns"]},
            "detail": f"GW {_int(r['gameweek'])}" if method == "get_highest_single_gameweek_score" and r.get("gameweek") else None,
            "group": ss.normalize_position(_clean(r.get("position"))) if grouped else None,
        })

    phrase = season_phrase(season, current)
    follow_ups = []
    if not grouped:
        top = rows[0]["player"]["name"]
        follow_ups.append(_follow(f"{top} stats {phrase}".strip(), "player_stats"))
        if len(rows) >= 2:
            follow_ups.append(_follow(f"Compare {top} and {rows[1]['player']['name']} {phrase}".strip(), "player_comparison"))
    position = params.get("position")
    for pos in ("MID", "FWD", "DEF", "GK"):
        if pos != position:
            follow_ups.append(_follow(f"Top 5 {POSITION_WORDS[pos][1]} by points {phrase or 'this season'}", "top_points"))
            break
    return {
        "type": "leaderboard",
        "id": f"leaderboard:{method}:{params.get('sort_by') or ''}:{position or ''}:{season or 'all'}",
        "title": spec["title"],
        "subtitle": leaderboard_subtitle(method, params, current),
        "icon": spec["icon"],
        "metric": metric,
        "columns": spec["columns"],
        "grouped": grouped,
        "season": season,
        "rows": rows,
        "highlights": leaderboard_highlights(rows, metric, grouped, bool(position), short,
                                             one_club=method in ("get_team_top_performers", "get_team_squad")),
        "_follow_ups": follow_ups,
    }


async def build_leaderboard_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    if not ctx.results or ctx.method not in LEADERBOARD_METHODS:
        return None
    names = list(dict.fromkeys(r["player_name"] for r in ctx.results[:40] if r.get("player_name")))
    infos = {r["name"]: r for r in await ctx.query(PLAYERS_QUERY, {"names": names, "season": ctx.params.get("season")})}
    return leaderboard_card(ctx.method, ctx.params, ctx.results, infos, photo=ctx.photo,
                            current=ctx.current_season, short=ctx.short)


# ------------------------------------------------------------------ prediction card

def prediction_card(df: Any, *, position: Optional[str], fixtures: List[Dict[str, Any]],
                    photo: Callable[[str, Optional[str], Optional[int]], Optional[str]],
                    codes: Dict[int, int], meta: Dict[str, Any], captain: bool,
                    limit: int = PREDICTION_ROWS, short: Callable[[str], str] = short_name) -> Optional[Dict[str, Any]]:
    if df is None or not len(df):
        return None
    frame = df
    if position:
        frame = df[df["position"].map(lambda p: ss.normalize_position(_clean(p))) == position]
    frame = frame.sort_values("predicted_points", ascending=False).head(limit)
    if not len(frame):
        return None
    gameweek = _int(frame["gameweek"].iloc[0])
    points = [round(_float(p) or 0.0, 1) for p in frame["predicted_points"]]
    ranks = competition_ranks(points)
    rows = []
    for i, (_, r) in enumerate(frame.iterrows()):
        name, team = str(r["name"]), _clean(r.get("team"))
        code = codes.get(_int(r.get("element"))) if _int(r.get("element")) is not None else None
        value = _float(r.get("value"))
        rows.append({
            "rank": ranks[i],
            "player": player_ref(name, team, r.get("position"), photo(name, team, code), r.get("status"),
                                 r.get("chance_of_playing_next_round"), r.get("news")),
            "points": points[i],
            "opponents": _prediction_opponents(r, fixtures, team, gameweek),
            "price": round(value / 10.0, 1) if value is not None else None,
        })
    deadline = meta.get("next_deadline") if _int(meta.get("next_gameweek")) == gameweek else None
    follow_ups = [_follow(f"{rows[0]['player']['name']} stats this season", "player_stats")]
    if len(rows) >= 2:
        follow_ups.append(_follow(f"Compare {rows[0]['player']['name']} and {rows[1]['player']['name']} this season",
                                  "player_comparison"))
    for pos in ("MID", "FWD", "DEF", "GK"):
        if pos != position:
            follow_ups.append(_follow(f"Predicted points for {POSITION_WORDS[pos][1]} next gameweek", "prediction"))
            break
    if not captain:
        follow_ups.append(_follow("Who should I captain this gameweek?", "prediction"))
    return {
        "type": "prediction",
        "id": f"prediction:{gameweek}:{position or 'all'}",
        "title": "Captain picks" if captain else (f"Predicted {POSITION_WORDS[position][1]}" if position in POSITION_WORDS else "Predicted points"),
        "gameweek": gameweek,
        "deadline": deadline,
        "position": position,
        "captain": captain,
        "model": model_label(df),
        "rows": rows,
        "highlights": prediction_highlights(rows, short),
        "_follow_ups": follow_ups,
    }


async def build_fixture_picks_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    """The top predicted players of each next-gameweek match (services/fixture_picks.py)."""
    from services import fixture_picks as fp
    if ctx.predictions is None or not len(ctx.predictions):
        return None
    per = fp.requested_count(ctx.question)
    fixtures = fp.top_by_fixture(ctx.predictions.to_dict("records"), per)
    gw = next((f["gameweek"] for f in fixtures if f.get("gameweek")), None)
    deadline = ctx.meta.get("next_deadline") if _int(ctx.meta.get("next_gameweek")) == gw else None
    return fp.card(fixtures, per, player_ref=player_ref, photo=ctx.photo, codes=ctx.photo_codes, deadline=deadline)


async def build_prediction_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    if ctx.predictions is None:
        return None
    positions = ctx.entities.get("positions") or []
    position = ss.normalize_position(positions[0]) if positions else None
    return prediction_card(ctx.predictions, position=position if position in POSITION_WORDS else None,
                           fixtures=await ctx.fixtures(), photo=ctx.photo, codes=ctx.photo_codes,
                           meta=ctx.meta, captain="captain" in ctx.question.lower(), short=ctx.short)


# ------------------------------------------------------------------ team card

def team_card(team: str, *, season: str, fixtures: List[Dict[str, Any]], top_rows: List[Dict[str, Any]],
              flagged: List[Dict[str, Any]], photo: Callable[[str, Optional[str], Optional[int]], Optional[str]],
              short: Callable[[str], str] = short_name) -> Optional[Dict[str, Any]]:
    if not any(team in (f["home_team"], f["away_team"]) for f in fixtures):
        return None  # not in the current season
    table = ss.compute_table(fixtures)
    standing = next((r for r in table if r["team"] == team), None)
    results = team_results(fixtures, team)
    upcoming = upcoming_fixtures(fixtures, team)
    top_players = [{
        "player": player_ref(r["name"], team, r.get("position"),
                             photo(r["name"], _clean(r.get("current_team")) or _clean(r.get("last_team")) or team,
                                   _int(r.get("fpl_code")))),
        "points": _int(r.get("total_points")) or 0, "goals": _int(r.get("goals")) or 0,
        "assists": _int(r.get("assists")) or 0, "games": _int(r.get("games")) or 0,
    } for r in top_rows if r.get("name")]
    flagged_refs = [player_ref(p["name"], team, p.get("position"), p.get("photo"), p.get("status"),
                               p.get("chance_of_playing_next_round"), p.get("news")) for p in flagged]
    follow_ups = []
    if askable_team(team):
        follow_ups += [_follow(f"Show {team} match results this season", "fixture_results"),
                       _follow(f"Best players at {team} this season", "team_analysis")]
        if upcoming and askable_team(upcoming[0]["team"]):
            follow_ups.append(_follow(f"Head to head {team} vs {upcoming[0]['team']}", "head_to_head"))
    if top_players:
        follow_ups.append(_follow(f"{top_players[0]['player']['name']} stats this season", "player_stats"))
    keys = ("position", "played", "won", "drawn", "lost", "goals_for", "goals_against", "goal_difference", "points")
    return {
        "type": "team",
        "id": f"team:{team}",
        "team": team,
        "season": season,
        "standing": {k: standing[k] for k in keys} if standing else None,
        "teams_in_table": len(table),
        "form": [{k: r[k] for k in ("opponent", "home", "score", "result", "gameweek")} for r in results[-TEAM_FORM:]],
        "next_fixtures": upcoming,
        "top_players": top_players,
        "flagged": flagged_refs[:4],
        "flagged_total": len(flagged_refs),
        "highlights": team_highlights(team, results, standing, table, upcoming, top_players, short),
        "_follow_ups": follow_ups,
    }


async def build_team_card(ctx: CardContext, team: str) -> Optional[Dict[str, Any]]:
    fixtures = await ctx.fixtures()
    if not any(team in (f["home_team"], f["away_team"]) for f in fixtures):
        return None

    async def flagged() -> List[Dict[str, Any]]:
        try:
            payload = await asyncio.to_thread(ss.get_injuries, ctx.conn, team, ctx.photo_for, ctx.current_season)
            return payload.get("players") or []
        except Exception as exc:
            logger.warning("Injuries unavailable for the %s card: %s", team, exc)
            return []

    top_rows, flagged_rows = await asyncio.gather(
        ctx.query(TEAM_PLAYERS_QUERY, {"team": team, "season": ctx.current_season, "limit": TEAM_PLAYERS}),
        flagged(),
    )
    return team_card(team, season=ctx.current_season, fixtures=fixtures, top_rows=top_rows,
                     flagged=flagged_rows, photo=ctx.photo, short=ctx.short)


# ------------------------------------------------------------------ fixtures card

def fixture_rows(raw: Iterable[Dict[str, Any]], team: str) -> List[Dict[str, Any]]:
    """get_fixture_results / get_head_to_head rows with scores, oldest first, `result` from
    `team`'s side."""
    out = []
    for r in raw:
        home_score, away_score = _int(r.get("home_score")), _int(r.get("away_score"))
        home_team, away_team = _clean(r.get("home_team")), _clean(r.get("away_team"))
        if home_score is None or away_score is None or not home_team or not away_team:
            continue
        gf, ga = (home_score, away_score) if home_team == team else (away_score, home_score)
        out.append({
            "season": _clean(r.get("season")), "gameweek": _int(r.get("gameweek")),
            "home_team": home_team, "away_team": away_team, "home_score": home_score, "away_score": away_score,
            "kickoff_time": ss.to_iso(r.get("kickoff_time")),
            "result": None if team not in (home_team, away_team) else ("W" if gf > ga else ("D" if gf == ga else "L")),
        })
    out.sort(key=lambda m: (m["season"] or "", m["gameweek"] or 0, m["kickoff_time"] or ""))
    return out


def fixtures_card(teams: Sequence[str], raw: List[Dict[str, Any]], season: Optional[str],
                  current: str = CURRENT_SEASON) -> Optional[Dict[str, Any]]:
    team = teams[0]
    matches = fixture_rows(raw, team)
    if not matches:
        return None
    h2h = len(teams) > 1
    seasons = sorted({m["season"] for m in matches if m["season"]})
    if h2h:
        title = f"{teams[0]} v {teams[1]}"
        subtitle = f"Head to head · {seasons[0]} to {seasons[-1]}" if len(seasons) > 1 else f"Head to head · {seasons[0]}"
        follow_ups = [_follow(f"How is {t} doing this season?", "team_analysis") for t in teams if askable_team(t)]
    else:
        title = f"{team} results"
        subtitle = season_label(season, current)
        follow_ups = [_follow(f"How is {team} doing this season?", "team_analysis"),
                      _follow(f"Best players at {team} this season", "team_analysis")] if askable_team(team) else []
    return {
        "type": "fixtures",
        "id": f"fixtures:{':'.join(teams)}:{season or 'all'}",
        "title": title,
        "subtitle": subtitle,
        "teams": list(teams),
        "record": results_record(team, matches),
        "matches": list(reversed(matches))[:MATCH_ROWS],
        "total": len(matches),
        "highlights": results_highlights(team, matches, teams[1] if h2h else None),
        "_follow_ups": follow_ups,
    }


async def build_fixtures_card(ctx: CardContext, teams: Sequence[str], season: Optional[str]) -> Optional[Dict[str, Any]]:
    return fixtures_card(teams, ctx.results, season, ctx.current_season)


# ------------------------------------------------------------------ season card

SEASON_STATS = (
    ("total_gameweeks", "Gameweeks", "int"),
    ("total_fixtures", "Matches", "int"),
    ("total_goals", "Goals", "int"),
    ("goals_per_game", "Goals/match", "dec2"),
    ("total_players", "Players", "int"),
)


def season_card(row: Dict[str, Any], leaders: List[Dict[str, Any]], scorers: List[Dict[str, Any]],
                current: str = CURRENT_SEASON, short: Callable[[str], str] = short_name) -> Optional[Dict[str, Any]]:
    season = _clean(row.get("season"))
    if not season:
        return None
    highlights = []
    if scorers:
        s = scorers[0]
        highlights.append(highlight("Top scorer", short(s["name"]), f"{_plural(s['goals'], 'goal')} for {s.get('team') or 'their club'}",
                                    "accent", "target"))
    if leaders:
        top = leaders[0]
        highlights.append(highlight("Most points", short(top["name"]), f"{top['total_points']} pts", "accent", "trophy"))
    return {
        "type": "season",
        "id": f"season:{season}",
        "season": season,
        "season_label": season_label(season, current),
        "in_progress": season == current,
        "stats": [{"key": k, "label": lab, "value": _num(row.get(k)), "format": f} for k, lab, f in SEASON_STATS],
        "leaders": [{
            "player": player_ref(p["name"], p.get("team"), p.get("position"), p.get("photo")),
            "points": p.get("total_points"), "goals": p.get("goals"), "assists": p.get("assists"),
        } for p in leaders],
        "highlights": highlights,
        "_follow_ups": [_follow(f"Top goal scorers {season_phrase(season, current)}", "top_scorers"),
                        _follow(f"Top 5 forwards by points {season_phrase(season, current)}", "top_points")],
    }


async def build_season_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    row = ctx.results[0] if ctx.results else None
    if not row or not row.get("season"):
        return None
    season = row["season"]

    async def leaders(sort: str, limit: int) -> List[Dict[str, Any]]:
        try:
            payload = await asyncio.to_thread(ss.get_player_leaders, ctx.conn, season, None, sort, limit, ctx.photo_for)
            return payload.get("players") or []
        except Exception as exc:
            logger.warning("Season leaders unavailable for %s: %s", season, exc)
            return []

    top, scorers = await asyncio.gather(leaders("total_points", 3), leaders("goals", 1))
    return season_card(row, top, scorers, ctx.current_season, ctx.short)


# ------------------------------------------------------------------ pitch card (best XI)

def pitch_highlights(team: Dict[str, Any], short: Callable[[str], str] = short_name) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    starters = team["starters"]
    predicted = team["mode"] == "predicted"
    if predicted:
        captain = next((p for p in starters if p.get("captain")), None)
        if captain:
            out.append(highlight("Captain", short(captain["name"]),
                                 f"{captain['points']:.1f} pts, doubled to {2 * captain['points']:.1f}", "accent", "star"))
    else:
        star = max(starters, key=lambda p: p["points"])
        out.append(highlight("Star of the week", short(star["name"]), f"{star['points']:g} pts", "accent", "trophy"))
    doubts = [p for p in starters if predicted and p.get("status") in ("d", "i", "s", "u", "n")]
    if doubts:
        p = doubts[0]
        out.append(highlight("Fitness doubt", short(p["name"]), p.get("news") or "Flagged by FPL", "warning", "alert"))
    clubs = Counter(p["team"] for p in starters if p.get("team")).most_common(1)
    if clubs and clubs[0][1] >= 2:
        out.append(highlight("Most picked club", clubs[0][0], _plural(clubs[0][1], "starter"), "info", "shield"))
    if predicted and team.get("budget") is not None and team.get("total_cost") is not None:
        left = round(team["budget"] - team["total_cost"], 1)
        out.append(highlight("In the bank", f"£{left:.1f}m", f"Squad cost £{team['total_cost']:.1f}m of £{team['budget']:.1f}m",
                             "neutral", "zap"))
        priced = [p for p in starters if p.get("cost")]
        if priced:
            value = max(priced, key=lambda p: p["points"] / p["cost"])
            out.append(highlight("Value pick", short(value["name"]), f"{value['points']:.1f} pts for £{value['cost']:.1f}m",
                                 "info", "target"))
    return out[:MAX_HIGHLIGHTS]


def pitch_card(team: Dict[str, Any], finished_gameweeks: Sequence[int] = (),
               short: Callable[[str], str] = short_name) -> Optional[Dict[str, Any]]:
    """A Team of the Week payload (season_service.get_team_of_the_week, with photos) as a card."""
    predicted = team["mode"] == "predicted"

    def entry(p: Dict[str, Any]) -> Dict[str, Any]:
        availability = (p.get("status"), p.get("chance_of_playing_next_round"), p.get("news")) if predicted else ()
        return {
            "player": player_ref(p["name"], p.get("team"), p.get("position"), p.get("photo"), *availability),
            "points": _num(p.get("points"), 1),
            "cost": _num(p.get("cost"), 1),
            "captain": bool(p.get("captain")) and predicted,
            "vice_captain": bool(p.get("vice_captain")) and predicted,
            "opponents": [{"team": o["team"], "home": bool(o["home"])} for o in p.get("opponents") or []],
        }

    if not team.get("starters"):
        return None
    follow_ups = []
    if predicted:
        follow_ups.append(_follow("Who should I captain this gameweek?", "prediction"))
        if finished_gameweeks:
            follow_ups.append(_follow(f"Dream team for gameweek {max(finished_gameweeks)}", "team_of_the_week"))
    else:
        follow_ups.append(_follow("Best team for next gameweek", "team_of_the_week"))
        earlier = [gw for gw in finished_gameweeks if gw < team["gameweek"]]
        if earlier:
            follow_ups.append(_follow(f"Dream team for gameweek {earlier[-1]}", "team_of_the_week"))
    top = max(team["starters"], key=lambda p: p["points"])
    follow_ups.append(_follow(f"{top['name']} stats this season", "player_stats"))
    return {
        "type": "pitch",
        "id": f"pitch:{team['mode']}:{team['gameweek']}",
        "mode": team["mode"],
        "gameweek": team["gameweek"],
        "formation": team["formation"],
        "total_points": _num(team["total_points"], 1),
        "total_cost": _num(team.get("total_cost"), 1),
        "budget": _num(team.get("budget"), 1),
        "starters": [entry(p) for p in team["starters"]],
        "bench": [entry(p) for p in team.get("bench") or []],
        "highlights": pitch_highlights(team, short),
        "_follow_ups": follow_ups,
    }


async def build_pitch_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    mode = ctx.params.get("team_mode")
    if mode not in ("predicted", "actual"):
        return None
    gameweek = ctx.params.get("gameweek") if mode == "actual" else None
    team, fixtures = await asyncio.gather(
        asyncio.to_thread(ss.get_team_of_the_week, ctx.conn, mode, gameweek, ctx.photo_for, ctx.current_season),
        ctx.fixtures(),
    )
    finished = ss.summarize_season(fixtures, {}, ctx.current_season)["finished_gameweeks"]
    return pitch_card(team, finished, ctx.short)


async def build_my_team_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    """The visitor's suggested XI (services/my_team.get_team, in params["my_team"]) on the pitch."""
    from services import my_team_assistant as mta
    team = ctx.params.get("my_team")
    if not team:
        return None
    payload = mta.pitch_team(team, ctx.photo)
    if payload is None:
        return None
    card = pitch_card(payload, (), ctx.short)
    if card is None:
        return None
    entry = team.get("entry") or {}
    card["id"] = f"pitch:my_team:{entry.get('id')}:{payload['gameweek']}"
    card["variant"] = "my_team"
    card["team_name"] = entry.get("name") or None
    card["_follow_ups"] = [
        _follow("What transfers should I make?", "my_team"),
        _follow("Who should I captain from my team?", "my_team"),
        _follow("When should I use my bench boost?", "chip_advice"),
    ]
    return card


async def build_projection_card(ctx: CardContext) -> Optional[Dict[str, Any]]:
    """The best players by projected points over the next gameweeks (services/fixture_runs.py)."""
    from services import fixture_runs as fr
    rows, weeks = ctx.params.get("projection_rows") or [], ctx.params.get("projection_weeks") or []
    card = fr.projection_card(rows, weeks, ctx.params.get("position"), player_ref, ctx.photo, ctx.photo_codes,
                              ctx.current_season, POSITION_WORDS)
    if card and rows:
        card["_follow_ups"] = [_follow(f"{rows[0]['name']} stats this season", "player_stats"),
                               _follow("Which teams have the easiest fixtures?", "fixture_run")]
    return card


# ------------------------------------------------------------------ follow-up questions

_classifier = None


def _intent_of(question: str) -> Optional[str]:
    """The intent the assistant will give this question (so a chip never misroutes)."""
    global _classifier
    if _classifier is None:
        from preprocessing.intent_classifier import IntentClassifier
        _classifier = IntentClassifier()
    try:
        return _classifier.classify(question).intent.value
    except Exception:
        return None


def _routes_to(question: str, intent: str) -> bool:
    """Whether the assistant would answer `question` with `intent`. My-team, chip and fixture-run
    questions are routed by the API's detectors (not the classifier), so those are checked there."""
    if intent in ("my_team", "chip_advice"):
        from services import my_team_assistant as mta
        kind = mta.question_kind(question)
        return kind is not None and (kind == "chips") == (intent == "chip_advice")
    if intent == "fixture_run":
        from services import fixture_runs as fr
        return fr.classify(question) is not None
    return _intent_of(question) == intent


def askable_team(team: str) -> bool:
    """The entity extractor finds exactly this team in its name, so a follow-up resolves to it.

    Uses the extractor's whole-word matching (`find_teams`) where it has it; the older substring
    scan can't tell "Coventry City" from Man City, so such teams get no follow-ups there."""
    from preprocessing.entity_extractor import EntityExtractor
    find_teams = getattr(EntityExtractor, "find_teams", None)
    if find_teams is not None:
        return find_teams(team) == [team]
    lowered = team.lower()
    return {name for key, name in EntityExtractor.TEAMS.items() if key in lowered} == {team}


STARTER_QUESTIONS = (
    "Who should I captain this gameweek?",
    "Top 5 midfielders by points this season",
    "Compare Erling Haaland and Bukayo Saka this season",
)


def follow_up_questions(question: str, candidates: Iterable[Tuple[str, str]], limit: int = MAX_FOLLOW_UPS) -> List[str]:
    """Distinct candidates that route to their intended intent, minus the question just asked."""
    asked = normalize_name(question)
    out: List[str] = []
    seen = {asked}
    for text, intent in candidates:
        key = normalize_name(text)
        if key in seen or not _routes_to(text, intent):
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ entry point

def _named_in(question: str, player: str) -> bool:
    """The player's surname (or full name) is literally in the question, so a general question's
    fuzzy entity match doesn't put an unrelated player card on screen."""
    text, name = normalize_name(question), normalize_name(player)
    tokens = name.split()
    if not tokens:
        return False
    return (len(tokens[-1]) >= 4 and tokens[-1] in text.split()) or f" {name} " in f" {text} "


def plan(ctx: CardContext) -> List[Awaitable[Any]]:
    """The card builders for this answer, in display order."""
    intent, params = ctx.intent, ctx.params
    players = [p for p in (ctx.entities.get("players") or []) if p]
    season = params.get("season")

    if intent == "team_of_the_week":
        return [build_pitch_card(ctx)]
    if intent in ("my_team", "chip_advice") and params.get("my_team"):
        # The visitor's own squad; a transfer plan adds one comparison per planned move
        moves = [m for m in (params.get("transfer_moves") or []) if len(m) == 2 and all(m)]
        return [build_my_team_card(ctx)] + [
            build_comparison_card(ctx, list(m), ctx.current_season, emphasis="prediction")
            for m in moves[:MAX_CARDS - 1]]
    if intent == "chip_advice":
        return [build_prediction_card(ctx)]
    if intent == "fixture_run":
        if params.get("run_kind") == "players":
            return [build_projection_card(ctx)]
        if params.get("team_name"):
            return [build_team_card(ctx, params["team_name"])]
        return []
    if intent == "transfer_advice":
        # One comparison per move, sold player first, this season with the predictions on top
        moves = [m for m in (params.get("transfer_moves") or []) if len(m) == 2 and all(m)]
        if moves:
            return [build_comparison_card(ctx, list(m), ctx.current_season, emphasis="prediction")
                    for m in moves[:MAX_TRANSFER_CARDS]]
        if len(players) >= 2:
            return [build_comparison_card(ctx, players[:2], ctx.current_season, emphasis="prediction")]
        if players:
            return [build_player_card(ctx, players[0], ctx.current_season, emphasis="prediction")]
        return []
    if intent == "prediction":
        if len(players) >= 2:
            return [build_comparison_card(ctx, players[:2], season or ctx.current_season, emphasis="prediction")]
        if players:
            return [build_player_card(ctx, players[0], season, emphasis="prediction")]
        from services.fixture_picks import is_per_fixture_question
        if is_per_fixture_question(ctx.question):
            return [build_fixture_picks_card(ctx)]
        return [build_prediction_card(ctx)]
    if intent == "player_comparison" and len(players) >= 2:
        # "Who's the better pick this week, Salah or Saka?" (params["forecast"]): predictions first
        return [build_comparison_card(ctx, players[:2], season,
                                      emphasis="prediction" if params.get("forecast") else "stats")]
    if intent in ("player_stats", "player_comparison", "player_search") and players:
        return [build_player_card(ctx, players[0], season, params.get("gameweek"))]
    if ctx.method == "get_team_squad" and params.get("team_name"):
        return [build_leaderboard_card(ctx), build_team_card(ctx, params["team_name"])]
    if intent == "team_analysis" and params.get("team_name") and ctx.method == "get_team_top_performers":
        return [build_team_card(ctx, params["team_name"]), build_leaderboard_card(ctx)]
    if intent == "fixture_results" and params.get("team_name") and ctx.method == "get_fixture_results":
        return [build_fixtures_card(ctx, [params["team_name"]], season), build_team_card(ctx, params["team_name"])]
    if intent == "head_to_head" and params.get("team1") and params.get("team2") and ctx.method == "get_head_to_head":
        return [build_fixtures_card(ctx, [params["team1"], params["team2"]], None)]
    if ctx.method == "get_season_summary":
        return [build_season_card(ctx)]
    if ctx.method in LEADERBOARD_METHODS:
        return [build_leaderboard_card(ctx)]
    if intent in ("general_question", "unknown"):
        named = [p for p in players if _named_in(ctx.question, p)]
        if len(named) >= 2:
            return [build_comparison_card(ctx, named[:2], season)]
        if named:
            return [build_player_card(ctx, named[0], season)]
    return []


async def build_cards(ctx: CardContext) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Cards and follow-up questions for one answer. Builders run concurrently; one that
    fails is logged and left out."""
    outcomes = await asyncio.gather(*plan(ctx), return_exceptions=True)
    cards: List[Dict[str, Any]] = []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            logger.warning("Answer card failed: %r", outcome, exc_info=outcome)
            continue
        if outcome:
            cards.append(outcome)
    cards = cards[:MAX_CARDS]
    candidates = [c for card in cards for c in card.pop("_follow_ups", [])]
    return cards, follow_up_questions(ctx.question, candidates)
