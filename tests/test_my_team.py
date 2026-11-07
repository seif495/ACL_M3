"""My team (services/my_team.py, GET /api/my-team/{id}, POST /api/my-team/{id}/plan): a visitor's
FPL team imported from FPL's public API, with what FPL doesn't publish worked out. Offline:
FPL's answers are dicts here and the planner is faked where its MILP isn't the point."""
import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import api_main  # noqa: E402
from services import fpl_manager as fm  # noqa: E402
from services import my_team as mt  # noqa: E402

RULES = fm.Rules()

# 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD, each at a different club
POSITIONS = ["GK"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
PLAYERS = {el: mt.PlayerInfo(element=el, code=1000 + el, name=f"First Player{el}", web_name=f"Player{el}",
                             team=f"Club {el}", position=pos, now_cost=50 + el, status="a", chance=None, news="")
           for el, pos in enumerate(POSITIONS, start=1)}
PLAYERS[20] = mt.PlayerInfo(element=20, code=1020, name="Spare Striker", web_name="Spare", team="Club 20",
                            position="FWD", now_cost=60, status="a", chance=None, news="")


@pytest.fixture(autouse=True)
def fresh_caches():
    mt.clear_cache()
    yield
    mt.clear_cache()


def history_row(gw, made=0, cost=0, points=50, bank=5, value=1000):
    return {"event": gw, "points": points, "total_points": points * gw, "rank": 100, "overall_rank": 1000 + gw,
            "bank": bank, "value": value, "event_transfers": made, "event_transfers_cost": cost, "points_on_bench": 0}


def raw_team(last=3, started=1, history=None, chips=(), transfers=(), picks_gw=None):
    return {
        "entry": {"id": 77, "name": "Moose FC", "player_first_name": "Chris", "player_last_name": "M",
                  "started_event": started, "current_event": last, "summary_overall_points": 150,
                  "summary_overall_rank": 12345, "summary_event_points": 55, "last_deadline_bank": 5,
                  "last_deadline_value": 1000},
        "history": {"current": history or [history_row(g) for g in range(started, last + 1)],
                    "chips": [{"name": n, "event": g} for g, n in chips]},
        "transfers": list(transfers),
        "picks": {"picks": [{"element": el, "position": el} for el in range(1, 16)],
                  "entry_history": {"event": picks_gw or last, "bank": 5, "value": 1000}, "active_chip": None},
        "picks_gameweek": picks_gw or last, "last_gameweek": last,
    }


def predictions_df(gw=4, points=None):
    points = points or {}
    return pd.DataFrame([{"element": el, "name": p.name, "predicted_points": points.get(el, 2.0 + el / 10),
                          "gameweek": gw, "opponent": "Arsenal", "is_home": el % 2 == 0}
                         for el, p in PLAYERS.items()])


def projections_df(first=4, weeks=4):
    return pd.DataFrame([{"element": el, "gameweek": first + k, "predicted_points": 2.0 + k}
                         for el in PLAYERS for k in range(weeks)])


META = {"next_gameweek": 4, "next_deadline": "2026-09-12T10:00:00Z",
        "events": [{"id": g, "average_entry_score": 40 + g} for g in range(1, 4)]}


# ------------------------------------------------------------------ free transfers

def test_free_transfers_bank_up_to_the_cap():
    hist = [history_row(g) for g in range(1, 9)]
    assert mt.replay_free_transfers(hist[:2], {}, 1, 2, RULES) == 2      # GW2 had 1, unused -> 2
    assert mt.replay_free_transfers(hist[:4], {}, 1, 4, RULES) == 4
    assert mt.replay_free_transfers(hist, {}, 1, 8, RULES) == 5          # capped at 5


def test_free_transfers_used_and_hits():
    hist = [history_row(1), history_row(2), history_row(3, made=3, cost=4)]
    # GW3 started with 2, made 3 with one hit -> all free ones used -> 1 next week
    assert mt.replay_free_transfers(hist, {}, 1, 3, RULES) == 1
    hist = [history_row(1), history_row(2), history_row(3, made=1)]
    assert mt.replay_free_transfers(hist, {}, 1, 3, RULES) == 2          # 2 - 1 + 1


def test_wildcard_and_free_hit_keep_the_banked_ones():
    hist = [history_row(1), history_row(2), history_row(3, made=12), history_row(4, made=9)]
    chips = {3: "wildcard", 4: "freehit"}
    # GW2: 1 -> GW3: 2 (wildcard keeps them) -> GW4: 3 (free hit keeps them) -> next: 4
    assert mt.replay_free_transfers(hist, chips, 1, 4, RULES) == 4


def test_the_hit_cost_corrects_the_replay():
    # More free transfers than the replay knew of (an FPL top-up): 3 moves at no cost
    hist = [history_row(1), history_row(2), history_row(3, made=3)]
    assert mt.replay_free_transfers(hist, {}, 1, 3, RULES) == 1          # all 3 were free -> 1 next
    # A hit while the replay thought there were spares: only 1 was free
    hist = [history_row(g) for g in range(1, 5)] + [history_row(5, made=2, cost=4)]
    assert mt.replay_free_transfers(hist, {}, 1, 5, RULES) == 1


def test_a_team_that_started_late_gets_one_free_transfer():
    assert mt.replay_free_transfers([history_row(6)], {}, 6, 6, RULES) == 1


# ------------------------------------------------------------------ squad and purchase prices

def transfer(el_in, el_out, event, cost_in, time):
    return {"element_in": el_in, "element_out": el_out, "element_in_cost": cost_in, "element_out_cost": 50,
            "event": event, "time": time}


def test_purchase_price_is_the_latest_transfer_in_outside_free_hits():
    transfers = [
        transfer(3, 21, 2, 48, "2026-08-28T10:00:00Z"),
        transfer(3, 22, 3, 52, "2026-09-04T10:00:00Z"),      # bought again later: this one counts
        transfer(4, 23, 5, 70, "2026-09-18T10:00:00Z"),      # a free-hit week: that squad reverted
    ]
    paid = mt.purchase_prices([3, 4, 5, 6], transfers, {5}, start_prices={4: 45, 5: 55}, now_costs={6: 61})
    assert paid == {3: (52, "transfer"), 4: (45, "first squad"), 5: (55, "first squad"), 6: (61, "estimate")}


def test_pending_transfers_are_applied_in_order_and_bad_ones_skipped():
    transfers = [
        transfer(20, 15, 4, 60, "2026-09-10T10:00:00Z"),
        transfer(21, 99, 4, 60, "2026-09-10T11:00:00Z"),      # sells someone not in the squad
        transfer(8, 2, 3, 60, "2026-09-01T10:00:00Z"),        # an old one: already in the picks
    ]
    squad, applied = mt.apply_pending(list(range(1, 16)), transfers, after=3)
    assert 20 in squad and 15 not in squad and len(squad) == 15
    assert [t["element_in"] for t in applied] == [20]


def test_selling_price_follows_fpl():
    assert mt.selling_price(56, 60) == 58     # half the rise
    assert mt.selling_price(56, 59) == 57     # rounded down
    assert mt.selling_price(70, 68) == 68     # a fall costs it all


# ------------------------------------------------------------------ FPL requests

def test_load_entry_takes_the_squad_before_a_free_hit():
    calls = []

    def fetch(path):
        calls.append(path)
        raw = raw_team(last=5, chips=[(5, "freehit")])
        return {"entry/77/": raw["entry"], "entry/77/history/": raw["history"],
                "entry/77/transfers/": [], "entry/77/event/4/picks/": raw["picks"]}[path]

    out = mt.load_entry(77, fetch)
    assert out["picks_gameweek"] == 4 and out["last_gameweek"] == 5
    assert "entry/77/event/4/picks/" in calls


def test_load_entry_before_the_first_deadline():
    def fetch(path):
        return {"id": 77, "started_event": 6, "current_event": 5}
    with pytest.raises(mt.TeamNotReady):
        mt.load_entry(77, fetch)
    with pytest.raises(mt.TeamNotReady):
        mt.load_entry(77, lambda path: {"id": 77, "started_event": None, "current_event": None})


def test_fpl_answers_are_cached_per_team():
    raw = raw_team()
    calls = []

    def fetch(path):
        calls.append(path)
        return {"entry/77/": raw["entry"], "entry/77/history/": raw["history"], "entry/77/transfers/": [],
                "entry/77/event/3/picks/": raw["picks"]}[path]

    mt.cached_entry(77, fetch)
    mt.cached_entry(77, fetch)
    assert len(calls) == 4


# ------------------------------------------------------------------ the payload

def build(raw=None, predictions=None, projections=None, meta=META):
    return mt.build_team(raw or raw_team(), PLAYERS, predictions if predictions is not None else predictions_df(),
                         projections, {el: 45 for el in PLAYERS}, RULES, meta)


def test_build_team_payload():
    team = build(raw_team(chips=[(2, "bboost")]), projections=projections_df())
    assert team["entry"] == {"id": 77, "name": "Moose FC", "manager": "Chris M", "overall_points": 150,
                             "overall_rank": 12345, "event_points": 55, "last_gameweek": 3, "started_gameweek": 1}
    assert team["gameweek"] == 4 and team["based_on_gameweek"] == 3 and team["predictions_gameweek"] == 4
    assert team["bank"] == 5 and team["free_transfers"] == 3
    row = next(r for r in team["squad"] if r["element"] == 3)
    assert row["purchase_price"] == 45 and row["purchase_source"] == "first squad"
    assert row["now_cost"] == 53 and row["selling_price"] == 49
    assert row["projected"] == [{"gameweek": 4 + k, "points": 2.0 + k} for k in range(4)] and row["projected_total"] == 14.0
    assert row["_code"] == 1003 and row["opponents"] == [{"team": "Arsenal", "home": False}]
    lineup = team["lineup"]
    assert len(lineup["starters"]) == 11 and len(lineup["bench"]) == 4
    assert lineup["captain"] == 15                         # the highest prediction
    used = {(c["chip"], c["half"]): c["used_gameweek"] for c in team["chips"]}
    assert used[("bboost", 1)] == 2 and used[("wildcard", 1)] is None
    assert team["history"][1] == {"gameweek": 2, "points": 50, "transfer_cost": 0, "overall_rank": 1002,
                                  "average": 42, "chip": "bboost"}


def test_stale_projections_are_left_out():
    team = build(projections=projections_df(first=3))     # an older run: starts a week earlier
    assert all(r["projected"] == [] and r["projected_total"] is None for r in team["squad"])


def test_the_gameweek_never_lags_behind_fpl():
    team = build(meta={**META, "next_gameweek": 2})
    assert team["gameweek"] == 4


# ------------------------------------------------------------------ plans

@pytest.fixture
def live(monkeypatch):
    """get_team/plan_team with our data files replaced by the fixtures above."""
    raw = raw_team()
    monkeypatch.setattr(mt, "cached_entry", lambda entry_id, fetch=None: raw)
    monkeypatch.setattr(mt, "players_by_element", lambda season=None: PLAYERS)
    monkeypatch.setattr(mt, "prices_in_gameweek", lambda gw, season=None: {el: 45 for el in PLAYERS})
    monkeypatch.setattr(mt, "load_projections", lambda: projections_df())
    monkeypatch.setattr(mt, "projections_path", lambda: __file__)
    monkeypatch.setattr(mt.fm, "load_rules", lambda live_dir, season=None: RULES)
    monkeypatch.setattr(mt.ss, "read_meta", lambda season=None: META)
    import services.predictions as predictions
    monkeypatch.setattr(predictions, "get_predictions_df", lambda path=None: predictions_df())
    return raw


def fake_planner(calls):
    def plan(current, projections, rules, gameweek, chips, horizon, **kw):
        calls.append({"current": current, "gameweek": gameweek, "chips": chips, "horizon": horizon, **kw})
        return {"gameweek": gameweek, "chip": None, "transfers": [], "projected_points": 60.0,
                "squad": [{"element": p["element"], "name": p["name"], "team": p["team"], "role": "starter"}
                          for p in current["squad"]], "reason": "Keep the team."}
    return plan


def test_plan_passes_the_options_and_caches(live):
    calls = []
    out = mt.plan_team(77, free_transfers=9, max_hits=5, horizon=2, chips=True, planner=fake_planner(calls))
    call = calls[0]
    assert call["current"]["free_transfers"] == 5            # clamped to the rules' cap
    assert call["max_hits"] == 2 and call["horizon"] == 2 and call["gameweek"] == 4
    assert call["time_limit"] == mt.WEB_SOLVE_SECONDS
    assert set(call["chips"]) == {"wildcard", "freehit", "bboost", "3xc"}
    assert call["current"]["bank"] == 5 and len(call["current"]["squad"]) == 15
    assert out["inputs"] == {"free_transfers": 5, "max_hits": 2, "horizon": 2, "chips": True}
    assert out["squad"][0]["_code"] == 1001 and out["current_projected"] > 0
    mt.plan_team(77, free_transfers=9, max_hits=5, horizon=2, chips=True, planner=fake_planner(calls))
    assert len(calls) == 1                                    # the same plan from the cache


def test_plan_defaults_free_transfers_only_without_chips(live):
    calls = []
    mt.plan_team(77, planner=fake_planner(calls))
    assert calls[0]["max_hits"] == 0 and calls[0]["chips"] == {} and calls[0]["current"]["free_transfers"] == 3


def test_plan_needs_projections(live, monkeypatch):
    monkeypatch.setattr(mt, "load_projections", lambda: None)
    with pytest.raises(mt.PlanUnavailable):
        mt.plan_team(77, planner=fake_planner([]))


def test_the_real_planner_keeps_a_squad_with_nothing_better(live):
    """One real solve on the fixtures: every non-squad player projects the same, so no move pays."""
    out = mt.plan_team(77, max_hits=0, horizon=2)
    assert len(out["squad"]) == 15 and out["hits"] == 0
    assert {p["element"] for p in out["squad"]} <= set(PLAYERS)


# ------------------------------------------------------------------ routes

@pytest.fixture
def client():
    return TestClient(api_main.app)


def test_route_returns_the_team_with_photos(client, live):
    body = client.get("/api/my-team/77").json()
    assert body["entry"]["name"] == "Moose FC" and len(body["squad"]) == 15
    assert "_code" not in body["squad"][0] and "photo" in body["squad"][0]


@pytest.mark.parametrize("error, status", [
    (mt.TeamNotFound("No FPL team has that ID"), 404),
    (mt.TeamNotReady("no squad yet"), 409),
    (mt.FplUnavailable("down"), 502),
])
def test_route_errors(client, monkeypatch, error, status):
    def fail(entry_id, fetch=None):
        raise error
    monkeypatch.setattr(mt, "cached_entry", fail)
    r = client.get("/api/my-team/77")
    assert r.status_code == status and r.json()["detail"]


def test_route_rejects_bad_ids(client):
    assert client.get("/api/my-team/0").status_code == 422
    assert client.get("/api/my-team/abc").status_code == 422
    assert client.get("/api/my-team/100000000").status_code == 422


def test_plan_route(client, live, monkeypatch):
    calls = []
    real = mt.plan_team
    monkeypatch.setattr(mt, "plan_team", lambda *a, **kw: real(*a, planner=fake_planner(calls), **kw))
    r = client.post("/api/my-team/77/plan", json={"max_hits": 1, "chips": False})
    assert r.status_code == 200 and calls[0]["max_hits"] == 1
    assert client.post("/api/my-team/77/plan", json={"max_hits": 3}).status_code == 422
    monkeypatch.setattr(mt, "load_projections", lambda: None)
    mt.clear_cache()
    r = client.post("/api/my-team/77/plan")
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
def test_route_passes_fpl_update_message_through(client, monkeypatch):
    """For a while after every deadline FPL answers 503 "The game is being updated." """
    def updating(entry_id, fetch=None):
        raise mt.FplUnavailable("FPL is updating the game after the deadline. Try again in a few minutes.", updating=True)
    monkeypatch.setattr(mt, "cached_entry", updating)
    r = client.get("/api/my-team/77")
    assert r.status_code == 502 and "updating the game" in r.json()["detail"]


def test_fetch_json_recognises_the_update_page(monkeypatch):
    import httpx

    class Resp:
        status_code, text = 503, '"The game is being updated."'

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: Resp())
    with pytest.raises(mt.FplUnavailable) as exc:
        mt.fetch_json("entry/1/")
    assert exc.value.updating
