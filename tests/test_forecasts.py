"""Forecast routes (services/forecasts.py): a fresh FastAPI app and a fake connection. Offline."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable

from config.settings import CURRENT_SEASON, SEASONS
from services import forecasts
from services import season_service as ss

TEAMS = [f"Club {c}" for c in "ABCDEFGHIJKLMNOPQRST"]
KICKOFF = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
PREVIOUS = SEASONS[SEASONS.index(CURRENT_SEASON) - 1]


def row(i, gw, home, away, hs=None, as_=None, status=None, season=CURRENT_SEASON):
    return {"id": f"{i}-{season}", "gameweek": gw, "kickoff_time": KICKOFF + timedelta(days=7 * gw, minutes=i),
            "status": status or ("finished" if hs is not None else "scheduled"),
            "home_team": home, "away_team": away, "home_score": hs, "away_score": as_,
            "home_difficulty": 3, "away_difficulty": 3}


def double_round_robin(teams, season, played, seed):
    """Every pair home and away; the first `played` matches have scores, the rest are scheduled."""
    rng = np.random.default_rng(seed)
    out, n = [], 0
    for i, h in enumerate(teams):
        for j, a in enumerate(teams):
            if i == j:
                continue
            n += 1
            gw = 1 + (n - 1) // 10
            if n <= played:
                out.append(row(n, gw, h, a, int(rng.poisson(1.5)), int(rng.poisson(1.2)), season=season))
            else:
                out.append(row(n, gw, h, a, season=season))
    return out


class FakeConn:
    """Current-season rows for the current season's query, last season's for any other."""

    def __init__(self, current, previous=(), fail=False):
        self.current, self.previous, self.fail = list(current), list(previous), fail
        self.calls = 0

    def execute_query(self, query, params=None):
        self.calls += 1
        if self.fail:
            raise ServiceUnavailable("Neo4j is down")
        rows = self.current if params["season"] == CURRENT_SEASON else self.previous
        return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(forecasts, "SIMULATIONS", 400)       # keep the routes quick
    ss.clear_cache()
    forecasts.clear_cache()
    yield
    ss.clear_cache()
    forecasts.clear_cache()


def client_for(conn):
    app = FastAPI()
    forecasts.register(app, lambda: conn)
    return TestClient(app)


@pytest.fixture
def conn():
    current = double_round_robin(TEAMS, CURRENT_SEASON, played=150, seed=1)
    previous = double_round_robin(TEAMS, PREVIOUS, played=380, seed=2)
    return FakeConn(current, previous)


# ------------------------------------------------------------------ the table

def test_table_forecast_payload(conn):
    r = client_for(conn).get("/api/table/forecast")
    assert r.status_code == 200
    data = r.json()
    assert data["season"] == CURRENT_SEASON
    assert data["simulations"] == 400 and data["seed"] == forecasts.ts.DEFAULT_SEED
    assert data["played_matches"] == 150 and data["remaining_matches"] == 230
    assert datetime.fromisoformat(data["generated_at"]).tzinfo is not None
    assert data["zones"] == {"champions_league": 4, "europe": 6, "relegation": 3}
    assert data["model"]["home_advantage"] > 0 and data["model"]["prior_season"] is True
    assert len(data["ratings"]) == 20 and {"attack", "defence", "strength"} <= set(data["ratings"][0])
    rows = data["rows"]
    assert len(rows) == 20
    assert [x["predicted_position"] for x in rows] == list(range(1, 21))
    assert sum(x["p_title"] for x in rows) == pytest.approx(1.0)
    assert sum(x["p_relegation"] for x in rows) == pytest.approx(3.0)
    for x in rows:
        assert len(x["position_probabilities"]) == 20
        assert x["expected_points"] >= x["points"]
        assert 1 <= x["expected_position"] <= 20


def test_table_forecast_is_stable_and_cached(conn):
    client = client_for(conn)
    first = client.get("/api/table/forecast").json()
    queries = conn.calls
    second = client.get("/api/table/forecast").json()
    assert first == second                           # same seed, same cached answer
    assert first["generated_at"] == second["generated_at"]
    assert queries >= 1


def test_a_new_result_refits_the_model(conn):
    client = client_for(conn)
    before = client.get("/api/table/forecast").json()
    # the next scheduled fixture finishes
    nxt = next(f for f in conn.current if f["status"] == "scheduled")
    nxt.update(status="finished", home_score=4, away_score=0)
    ss.clear_cache()                                 # season_service's own 5-minute cache, as a reload does
    after = client.get("/api/table/forecast").json()
    assert after["played_matches"] == before["played_matches"] + 1
    assert after["rows"] != before["rows"]


def test_table_forecast_without_a_previous_season(conn):
    conn.previous = []
    data = client_for(conn).get("/api/table/forecast").json()
    assert data["model"]["prior_season"] is False
    assert len(data["rows"]) == 20


def test_table_forecast_neo4j_down_is_503():
    r = client_for(FakeConn([], fail=True)).get("/api/table/forecast")
    assert r.status_code == 503


def test_connection_dependency_503_passes_through():
    def no_conn():
        raise HTTPException(status_code=503, detail="Neo4j not connected")

    app = FastAPI()
    forecasts.register(app, no_conn)
    assert TestClient(app).get("/api/table/forecast").status_code == 503


# ------------------------------------------------------------------ one match

def test_match_forecast_for_an_unplayed_fixture(conn):
    scheduled = next(f for f in conn.current if f["status"] == "scheduled")
    r = client_for(conn).get(f"/api/matches/{scheduled['id']}/forecast")
    assert r.status_code == 200
    d = r.json()
    assert d["fixture_id"] == scheduled["id"] and d["gameweek"] == scheduled["gameweek"]
    assert d["home_team"] == scheduled["home_team"] and d["away_team"] == scheduled["away_team"]
    assert d["home_win"] + d["draw"] + d["away_win"] == pytest.approx(1.0)
    assert d["expected_goals_home"] > 0 and d["expected_goals_away"] > 0
    assert len(d["most_likely_scores"]) == 3
    assert 0 < d["clean_sheet_home"] < 1 and 0 < d["clean_sheet_away"] < 1
    assert d["kickoff_time"].startswith("2026-")
    assert d["matches_in_model"] == 150


def test_match_forecast_for_a_played_fixture_is_404(conn):
    played = next(f for f in conn.current if f["status"] == "finished")
    r = client_for(conn).get(f"/api/matches/{played['id']}/forecast")
    assert r.status_code == 404
    assert "played" in r.json()["detail"]


def test_match_forecast_for_a_live_fixture_is_404(conn):
    live = next(f for f in conn.current if f["status"] == "scheduled")
    live.update(status="in_progress", home_score=1, away_score=0)
    assert client_for(conn).get(f"/api/matches/{live['id']}/forecast").status_code == 404


@pytest.mark.parametrize("fixture_id", ["999999-2026-27", f"12-{PREVIOUS}", "nope", "a", "x" * 60, "bad id"])
def test_match_forecast_for_an_unknown_fixture_is_404(conn, fixture_id):
    assert client_for(conn).get(f"/api/matches/{fixture_id}/forecast").status_code == 404


def test_match_forecast_shares_the_cached_model(conn):
    client = client_for(conn)
    scheduled = [f for f in conn.current if f["status"] == "scheduled"][:2]
    client.get("/api/table/forecast")
    calls = conn.calls
    a = client.get(f"/api/matches/{scheduled[0]['id']}/forecast").json()
    b = client.get(f"/api/matches/{scheduled[1]['id']}/forecast").json()
    assert a["generated_at"] == b["generated_at"]
    assert conn.calls >= calls          # fixtures come from season_service's cache; the model is not refitted
    assert len(forecasts._cache) == 1


def test_previous_season_helper():
    assert forecasts.previous_season(CURRENT_SEASON) == PREVIOUS
    assert forecasts.previous_season(SEASONS[0]) is None


def test_signature_changes_with_scores(conn):
    fixtures = ss.load_fixtures(conn)
    a = forecasts.signature(fixtures, [])
    changed = [dict(f) for f in fixtures]
    changed[-1].update(status="finished", home_score=1, away_score=1)
    assert forecasts.signature(changed, []) != a
    assert forecasts.signature(fixtures, []) == a
