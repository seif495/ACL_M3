"""
Club pages (GET /api/teams, /api/teams/{slug}): a season's table row, form, results and fixtures
(each with its match page id), league position after every gameweek, goals and xG for and
against, the squad with points, price and predictions, and the top scorers and assisters.

Clubs are addressed by a slug of their graph name ("Man City" -> "man-city"). The current season
reads season_service's cached fixtures (FPL's, with status and difficulty); past seasons read
the HOME_TEAM/AWAY_TEAM fixtures every season has. Pure functions do the shaping, so they're
tested without Neo4j.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional

from config.settings import CURRENT_SEASON, SEASONS
from services import season_service as ss

PAST_FIXTURES_QUERY = """
MATCH (f:Fixture {season_id: $season})-[:HOME_TEAM]->(h:Team)
MATCH (f)-[:AWAY_TEAM]->(a:Team)
RETURN f.id AS id, f.gameweek AS gameweek, f.kickoff_time AS kickoff_time,
       CASE WHEN f.home_score IS NULL THEN 'scheduled' ELSE 'finished' END AS status,
       h.name AS home_team, a.name AS away_team, f.home_score AS home_score, f.away_score AS away_score,
       f.home_difficulty AS home_difficulty, f.away_difficulty AS away_difficulty
"""

# xG per side of each of the club's matches: the sum of its players' xG (null before 2022-23)
TEAM_XG_QUERY = """
MATCH (f:Fixture {season_id: $season})-[:HOME_TEAM|AWAY_TEAM]->(:Team {name: $team})
MATCH (:Player)-[r:PLAYED_IN]->(f)
RETURN f.id AS fixture_id, r.team AS team, sum(r.expected_goals) AS xg, count(r.expected_goals) AS rows
"""

CURRENT_SQUAD_QUERY = """
MATCH (p:Player)-[:PLAYS_FOR]->(:Team {name: $team})
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
OPTIONAL MATCH (p)-[st:SEASON_STATS]->(:Season {id: $season})
RETURN p.name AS name, pos.code AS position, p.status AS status, p.news AS news,
       p.chance_of_playing_next_round AS chance_of_playing_next_round, p.fpl_code AS fpl_code,
       st.total_points AS total_points, st.goals AS goals, st.assists AS assists, st.minutes AS minutes,
       st.appearances AS appearances, st.clean_sheets AS clean_sheets, st.bonus AS bonus
"""

PAST_SQUAD_QUERY = """
MATCH (p:Player)-[st:SEASON_STATS]->(:Season {id: $season})
WHERE st.team = $team
RETURN p.name AS name, st.position AS position, null AS status, null AS news,
       null AS chance_of_playing_next_round, p.fpl_code AS fpl_code,
       st.total_points AS total_points, st.goals AS goals, st.assists AS assists, st.minutes AS minutes,
       st.appearances AS appearances, st.clean_sheets AS clean_sheets, st.bonus AS bonus,
       CASE WHEN st.games > 0 THEN st.value_sum / 10.0 / st.games END AS price
"""

POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
TOP_PLAYERS = 5


class NotFoundError(Exception):
    pass


def slugify(team: str) -> str:
    """"Man City" -> "man-city", "Nott'm Forest" -> "nottm-forest"."""
    text = unicodedata.normalize("NFKD", team).encode("ascii", "ignore").decode()
    text = re.sub(r"['’]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def resolve(slug: str, teams: Iterable[str]) -> Optional[str]:
    wanted = slugify(slug)
    return next((t for t in teams if slugify(t) == wanted), None)


def season_fixtures(conn, season: str) -> List[Dict[str, Any]]:
    if season == CURRENT_SEASON:
        return ss.load_fixtures(conn, season)
    return ss._cached(("past-fixtures", season),
                      lambda: ss.clean_fixture_rows(conn.execute_query(PAST_FIXTURES_QUERY, {"season": season})))


# ------------------------------------------------------------------ pure

def _played(f: Dict[str, Any]) -> bool:
    return f["home_score"] is not None and f["away_score"] is not None and f["status"] != "scheduled"


def match_row(f: Dict[str, Any], team: str, xg: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    """One match from `team`'s side, with the id of its match page."""
    home = f["home_team"] == team
    opponent = f["away_team"] if home else f["home_team"]
    played = _played(f)
    gf, ga = ((f["home_score"], f["away_score"]) if home else (f["away_score"], f["home_score"])) if played else (None, None)
    side = (xg or {}).get(f["id"], {})
    return {
        "fixture_id": f["id"], "gameweek": f["gameweek"], "kickoff_time": ss.to_iso(f["kickoff_time"]),
        "status": f["status"], "opponent": opponent, "home": home,
        "goals_for": gf, "goals_against": ga, "score": f"{gf}-{ga}" if played else None,
        "result": ("W" if gf > ga else "D" if gf == ga else "L") if played else None,
        "difficulty": f["home_difficulty"] if home else f["away_difficulty"],
        "xg_for": side.get(team), "xg_against": side.get(opponent),
    }


