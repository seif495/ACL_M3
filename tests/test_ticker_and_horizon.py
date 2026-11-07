"""Fixture difficulty ticker (season_service.fixture_ticker, GET /api/fixtures/ticker) and the
multi-gameweek horizon of GET /api/ml/top-performers."""
import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import api_main  # noqa: E402
from services import my_team as mt  # noqa: E402
from services import season_service as ss  # noqa: E402


def fx(fid, gw, home, away, hd, ad):
    return {"id": f"{fid}-2026-27", "gameweek": gw, "kickoff_time": None, "status": "scheduled",
            "home_team": home, "away_team": away, "home_score": None, "away_score": None,
            "home_difficulty": hd, "away_difficulty": ad}


FIXTURES = [
    fx(1, 10, "Arsenal", "Hull City", 2, 5), fx(2, 10, "Leeds", "Spurs", 3, 3),
    fx(3, 11, "Hull City", "Leeds", 2, 2), fx(4, 11, "Spurs", "Arsenal", 4, 4),
    fx(5, 12, "Arsenal", "Leeds", 2, 4), fx(6, 12, "Spurs", "Arsenal", 3, 3),   # Arsenal double
    fx(7, 13, "Arsenal", "Spurs", 3, 3),                                          # outside 3 weeks
]


def test_ticker_rows_from_each_side():
    t = ss.fixture_ticker(FIXTURES, 10, 3)
    assert t["gameweeks"] == [10, 11, 12]
    by_team = {c["team"]: c for c in t["clubs"]}
    hull = by_team["Hull City"]
    assert [len(g["fixtures"]) for g in hull["gameweeks"]] == [1, 1, 0]          # blank in GW12
    assert hull["gameweeks"][0]["fixtures"][0] == {"fixture_id": "1-2026-27", "opponent": "Arsenal", "home": False,
                                                   "difficulty": 5, "kickoff_time": None}
    arsenal = by_team["Arsenal"]
    assert [len(g["fixtures"]) for g in arsenal["gameweeks"]] == [1, 1, 2]       # double in GW12
    assert arsenal["fixtures"] == 4 and arsenal["total_difficulty"] == 2 + 4 + 2 + 3
    assert arsenal["average_difficulty"] == 2.75


def test_ticker_orders_easiest_first_then_more_fixtures():
    t = ss.fixture_ticker(FIXTURES, 10, 3)
    order = [c["team"] for c in t["clubs"]]
    averages = [c["average_difficulty"] for c in t["clubs"]]
    assert averages == sorted(averages)
    # Arsenal (2.75 over 4 fixtures) comes before Spurs (3.33 over 3)
    assert order.index("Arsenal") < order.index("Spurs")


def test_get_fixture_ticker_starts_at_the_next_gameweek_and_stops_at_the_last(monkeypatch):
    monkeypatch.setattr(ss, "load_fixtures", lambda conn, season=None: FIXTURES)
    monkeypatch.setattr(ss, "read_meta", lambda season=None: {"next_gameweek": 11})
    t = ss.get_fixture_ticker(object(), gameweeks=6)
    assert t["from_gameweek"] == 11 and t["gameweeks"] == [11, 12, 13]           # the season ends at 13
    with pytest.raises(ss.NotFoundError):
        ss.get_fixture_ticker(object(), start=30)


def test_ticker_route(monkeypatch):
    monkeypatch.setattr(ss, "load_fixtures", lambda conn, season=None: FIXTURES)
    monkeypatch.setattr(ss, "read_meta", lambda season=None: {"next_gameweek": 10})
    with patch.dict(api_main.app_state, {"neo4j_conn": object()}):
        client = TestClient(api_main.app)
        r = client.get("/api/fixtures/ticker?gameweeks=2")
        assert r.status_code == 200 and r.json()["gameweeks"] == [10, 11]
        assert client.get("/api/fixtures/ticker?gameweeks=11").status_code == 422
        assert client.get("/api/fixtures/ticker?start=30").status_code == 404


# ------------------------------------------------------------------ predictions over several weeks

def predictions_df():
    df = pd.DataFrame([{"element": el, "name": name, "position": "MID", "team": "Arsenal",
                        "predicted_points": pts, "value": 60, "opponent": "Spurs", "is_home": True,
                        "gameweek": 10, "kickoff_time": "2026-11-07T15:00:00Z",
                        "prediction_method": "xgboost_v4"}
                       for el, name, pts in [(1, "Next Week Star", 6.0), (2, "Long Run Star", 4.0)]])
    df["name_lower"] = df["name"].str.lower()
    return df


def projections(first=10):
    rows = [(1, [6.0, 1.0, 1.0, 1.0]), (2, [4.0, 5.0, 5.0, 5.0])]
    return pd.DataFrame([{"element": el, "gameweek": first + k, "predicted_points": p}
                         for el, pts in rows for k, p in enumerate(pts)])


@pytest.fixture
def client():
    with patch.object(api_main, "get_predictions_df", side_effect=predictions_df), \
            patch.object(ss, "status_lookup", return_value={"web_name": {}, "code": {}}):
        yield TestClient(api_main.app)


def test_one_week_ranks_by_the_next_gameweek(client):
    with patch.object(mt, "load_projections", return_value=projections()):
        body = client.get("/api/ml/top-performers").json()
    assert [p["player_name"] for p in body["predictions"]] == ["Next Week Star", "Long Run Star"]
    assert "projected" not in body["predictions"][0] and body["metadata"]["horizon"] == 1


def test_four_weeks_rank_by_the_projected_total(client):
    with patch.object(mt, "load_projections", return_value=projections()):
        body = client.get("/api/ml/top-performers?horizon=4").json()
    rows = body["predictions"]
    assert [p["player_name"] for p in rows] == ["Long Run Star", "Next Week Star"]
    assert rows[0]["projected_total"] == 19.0 and [w["gameweek"] for w in rows[0]["projected"]] == [10, 11, 12, 13]
    assert body["metadata"]["horizon"] == 4 and body["metadata"]["horizon_gameweeks"] == [10, 11, 12, 13]


def test_stale_projections_fall_back_to_one_week(client):
    with patch.object(mt, "load_projections", return_value=projections(first=9)):
        body = client.get("/api/ml/top-performers?horizon=4").json()
    assert body["metadata"]["horizon"] == 1 and "projected" not in body["predictions"][0]
    assert client.get("/api/ml/top-performers?horizon=5").status_code == 422
