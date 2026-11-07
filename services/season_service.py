"""
Current-season views for the read-only season endpoints: season status, fixtures,
league table, injuries and team of the week, plus the per-season player leaders
table (any season with SEASON_STATS).

Layout:
  * pure functions over plain lists/dicts (summarize_season, fixtures_for_gameweek,
    compute_table, sort_injuries, team_opponents, build_team_of_the_week,
    leader_rows) - tested without Neo4j;
  * loaders that run one Cypher query each and read data/live files, cached in-process
    by input (cleared by clear_cache(), called from api_main.reset_graph_caches, and
    expiring after CACHE_TTL_SECONDS as a safety net);
  * get_* functions that assemble the response dicts.

Nothing here makes network calls. Player photos are added per request through the
`photo_for(name, team, fpl_code)` callable the API passes in.
"""
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from config.settings import CURRENT_SEASON, SEASONS
from services import team_optimizer as opt

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
CSV_FIXTURE_PREFIX = "fixture-"          # offline schedule nodes from the fixtures CSV
STATUS_ORDER = {"i": 0, "s": 1, "d": 2, "u": 3, "n": 4}
UNAVAILABLE = {"i", "s", "u", "n"}      # excluded from the predicted team of the week
FORM_LENGTH = 5
BUDGET = opt.DEFAULT_BUDGET              # tenths

PhotoFn = Callable[[str, Optional[str], Optional[int]], Optional[str]]


class NotFoundError(LookupError):
    """Unknown gameweek, or nothing to show for it (mapped to HTTP 404)."""


# ------------------------------------------------------------------ cache

_cache: Dict[Tuple, Tuple[float, Any]] = {}
_cache_lock = Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: Tuple, loader: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


# ------------------------------------------------------------------ small helpers

def to_datetime(value: Any) -> Optional[datetime]:
    """Neo4j DateTime / datetime / ISO string -> aware datetime (UTC if naive)."""
    if value is None:
        return None
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def to_iso(value: Any) -> Optional[str]:
    dt = to_datetime(value)
    return dt.isoformat() if dt else None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else int(f)


