"""Insights: captains, differentials and value, transfers and prices, set pieces
(services/insights.py)."""
import os
import sys

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import insights as ins  # noqa: E402
from services import my_team as mt  # noqa: E402
from services import predictions as preds  # noqa: E402
from services import season_service as ss  # noqa: E402


def status_row(element, name, team, position, **kw):
    row = {"element": element, "code": 1000 + element, "name": name, "web_name": name.split()[-1], "team": team,
           "position": position, "status": "a", "chance_of_playing_next_round": None, "news": None,
           "now_cost": 80, "selected_by_percent": 5.0}
    row.update(kw)
    return row


def pred_row(element, name, team, position, pts, value=80, opponent="Leeds", **kw):
    row = {"name": name, "element": element, "position": position, "team": team, "predicted_points": pts,
           "value": value, "opponent": opponent, "is_home": True, "gameweek": 10, "status": "a",
           "chance_of_playing_next_round": None, "news": None}
    row.update(kw)
    return row


STATUS = [
    status_row(1, "Erling Haaland", "Man City", "FWD", now_cost=150, selected_by_percent=60.0,
               transfers_in_event=90000, transfers_out_event=1000, cost_change_event=1,
               penalties_order=1, direct_freekicks_order=None, corners_and_indirect_freekicks_order=None),
    status_row(2, "Mo Salah", "Liverpool", "MID", now_cost=130, selected_by_percent=30.0,
               transfers_in_event=500, transfers_out_event=80000, cost_change_event=-1,
               penalties_order=1, direct_freekicks_order=2, corners_and_indirect_freekicks_order=1),
    status_row(3, "Cheap Striker", "Leeds", "FWD", now_cost=55, selected_by_percent=2.0, penalties_order=2),
    status_row(4, "Bargain Keeper", "Leeds", "GK", now_cost=40, selected_by_percent=3.0),
    status_row(5, "Pricey Keeper", "Spurs", "GK", now_cost=55, selected_by_percent=3.0),
    status_row(6, "Trent Alexander", "Liverpool", "DEF", now_cost=70, selected_by_percent=8.0,
               direct_freekicks_order=1, corners_and_indirect_freekicks_order=2),
    status_row(7, "Doubtful Star", "Spurs", "MID", now_cost=100, selected_by_percent=15.0,
               status="d", chance_of_playing_next_round=25.0, news="Knock"),
]
PREDS = [
    pred_row(1, "Erling Haaland", "Man City", "FWD", 7.0, 150),
    pred_row(2, "Mo Salah", "Liverpool", "MID", 6.0, 130, "Arsenal"),
    pred_row(3, "Cheap Striker", "Leeds", "FWD", 4.0, 55),
    pred_row(4, "Bargain Keeper", "Leeds", "GK", 3.5, 40),
    pred_row(5, "Pricey Keeper", "Spurs", "GK", 4.5, 55),
    pred_row(6, "Trent Alexander", "Liverpool", "DEF", 3.0, 70),
    pred_row(7, "Doubtful Star", "Spurs", "MID", 1.0, 100, status="d", chance_of_playing_next_round=25.0),
    pred_row(2, "Mo Salah", "Liverpool", "MID", 2.0, 130, "Brentford"),     # double gameweek
]
PROJ = {3: [{"gameweek": 10, "points": 4.0}, {"gameweek": 11, "points": 3.0}]}


def table():
    return ins.player_table(PREDS, STATUS, PROJ)


def by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_player_table_sums_double_gameweek_and_joins_status():
    rows = table()
    assert len(rows) == 7
    salah = by_name(rows, "Mo Salah")
    assert salah["predicted_points"] == 8.0
    assert [f["opponent"] for f in salah["fixtures"]] == ["Arsenal", "Brentford"]
    assert salah["web_name"] == "Salah" and salah["_code"] == 1002 and salah["price"] == 13.0
    assert salah["ownership"] == "template" and by_name(rows, "Doubtful Star")["ownership"] == "safe"
    assert by_name(rows, "Cheap Striker")["ownership"] == "differential"
    assert by_name(rows, "Cheap Striker")["points_4gw"] == 7.0
    assert by_name(rows, "Cheap Striker")["points_per_m_4gw"] == pytest.approx(1.27)
    assert by_name(rows, "Doubtful Star")["chance_of_playing"] == 0.25


def test_ownership_tag_boundaries():
    assert ins.ownership_tag(None) is None
    assert ins.ownership_tag(9.99) == "differential" and ins.ownership_tag(10.0) == "safe"
    assert ins.ownership_tag(25.0) == "template"


def test_captain_options_rank_and_armband():
    res = ins.captain_options(table(), limit=3)
    assert [o["name"] for o in res["options"][:3]] == ["Mo Salah", "Erling Haaland", "Pricey Keeper"]
    assert res["captain"] == "Mo Salah" and res["vice"] == "Erling Haaland"
    assert res["options"][0]["role"] == "captain" and res["options"][1]["role"] == "vice"
    assert res["expected_extra_points"] == 8.0           # nailed captain: the vice adds nothing


def test_captain_vice_matters_when_captain_may_miss():
    rows = ins.player_table([pred_row(1, "A Star", "X", "MID", 6.0, chance_of_playing_next_round=50.0, status="d"),
                             pred_row(2, "B Safe", "Y", "MID", 5.0)],
                            [status_row(1, "A Star", "X", "MID", status="d", chance_of_playing_next_round=50.0),
                             status_row(2, "B Safe", "Y", "MID")])
    res = ins.captain_options(rows, limit=2)
    assert res["captain"] == "A Star" and res["vice"] == "B Safe"
    assert res["expected_extra_points"] == 8.5            # 6 + (1 - 0.5) * 5


