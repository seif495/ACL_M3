"""Gameweek page (services/gameweek_page.py): bonus by BPS, state, the model's calls, and the
payload in each state from temp files, a fake connection and a stubbed live feed. All offline."""
import json
import os
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import gameweek_page as gp
from services import live_points as lp
from services import season_service as ss

SEASON = "2026-27"
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
PAST, FUTURE = "2026-10-23T17:30:00Z", "2099-01-01T17:30:00Z"


# ------------------------------------------------------------------ pure

def rows_of(*bps):
    return {i + 1: {"bps": b} for i, b in enumerate(bps)}


def awards(rows):
    return {e: bonus for e, _, bonus in gp.fixture_bonus(rows)}


def test_bonus_plain_top_three():
    assert awards(rows_of(40, 30, 20, 10)) == {1: 3, 2: 2, 3: 1, 4: 0}


def test_bonus_tie_for_first_pushes_third_place_down():
    assert awards(rows_of(40, 40, 30, 20)) == {1: 3, 2: 3, 3: 1, 4: 0}


def test_bonus_tie_for_second_leaves_no_third_award():
    assert awards(rows_of(40, 30, 30, 20)) == {1: 3, 2: 2, 3: 2, 4: 0}


def test_bonus_tie_for_third_shares_the_point():
    assert awards(rows_of(40, 30, 20, 20, 10)) == {1: 3, 2: 2, 3: 1, 4: 1, 5: 0}


def test_bonus_three_way_tie_for_first():
    assert awards(rows_of(40, 40, 40, 30)) == {1: 3, 2: 3, 3: 3, 4: 0}


def test_bonus_ignores_players_without_bps():
    assert awards({1: {"bps": 12}, 2: {"points": 3}}) == {1: 3}


def test_state():
    live_event = {"deadline_time": PAST}
    assert gp.gameweek_state({"finished": True}, [], NOW) == "finished"
    assert gp.gameweek_state(live_event, [{"status": "finished"}, {"status": "scheduled"}], NOW) == "live"
    assert gp.gameweek_state(live_event, [{"status": "finished"}], NOW) == "finished"
    assert gp.gameweek_state({"deadline_time": FUTURE}, [{"status": "scheduled"}], NOW) == "upcoming"


def test_model_calls_split_backed_and_missed():
    rows = [{"element": i, "name": f"P{i}", "predicted": p, "has_fixture": True}
            for i, p in [(1, 8), (2, 7), (3, 6), (4, 1), (5, 0.5)]]
    actual = {1: {"points": 14, "minutes": 90}, 2: {"points": 1, "minutes": 90}, 3: {"points": 6, "minutes": 90},
              4: {"points": 12, "minutes": 90}, 5: {"points": 0, "minutes": 0}}
    calls = gp.model_calls(rows, actual, {}, None, n=2, pool=3)
    assert [c["element"] for c in calls["best"]] == [1, 3]
    assert [c["element"] for c in calls["worst"]] == [2]             # only real shortfalls
    assert [c["element"] for c in calls["missed"]] == [4]            # outside the backed pool, far above forecast
    assert calls["missed"][0]["gap"] == 11.0 and calls["worst"][0]["name"] == "P2"


# ------------------------------------------------------------------ payloads

class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute_query(self, query, params=None):
        if "PLAYS_HOME" in query:
            return self.rows
        return []                                  # the Dream Team's points query: nothing yet


def fixture_rows(gw, status, scores=(None, None)):
    return [
        {"id": f"101-{SEASON}", "gameweek": gw, "kickoff_time": "2026-10-24T11:30:00Z", "status": status,
         "home_team": "Arsenal", "away_team": "Spurs", "home_score": scores[0], "away_score": scores[1]},
        {"id": f"102-{SEASON}", "gameweek": gw, "kickoff_time": "2026-10-24T16:00:00Z", "status": "scheduled",
         "home_team": "Chelsea", "away_team": "Fulham", "home_score": None, "away_score": None},
    ]


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    ss.clear_cache()
    gp.clear_cache()
    status = pd.DataFrame([
        {"element": 1, "code": 11, "name": "Bukayo Saka", "web_name": "Saka", "team": "Arsenal", "position": "MID"},
        {"element": 2, "code": 12, "name": "Richarlison de Andrade", "web_name": "Richarlison", "team": "Spurs", "position": "FWD"},
        {"element": 3, "code": 13, "name": "David Raya", "web_name": "Raya", "team": "Arsenal", "position": "GK"},
    ])
    status.to_csv(tmp_path / "player_status_2026_27.csv", index=False)
    (tmp_path / "meta.json").write_text(json.dumps({
        "season": SEASON, "files": {"season": "FPL_2026_2027.csv", "status": "player_status_2026_27.csv"},
        "events": [{"id": 8, "deadline_time": PAST, "finished": False, "average_entry_score": None},
                   {"id": 9, "deadline_time": FUTURE, "finished": False}],
        "finished_fixtures": [], "next_gameweek": 9, "next_deadline": FUTURE}), encoding="utf-8")
    yield tmp_path
    ss.clear_cache()
    gp.clear_cache()


