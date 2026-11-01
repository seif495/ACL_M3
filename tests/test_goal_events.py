"""Goal minutes and penalties (services/goal_events.py, pipeline/goal_events.py) and their
place on match pages (services/matches.timeline)."""
from datetime import datetime, timezone

import pytest

from pipeline import goal_events as pge
from services import goal_events as ge
from services import matches as mt

SEASON = "2026-27"
K = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)
OURS = [
    {"id": f"90-{SEASON}", "home_team": "Chelsea", "away_team": "Man Utd", "kickoff_time": K, "status": "finished",
     "home_score": 2, "away_score": 0},
    {"id": f"91-{SEASON}", "home_team": "Spurs", "away_team": "Nott'm Forest", "kickoff_time": K, "status": "finished",
     "home_score": 1, "away_score": 1},
    {"id": f"92-{SEASON}", "home_team": "Arsenal", "away_team": "Leeds", "kickoff_time": K, "status": "scheduled",
     "home_score": None, "away_score": None},
]
EVENTS = [
    {"time": {"elapsed": 64, "extra": None}, "team": {"name": "Chelsea"}, "player": {"name": "C. Palmer"},
     "assist": {"name": None}, "type": "Goal", "detail": "Penalty"},
    {"time": {"elapsed": 50, "extra": None}, "team": {"name": "Chelsea"}, "player": {"name": "M. Rogers"},
     "assist": {"name": "E. Fernández"}, "type": "Goal", "detail": "Normal Goal"},
    {"time": {"elapsed": 63, "extra": None}, "team": {"name": "Manchester United"}, "player": {"name": "L. Martínez"},
     "type": "Card", "detail": "Red Card"},
    {"time": {"elapsed": 90, "extra": 3}, "team": {"name": "Manchester United"}, "player": {"name": "H. Maguire"},
     "type": "Goal", "detail": "Own Goal"},
    {"time": {"elapsed": 30, "extra": None}, "team": {"name": "Manchester United"}, "player": {"name": "B. Fernandes"},
     "type": "Goal", "detail": "Missed Penalty"},
]


def listing():
    return {"response": [
        {"fixture": {"id": 1001, "date": "2026-10-31T15:00:00+00:00", "status": {"short": "FT"}},
         "teams": {"home": {"name": "Chelsea"}, "away": {"name": "Manchester United"}}},
        {"fixture": {"id": 1002, "date": "2026-10-31T15:00:00+00:00", "status": {"short": "FT"}},
         "teams": {"home": {"name": "Tottenham"}, "away": {"name": "Nottingham Forest"}}},
    ]}


def test_team_names_and_pairing():
    teams = ["Chelsea", "Man Utd", "Spurs", "Nott'm Forest", "Coventry City"]
    assert ge.graph_team("Manchester United", teams) == "Man Utd" and ge.graph_team("Coventry", teams) == "Coventry City"
    assert ge.graph_team("Chelsea FC", teams) == "Chelsea" and ge.graph_team("Real Madrid", teams) is None
    assert ge.pair_fixtures(ge.api_fixtures(listing()), OURS) == {f"90-{SEASON}": 1001, f"91-{SEASON}": 1002}


def test_goal_rows_keep_goals_and_missed_penalties_in_time_order():
    rows = ge.goal_rows(EVENTS, ["Chelsea", "Man Utd"])
    assert [(r["minute"], r["kind"]) for r in rows] == [(30, "missed_penalty"), (50, "goal"), (64, "penalty"), (90, "own_goal")]
    assert rows[1]["assist"] == "E. Fernández" and rows[3]["extra"] == 3 and rows[3]["team"] == "Man Utd"


def test_update_lists_once_then_fetches_by_ids_and_saves(tmp_path):
    calls = []

    def fetch(path, params):
        calls.append(params)
        if "league" in params:
            return listing()
        return {"response": [{"fixture": {"id": 1001, "date": None, "status": {"short": "FT"}},
                              "teams": {"home": {"name": "Chelsea"}, "away": {"name": "Manchester United"}}, "events": EVENTS},
                             {"fixture": {"id": 1002, "date": None, "status": {"short": "FT"}},
                              "teams": {"home": {"name": "Tottenham"}, "away": {"name": "Nottingham Forest"}}, "events": []}]}

    out = ge.update(OURS, SEASON, str(tmp_path), fetch=fetch)
    assert out == {"season": SEASON, "fetched": 2, "calls": 2, "missing": 0, "unpaired": 0}
    assert calls[0] == {"league": 39, "season": 2026} and calls[1] == {"ids": "1001-1002"}
    store = ge.load_store(str(tmp_path), SEASON)
    assert len(store["fixtures"][f"90-{SEASON}"]) == 4 and store["fixtures"][f"91-{SEASON}"] == []
    # nothing left to fetch: no calls
    assert ge.update(OURS, SEASON, str(tmp_path), fetch=lambda p, q: pytest.fail("called"))["calls"] == 0


def test_budget_stops_mid_season(tmp_path):
    many = [{**OURS[0], "id": f"{i}-{SEASON}"} for i in range(45)]
    ids = {f"{i}-{SEASON}": 5000 + i for i in range(45)}
    store = ge.load_store(str(tmp_path), SEASON)
    store["api_ids"] = ids
    ge.save_store(str(tmp_path), SEASON, store)
    calls = []
    out = ge.update(many, SEASON, str(tmp_path), fetch=lambda p, q: calls.append(q) or {"response": []}, max_calls=2)
    assert len(calls) == 2 and out["calls"] == 2           # 45 matches need 3 calls of 20


def test_run_without_a_key_does_nothing(monkeypatch):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    assert pge.run(fixtures=OURS) == {"skipped": "no API_FOOTBALL_KEY"}


def test_run_reports_plan_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("API_FOOTBALL_KEY", "k")

    def refuse(*a, **k):
        raise ge.ApiFootballError('{"plan": "Free plans do not have access to this season"}')

    monkeypatch.setattr(ge, "update", refuse)
    out = pge.run(live_dir=str(tmp_path), fixtures=OURS)
    assert "Free plans" in out["error"]


def test_timeline_credits_own_goals_and_matches_names():
    players = [mt.player_row(r) for r in [
        {"name": "Cole Palmer", "team": "Chelsea", "minutes": 90}, {"name": "Morgan Rogers", "team": "Chelsea", "minutes": 90},
        {"name": "Harry Maguire", "team": "Man Utd", "minutes": 90}, {"name": "Bruno Borges Fernandes", "team": "Man Utd", "minutes": 90}]]
    t = mt.timeline(ge.goal_rows(EVENTS, ["Chelsea", "Man Utd"]), {"home_team": "Chelsea", "away_team": "Man Utd"}, players)
    assert [(g["minute"], g["kind"], g["player"]) for g in t["home"]] == [
        (50, "goal", "Morgan Rogers"), (64, "penalty", "Cole Palmer"), (90, "own_goal", "Harry Maguire")]
    assert [(g["kind"], g["player"]) for g in t["away"]] == [("missed_penalty", "Bruno Borges Fernandes")]
