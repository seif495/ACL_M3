"""Goal minutes and penalties (services/goal_events.py, pipeline/goal_events.py) and their
place on match pages (services/matches.timeline)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import goal_events as pge
from services import goal_events as ge
from services import matches as mt

SEASON = "2026-27"
K = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)
OURS = [
    {"id": f"90-{SEASON}", "home_team": "Chelsea", "away_team": "Man Utd", "kickoff_time": K, "status": "finished",
     "home_score": 3, "away_score": 0},
    {"id": f"91-{SEASON}", "home_team": "Spurs", "away_team": "Nott'm Forest", "kickoff_time": K, "status": "finished",
     "home_score": 0, "away_score": 0},
    {"id": f"92-{SEASON}", "home_team": "Arsenal", "away_team": "Leeds", "kickoff_time": K, "status": "scheduled",
     "home_score": None, "away_score": None},
]


def detail(text, clock, team_id, player, *, scoring=True, pen=False, og=False):
    return {"type": {"text": text}, "clock": {"displayValue": clock}, "team": {"id": team_id},
            "scoringPlay": scoring, "penaltyKick": pen, "ownGoal": og, "athletesInvolved": [{"displayName": player}]}


def event(eid, home, hid, away, aid, score, details, completed=True, when=None):
    return {"id": eid, "date": (when or K).strftime("%Y-%m-%dT%H:%MZ"), "competitions": [{
        "status": {"type": {"completed": completed}},
        "competitors": [{"homeAway": "home", "score": str(score[0]), "team": {"id": hid, "displayName": home}},
                        {"homeAway": "away", "score": str(score[1]), "team": {"id": aid, "displayName": away}}],
        "details": details}]}


# Chelsea 3-0 Man Utd: a penalty, a goal, a red card, an own goal (listed under the scorer's club)
CHELSEA_DETAILS = [
    detail("Penalty - Scored", "64'", "363", "Cole Palmer", pen=True),
    detail("Goal", "50'", "363", "Morgan Rogers"),
    detail("Red Card", "63'", "360", "Lisandro Martínez", scoring=False),
    detail("Own Goal", "90'+3'", "360", "Harry Maguire", og=True),
    detail("Penalty - Missed", "30'", "360", "Bruno Fernandes", scoring=False),
]


def board(*events):
    return {"events": list(events)}


def chelsea(details=CHELSEA_DETAILS, score=(3, 0)):
    return event("740001", "Chelsea", "363", "Manchester United", "360", score, details)


def test_team_names_and_pairing():
    teams = ["Chelsea", "Man Utd", "Spurs", "Nott'm Forest", "Coventry City", "Wolves"]
    assert ge.graph_team("Manchester United", teams) == "Man Utd" and ge.graph_team("Coventry City", teams) == "Coventry City"
    assert ge.graph_team("Wolverhampton Wanderers", teams) == "Wolves" and ge.graph_team("Chelsea FC", teams) == "Chelsea"
    assert ge.graph_team("Real Madrid", teams) is None
    spurs = event("740002", "Tottenham Hotspur", "367", "Nottingham Forest", "393", (0, 0), [])
    far = event("740003", "Chelsea", "363", "Manchester United", "360", (1, 1), [], when=K + timedelta(days=60))
    paired = ge.pair_fixtures(ge.espn_matches(board(chelsea(), spurs, far)), OURS)
    assert {k: v["source_id"] for k, v in paired.items()} == {f"90-{SEASON}": "740001", f"91-{SEASON}": "740002"}


def test_clock():
    assert ge.parse_clock({"displayValue": "64'"}) == (64, None)
    assert ge.parse_clock({"displayValue": "90'+4'"}) == (90, 4)
    assert ge.parse_clock({"displayValue": "45' + 2'"}) == (45, 2)
    assert ge.parse_clock({"value": 3001.0}) == (51, None) and ge.parse_clock(None) == (None, None)


def test_goal_rows_kinds_order_and_own_goal_club():
    rows = ge.goal_rows(ge.espn_matches(board(chelsea()))[0], "Chelsea", "Man Utd")
    assert [(r["minute"], r["extra"], r["kind"], r["team"]) for r in rows] == [
        (30, None, "missed_penalty", "Man Utd"), (50, None, "goal", "Chelsea"), (64, None, "penalty", "Chelsea"),
        (90, 3, "own_goal", "Man Utd")]


def test_own_goal_listed_under_the_club_it_counts_for():
    details = [detail("Goal", "50'", "363", "Morgan Rogers"), detail("Own Goal", "70'", "363", "Harry Maguire", og=True)]
    rows = ge.goal_rows(ge.espn_matches(board(chelsea(details, (2, 0))))[0], "Chelsea", "Man Utd")
    assert [(r["kind"], r["team"]) for r in rows] == [("goal", "Chelsea"), ("own_goal", "Man Utd")]


def test_goals_that_dont_add_up_are_not_kept():
    details = [detail("Goal", "50'", "363", "Morgan Rogers")]          # 1 goal for a 3-0
    assert ge.goal_rows(ge.espn_matches(board(chelsea(details)))[0], "Chelsea", "Man Utd") is None


def test_shootout_and_unfinished_are_ignored():
    details = CHELSEA_DETAILS + [{**detail("Penalty - Scored", "120'", "363", "Cole Palmer", pen=True), "shootout": True}]
    assert len(ge.goal_rows(ge.espn_matches(board(chelsea(details)))[0], "Chelsea", "Man Utd")) == 4
    live = event("740009", "Chelsea", "363", "Manchester United", "360", (3, 0), CHELSEA_DETAILS, completed=False)
    assert not ge.espn_matches(board(live))[0]["completed"]


def test_windows_cover_every_day_a_week_at_most():
    days = [date(2026, 10, d) for d in (3, 4, 5, 17, 18, 21, 22, 28)]
    ws = ge.windows(days)
    assert all((e - s).days < ge.WINDOW_DAYS for s, e in ws)
    assert all(any(s <= d <= e for s, e in ws) for d in days)
    assert len(ws) <= 4


def test_update_fetches_saves_and_stops(tmp_path):
    calls = []
    spurs = event("740002", "Tottenham Hotspur", "367", "Nottingham Forest", "393", (0, 0), [])

    def fetch(params):
        calls.append(params)
        return board(chelsea(), spurs)

    out = ge.update(OURS, SEASON, str(tmp_path), fetch=fetch, pause=0)
    assert out == {"season": SEASON, "fetched": 2, "calls": 1, "unpaired": 0, "unmatched": 0, "left": 0}
    assert calls[0]["limit"] == 200 and "-" in calls[0]["dates"]
    store = ge.load_store(str(tmp_path), SEASON)
    assert len(store["fixtures"][f"90-{SEASON}"]) == 4 and store["fixtures"][f"91-{SEASON}"] == []
    assert store["source"] == "espn"
    assert ge.update(OURS, SEASON, str(tmp_path), fetch=lambda p: pytest.fail("called"))["calls"] == 0


def test_mismatched_or_missing_matches_are_retried_for_three_days_only(tmp_path):
    out = ge.update(OURS, SEASON, str(tmp_path), fetch=lambda p: board(chelsea(score=(2, 0))), pause=0)
    assert out["unmatched"] == 1 and out["unpaired"] == 1 and out["fetched"] == 0
    assert set(ge.load_store(str(tmp_path), SEASON)["unmatched"]) == {f"90-{SEASON}", f"91-{SEASON}"}
    # still recent: tried again
    assert ge.update(OURS, SEASON, str(tmp_path), fetch=lambda p: board(), pause=0)["calls"] == 1
    old = [{**f, "kickoff_time": K - timedelta(days=5)} for f in OURS]
    assert ge.update(old, SEASON, str(tmp_path), fetch=lambda p: pytest.fail("called"))["calls"] == 0


def test_budget_stops(tmp_path):
    many = [{**OURS[0], "id": f"{i}-{SEASON}", "kickoff_time": K - timedelta(days=10 * i)} for i in range(6)]
    calls = []
    out = ge.update(many, SEASON, str(tmp_path), fetch=lambda p: calls.append(p) or board(), max_calls=2, pause=0)
    assert len(calls) == 2 and out["calls"] == 2 and out["left"] == 4


def test_run_reports_errors_and_can_be_turned_off(monkeypatch, tmp_path):
    def down(*a, **k):
        raise ConnectionError("ESPN is down")

    monkeypatch.setattr(ge, "update", down)
    assert "ESPN is down" in pge.run(live_dir=str(tmp_path), fixtures=OURS)["error"]
    monkeypatch.setenv("GOAL_EVENTS", "0")
    assert pge.run(live_dir=str(tmp_path), fixtures=OURS) == {"skipped": "GOAL_EVENTS=0"}


def test_timeline_credits_own_goals_and_matches_names():
    players = [mt.player_row(r) for r in [
        {"name": "Cole Palmer", "team": "Chelsea", "minutes": 90}, {"name": "Morgan Rogers", "team": "Chelsea", "minutes": 90},
        {"name": "Harry Maguire", "team": "Man Utd", "minutes": 90}, {"name": "Bruno Borges Fernandes", "team": "Man Utd", "minutes": 90}]]
    rows = ge.goal_rows(ge.espn_matches(board(chelsea()))[0], "Chelsea", "Man Utd")
    t = mt.timeline(rows, {"home_team": "Chelsea", "away_team": "Man Utd"}, players)
    assert [(g["minute"], g["kind"], g["player"]) for g in t["home"]] == [
        (50, "goal", "Morgan Rogers"), (64, "penalty", "Cole Palmer"), (90, "own_goal", "Harry Maguire")]
    assert [(g["kind"], g["player"]) for g in t["away"]] == [("missed_penalty", "Bruno Borges Fernandes")]
