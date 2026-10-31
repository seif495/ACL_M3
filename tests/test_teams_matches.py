"""Club pages (services/teams.py) and match pages (services/matches.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api_main
from config.settings import CURRENT_SEASON, SEASONS
from services import matches as mt
from services import season_service as ss
from services import teams as tm

PAST = SEASONS[-2]
T0 = datetime(2025, 8, 16, 14, tzinfo=timezone.utc)


def fx(i, home, away, hs=None, as_=None, gw=1, status=None):
    return {"id": f"{i}-{PAST}", "gameweek": gw, "kickoff_time": T0 + timedelta(days=7 * (gw - 1), hours=i),
            "status": status or ("finished" if hs is not None else "scheduled"), "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_, "home_difficulty": 3, "away_difficulty": 2}


FIXTURES = [fx(1, "Arsenal", "Chelsea", 2, 0, 1), fx(2, "Spurs", "Man City", 1, 1, 1),
            fx(3, "Man City", "Arsenal", 3, 1, 2), fx(4, "Chelsea", "Spurs", 0, 2, 2),
            fx(5, "Arsenal", "Spurs", gw=3), fx(6, "Chelsea", "Man City", gw=3)]


@pytest.fixture(autouse=True)
def fresh():
    ss.clear_cache()
    yield
    ss.clear_cache()


def test_slugs():
    assert tm.slugify("Man City") == "man-city" and tm.slugify("Nott'm Forest") == "nottm-forest"
    assert tm.resolve("MAN-CITY", ["Arsenal", "Man City"]) == "Man City" and tm.resolve("x", ["Arsenal"]) is None


def test_match_rows_and_stats():
    xg = {f"1-{PAST}": {"Arsenal": 1.8, "Chelsea": 0.4}}
    rows = [tm.match_row(f, "Arsenal", xg) for f in FIXTURES if "Arsenal" in (f["home_team"], f["away_team"])]
    assert [(r["opponent"], r["score"], r["result"]) for r in rows] == [("Chelsea", "2-0", "W"), ("Man City", "1-3", "L"),
                                                                          ("Spurs", None, None)]
    assert rows[0]["xg_for"] == 1.8 and rows[1]["xg_for"] is None and rows[1]["home"] is False
    assert rows[2]["difficulty"] == 3                     # Arsenal at home: the home side's FDR
    st = tm.team_stats(rows)
    assert (st["played"], st["goals_for"], st["goals_against"], st["clean_sheets"]) == (2, 3, 3, 1)
    assert st["xg_for"] == 1.8 and st["xg_matches"] == 1 and st["biggest_win"]["score"] == "2-0"
    assert st["home"] == {"won": 1, "drawn": 0, "lost": 0} and st["away"] == {"won": 0, "drawn": 0, "lost": 1}


def test_position_history():
    hist = tm.position_history(FIXTURES, "Arsenal")
    assert [h["gameweek"] for h in hist] == [1, 2] and hist[0]["position"] == 1 and hist[1]["points"] == 3


def test_xg_by_fixture_skips_sides_without_data():
    out = tm.xg_by_fixture([{"fixture_id": "1", "team": "A", "xg": 1.234, "rows": 11},
                            {"fixture_id": "1", "team": "B", "xg": None, "rows": 0}])
    assert out == {"1": {"A": 1.23}}


def test_squad_and_leaders():
    raw = [{"name": "Keeper", "position": "GKP", "total_points": 50, "goals": 0, "assists": 0, "fpl_code": 1},
           {"name": "Striker", "position": "FWD", "total_points": 90, "goals": 9, "assists": 2, "fpl_code": 2},
           {"name": "Winger", "position": "MID", "total_points": 80, "goals": 4, "assists": 6, "price": 7.04}]
    squad = tm.squad_rows(raw, prices={"Striker": 8.5}, predicted={"Winger": 5.5}, web_names={})
    assert [p["name"] for p in squad] == ["Keeper", "Winger", "Striker"]          # by position
    assert squad[2]["price"] == 8.5 and squad[1]["price"] == 7.0 and squad[1]["predicted_points"] == 5.5
    assert [x["name"] for x in tm.leaders(squad, "goals")] == ["Striker", "Winger"]
    assert [x["name"] for x in tm.leaders(squad, "assists")][0] == "Winger"


def test_team_page_payload():
    conn = MagicMock()
    conn.execute_query.side_effect = lambda q, p: (
        [{"fixture_id": f"1-{PAST}", "team": "Arsenal", "xg": 1.5, "rows": 11}] if "sum(r.expected_goals)" in q else
        [{"name": "Striker", "position": "FWD", "total_points": 90, "goals": 9, "assists": 2, "fpl_code": 7}])
    with patch.object(tm, "season_fixtures", return_value=FIXTURES):
        page = tm.team_page(conn, "arsenal", PAST, photo_for=lambda n, t, c: f"p/{c}")
        with pytest.raises(tm.NotFoundError):
            tm.team_page(conn, "leeds", PAST)
    assert page["team"] == "Arsenal" and page["standing"]["position"] == 3   # City and Spurs have 4 points
    assert [m["fixture_id"] for m in page["results"]] == [f"3-{PAST}", f"1-{PAST}"]       # newest first
    assert [m["opponent"] for m in page["fixtures"]] == ["Spurs"]
    assert page["squad"][0]["photo"] == "p/7" and "_code" not in page["squad"][0]
    assert page["top_scorers"][0]["photo"] == "p/7"
    with pytest.raises(tm.NotFoundError):
        tm.team_page(conn, "arsenal", "1999-00")


ROWS = [  # Arsenal 2-1 Chelsea: two goals from one player, an own goal by Chelsea... is Arsenal's
    {"name": "Saka", "team": "Arsenal", "position": "MID", "minutes": 90, "starts": 1, "goals": 1, "assists": 0,
     "bonus": 3, "bps": 40, "points": 9, "xg": 0.8, "fpl_code": 1},
    {"name": "Havertz", "team": "Arsenal", "position": "FWD", "minutes": 25, "starts": 0, "goals": 0, "assists": 1,
     "bonus": 0, "bps": 12, "points": 4, "xg": 0.3, "fpl_code": 2},
    {"name": "Bench", "team": "Arsenal", "position": "DEF", "minutes": 0, "starts": 0, "points": 0, "xg": 0.0},
    {"name": "Palmer", "team": "Chelsea", "position": "MID", "minutes": 90, "starts": 1, "goals": 1, "assists": 0,
     "yellow_cards": 1, "bonus": 2, "bps": 30, "points": 6, "xg": 0.6},
    {"name": "Unlucky", "team": "Chelsea", "position": "DEF", "minutes": 90, "starts": 1, "own_goals": 1,
     "bonus": 0, "bps": 2, "points": -1, "xg": None},
]
FIXTURE = {"home_team": "Arsenal", "away_team": "Chelsea"}


def test_summary():
    s = mt.summarize(FIXTURE, ROWS)
    home, away = s["home"], s["away"]
    assert home["scorers"] == [{"name": "Saka", "count": 1}] and home["assists"] == [{"name": "Havertz", "count": 1}]
    assert home["own_goals_for"] == [{"name": "Unlucky", "count": 1}] and away["own_goals_for"] == []
    assert away["yellow_cards"] == [{"name": "Palmer", "count": 1}]
    assert home["xg"] == 1.1 and away["xg"] == 0.6
    assert [p["name"] for p in home["starters"]] == ["Saka"] and [p["name"] for p in home["substitutes"]] == ["Havertz"]
    assert [p["name"] for p in home["unused"]] == ["Bench"]
    assert [b["name"] for b in s["bonus"]] == ["Saka", "Palmer"] and s["top_scorer"]["name"] == "Saka"


def test_starts_fall_back_to_sixty_minutes_before_2022():
    assert mt.player_row({"name": "A", "minutes": 75, "starts": None})["started"] is True
    assert mt.player_row({"name": "B", "minutes": 30, "starts": None})["started"] is False


def test_meetings_before_the_match_only():
    k = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"id": "x", "kickoff_time": k + timedelta(days=30), "home_team": "A", "away_team": "B", "home_score": 1, "away_score": 0},
            {"id": "y", "kickoff_time": k - timedelta(days=30), "home_team": "B", "away_team": "A", "home_score": 2, "away_score": 2}]
    assert [m["fixture_id"] for m in mt.meetings(rows, k)] == ["y"]


def test_predicted_needs_the_files_gameweek():
    df = pd.DataFrame([{"name": "Saka", "team": "Arsenal", "position": "MID", "predicted_points": 6.2, "gameweek": 3},
                       {"name": "Out", "team": "Arsenal", "position": "DEF", "predicted_points": 0.0, "gameweek": 3},
                       {"name": "Palmer", "team": "Chelsea", "position": "MID", "predicted_points": 5.1, "gameweek": 3}])
    got = mt.predicted_for(df, {**FIXTURE, "gameweek": 3})
    assert [p["name"] for p in got["home"]] == ["Saka"] and got["away"][0]["predicted_points"] == 5.1
    assert mt.predicted_for(df, {**FIXTURE, "gameweek": 4}) == {"home": [], "away": []}


def test_live_rows_from_the_feed():
    feed = {"by_fixture": {12: {101: {"minutes": 60, "goals_scored": 1, "points": 7, "starts": 1}}}}
    rows = mt.live_players(feed, f"12-{CURRENT_SEASON}", {101: {"name": "Saka", "team": "Arsenal", "position": "MID", "code": 9}})
    assert rows[0]["name"] == "Saka" and rows[0]["goals"] == 1 and rows[0]["points"] == 7


def _match_conn(fixture, players, meetings=()):
    conn = MagicMock()
    conn.execute_query.side_effect = lambda q, p: (
        [fixture] if "MATCH (f:Fixture {id: $id})" in q and "PLAYED_IN" not in q else
        list(players) if "PLAYED_IN" in q else list(meetings))
    return conn


def test_match_page_played_and_scheduled():
    played = {"id": f"1-{PAST}", "season": PAST, "gameweek": 1, "kickoff_time": T0, "status": None,
              "home_team": "Arsenal", "away_team": "Chelsea", "home_score": 2.0, "away_score": 1.0}
    with patch.object(tm, "season_fixtures", return_value=FIXTURES):
        page = mt.match_page(_match_conn(played, ROWS), f"1-{PAST}", photo_for=lambda n, t, c: f"p/{n}")
        assert (page["home_score"], page["away_score"], page["status"]) == (2, 1, "finished")
        assert page["summary"]["home"]["starters"][0]["photo"] == "p/Saka"
        upcoming = {**played, "id": f"5-{PAST}", "home_team": "Arsenal", "away_team": "Spurs", "home_score": None,
                    "away_score": None, "gameweek": 3, "kickoff_time": T0 + timedelta(days=14)}
        page = mt.match_page(_match_conn(upcoming, []), f"5-{PAST}")
        assert page["summary"] is None and page["status"] == "scheduled"
        assert [m["result"] for m in page["form"]["home"]] == ["W", "L"]
    with pytest.raises(mt.NotFoundError):
        mt.match_page(_match_conn({}, []), "nope")


def test_live_match_uses_the_feed():
    fixture = {"id": f"12-{CURRENT_SEASON}", "season": CURRENT_SEASON, "gameweek": 10, "kickoff_time": None,
               "status": "in_progress", "home_team": "Arsenal", "away_team": "Chelsea", "home_score": None, "away_score": None}
    feed = {"fetched_at": "t", "fixtures": {12: {"started": True, "finished": False, "home_score": 1, "away_score": 0, "minutes": 55}},
            "by_fixture": {12: {101: {"minutes": 55, "goals_scored": 1, "points": 7, "starts": 1}}}}
    with patch.object(tm, "season_fixtures", return_value=[]), \
            patch.object(mt, "_status_elements", return_value={101: {"name": "Saka", "team": "Arsenal", "position": "MID"}}):
        page = mt.match_page(_match_conn(fixture, []), f"12-{CURRENT_SEASON}", live=lambda gw: feed)
    assert page["status"] == "in_progress" and (page["home_score"], page["away_score"]) == (1, 0)
    assert page["live"] == {"fetched_at": "t", "minutes": 55}
    assert page["summary"]["home"]["scorers"] == [{"name": "Saka", "count": 1}]


def test_routes():
    client = TestClient(api_main.app)
    state = dict(api_main.app_state, neo4j_conn=MagicMock(), avatar_service=None)
    with patch.dict(api_main.app_state, state), \
            patch.object(tm, "team_page", return_value={"team": "Arsenal"}) as page, \
            patch.object(mt, "match_page", side_effect=mt.NotFoundError("no")):
        assert client.get("/api/teams/arsenal").json() == {"team": "Arsenal"}
        assert page.call_args.args[1:3] == ("arsenal", CURRENT_SEASON)
        assert client.get("/api/teams/Bad%20Slug!").status_code == 404
        assert client.get("/api/matches/1-2025-26").status_code == 404