def feed():
    return lp.parse_live(
        {"elements": [
            {"id": 1, "stats": {"total_points": 9, "minutes": 90, "bps": 40, "bonus": 0},
             "explain": [{"fixture": 101, "stats": [{"identifier": "goals_scored", "value": 1, "points": 9}]}]},
            {"id": 2, "stats": {"total_points": 2, "minutes": 90, "bps": 25, "bonus": 0},
             "explain": [{"fixture": 101, "stats": [{"identifier": "minutes", "value": 90, "points": 2}]}]},
            {"id": 3, "stats": {"total_points": 6, "minutes": 90, "bps": 30, "bonus": 0},
             "explain": [{"fixture": 101, "stats": [{"identifier": "clean_sheets", "value": 1, "points": 4}]}]},
        ]},
        [{"id": 101, "started": True, "finished": False, "team_h_score": 2, "team_a_score": 1, "minutes": 70},
         {"id": 102, "started": False, "finished": False}])


def test_live_gameweek(live_dir):
    conn = FakeConn(fixture_rows(8, "scheduled"))
    page = gp.build_gameweek(8, conn, None, lambda gw: feed(), now=NOW)
    assert page["state"] == "live" and page["live"]["fetched_at"]
    first = page["fixtures"][0]
    assert (first["status"], first["home_score"], first["away_score"]) == ("in_progress", 2, 1)
    assert page["fixtures"][1]["status"] == "scheduled"
    assert [(p["name"], p["points"], p["bps"]) for p in page["top_scorers"]] == [
        ("Bukayo Saka", 9, 40), ("David Raya", 6, 30), ("Richarlison de Andrade", 2, 25)]
    table = page["bonus"][0]
    assert table["confirmed"] is False
    assert [(p["web_name"], p["bonus"]) for p in table["players"]] == [("Saka", 3), ("Raya", 2), ("Richarlison", 1)]
    assert page["hauls"] == {} and page["dream_team"] is None
    assert any("model" in n for n in page["notes"])         # no saved pick in this temp dir


def test_live_feed_down_degrades(live_dir):
    page = gp.build_gameweek(8, FakeConn(fixture_rows(8, "scheduled")), None, lambda gw: None, now=NOW)
    assert page["state"] == "live" and page["top_scorers"] == [] and page["bonus"] == []
    assert any("live feed" in n for n in page["notes"])


def test_upcoming_gameweek_is_short(live_dir):
    page = gp.build_gameweek(9, FakeConn(fixture_rows(9, "scheduled")), None, None, now=NOW)
    assert page["state"] == "upcoming" and page["deadline"] == FUTURE
    assert page["top_scorers"] == [] and page["model_squad"] is None and len(page["fixtures"]) == 2