def test_captain_options_empty():
    assert ins.captain_options([])["options"] == []


def test_differentials_filter_and_sort():
    rows = table()
    res = ins.differential_rows(rows, max_ownership=10, limit=20)
    assert [r["name"] for r in res] == ["Pricey Keeper", "Cheap Striker", "Bargain Keeper", "Trent Alexander"]
    assert [r["name"] for r in ins.differential_rows(rows, position="FWD", max_ownership=10)] == ["Cheap Striker"]
    assert "Doubtful Star" in [r["name"] for r in ins.differential_rows(rows, max_ownership=20)]
    assert [r["name"] for r in ins.differential_rows(rows, max_ownership=5, limit=1)] == ["Pricey Keeper"]


def test_unknown_ownership_is_not_a_differential():
    rows = ins.player_table([pred_row(9, "No Data", "X", "MID", 5.0)], [])
    assert rows[0]["selected_by_percent"] is None and ins.differential_rows(rows) == []


def test_budget_picks_respect_caps():
    b = ins.budget_picks(table())
    assert [r["name"] for r in b["GK"]] == ["Bargain Keeper"]          # 4.0 fits, 5.5 does not
    assert b["FWD"][0]["name"] == "Cheap Striker" and b["DEF"] == []
    assert list(ins.budget_picks(table(), position="MID")) == ["MID"]


def test_transfer_summary():
    t = ins.transfer_summary(STATUS, limit=2)
    assert t["transfers_in"][0]["name"] == "Erling Haaland" and t["transfers_in"][0]["transfers"] == 90000
    assert t["transfers_out"][0]["name"] == "Mo Salah"
    assert [r["name"] for r in t["risers"]] == ["Erling Haaland"] and t["risers"][0]["change"] == 0.1
    assert t["fallers"][0]["change"] == -0.1 and t["has_prices"]


def test_transfer_summary_without_columns():
    t = ins.transfer_summary([status_row(1, "A B", "X", "MID")])
    assert t["transfers_in"] == [] and not t["has_transfers"] and not t["has_prices"]


def test_set_piece_takers_order_and_filter():
    clubs = {c["team"]: c for c in ins.set_piece_takers(STATUS)}
    assert set(clubs) == {"Man City", "Liverpool", "Leeds"}
    liv = clubs["Liverpool"]
    assert [p["web_name"] for p in liv["free_kicks"]] == ["Alexander", "Salah"]
    assert [p["web_name"] for p in liv["corners"]] == ["Salah", "Alexander"]
    assert [c["team"] for c in ins.set_piece_takers(STATUS, "leeds")] == ["Leeds"]
    assert ins.set_piece_takers([status_row(1, "A B", "X", "MID")]) == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    ss.clear_cache()
    status, pred = tmp_path / "status.csv", tmp_path / "preds.csv"
    pd.DataFrame(STATUS).to_csv(status, index=False)
    pd.DataFrame(PREDS).to_csv(pred, index=False)
    proj = tmp_path / "projections.csv"
    pd.DataFrame([{"element": 3, "gameweek": 10, "predicted_points": 4.0},
                  {"element": 3, "gameweek": 11, "predicted_points": 3.0}]).to_csv(proj, index=False)
    monkeypatch.setattr(ss, "status_path", lambda season=None: str(status))
    monkeypatch.setattr(preds, "predictions_path", lambda: str(pred))
    monkeypatch.setattr(mt, "projections_path", lambda: str(proj))
    preds._cached = None
    app = FastAPI()
    ins.register(app, lambda name, team, code: f"/photo/{code}")
    yield TestClient(app)
    ss.clear_cache()


def test_routes(client):
    c = client.get("/api/insights/captains?limit=3").json()
    assert c["captain"] == "Mo Salah" and c["gameweek"] == 10 and c["options"][0]["photo"] == "/photo/1002"
    assert "_code" not in c["options"][0]
    d = client.get("/api/insights/differentials?position=FWD&max_ownership=10").json()
    assert [r["name"] for r in d["differentials"]] == ["Cheap Striker"] and d["has_projections"]
    assert d["differentials"][0]["points_4gw"] == 7.0 and d["note"] is None
    assert list(d["budget"]) == ["FWD"]
    t = client.get("/api/insights/transfers").json()
    assert t["transfers_in"][0]["name"] == "Erling Haaland" and t["note"] is None
    s = client.get("/api/insights/set-pieces?team=Liverpool").json()
    assert [x["team"] for x in s["teams"]] == ["Liverpool"]
    assert client.get("/api/insights/differentials?position=XYZ").status_code == 200
    assert client.get("/api/insights/captains?limit=500").status_code == 422


def test_routes_without_files(tmp_path, monkeypatch):
    ss.clear_cache()
    monkeypatch.setattr(ss, "status_path", lambda season=None: str(tmp_path / "none.csv"))
    monkeypatch.setattr(preds, "predictions_path", lambda: str(tmp_path / "none2.csv"))
    monkeypatch.setattr(mt, "projections_path", lambda: str(tmp_path / "none3.csv"))
    preds._cached = None
    app = FastAPI()
    ins.register(app, None)
    c = TestClient(app)
    for url, key in (("captains", "options"), ("differentials", "differentials"),
                     ("transfers", "transfers_in"), ("set-pieces", "teams")):
        r = c.get(f"/api/insights/{url}")
        assert r.status_code == 200, url
        assert r.json()[key] == [] and r.json()["note"], url
    ss.clear_cache()