def _str_or_none(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def chance_of_playing(status: Any, chance_next: Any) -> float:
    """Chance a player plays from FPL's status and chance_of_playing_next_round: 0 for u
    (left / unavailable) and n (not in squad); else the chance / 100 when set; injured or
    suspended players without a chance value are out; anyone else 1. The captain pick's chance
    of playing. The predictions file's points use pipeline.predict_next_gw.availability_factor,
    which keeps 0 and 1 but gives a doubt a fitted factor instead of this chance."""
    status = (_str_or_none(status) or "a").lower()
    if status in ("u", "n"):
        return 0.0
    try:
        chance = float(chance_next)
    except (TypeError, ValueError):
        chance = math.nan
    if not math.isnan(chance):
        return min(max(chance / 100.0, 0.0), 1.0)
    return 0.0 if status in ("i", "s") else 1.0


def normalize_position(position: Any) -> Optional[str]:
    if position is None:
        return None
    p = str(position).upper()
    return {"GKP": "GK", "GOALKEEPER": "GK"}.get(p, p)


def _kickoff_key(fixture: Dict[str, Any]) -> Tuple:
    ko = fixture.get("kickoff_time")
    return (ko is None, ko or datetime.min.replace(tzinfo=timezone.utc))


# ------------------------------------------------------------------ live files

def live_dir() -> str:
    return os.getenv("LIVE_DATA_DIR", os.path.join("data", "live"))


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def meta_path() -> str:
    return os.path.join(live_dir(), "meta.json")


def read_meta(season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """meta.json of the live pipeline, or {} when missing or for another season."""
    path = meta_path()

    def load() -> Dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return {}
        return meta if meta.get("season") in (None, season) else {}

    return _cached(("meta", path, _mtime(path), season), load)


def status_path(season: str = CURRENT_SEASON) -> str:
    name = (read_meta(season).get("files") or {}).get("status")         or f"player_status_{season.replace('-', '_')}.csv"
    return os.path.join(live_dir(), name)


def status_lookup(season: str = CURRENT_SEASON) -> Dict[str, Dict[Any, Any]]:
    """From the FPL status file: {'web_name': {name: web_name}, 'code': {element: code}}."""
    path = status_path(season)

    def load() -> Dict[str, Dict[Any, Any]]:
        out: Dict[str, Dict[Any, Any]] = {"web_name": {}, "code": {}}
        if not os.path.exists(path):
            return out
        import pandas as pd
        df = pd.read_csv(path, usecols=lambda c: c in ("element", "code", "name", "web_name"))
        for r in df.to_dict("records"):
            if _str_or_none(r.get("web_name")):
                out["web_name"][r["name"]] = str(r["web_name"])
            element, code = _int_or_none(r.get("element")), _int_or_none(r.get("code"))
            if element is not None and code is not None:
                out["code"][element] = code
        return out

    return _cached(("status", path, _mtime(path)), load)


# ------------------------------------------------------------------ pure: fixtures/season

def clean_fixture_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise raw fixture rows; CSV-schedule nodes are dropped when API fixtures exist.

    Output keys: id, gameweek, kickoff_time (aware datetime|None), status, home_team,
    away_team, home_score, away_score, home_difficulty, away_difficulty. Rows without
    both teams are skipped. Sorted by kickoff time (unscheduled last), then home team.
    """
    rows = [r for r in rows if r.get("home_team") and r.get("away_team")]
    if any(not str(r.get("id", "")).startswith(CSV_FIXTURE_PREFIX) for r in rows):
        rows = [r for r in rows if not str(r.get("id", "")).startswith(CSV_FIXTURE_PREFIX)]
    out = []
    for r in rows:
        status = r.get("status") or "scheduled"
        if status not in ("scheduled", "in_progress", "finished"):
            status = "scheduled"
        home_score, away_score = _int_or_none(r.get("home_score")), _int_or_none(r.get("away_score"))
        if status == "scheduled":
            home_score = away_score = None
        out.append({
            "id": str(r.get("id")),
            "gameweek": _int_or_none(r.get("gameweek")),
            "kickoff_time": to_datetime(r.get("kickoff_time")),
            "status": status,
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "home_score": home_score,
            "away_score": away_score,
            "home_difficulty": _int_or_none(r.get("home_difficulty")),
            "away_difficulty": _int_or_none(r.get("away_difficulty")),
        })
    out.sort(key=lambda f: (_kickoff_key(f), f["home_team"]))
    return out


def gameweeks_of(fixtures: Iterable[Dict[str, Any]]) -> List[int]:
    return sorted({f["gameweek"] for f in fixtures if f["gameweek"] is not None})


def summarize_season(fixtures: List[Dict[str, Any]], meta: Dict[str, Any],
                     season: str = CURRENT_SEASON) -> Dict[str, Any]:
    by_gw: Dict[int, List[str]] = {}
    for f in fixtures:
        if f["gameweek"] is not None:
            by_gw.setdefault(f["gameweek"], []).append(f["status"])
    started = [gw for gw, st in by_gw.items() if any(s in ("in_progress", "finished") for s in st)]
    finished = sorted(gw for gw, st in by_gw.items() if st and all(s == "finished" for s in st))

    next_gw = _int_or_none(meta.get("next_gameweek"))
    next_deadline = meta.get("next_deadline") if next_gw is not None else None
    if next_gw is None and "next_gameweek" not in meta:
        scheduled = [gw for gw, st in by_gw.items() if "scheduled" in st]
        next_gw = min(scheduled) if scheduled else None
        next_deadline = None
    return {
        "season": season,
        "current_gameweek": max(started) if started else None,
        "next_gameweek": next_gw,
        "next_deadline": next_deadline,
        "finished_gameweeks": finished,
        "updated_at": meta.get("status_fetched_at"),
    }


def default_gameweek(summary: Dict[str, Any], gameweeks: List[int]) -> int:
    for gw in (summary.get("next_gameweek"), summary.get("current_gameweek")):
        if gw is not None and gw in gameweeks:
            return gw
    if gameweeks:
        return gameweeks[-1] if summary.get("next_gameweek") is not None else gameweeks[0]
    return 1


def fixture_out(f: Dict[str, Any]) -> Dict[str, Any]:
    show_score = f["status"] in ("finished", "in_progress")
    return {
        "id": f["id"],
        "gameweek": f["gameweek"],
        "kickoff_time": to_iso(f["kickoff_time"]),
        "status": f["status"],
        "home_team": f["home_team"],
        "away_team": f["away_team"],
        "home_score": f["home_score"] if show_score else None,
        "away_score": f["away_score"] if show_score else None,
        "home_difficulty": f["home_difficulty"],
        "away_difficulty": f["away_difficulty"],
    }


def fixtures_for_gameweek(fixtures: List[Dict[str, Any]], gameweek: int) -> List[Dict[str, Any]]:
    """API rows of one gameweek, by kickoff time then home team."""
    rows = [f for f in fixtures if f["gameweek"] == gameweek]
    rows.sort(key=lambda f: (_kickoff_key(f), f["home_team"]))
    return [fixture_out(f) for f in rows]


def team_opponents(fixtures: List[Dict[str, Any]], gameweek: int) -> Dict[str, List[Dict[str, Any]]]:
    """team -> [{'team': opponent, 'home': bool}] for its fixtures in the gameweek, in kickoff order."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for f in sorted((f for f in fixtures if f["gameweek"] == gameweek),
                    key=lambda f: (_kickoff_key(f), f["home_team"])):
        out.setdefault(f["home_team"], []).append({"team": f["away_team"], "home": True})
        out.setdefault(f["away_team"], []).append({"team": f["home_team"], "home": False})
    return out


# ------------------------------------------------------------------ pure: table

def compute_table(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """League table from fixtures with both scores (finished matches).

    Every club with a fixture gets a row, played or not. Sort: points, goal
    difference, goals scored (all desc), then team name. `form` is the last five
    results, oldest first; `next_fixture` the earliest scheduled match.
    """
    teams = sorted({f["home_team"] for f in fixtures} | {f["away_team"] for f in fixtures})
    stats = {t: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0,
                 "results": [], "recent": [], "next": None} for t in teams}

    played = [f for f in fixtures if f["home_score"] is not None and f["away_score"] is not None
              and f["status"] != "scheduled"]
    played.sort(key=lambda f: (_kickoff_key(f), f["home_team"]))
    for f in played:
        for team, gf, ga in ((f["home_team"], f["home_score"], f["away_score"]),
                             (f["away_team"], f["away_score"], f["home_score"])):
            s = stats[team]
            s["played"] += 1
            s["goals_for"] += gf
            s["goals_against"] += ga
            result = "W" if gf > ga else ("D" if gf == ga else "L")
            s[{"W": "won", "D": "drawn", "L": "lost"}[result]] += 1
            s["results"].append(result)
            home = team == f["home_team"]
            s["recent"].append({"fixture_id": f["id"], "result": result, "home": home, "score": f"{gf}-{ga}",
                                "opponent": f["away_team"] if home else f["home_team"]})

    upcoming = sorted((f for f in fixtures if f["status"] == "scheduled"),
                      key=lambda f: (_kickoff_key(f), f["home_team"]))
    for f in upcoming:
        for team, opponent, home in ((f["home_team"], f["away_team"], True),
                                     (f["away_team"], f["home_team"], False)):
            if stats[team]["next"] is None:
                stats[team]["next"] = {"opponent": opponent, "home": home,
                                       "kickoff_time": to_iso(f["kickoff_time"])}

    rows = []
    for team, s in stats.items():
        rows.append({
            "team": team,
            "played": s["played"], "won": s["won"], "drawn": s["drawn"], "lost": s["lost"],
            "goals_for": s["goals_for"], "goals_against": s["goals_against"],
            "goal_difference": s["goals_for"] - s["goals_against"],
            "points": 3 * s["won"] + s["drawn"],
            "form": s["results"][-FORM_LENGTH:],
            # the same matches, for links to their match pages
            "recent": s["recent"][-FORM_LENGTH:],
            "next_fixture": s["next"],
        })
    rows.sort(key=lambda r: (-r["points"], -r["goal_difference"], -r["goals_for"], r["team"]))
    return [{"position": i + 1, **r} for i, r in enumerate(rows)]


# ------------------------------------------------------------------ pure: injuries

def _news_added_key(value: Any) -> float:
    dt = to_datetime(value)
    return -dt.timestamp() if dt else math.inf      # newest first, missing last


def sort_injuries(players: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Status i, s, d, u, n (others last); then news_added newest first; then name."""
    return sorted(players, key=lambda p: (STATUS_ORDER.get(p.get("status"), len(STATUS_ORDER)),
                                          _news_added_key(p.get("news_added")), p.get("name") or ""))


def injury_rows(raw: Iterable[Dict[str, Any]], web_names: Dict[str, str]) -> List[Dict[str, Any]]:
    """Raw query rows -> API rows (photo left to the caller via `_code`), sorted."""
    out = []
    for r in raw:
        status = _str_or_none(r.get("status"))
        if status is None or status == "a":
            continue
        out.append({
            "name": r["name"],
            "web_name": web_names.get(r["name"]),
            "team": r.get("team"),
            "position": normalize_position(r.get("position")),
            "status": status,
            "chance_of_playing_next_round": _int_or_none(r.get("chance_of_playing_next_round")),
            "news": _str_or_none(r.get("news")),
            "news_added": to_iso(r.get("news_added")),
            "_code": _int_or_none(r.get("fpl_code")),
        })
    return sort_injuries(out)


# ------------------------------------------------------------------ pure: team of the week

def predicted_candidates(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prediction rows eligible for the predicted team: available, positive points."""
    out = []
    for r in rows:
        status = _str_or_none(r.get("status")) or "a"
        points = r.get("predicted_points")
        if status in UNAVAILABLE or points is None or not float(points) > 0:
            continue
        if _int_or_none(r.get("value")) is None:
            continue
        out.append(r)
    return out


def predicted_selection(rows: Iterable[Dict[str, Any]],
                        opponents: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                        codes: Optional[Dict[int, int]] = None) -> Tuple[opt.Selection, Dict[str, Dict[str, Any]]]:
    """The predicted team of the week from prediction rows: (selection, details by name).
    Shared with pipeline.predict_next_gw, which saves it as the gameweek's pick, so the
    saved squad is the one the page showed. NotFoundError when no squad fits."""
    pool, details = predicted_pool(rows, opponents, codes)
    try:
        return opt.best_squad(pool, budget=BUDGET), details
    except opt.InfeasibleError as exc:
        raise NotFoundError(f"Not enough available players for a squad: {exc}") from exc


def predicted_pool(rows: Iterable[Dict[str, Any]],
                   opponents: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                   codes: Optional[Dict[int, int]] = None) -> Tuple[List[opt.Candidate], Dict[str, Dict[str, Any]]]:
    """The optimiser's candidates from prediction rows, with the per-player details
    (predicted_selection's input; services/team_builder solves it with its own constraints)."""
    opponents, codes = opponents or {}, codes or {}
    pool, details = [], {}
    for r in predicted_candidates(rows):
        name, position = str(r["name"]), normalize_position(r.get("position"))
        if name in details or position not in opt.POSITIONS:
            continue
        value = _int_or_none(r["value"])
        team = str(r["team"])
        faces = opponents.get(team) if team in opponents else opponents_from_prediction(r)
        pool.append(opt.Candidate(id=name, position=position, team=team,
                                  points=float(r["predicted_points"]), cost=value,
                                  p_play=chance_of_playing(r.get("status"), r.get("chance_of_playing_next_round")),
                                  opponents=tuple(o["team"] for o in faces or [])))
        details[name] = {
            "team": team,
            "cost": round(value / 10.0, 1),
            "opponents": faces,
            "status": _str_or_none(r.get("status")) or "a",
            "chance_of_playing_next_round": _int_or_none(r.get("chance_of_playing_next_round")),
            "news": _str_or_none(r.get("news")),
            "element": _int_or_none(r.get("element")),
            "_code": codes.get(_int_or_none(r.get("element"))),
        }
    return pool, details


def opponents_from_prediction(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback when the graph has no fixtures: 'A & B' with the first fixture's venue."""
    text = _str_or_none(row.get("opponent"))
    if not text or text == "No fixture":
        return []
    home = str(row.get("is_home")).lower() in ("true", "1")
    return [{"team": t.strip(), "home": home} for t in text.split(" & ") if t.strip()]


def build_team_of_the_week(selection: opt.Selection, details: Dict[str, Dict[str, Any]],
                           mode: str, gameweek: int, budget: Optional[float]) -> Dict[str, Any]:
    """Response dict from an optimiser selection. details[id] holds the per-player
    fields (team, cost, opponents, status, chance_of_playing_next_round, news, _code)."""
    def player(c: opt.Candidate) -> Dict[str, Any]:
        d = details[c.id]
        return {
            "name": c.id,
            "team": d["team"],
            "position": c.position,
            "points": round(float(c.points), 2),
            "cost": d.get("cost"),
            "captain": c.id == selection.captain.id,
            "vice_captain": selection.vice_captain is not None and c.id == selection.vice_captain.id,
            "opponents": d.get("opponents") or [],
            "status": d.get("status"),
            "chance_of_playing_next_round": d.get("chance_of_playing_next_round"),
            "news": d.get("news"),
            "_code": d.get("_code"),
        }

    starters = [player(c) for c in selection.starters]
    bench = [player(c) for c in selection.bench]
    total = sum(c.points for c in selection.starters)
    if mode == "predicted":
        total += selection.captain.points
    costs = [p["cost"] for p in starters + bench]
    total_cost = round(sum(costs), 1) if costs and all(c is not None for c in costs) else None
    return {
        "mode": mode,
        "gameweek": gameweek,
        "formation": selection.formation,
        "total_points": round(float(total), 2),
        "total_cost": total_cost if mode == "predicted" else None,
        "budget": budget,
        "starters": starters,
        "bench": bench,
    }


def team_of_week_target(question: str, gameweek: Optional[int],
                        finished_gameweeks: Iterable[int]) -> Tuple[str, Optional[int]]:
    """(mode, gameweek) a "best XI" question asks for: a finished gameweek's dream team
    ("actual") when it names one, says "dream team" or "last gameweek", or is in the past
    tense; otherwise the predicted squad for the next gameweek (gameweek None)."""
    text = question.lower()
    finished = sorted(finished_gameweeks)
    if gameweek is not None and gameweek in finished:
        return "actual", gameweek
    past = re.search(r"\bdream ?team\b|\blast (?:gameweek|gw|week)\b|\b(?:was|were)\b", text)
    if past and finished:
        return "actual", finished[-1]
    return "predicted", None


def team_of_week_context(team: Dict[str, Any]) -> str:
    """The squad as plain text for the LLM prompt."""
    predicted = team["mode"] == "predicted"

    def line(p: Dict[str, Any]) -> str:
        price = f", £{p['cost']:.1f}m" if p.get("cost") is not None else ""
        versus = " & ".join(f"{o['team']} ({'H' if o['home'] else 'A'})" for o in p.get("opponents") or [])
        role = " (captain)" if p.get("captain") else (" (vice-captain)" if p.get("vice_captain") else "")
        status = p.get("status")
        flag = f" [FPL availability: {status}{': ' + p['news'] if p.get('news') else ''}]" if predicted and status not in (None, "a") else ""
        points = f"{p['points']:.1f} predicted pts" if predicted else f"{p['points']:g} pts"
        return f"- {p['name']} ({p['position']}, {p['team']}{price}){role}: {points}{' vs ' + versus if versus else ''}{flag}"

    head = (f"Predicted best FPL squad for gameweek {team['gameweek']} (picked by the optimiser from the "
            f"points model: £{team['budget']:.1f}m budget, at most 3 players per club, captain's points count twice)"
            if predicted else
            f"Dream team for gameweek {team['gameweek']}: the best XI by actual FPL points (no budget or club limit)")
    lines = [f"{head}. Formation {team['formation']}.", "Starting XI:"]
    lines += [line(p) for p in team["starters"]]
    if team.get("bench"):
        lines.append("Bench:")
        lines += [line(p) for p in team["bench"]]
    total = f"Total {'predicted ' if predicted else ''}points: {team['total_points']:g}"
    if predicted:
        total += " (captain counted twice)"
        if team.get("total_cost") is not None:
            total += f"; squad cost £{team['total_cost']:.1f}m"
    lines.append(total + ".")
    return "\n".join(lines)


def team_of_week_rows(team: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per squad player, shaped like query results (player_name, position, points)."""
    return [{"player_name": p["name"], "position": p["position"], "team": p["team"], "points": p["points"],
             "cost": p.get("cost"), "captain": p.get("captain"), "starter": i < len(team["starters"])}
            for i, p in enumerate(team["starters"] + team.get("bench", []))]


# ------------------------------------------------------------------ pure: player leaders

def leader_rows(raw: Iterable[Dict[str, Any]], include_availability: bool) -> List[Dict[str, Any]]:
    """Raw LEADERS_QUERY rows -> API rows (photo left to the caller via `_code`).

    Keeps the query's order. games is matches played (appearances); points_per_game is
    total_points / games (0 when no games),
    rounded to 2 dp. FPL availability describes the player today, so it is only
    returned for the current season (include_availability) and is null otherwise.
    """
    out = []
    for r in raw:
        games = _int_or_none(r.get("games")) or 0
        total_points = _int_or_none(r.get("total_points")) or 0
        out.append({
            "name": r["name"],
            "team": _str_or_none(r.get("team")),
            "position": normalize_position(r.get("position")),
            "games": games,
            "minutes": _int_or_none(r.get("minutes")) or 0,
            "goals": _int_or_none(r.get("goals")) or 0,
            "assists": _int_or_none(r.get("assists")) or 0,
            "clean_sheets": _int_or_none(r.get("clean_sheets")) or 0,
            "bonus": _int_or_none(r.get("bonus")) or 0,
            "total_points": total_points,
            "points_per_game": round(total_points / games, 2) if games > 0 else 0.0,
            "price": round(float(r["price"]), 1) if r.get("price") is not None else None,
            "status": _str_or_none(r.get("status")) if include_availability else None,
            "chance_of_playing_next_round":
                _int_or_none(r.get("chance_of_playing_next_round")) if include_availability else None,
            "news": _str_or_none(r.get("news")) if include_availability else None,
            "_code": _int_or_none(r.get("fpl_code")),
        })
    return out


def with_photos(payload: Any, photo_for: Optional[PhotoFn]) -> Any:
    """Deep copy with every player's internal `_code` replaced by a `photo` URL."""
    if isinstance(payload, list):
        return [with_photos(x, photo_for) for x in payload]
    if isinstance(payload, dict):
        out = {k: with_photos(v, photo_for) for k, v in payload.items() if k != "_code"}
        if "_code" in payload:
            out["photo"] = photo_for(payload["name"], payload.get("team"), payload["_code"]) if photo_for else None
        return out
    return payload


# ------------------------------------------------------------------ Cypher

FIXTURES_QUERY = """
MATCH (f:Fixture {season_id: $season})
OPTIONAL MATCH (f)-[:PLAYS_HOME]->(h:Team)
OPTIONAL MATCH (f)-[:PLAYS_AWAY]->(a:Team)
RETURN f.id AS id, coalesce(f.gameweek, f.matchday) AS gameweek, f.kickoff_time AS kickoff_time,
       f.status AS status, h.name AS home_team, a.name AS away_team,
       f.home_score AS home_score, f.away_score AS away_score,
       f.home_difficulty AS home_difficulty, f.away_difficulty AS away_difficulty
"""

INJURIES_QUERY = """
MATCH (p:Player)-[:PLAYS_FOR]->(t:Team)
WHERE p.status IS NOT NULL AND p.status <> 'a'
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
RETURN p.name AS name, t.name AS team, pos.code AS position, p.status AS status,
       p.chance_of_playing_next_round AS chance_of_playing_next_round, p.news AS news,
       p.news_added AS news_added, p.fpl_code AS fpl_code
"""

GAMEWEEK_POINTS_QUERY = """
MATCH (f:Fixture {season_id: $season, gameweek: $gameweek})<-[r:PLAYED_IN]-(p:Player)
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
RETURN p.name AS name, pos.code AS position, r.team AS team, r.total_points AS total_points,
       p.status AS status, p.chance_of_playing_next_round AS chance_of_playing_next_round,
       p.news AS news, p.fpl_code AS fpl_code
"""

# Sort key (API value) -> ORDER BY column of LEADERS_QUERY. The only text spliced into
# the query; user input never reaches it unchecked.
LEADER_SORTS = {
    "total_points": "total_points",
    "goals": "goals",
    "assists": "assists",
    "clean_sheets": "clean_sheets",
    "bonus": "bonus",
    "points_per_game": "points_per_game",
    "minutes": "minutes",
}
LEADER_POSITIONS = ("GK", "DEF", "MID", "FWD")
# Rates need a sample: without it a single 15-point cameo tops "points per game".
PPG_MIN_MINUTES = 270

# SEASON_STATS holds per-season sums plus the team and position the player had that
# season, so "midfielders in 2022-23" means midfielders then, not today.
LEADERS_QUERY = """
MATCH (p:Player)-[st:SEASON_STATS]->(:Season {id: $season})
WHERE ($position IS NULL OR st.position = $position) AND coalesce(st.minutes, 0) >= $min_minutes
  AND ($team IS NULL OR toLower(st.team) = $team)
// games = matches actually played (minutes > 0), FPL's basis for points per game. st.games
// counts every gameweek row, including ones on the bench or injured, which is the base of the
// average price (value is in tenths of a million).
WITH p, st, coalesce(st.appearances, st.games, 0) AS games, coalesce(st.total_points, 0) AS total_points,
     CASE WHEN coalesce(st.games, 0) > 0 THEN st.value_sum / 10.0 / st.games END AS price
WHERE $max_price IS NULL OR (price IS NOT NULL AND price <= $max_price + 1e-9)
RETURN p.name AS name, st.team AS team, st.position AS position, games, price,
       coalesce(st.minutes, 0) AS minutes, coalesce(st.goals, 0) AS goals,
       coalesce(st.assists, 0) AS assists, coalesce(st.clean_sheets, 0) AS clean_sheets,
       coalesce(st.bonus, 0) AS bonus, total_points,
       CASE WHEN games > 0 THEN total_points * 1.0 / games ELSE 0.0 END AS points_per_game,
       p.status AS status, p.chance_of_playing_next_round AS chance_of_playing_next_round,
       p.news AS news, p.fpl_code AS fpl_code
ORDER BY {order_by} DESC, total_points DESC, name
LIMIT $limit
"""


def leaders_query(sort: str = "total_points", position: Optional[str] = None,
                  season: str = CURRENT_SEASON, limit: int = 50, team: Optional[str] = None,
                  max_price: Optional[float] = None) -> Tuple[str, Dict[str, Any]]:
    """(cypher, params) for the season leaders table, optionally one club (any case) and a
    highest average price in millions. Raises ValueError on an unknown sort key or position."""
    if sort not in LEADER_SORTS:
        raise ValueError(f"sort must be one of {', '.join(LEADER_SORTS)}")
    if position is not None and position not in LEADER_POSITIONS:
        raise ValueError(f"position must be one of {', '.join(LEADER_POSITIONS)}")
    query = LEADERS_QUERY.replace("{order_by}", LEADER_SORTS[sort])
    min_minutes = PPG_MIN_MINUTES if sort == "points_per_game" else 0
    return query, {"season": season, "position": position, "limit": int(limit), "min_minutes": min_minutes,
                   "team": team.strip().lower() if team and team.strip() else None,
                   "max_price": float(max_price) if max_price is not None else None}

# ------------------------------------------------------------------ loaders (cached)

def load_fixtures(conn, season: str = CURRENT_SEASON) -> List[Dict[str, Any]]:
    return _cached(("fixtures", season),
                   lambda: clean_fixture_rows(conn.execute_query(FIXTURES_QUERY, {"season": season})))


# ------------------------------------------------------------------ endpoint payloads

def get_season_current(conn, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    return summarize_season(load_fixtures(conn, season), read_meta(season), season)


TICKER_MAX_GAMEWEEKS = 10


def fixture_ticker(fixtures: Iterable[Dict[str, Any]], start: int, count: int) -> Dict[str, Any]:
    """Every club's fixtures for `count` gameweeks from `start`, with FPL's difficulty (1-5) from
    the club's side, easiest run first. A blank gameweek has no items, a double two. Clubs are
    ranked by their average difficulty over the fixtures they play, then by more fixtures (a
    double gameweek is worth more than a blank), then by name. Pure."""
    gws = list(range(int(start), int(start) + int(count)))
    clubs: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for f in fixtures:
        gw = f.get("gameweek")
        for side, other, home in (("home_team", "away_team", True), ("away_team", "home_team", False)):
            clubs.setdefault(f[side], {})
            if gw in gws:
                clubs[f[side]].setdefault(gw, []).append({
                    "fixture_id": f["id"], "opponent": f[other], "home": home,
                    "difficulty": f["home_difficulty"] if home else f["away_difficulty"],
                    "kickoff_time": f.get("kickoff_time"),
                })
    rows = []
    for team, by_gw in clubs.items():
        items = [i for gw in gws for i in by_gw.get(gw, [])]
        known = [i["difficulty"] for i in items if i["difficulty"] is not None]
        rows.append({
            "team": team,
            "gameweeks": [{"gameweek": gw, "fixtures": by_gw.get(gw, [])} for gw in gws],
            "fixtures": len(items),
            "total_difficulty": sum(known),
            "average_difficulty": round(sum(known) / len(known), 2) if known else None,
        })
    rows.sort(key=lambda r: (r["average_difficulty"] if r["average_difficulty"] is not None else 99,
                             -r["fixtures"], r["team"]))
    return {"from_gameweek": int(start), "gameweeks": gws, "clubs": rows}


def get_fixture_ticker(conn, gameweeks: int = 6, start: Optional[int] = None,
                       season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """The fixture difficulty ticker from the next gameweek (or `start`) for `gameweeks` weeks,
    cut at the season's last gameweek. Unknown start -> NotFoundError."""
    fixtures = load_fixtures(conn, season)
    all_gws = gameweeks_of(fixtures)
    if not all_gws:
        raise NotFoundError(f"No {season} fixtures yet")
    if start is None:
        summary = summarize_season(fixtures, read_meta(season), season)
        start = summary.get("next_gameweek") or default_gameweek(summary, all_gws)
    if start not in all_gws:
        raise NotFoundError(f"No {season} fixtures for gameweek {start}")
    count = max(1, min(int(gameweeks), TICKER_MAX_GAMEWEEKS, all_gws[-1] - int(start) + 1))
    return {"season": season, **fixture_ticker(fixtures, int(start), count)}


def get_fixtures(conn, gameweek: Optional[int] = None, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    fixtures = load_fixtures(conn, season)
    gameweeks = gameweeks_of(fixtures)
    if gameweek is None:
        gameweek = default_gameweek(summarize_season(fixtures, read_meta(season), season), gameweeks)
    if gameweek not in gameweeks:
        raise NotFoundError(f"No {season} fixtures for gameweek {gameweek}")
    return {"season": season, "gameweek": gameweek, "gameweeks": gameweeks,
            "fixtures": fixtures_for_gameweek(fixtures, gameweek)}


def get_table(conn, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    fixtures = load_fixtures(conn, season)
    return {"season": season, "updated_at": read_meta(season).get("status_fetched_at"),
            "rows": compute_table(fixtures)}


def get_injuries(conn, team: Optional[str] = None, photo_for: Optional[PhotoFn] = None,
                 season: str = CURRENT_SEASON) -> Dict[str, Any]:
    web_names = status_lookup(season)["web_name"]
    path = status_path(season)
    rows = _cached(("injuries", path, _mtime(path)),
                   lambda: injury_rows(conn.execute_query(INJURIES_QUERY, {}), web_names))
    if team:
        wanted = team.strip().lower()
        rows = [r for r in rows if (r.get("team") or "").lower() == wanted]
    return {"updated_at": read_meta(season).get("status_fetched_at"),
            "players": with_photos(rows, photo_for)}


def get_player_leaders(conn, season: str = CURRENT_SEASON, position: Optional[str] = None,
                       sort: str = "total_points", limit: int = 50,
                       photo_for: Optional[PhotoFn] = None, team: Optional[str] = None,
                       max_price: Optional[float] = None) -> Dict[str, Any]:
    """Season leaders: one row per player with SEASON_STATS in `season`, sorted by `sort`
    (desc), then total points and name. Unknown season -> NotFoundError."""
    if season not in SEASONS:
        raise NotFoundError(f"Unknown season {season}; expected one of {', '.join(SEASONS)}")
    query, params = leaders_query(sort, position, season, limit, team, max_price)
    rows = _cached(("leaders", season, position, sort, int(limit), params["team"], params["max_price"]),
                   lambda: leader_rows(conn.execute_query(query, params),
                                       include_availability=season == CURRENT_SEASON))
    # FPL's display name for players in this season's game (display only: `name` stays the key)
    web_names = status_lookup()["web_name"]
    rows = [{**r, "web_name": web_names.get(r["name"])} for r in rows]
    return {"season": season, "sort": sort, "position": position, "team": team or None,
            "max_price": max_price, "players": with_photos(rows, photo_for)}


CLUBS_QUERY = """
MATCH (:Player)-[st:SEASON_STATS]->(:Season {id: $season})
WHERE st.team IS NOT NULL
RETURN DISTINCT st.team AS team
ORDER BY team
"""


def get_clubs(conn, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """The clubs with players in `season` (graph names, A-Z), for the tables' club filters.
    Unknown season -> NotFoundError."""
    if season not in SEASONS:
        raise NotFoundError(f"Unknown season {season}; expected one of {', '.join(SEASONS)}")
    clubs = _cached(("clubs", season),
                    lambda: [str(r["team"]) for r in conn.execute_query(CLUBS_QUERY, {"season": season})
                             if r.get("team")])
    return {"season": season, "clubs": clubs}


def get_team_of_the_week(conn, mode: str = "predicted", gameweek: Optional[int] = None,
                         photo_for: Optional[PhotoFn] = None,
                         season: str = CURRENT_SEASON) -> Dict[str, Any]:
    if mode == "predicted":
        payload = _predicted_team(conn, gameweek, season)
    elif mode == "actual":
        payload = _actual_team(conn, gameweek, season)
    else:
        raise ValueError("mode must be 'predicted' or 'actual'")
    out = with_photos(payload, photo_for)
    # FPL's short name for the pitch's name plates ("Thiago", not the last word of the full name)
    web_names = status_lookup(season)["web_name"]
    for p in out.get("starters", []) + out.get("bench", []):
        p["web_name"] = web_names.get(p["name"])
    return out


def _predicted_team(conn, gameweek: Optional[int], season: str) -> Dict[str, Any]:
    from services.predictions import get_predictions_df, predictions_path

    path = predictions_path()
    df = get_predictions_df(path)
    if df is None or df.empty:
        raise NotFoundError("No predictions available")
    file_gw = _int_or_none(df["gameweek"].iloc[0])
    if file_gw is None:
        raise NotFoundError("Predictions file has no gameweek")
    if gameweek is not None and gameweek != file_gw:
        raise NotFoundError(f"Predictions are for gameweek {file_gw}, not {gameweek}")

    def build() -> Dict[str, Any]:
        opponents: Dict[str, List[Dict[str, Any]]] = {}
        if conn is not None:
            try:
                opponents = team_opponents(load_fixtures(conn, season), file_gw)
            except Exception as exc:  # the predictions file alone is enough
                logger.warning("Fixtures unavailable for team of the week opponents: %s", exc)
        selection, details = predicted_selection(df.to_dict("records"), opponents, status_lookup(season)["code"])
        return build_team_of_the_week(selection, details, "predicted", file_gw, BUDGET / 10.0)

    return _cached(("totw", "predicted", path, _mtime(path), conn is not None), build)


def _actual_team(conn, gameweek: Optional[int], season: str) -> Dict[str, Any]:
    fixtures = load_fixtures(conn, season)
    gameweeks = gameweeks_of(fixtures)
    if gameweek is None:
        finished = summarize_season(fixtures, {}, season)["finished_gameweeks"]
        if not finished:
            raise NotFoundError(f"No finished {season} gameweek yet")
        gameweek = finished[-1]
    elif gameweek not in gameweeks:
        raise NotFoundError(f"No {season} fixtures for gameweek {gameweek}")

    def build() -> Dict[str, Any]:
        raw = conn.execute_query(GAMEWEEK_POINTS_QUERY, {"season": season, "gameweek": gameweek})
        if not raw:
            raise NotFoundError(f"No player points for gameweek {gameweek} yet")
        opponents = team_opponents(fixtures, gameweek)
        totals: Dict[str, Dict[str, Any]] = {}
        for r in raw:
            position = normalize_position(r.get("position"))
            if position not in opt.POSITIONS:
                continue
            entry = totals.setdefault(r["name"], {"points": 0.0, "position": position, "team": r.get("team"),
                                                  "row": r})
            entry["points"] += float(r.get("total_points") or 0)
        pool = [opt.Candidate(id=name, position=e["position"], team=e["team"] or "", points=e["points"])
                for name, e in totals.items()]
        details = {
            name: {
                "team": e["team"],
                "cost": None,
                "opponents": opponents.get(e["team"], []),
                "status": _str_or_none(e["row"].get("status")),
                "chance_of_playing_next_round": _int_or_none(e["row"].get("chance_of_playing_next_round")),
                "news": _str_or_none(e["row"].get("news")),
                "_code": _int_or_none(e["row"].get("fpl_code")),
            }
            for name, e in totals.items()
        }
        try:
            selection = opt.best_xi(pool)
        except opt.InfeasibleError as exc:
            raise NotFoundError(f"Not enough players with points in gameweek {gameweek}: {exc}") from exc
        return build_team_of_the_week(selection, details, "actual", gameweek, None)

    return _cached(("totw", "actual", season, gameweek), build)
