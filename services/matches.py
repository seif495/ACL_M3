"""
Match pages (GET /api/matches/{fixture_id}): one fixture of any season from its PLAYED_IN rows.

  played     the score, who scored and assisted (FPL has no goal minutes), own goals, cards,
             missed and saved penalties, bonus points, each side's xG, and both line-ups
             (starters, substitutes who came on, unused) with every player's FPL points
  scheduled  each side's form before the match, the last meetings, and, for the next
             gameweek, both sides' top predicted players
  live       FPL's live points for the fixture (services/live_points), while it's played

Pure functions do the shaping, so they're tested without Neo4j.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from config.settings import CURRENT_SEASON
from services import season_service as ss

FIXTURE_QUERY = """
MATCH (f:Fixture {id: $id})
// FPL fixtures have both relationship styles; the offline CSV schedule only PLAYS_HOME/AWAY
OPTIONAL MATCH (f)-[:HOME_TEAM|PLAYS_HOME]->(h:Team)
OPTIONAL MATCH (f)-[:AWAY_TEAM|PLAYS_AWAY]->(a:Team)
WITH f, head(collect(DISTINCT h.name)) AS home_team, head(collect(DISTINCT a.name)) AS away_team
RETURN f.id AS id, f.season_id AS season, coalesce(f.gameweek, f.matchday) AS gameweek, f.kickoff_time AS kickoff_time,
       f.status AS status, home_team, away_team, f.home_score AS home_score,
       f.away_score AS away_score, f.home_difficulty AS home_difficulty, f.away_difficulty AS away_difficulty
LIMIT 1
"""

PLAYERS_QUERY = """
MATCH (p:Player)-[r:PLAYED_IN]->(f:Fixture {id: $id})
OPTIONAL MATCH (p)-[st:SEASON_STATS]->(:Season {id: f.season_id})
RETURN p.name AS name, p.fpl_code AS fpl_code, r.team AS team, st.position AS position,
       r.minutes AS minutes, r.starts AS starts, r.goals_scored AS goals, r.assists AS assists,
       r.yellow_cards AS yellow_cards, r.red_cards AS red_cards, r.own_goals AS own_goals,
       r.penalties_missed AS penalties_missed, r.penalties_saved AS penalties_saved, r.saves AS saves,
       r.bonus AS bonus, r.bps AS bps, r.total_points AS points, r.expected_goals AS xg,
       r.expected_assists AS xa, r.defensive_contribution AS defensive_contribution
"""

MEETINGS_QUERY = """
MATCH (f:Fixture)-[:HOME_TEAM]->(h:Team), (f)-[:AWAY_TEAM]->(a:Team)
WHERE ((h.name = $home AND a.name = $away) OR (h.name = $away AND a.name = $home))
  AND f.home_score IS NOT NULL AND f.id <> $id
RETURN f.id AS id, f.season_id AS season, f.gameweek AS gameweek, f.kickoff_time AS kickoff_time,
       h.name AS home_team, a.name AS away_team, f.home_score AS home_score, f.away_score AS away_score
ORDER BY f.kickoff_time DESC
LIMIT 12
"""

MEETINGS = 6
FORM = 5
PREDICTED_PER_SIDE = 3
POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}


class NotFoundError(Exception):
    pass


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def player_row(r: Dict[str, Any]) -> Dict[str, Any]:
    minutes = _int(r.get("minutes"))
    starts = r.get("starts")
    # FPL counts starts from 2022-23; before that, 60+ minutes stands in for a start
    started = bool(_int(starts)) if starts is not None else minutes >= 60
    return {
        "name": r["name"], "team": r.get("team"), "position": ss.normalize_position(r.get("position")),
        "minutes": minutes, "started": started, "points": _int(r.get("points")),
        "goals": _int(r.get("goals")), "assists": _int(r.get("assists")),
        "yellow_cards": _int(r.get("yellow_cards")), "red_cards": _int(r.get("red_cards")),
        "own_goals": _int(r.get("own_goals")), "penalties_missed": _int(r.get("penalties_missed")),
        "penalties_saved": _int(r.get("penalties_saved")), "saves": _int(r.get("saves")),
        "bonus": _int(r.get("bonus")), "bps": _int(r.get("bps")),
        "xg": _float(r.get("xg")), "xa": _float(r.get("xa")),
        "defensive_contribution": _float(r.get("defensive_contribution")),
        "_code": r.get("fpl_code"),
    }


def _events(players: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    return [{"name": p["name"], "count": p[key]} for p in sorted(players, key=lambda p: (-p[key], p["name"])) if p[key]]


def side(players: List[Dict[str, Any]], opponents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One team's half of a played match: events, xG and its line-up."""
    xg = [p["xg"] for p in players if p["xg"] is not None]
    order = lambda p: (POSITION_ORDER.get(p["position"], 9), -p["minutes"], p["name"])  # noqa: E731
    return {
        "scorers": _events(players, "goals"),
        "assists": _events(players, "assists"),
        # an own goal counts for the other side
        "own_goals_for": _events(opponents, "own_goals"),
        "yellow_cards": _events(players, "yellow_cards"),
        "red_cards": _events(players, "red_cards"),
        "penalties_missed": _events(players, "penalties_missed"),
        "penalties_saved": _events(players, "penalties_saved"),
        "xg": round(sum(xg), 2) if xg else None,
        "points": sum(p["points"] for p in players),
        "starters": sorted((p for p in players if p["started"]), key=order),
        "substitutes": sorted((p for p in players if not p["started"] and p["minutes"] > 0), key=lambda p: -p["minutes"]),
        "unused": sorted((p for p in players if not p["started"] and p["minutes"] == 0), key=order),
    }


