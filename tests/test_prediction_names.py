"""FPL short names on the predictions endpoints: shown by the frontend, searchable, never a key."""
import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import api_main  # noqa: E402
from services import season_service as ss  # noqa: E402


def predictions_df():
    rows = [
        ("Erling Haaland", "FWD", "Man City", 5.3, 158),
        ("Francisco Evanilson de Lima Barbosa", "FWD", "Bournemouth", 4.4, 64),
        ("Bruno Guimarães Rodriguez Moura", "MID", "Newcastle", 3.9, 70),
    ]
    df = pd.DataFrame([{
        "name": n, "position": pos, "team": team, "predicted_points": pts, "value": value,
        "opponent": "Arsenal", "is_home": True, "gameweek": 10,
        "kickoff_time": "2026-11-07T15:00:00Z", "prediction_method": "xgboost_v4",
    } for n, pos, team, pts, value in rows])
    df["name_lower"] = df["name"].str.lower()
    return df


LOOKUP = {"web_name": {"Erling Haaland": "Haaland",
                       "Francisco Evanilson de Lima Barbosa": "Evanilson",
                       "Bruno Guimarães Rodriguez Moura": "Bruno G."}, "code": {}}


@pytest.fixture
def client():
    with patch.object(api_main, "get_predictions_df", side_effect=predictions_df), \
            patch.object(ss, "status_lookup", return_value=LOOKUP):
        yield TestClient(api_main.app)


@pytest.mark.parametrize("route", ["/api/ml/top-performers", "/api/ml/best-value"])
def test_rows_carry_the_short_name_and_keep_the_full_one(client, route):
    rows = client.get(route).json()["predictions"]
    names = {r["player_name"]: r["web_name"] for r in rows}
    assert names["Francisco Evanilson de Lima Barbosa"] == "Evanilson"
    assert names["Erling Haaland"] == "Haaland"


@pytest.mark.parametrize("q, expected", [
    ("evanilson", "Francisco Evanilson de Lima Barbosa"),   # part of the full name
    ("Bruno G.", "Bruno Guimarães Rodriguez Moura"),         # only the short name has it
    ("guimaraes", "Bruno Guimarães Rodriguez Moura"),        # accents ignored
])
def test_search_matches_full_or_short_name(client, q, expected):
    rows = client.get("/api/ml/top-performers", params={"q": q}).json()["predictions"]
    assert [r["player_name"] for r in rows] == [expected]


def test_missing_status_file_only_drops_the_short_name():
    with patch.object(api_main, "get_predictions_df", side_effect=predictions_df), \
            patch.object(ss, "status_lookup", side_effect=OSError("no status file")):
        rows = TestClient(api_main.app).get("/api/ml/top-performers").json()["predictions"]
    assert rows and all(r["web_name"] is None for r in rows)