def position_history(fixtures: List[Dict[str, Any]], team: str) -> List[Dict[str, Any]]:
    """The club's league position after each gameweek with a finished match."""
    out = []
    for gw in ss.gameweeks_of(f for f in fixtures if _played(f)):
        upto = [f for f in fixtures if f["gameweek"] is not None and f["gameweek"] <= gw]
        table = ss.compute_table(upto)
        row = next((r for r in table if r["team"] == team), None)
        if row and row["played"]:
            out.append({"gameweek": gw, "position": row["position"], "points": row["points"]})
    return out


def xg_by_fixture(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """{fixture_id: {team: xG}} from TEAM_XG_QUERY rows (sides without xG data left out)."""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        if r.get("rows") and r.get("xg") is not None and r.get("team"):
            out.setdefault(str(r["fixture_id"]), {})[r["team"]] = round(float(r["xg"]), 2)
    return out


def team_stats(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    played = [m for m in matches if m["result"]]
    with_xg = [m for m in played if m["xg_for"] is not None and m["xg_against"] is not None]
    home = [m for m in played if m["home"]]
    away = [m for m in played if not m["home"]]

    def record(ms: List[Dict[str, Any]]) -> Dict[str, int]:
        return {k: sum(1 for m in ms if m["result"] == r) for k, r in (("won", "W"), ("drawn", "D"), ("lost", "L"))}

    return {
        "played": len(played),
        "goals_for": sum(m["goals_for"] for m in played),
        "goals_against": sum(m["goals_against"] for m in played),
        "clean_sheets": sum(1 for m in played if m["goals_against"] == 0),
        "failed_to_score": sum(1 for m in played if m["goals_for"] == 0),
        "xg_for": round(sum(m["xg_for"] for m in with_xg), 1) if with_xg else None,
        "xg_against": round(sum(m["xg_against"] for m in with_xg), 1) if with_xg else None,
        "xg_matches": len(with_xg),
        "home": record(home), "away": record(away),
        "biggest_win": max((m for m in played if m["result"] == "W"),
                           key=lambda m: (m["goals_for"] - m["goals_against"], m["goals_for"]), default=None),
    }


def squad_rows(raw: Iterable[Dict[str, Any]], *, prices: Dict[str, float], predicted: Dict[str, float],
               web_names: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for r in raw:
        name = r["name"]
        position = ss.normalize_position(r.get("position"))
        price = r.get("price")
        out.append({
            "name": name, "web_name": web_names.get(name), "position": position,
            "status": r.get("status"), "news": r.get("news") or None,
            "chance_of_playing_next_round": r.get("chance_of_playing_next_round"),
            "total_points": int(r.get("total_points") or 0), "goals": int(r.get("goals") or 0),
            "assists": int(r.get("assists") or 0), "minutes": int(r.get("minutes") or 0),
            "appearances": int(r.get("appearances") or 0), "clean_sheets": int(r.get("clean_sheets") or 0),
            "bonus": int(r.get("bonus") or 0),
            "price": round(float(price), 1) if price is not None else prices.get(name),
            "predicted_points": predicted.get(name),
            "_code": r.get("fpl_code"),
        })
    out.sort(key=lambda p: (POSITION_ORDER.get(p["position"], 9), -p["total_points"], p["name"]))
    return out


def leaders(squad: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    ranked = sorted((p for p in squad if p[key] > 0), key=lambda p: (-p[key], -p["total_points"], p["name"]))
    return [{"name": p["name"], "web_name": p["web_name"], "position": p["position"], "value": p[key],
             "_code": p["_code"]} for p in ranked[:TOP_PLAYERS]]


# ------------------------------------------------------------------ payloads

def _status_prices() -> Dict[str, float]:
    """Current prices (GBP m) by player name from the FPL status file."""
    import os
    path = ss.status_path(CURRENT_SEASON)

    def load() -> Dict[str, float]:
        if not os.path.exists(path):
            return {}
        import pandas as pd
        df = pd.read_csv(path, usecols=lambda c: c in ("name", "now_cost"))
        return {str(r["name"]): round(float(r["now_cost"]) / 10.0, 1) for r in df.to_dict("records")
                if r.get("now_cost") == r.get("now_cost")}

    return ss._cached(("status-prices", path, ss._mtime(path)), load)


def teams_index(conn, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    if season not in SEASONS:
        raise NotFoundError(f"Unknown season {season}")
    table = ss.compute_table(season_fixtures(conn, season))
    return {"season": season, "teams": [{"slug": slugify(r["team"]), **r} for r in table]}


def team_page(conn, slug: str, season: str = CURRENT_SEASON, *,
              photo_for: Optional[Callable[[str, Optional[str], Optional[int]], Optional[str]]] = None,
              predicted_for: Optional[Callable[[], Dict[str, float]]] = None) -> Dict[str, Any]:
    if season not in SEASONS:
        raise NotFoundError(f"Unknown season {season}")
    fixtures = season_fixtures(conn, season)
    teams = sorted({f["home_team"] for f in fixtures} | {f["away_team"] for f in fixtures})
    team = resolve(slug, teams)
    if team is None:
        raise NotFoundError(f"No club {slug} in {season}")

    def build() -> Dict[str, Any]:
        xg = xg_by_fixture(conn.execute_query(TEAM_XG_QUERY, {"season": season, "team": team}))
        mine = [f for f in fixtures if team in (f["home_team"], f["away_team"])]
        matches = [match_row(f, team, xg) for f in mine]
        current = season == CURRENT_SEASON
        raw = conn.execute_query(CURRENT_SQUAD_QUERY if current else PAST_SQUAD_QUERY, {"season": season, "team": team})
        squad = squad_rows(raw, prices=_status_prices() if current else {},
                           predicted=(predicted_for() if (current and predicted_for) else {}),
                           web_names=ss.status_lookup(season)["web_name"] if current else {})
        table = ss.compute_table(fixtures)
        row = next((r for r in table if r["team"] == team), None)
        results = [m for m in matches if m["result"]]
        return {
            "season": season, "team": team, "slug": slugify(team),
            "standing": row, "teams_in_table": len(table),
            "form": results[-ss.FORM_LENGTH:],
            "results": list(reversed(results)),
            "fixtures": [m for m in matches if not m["result"]],
            "position_history": position_history(fixtures, team),
            "stats": team_stats(matches),
            "squad": squad,
            "top_scorers": leaders(squad, "goals"),
            "top_assists": leaders(squad, "assists"),
            "_photo_team": team,
        }

    payload = ss._cached(("team-page", season, team), build)
    return _with_photos(payload, photo_for)


def _with_photos(payload: Dict[str, Any], photo_for) -> Dict[str, Any]:
    team = payload["_photo_team"]
    out = {k: v for k, v in payload.items() if k != "_photo_team"}
    for key in ("squad", "top_scorers", "top_assists"):
        out[key] = [{**{k: v for k, v in p.items() if k != "_code"},
                     "photo": photo_for(p["name"], team, p["_code"]) if photo_for else None} for p in payload[key]]
    return out