def test_finished_gameweek_recap_with_saved_calls(live_dir):
    meta = json.loads((live_dir / "meta.json").read_text(encoding="utf-8"))
    meta["events"][0].update(finished=True, data_checked=True, average_entry_score=50, highest_score=120)
    (live_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    pd.DataFrame([
        {"element": 1, "GW": 8, "minutes": 90, "total_points": 12},
        {"element": 2, "GW": 8, "minutes": 90, "total_points": 2},
        {"element": 3, "GW": 8, "minutes": 90, "total_points": 6},
    ]).to_csv(live_dir / "FPL_2026_2027.csv", index=False)
    picks = live_dir / "model_picks" / SEASON
    picks.mkdir(parents=True)
    (picks / "gw08.json").write_text(json.dumps({
        "season": SEASON, "gameweek": 8, "deadline": PAST, "archived_at": "2026-10-23T10:00:00+00:00",
        "players": []}), encoding="utf-8")
    pd.DataFrame([{"element": 1, "name": "Bukayo Saka", "predicted_points": 5.0, "fixtures": 1},
                  {"element": 2, "name": "Richarlison de Andrade", "predicted_points": 6.5, "fixtures": 1},
                  {"element": 3, "name": "David Raya", "predicted_points": 3.0, "fixtures": 1}]
                 ).to_csv(picks / "gw08_predictions.csv", index=False)

    page = gp.build_gameweek(8, FakeConn(fixture_rows(8, "finished", (2, 1))), None, lambda gw: pytest.fail("not live"),
                             now=NOW)
    assert page["state"] == "finished" and page["fpl_average"] == 50
    assert [p["points"] for p in page["top_scorers"]] == [12, 6, 2]
    assert [p["name"] for p in page["hauls"]["GK"]] == ["David Raya"]
    assert page["calls"]["best"][0]["name"] == "Bukayo Saka"
    assert page["calls"]["worst"][0]["name"] == "Richarlison de Andrade" and page["calls"]["worst"][0]["gap"] == -4.5


def test_predictions_saved_after_the_deadline_are_not_shown(live_dir):
    picks = live_dir / "model_picks" / SEASON
    picks.mkdir(parents=True)
    (picks / "gw08.json").write_text(json.dumps({"season": SEASON, "gameweek": 8, "deadline": PAST,
                                                  "archived_at": "2026-10-23T18:00:00+00:00", "players": []}))
    pd.DataFrame([{"element": 1, "predicted_points": 5.0}]).to_csv(picks / "gw08_predictions.csv", index=False)
    rows, why = gp.saved_predictions(SEASON, 8, {"deadline_time": PAST})
    assert rows is None and "predate" in why


def test_unknown_gameweek_and_no_connection(live_dir):
    with pytest.raises(ss.NotFoundError):
        gp.build_gameweek(30, FakeConn(fixture_rows(8, "scheduled")), None, None)
    page = gp.build_gameweek(8, None, None, lambda gw: feed(), now=NOW)       # events alone know GW8
    assert page["fixtures"] == [] and any("database" in n for n in page["notes"])


def test_routes(live_dir):
    conn = FakeConn(fixture_rows(9, "scheduled"))
    app = FastAPI()
    gp.register(app, lambda: conn)
    client = TestClient(app)
    assert client.get("/api/gameweek/9").json()["state"] == "upcoming"
    assert client.get("/api/gameweek/current").json()["gameweek"] in (8, 9)
    assert client.get("/api/gameweek/99").status_code == 404
    assert client.get("/api/gameweek/0").status_code == 404
    gp.clear_cache()
    app2 = FastAPI()
    gp.register(app2, lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert TestClient(app2).get("/api/gameweek/8").status_code == 200


def test_ml_manager_is_the_gameweeks_own_entry(live_dir, monkeypatch):
    # While GW8 is played the ML manager's latest entry is already GW9's plan, so the page asks
    # for GW8's entry by number.
    from services import fpl_manager as fm
    asked = []

    def view(season, photo_for=None, live=None, now=None, gameweek=None):
        asked.append(gameweek)
        if gameweek != 8:
            raise fm.NoEntryError(f"no team for GW{gameweek}")
        result = {"points": 31, "gross": 31, "hit_cost": 0, "chip": None, "captain": "Saka", "total": 99,
                  "live": {"fetched_at": "2026-10-24T17:59:00+00:00"}, "players": []}
        return {"current": {"gameweek": 8, "status": "locked", "transfers": [], "result": result}, "history": []}

    monkeypatch.setattr(fm, "get_manager_view", view)
    page = gp.build_gameweek(8, FakeConn(fixture_rows(8, "scheduled")), None, lambda gw: feed(), now=NOW)
    assert asked == [8]
    assert (page["ml_manager"]["status"], page["ml_manager"]["points"], page["ml_manager"]["total"]) == ("locked", 31, 99)

    meta = json.loads((live_dir / "meta.json").read_text(encoding="utf-8"))
    meta["events"][0]["id"] = 7                      # a past gameweek before the manager started
    (live_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    ss.clear_cache()
    gp.clear_cache()
    page = gp.build_gameweek(7, FakeConn(fixture_rows(7, "scheduled")), None, lambda gw: feed(), now=NOW)
    assert page["ml_manager"] is None
    assert "The ML manager didn't play this gameweek." in page["notes"]