def bonus_ranking(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted((p for p in players if p["bonus"]), key=lambda p: (-p["bonus"], -p["bps"], p["name"]))
    return [{"name": p["name"], "team": p["team"], "bonus": p["bonus"], "bps": p["bps"]} for p in ranked]


def summarize(fixture: Dict[str, Any], raw_players: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    players = [player_row(r) for r in raw_players]
    home = [p for p in players if p["team"] == fixture["home_team"]]
    away = [p for p in players if p["team"] == fixture["away_team"]]
    top = max(players, key=lambda p: (p["points"], p["bps"]), default=None)
    return {
        "home": side(home, away), "away": side(away, home),
        "bonus": bonus_ranking(players),
        "top_scorer": {"name": top["name"], "team": top["team"], "points": top["points"]} if top and top["points"] else None,
    }


def form_before(fixtures: List[Dict[str, Any]], team: str, kickoff: Any) -> List[Dict[str, Any]]:
    from services.teams import match_row
    when = ss.to_datetime(kickoff)
    played = [f for f in fixtures if team in (f["home_team"], f["away_team"]) and f["home_score"] is not None
              and f["status"] != "scheduled" and (when is None or (f["kickoff_time"] and f["kickoff_time"] < when))]
    return [match_row(f, team) for f in played][-FORM:]


def meetings(rows: Iterable[Dict[str, Any]], kickoff: Any) -> List[Dict[str, Any]]:
    when = ss.to_datetime(kickoff)
    out = []
    for r in rows:
        k = ss.to_datetime(r.get("kickoff_time"))
        if when is not None and k is not None and k >= when:
            continue
        out.append({"fixture_id": str(r["id"]), "season": r.get("season"), "gameweek": r.get("gameweek"),
                    "kickoff_time": ss.to_iso(k), "home_team": r["home_team"], "away_team": r["away_team"],
                    "home_score": _int(r["home_score"]), "away_score": _int(r["away_score"])})
    return out[:MEETINGS]


def predicted_for(df: Any, fixture: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Each side's top predicted players when the predictions file is for this gameweek."""
    out: Dict[str, List[Dict[str, Any]]] = {"home": [], "away": []}
    if df is None or not len(df) or "gameweek" not in df:
        return out
    try:
        if int(df["gameweek"].iloc[0]) != int(fixture.get("gameweek") or -1):
            return out
    except (TypeError, ValueError):
        return out
    for key, team in (("home", fixture["home_team"]), ("away", fixture["away_team"])):
        rows = df[(df["team"] == team) & (df["predicted_points"] > 0)].sort_values("predicted_points", ascending=False)
        out[key] = [{"name": str(r["name"]), "position": ss.normalize_position(r.get("position")),
                     "predicted_points": round(float(r["predicted_points"]), 1),
                     "element": int(r["element"]) if "element" in r and r["element"] == r["element"] else None}
                    for _, r in rows.head(PREDICTED_PER_SIDE).iterrows()]
    return out


def live_players(feed: Dict[str, Any], fixture_id: str, elements: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """PLAYERS_QUERY-shaped rows for one fixture from FPL's live feed (live_points' by_fixture),
    with names and teams from the status file's elements."""
    fpl_id = _int(str(fixture_id).split("-")[0])
    rows = []
    for element, stats in (feed.get("by_fixture", {}).get(fpl_id) or {}).items():
        who = elements.get(int(element))
        if not who:
            continue
        rows.append({"name": who["name"], "team": who["team"], "position": who.get("position"),
                     "fpl_code": who.get("code"), "minutes": stats.get("minutes"), "starts": stats.get("starts"),
                     "goals": stats.get("goals_scored"), "assists": stats.get("assists"),
                     "yellow_cards": stats.get("yellow_cards"), "red_cards": stats.get("red_cards"),
                     "own_goals": stats.get("own_goals"), "penalties_missed": stats.get("penalties_missed"),
                     "penalties_saved": stats.get("penalties_saved"), "saves": stats.get("saves"),
                     "bonus": stats.get("bonus"), "bps": stats.get("bps"), "points": stats.get("points"),
                     "xg": stats.get("expected_goals"), "xa": stats.get("expected_assists"),
                     "defensive_contribution": stats.get("defensive_contribution")})
    return rows


# ------------------------------------------------------------------ payload

def _status_elements() -> Dict[int, Dict[str, Any]]:
    import os
    path = ss.status_path(CURRENT_SEASON)

    def load() -> Dict[int, Dict[str, Any]]:
        if not os.path.exists(path):
            return {}
        import pandas as pd
        df = pd.read_csv(path, usecols=lambda c: c in ("element", "code", "name", "team", "position"))
        return {int(r["element"]): r for r in df.to_dict("records")}

    return ss._cached(("status-elements", path, ss._mtime(path)), load)


def match_page(conn, fixture_id: str, *,
               photo_for: Optional[Callable[[str, Optional[str], Optional[int]], Optional[str]]] = None,
               predictions: Optional[Callable[[], Any]] = None,
               live: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    found = conn.execute_query(FIXTURE_QUERY, {"id": fixture_id})
    if not found or not found[0].get("home_team"):
        raise NotFoundError(f"No match {fixture_id}")
    f = dict(found[0])
    season = f.get("season") or CURRENT_SEASON
    status = f.get("status") or ("finished" if f.get("home_score") is not None else "scheduled")
    raw = conn.execute_query(PLAYERS_QUERY, {"id": fixture_id})
    home_score = _int(f["home_score"]) if f.get("home_score") is not None else None
    away_score = _int(f["away_score"]) if f.get("away_score") is not None else None

    live_info = None
    if live is not None and season == CURRENT_SEASON and status != "finished" and f.get("gameweek"):
        feed = live(int(f["gameweek"]))
        fpl_id = _int(str(fixture_id).split("-")[0])
        info = (feed or {}).get("fixtures", {}).get(fpl_id)
        if feed and info and info.get("started"):
            rows = live_players(feed, fixture_id, _status_elements())
            if rows:
                raw = rows
            home_score, away_score = info.get("home_score"), info.get("away_score")
            status = "finished" if info.get("finished") else "in_progress"
            live_info = {"fetched_at": feed.get("fetched_at"), "minutes": info.get("minutes")}

    played = home_score is not None and away_score is not None and (status != "scheduled" or raw)
    fixture = {"id": str(f["id"]), "season": season, "gameweek": f.get("gameweek"),
               "kickoff_time": ss.to_iso(f.get("kickoff_time")), "status": status if played else "scheduled",
               "home_team": f["home_team"], "away_team": f["away_team"],
               "home_score": home_score if played else None, "away_score": away_score if played else None,
               "home_difficulty": f.get("home_difficulty"), "away_difficulty": f.get("away_difficulty")}

    from services.teams import season_fixtures
    fixtures = season_fixtures(conn, season)
    payload: Dict[str, Any] = {
        **fixture, "live": live_info,
        "summary": summarize(fixture, raw) if played else None,
        "form": {"home": form_before(fixtures, fixture["home_team"], f.get("kickoff_time")),
                 "away": form_before(fixtures, fixture["away_team"], f.get("kickoff_time"))},
        "meetings": meetings(conn.execute_query(MEETINGS_QUERY, {"id": fixture_id, "home": fixture["home_team"],
                                                                "away": fixture["away_team"]}), f.get("kickoff_time")),
        "predicted": predicted_for(predictions() if predictions else None, fixture)
        if fixture["status"] == "scheduled" and season == CURRENT_SEASON else {"home": [], "away": []},
    }
    return _with_photos(payload, photo_for)


def _with_photos(payload: Dict[str, Any], photo_for) -> Dict[str, Any]:
    def fix(p: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in p.items() if k != "_code"}
        out["photo"] = photo_for(p["name"], p.get("team"), p.get("_code")) if photo_for else None
        return out

    summary = payload.get("summary")
    if summary:
        for key in ("home", "away"):
            for part in ("starters", "substitutes", "unused"):
                summary[key][part] = [fix(p) for p in summary[key][part]]
    return payload
