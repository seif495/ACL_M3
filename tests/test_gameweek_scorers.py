"""Scorers of a gameweek's fixtures (services/gameweek_scorers.py) with a fake connection."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import gameweek_scorers as gs
from services import season_service as ss

HOME, AWAY = "Arsenal", "Hull City"
FIXTURE = {"id": "1-2026-27", "gameweek": 3, "status": "finished", "home_team": HOME, "away_team": AWAY,
           "home_score": 2, "away_score": 1, "kickoff_time": None, "home_difficulty": 2, "away_difficulty": 4}
ROWS = [
    {"fixture_id": "1-2026-27", "name": "Bukayo Saka", "team": HOME, "goals": 1, "own_goals": 0},
    {"fixture_id": "1-2026-27", "name": "Kai Havertz", "team": HOME, "goals": 0, "own_goals": 1},
    {"fixture_id": "1-2026-27", "name": "Joe Hull", "team": AWAY, "goals": 2, "own_goals": 0},
]


class FakeConn:
    def __init__(self, fixtures, rows):
        self.fixtures, self.rows, self.calls = fixtures, rows, []

    def execute_query(self, query, params=None):
        self.calls.append(query)
        return self.rows if "PLAYED_IN" in query else self.fixtures


@pytest.fixture(autouse=True)
def fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    ss.clear_cache()
    yield
    ss.clear_cache()


def test_from_rows_puts_own_goal_under_other_side():
    out = gs.from_rows(FIXTURE, ROWS)
    assert [(e["name"], e["kind"]) for e in out["home"]] == [("Bukayo Saka", "goal")]
    assert [(e["name"], e["kind"], e["count"]) for e in out["away"]] == [
        ("Joe Hull", "goal", 2), ("Kai Havertz", "own_goal", 1)]
    assert out["away"][0]["minute"] is None


def test_from_events_orders_by_time_and_splits_sides():
    events = [
        {"minute": 23, "extra": None, "kind": "goal", "player": "Bukayo Saka", "team": HOME, "assist": None},
        {"minute": 50, "extra": None, "kind": "missed_penalty", "player": "Joe Hull", "team": AWAY, "assist": None},
        {"minute": 60, "extra": None, "kind": "own_goal", "player": "Kai Havertz", "team": HOME, "assist": None},
        {"minute": 88, "extra": 2, "kind": "penalty", "player": "Joe Hull", "team": AWAY, "assist": None},
    ]
    out = gs.from_events(FIXTURE, ROWS, events, {"Bukayo Saka": "Saka"})
    assert [(e["name"], e["minute"], e["web_name"]) for e in out["home"]] == [("Bukayo Saka", 23, "Saka")]
    assert [(e["kind"], e["minute"], e["extra"]) for e in out["away"]] == [("own_goal", 60, None), ("penalty", 88, 2)]


def test_get_scorers_one_query_cached_and_only_finished():
    scheduled = {**FIXTURE, "id": "2-2026-27", "status": "scheduled", "home_score": None, "away_score": None}
    other_gw = {**FIXTURE, "id": "3-2026-27", "gameweek": 4}
    conn = FakeConn([FIXTURE, scheduled, other_gw], ROWS)
    first = gs.get_scorers(conn, 3)
    assert list(first) == ["1-2026-27"]
    assert gs.get_scorers(conn, 3) == first
    assert sum("PLAYED_IN" in q for q in conn.calls) == 1


def test_endpoint_and_empty_gameweek():
    app = FastAPI()
    conn = FakeConn([FIXTURE], ROWS)
    gs.register(app, lambda: conn)
    client = TestClient(app)
    body = client.get("/api/fixtures/scorers", params={"gameweek": 3}).json()
    assert body["1-2026-27"]["home"][0]["name"] == "Bukayo Saka"
    assert client.get("/api/fixtures/scorers", params={"gameweek": 9}).json() == {}
    assert client.get("/api/fixtures/scorers").status_code == 422
