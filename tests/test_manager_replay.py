"""scripts/analysis/manager_replay.py: what each deadline's projections may see (nothing from after
it), and the season loop on a small synthetic season (the real planner and game engine)."""
import argparse
import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from ml import odds as od
from ml import points_model as pm
from scripts.analysis import manager_replay as mrp
from services import fpl_manager_planner as planner

SEASON = "2023-24"
UTC = "UTC"


def match_rows():
    """GWs 1-4 of SEASON: A v B (A at home in odd gameweeks) and C v D, which blank in GW3; plus one
    GW1 row of an earlier season."""
    rows = []
    kick = pd.Timestamp("2023-08-12 14:00", tz=UTC)
    players = [(1, "Ann", "MID", "A"), (2, "Bob", "FWD", "B"), (3, "Cy", "DEF", "C"), (4, "Dee", "GK", "D"),
               (5, "Eve", "DEF", "B")]
    for gw in range(1, 5):
        ko = kick + timedelta(days=7 * (gw - 1))
        ab = ("A", "B") if gw % 2 else ("B", "A")
        fixtures = [(10 * gw + 1, *ab, ko), (10 * gw + 2, "C", "D", ko + timedelta(hours=2))]
        if gw == 3:
            fixtures = fixtures[:1]                       # C and D blank in GW3
        for fid, home, away, t in fixtures:
            for element, name, pos, team in players:
                if team in (home, away):
                    rows.append({"season": SEASON, "element": element, "name": name, "position": pos, "team": team,
                                 "fixture": fid, "kickoff_time": t, "GW": gw, "was_home": team == home,
                                 "value": 50 + gw, "selected": 1000 * element + gw, "transfers_in": 10 * gw,
                                 "transfers_out": gw, "minutes": 90, "total_points": gw})
    rows.append({"season": "2022-23", "element": 1, "name": "Ann", "position": "MID", "team": "A", "fixture": 1,
                 "kickoff_time": kick - timedelta(days=300), "GW": 1, "was_home": True, "value": 50, "selected": 1,
                 "transfers_in": 0, "transfers_out": 0, "minutes": 90, "total_points": 2})
    return pd.DataFrame(rows)


def season_rows():
    rows = match_rows()
    return rows[rows["season"] == SEASON].reset_index(drop=True)


# ----------------------------------------------------------------------------- what was known at t

def test_fixtures_and_deadlines_follow_the_season_as_played():
    rows = season_rows()
    fixtures, teams = mrp.api_fixtures(rows)
    assert sorted(teams.values()) == ["A", "B", "C", "D"]
    assert len(fixtures) == 7 and {f["event"] for f in fixtures} == {1, 2, 3, 4}
    gw1 = next(f for f in fixtures if f["id"] == 11)
    assert teams[gw1["team_h"]] == "A" and teams[gw1["team_a"]] == "B" and gw1["kickoff_time"].endswith("Z")
    gw2 = next(f for f in fixtures if f["id"] == 21)
    assert teams[gw2["team_h"]] == "B"
    dl = mrp.deadlines(rows)
    assert dl[1] == pd.Timestamp("2023-08-12 12:30", tz=UTC)          # first kickoff - 90 minutes


def test_history_at_holds_only_earlier_matches():
    history = match_rows()
    first_kickoff = mrp.deadlines(season_rows())[3] + mrp.DEADLINE
    h = mrp.history_at(history, SEASON, 3, first_kickoff)
    current = h[h["season"] == SEASON]
    assert current["GW"].max() == 2 and (current["kickoff_time"] < first_kickoff).all()
    assert (h["season"] == "2022-23").any()
    assert mrp.history_at(history, SEASON, 3, first_kickoff, past_seasons=0)["season"].eq(SEASON).all()


def test_players_at_uses_the_gameweeks_own_pre_deadline_values():
    rows = season_rows()
    status = pd.DataFrame({"GW": [3, 3, 4], "element": [1, 3, 1], "status": ["d", "i", "a"],
                           "chance_of_playing_next_round": [50, 0, np.nan]})
    p = mrp.players_at(rows, 3, status).set_index("element")
    assert sorted(p.index) == [1, 2, 3, 4, 5]                          # C and D blank: from GW2
    assert p.loc[1, "now_cost"] == 53 and p.loc[1, "transfers_in_event"] == 30
    assert p.loc[3, "now_cost"] == 52 and np.isnan(p.loc[3, "transfers_in_event"])
    assert p.loc[1, "status"] == "d" and p.loc[1, "chance_of_playing_next_round"] == 50
    assert p.loc[3, "status"] == "i" and p.loc[2, "status"] == "a"     # no flag: available
    # A player whose club plays but who has no row has left the league
    gone = rows[~((rows["element"] == 2) & (rows["GW"] == 3))]
    assert 2 not in set(mrp.players_at(gone, 3, status)["element"])


def test_extras_at_drops_prices_and_snapshots_from_after_the_deadline():
    rows = match_rows()
    deadline = mrp.deadlines(season_rows())[2]
    odds = pd.DataFrame({"season": SEASON, "home": ["A", "B", "A"], "away": ["B", "A", "B"],
                         "date": [None] * 3, "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "p_over": 0.5})
    odds = odds.iloc[:2]                                               # GW1 A v B, GW2 B v A
    timed = mrp.odds_collection(odds, rows)
    assert timed["collected_at"].notna().all()
    gw2_kickoff = rows.loc[rows["fixture"] == 21, "kickoff_time"].iloc[0]
    assert timed.loc[1, "collected_at"] == od.collected_at(pd.Series([gw2_kickoff])).iloc[0]
    snaps = pd.DataFrame({"season": SEASON, "sha": ["a", "b"], "element": 1, "status": "a",
                          "captured_at": [deadline - timedelta(days=1), deadline]})
    base = pm.ExtraInputs(codes=None, odds=odds, snapshots=snaps, ratings_prior=None)
    later = timed.assign(collected_at=[deadline - timedelta(days=6), deadline + timedelta(minutes=1)])
    known = mrp.extras_at(base, later, deadline)
    assert len(known.odds) == 1 and "collected_at" not in known.odds.columns
    assert list(known.snapshots["sha"]) == ["a"]                     # captured at the deadline: too late


def test_variants_only_set_constants_the_planner_has():
    for name, spec in mrp.VARIANTS.items():
        for key in spec.get("set", {}):
            assert hasattr(planner, key), (name, key)
        assert spec.get("horizon", 1) <= mrp.MAX_HORIZON


def test_planner_constants_are_restored():
    before = planner.DECAY
    with pytest.raises(RuntimeError):
        with mrp.planner_constants({"DECAY": 0.5}):
            assert planner.DECAY == 0.5
            raise RuntimeError
    assert planner.DECAY == before


# ----------------------------------------------------------------------------- the season loop

TEAMS = ["ARS", "AVL", "BOU", "BRE", "BHA", "CHE", "CRY", "EVE", "FUL", "LIV"]
POSITIONS = ["GK"] * 4 + ["DEF"] * 8 + ["MID"] * 8 + ["FWD"] * 5         # elements 1..25
GWS = [10, 11, 13]                                                      # GW12 had no matches


def synthetic_season():
    deadlines = {str(gw): (pd.Timestamp("2023-10-20 10:00", tz=UTC) + timedelta(days=7 * (gw - 10))).isoformat()
                 for gw in GWS}
    proj, preds, actual, prices = [], [], [], []
    for k, pos in enumerate(POSITIONS, start=1):
        team, cost = TEAMS[(k - 1) % len(TEAMS)], 45 + (k % 5) * 5
        for t in GWS:
            for gw in range(t, t + 4):
                proj.append({"deadline_gw": t, "element": k, "name": f"P{k}", "web_name": f"P{k}", "position": pos,
                             "team": team, "now_cost": cost, "gameweek": gw, "fixtures": 1, "opponents": "X",
                             "predicted_points": 2.0 + (k == 25) * 3.0, "availability": 1.0, "status": "a"})
            actual.append({"gameweek": t, "element": k, "points": 2 + (k == 21) * 8, "minutes": 90})
            prices.append({"gameweek": t, "element": k, "now_cost": cost})
        preds += [{"deadline_gw": t, "name": f"P{k}", "position": pos, "team": team,
                   "predicted_points": 2.0 + (k in (21, 22)) * 2.0, "value": cost, "status": "a",
                   "chance_of_playing_next_round": None, "element": k, "opponent": "X", "is_home": True}
                  for t in GWS]
    return {"season": SEASON, "summary": {"season": SEASON, "gameweeks": GWS, "deadlines": deadlines,
                                          "model": {"version": "v4"}},
            "projections": pd.DataFrame(proj), "predictions": pd.DataFrame(preds),
            "actual": pd.DataFrame(actual), "prices": pd.DataFrame(prices)}


def test_a_season_is_planned_scored_and_rolled_over_where_nothing_was_played():
    data = synthetic_season()
    res = mrp.play_season(data, "default", start_gw=10, time_limit=10)
    gws = {g["gameweek"]: g for g in res["gameweeks"]}
    assert list(gws) == [10, 11, 12, 13]
    assert gws[12]["void"] and gws[12]["points"] == 0
    assert all(not g["problems"] for g in res["gameweeks"])
    assert res["total"] == sum(g["points"] for g in res["gameweeks"])
    # The start is the predicted team: P21 and P22 (4 points each) are in it, one of them captain
    assert gws[10]["captain"] in ("P21", "P22") and gws[10]["transfers"] == 0
    # P25 projects 5 a gameweek and isn't owned: worth the GW11 free transfer
    assert gws[11]["transfers"] >= 1
    json.dumps(res)


def test_hold_never_transfers_and_nochips_never_plays_one():
    data = synthetic_season()
    before = (planner.TRANSFER_MARGIN, planner.MAX_HITS_PER_GW)
    hold = mrp.play_season(data, "hold", start_gw=10, time_limit=10)
    assert sum(g["transfers"] for g in hold["gameweeks"]) == 0 and not any(g["chip"] for g in hold["gameweeks"])
    assert (planner.TRANSFER_MARGIN, planner.MAX_HITS_PER_GW) == before     # restored
    nochips = mrp.play_season(data, "nochips", start_gw=10, time_limit=10)
    assert not any(g["chip"] for g in nochips["gameweeks"])


# ----------------------------------------------------------------------------- report helpers

def test_paired_ci_and_projection_quality():
    mean, lo, hi = mrp.paired_ci({"a": np.array([1.0, 1.0, 1.0]), "b": np.array([3.0])})
    assert mean == lo == hi == pytest.approx(1.5)                      # no spread within a season
    mean, lo, hi = mrp.paired_ci({"a": np.array([0.0, 2.0])})
    assert mean == pytest.approx(1.0) and lo == pytest.approx(0.0) and hi == pytest.approx(2.0)
    proj = pd.DataFrame({"deadline_gw": [1, 1, 1, 1], "gameweek": [1, 1, 2, 2], "element": [1, 2, 1, 2],
                         "predicted_points": [4.0, 2.0, 3.0, 1.0]})
    actual = pd.DataFrame({"gameweek": [1, 1, 2], "element": [1, 2, 1], "points": [2, 2, 3]})
    q = mrp.projection_quality(proj, actual, top=1).set_index("step")
    assert list(q.index) == [1, 2]
    assert q.loc[1, "top1_actual"] == 2 and q.loc[1, "top1_bias"] == 2
    assert q.loc[2, "rmse"] == pytest.approx(np.sqrt(0.5))           # element 2 has no GW2 row: 0 points


# ----------------------------------------------------------------------------- several start gameweeks

def test_parse_gws():
    assert mrp.parse_gws("13,1,5,9,5") == [1, 5, 9, 13]
    assert mrp.parse_gws("9") == [9]
    for bad in ("0,5", "39", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            mrp.parse_gws(bad)


def write_projected(tmp_path, data):
    prefix = tmp_path / f"proj_{SEASON}"
    for part in ("projections", "predictions", "actual", "prices"):
        data[part].to_parquet(f"{prefix}.{part}.parquet", index=False)
    (tmp_path / f"proj_{SEASON}.json").write_text(json.dumps(data["summary"]), encoding="utf-8")
    return str(prefix)


def test_plan_plays_every_variant_from_every_start(tmp_path):
    prefix = write_projected(tmp_path, synthetic_season())
    out = tmp_path / "plan.json"
    mrp.main(["plan", "--proj", prefix, "--variants", "default,hold", "--start-gws", "11,10",
              "--time-limit", "10", "--out", str(out)])
    res = json.loads(out.read_text(encoding="utf-8"))
    assert [(r["variant"], r["start_gw"]) for r in res] == [("default", 10), ("hold", 10), ("default", 11),
                                                             ("hold", 11)]
    from_11 = next(r for r in res if r["start_gw"] == 11 and r["variant"] == "default")
    assert [g["gameweek"] for g in from_11["gameweeks"]] == [11, 12, 13]
    assert from_11["gameweeks"][0]["transfers"] == 0                    # a fresh squad from GW11's pick


def fake_path(variant, season, start, points):
    gws = [{"gameweek": start + i, "points": p, "transfers": 1, "hits": 0, "chip": None, "problems": [],
            "optimal": True} for i, p in enumerate(points)]
    return {"variant": variant, "season": season, "start_gw": start, "total": sum(points), "gameweeks": gws}


def test_report_pools_the_paths_and_counts_those_ahead(tmp_path):
    res = [fake_path("default", s, start, [50, 50]) for s in ("2022-23", "2023-24") for start in (1, 5)]
    res += [fake_path("x", "2022-23", 1, [60, 50]), fake_path("x", "2022-23", 5, [55, 50]),
            fake_path("x", "2023-24", 1, [50, 40]), fake_path("x", "2023-24", 5, [52, 50])]
    (tmp_path / "plan_a.json").write_text(json.dumps(res), encoding="utf-8")
    out = tmp_path / "report.md"
    mrp.main(["report", str(tmp_path / "plan_*.json"), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "each from GW1, 5 (4 paths)" in text
    row = next(line for line in text.splitlines() if line.startswith("| x |"))
    assert "| 215 (+15, 2/2) |" in row                  # 110 + 105 against 100 + 100
    assert "| 192 (-8, 1/2) |" in row                   # 90 + 102 against 100 + 100
    assert "| 407 |" in row and "| 3/4 |" in row
    assert "| +0.88 [" in row                           # (10 + 5 - 10 + 2) / 8 gameweek differences
    ref = next(line for line in text.splitlines() if line.startswith("| default |"))
    assert "| 200 |" in ref and "| - |" in ref


# ------------------------------------------------------------------ input variants (project --input-variants)

def test_rating_implied_odds_follow_the_fitted_map():
    coef = ((0.1, 0.9), (0.05, 1.1))
    feats = pd.DataFrame({"season": "2023-24", "fixture": range(80), "team": "A",
                          "rt_xgf": np.linspace(0.6, 2.4, 80), "rt_xga": np.linspace(2.2, 0.5, 80)})
    feats["odds_gf"] = np.exp(coef[0][0] + coef[0][1] * np.log(feats["rt_xgf"]))
    feats["odds_ga"] = np.exp(coef[1][0] + coef[1][1] * np.log(feats["rt_xga"]))
    fitted = od.fit_ratings_map(pd.concat([feats, feats]))        # a player row each: one row a team a match
    assert np.allclose(fitted, coef)
    with pytest.raises(ValueError):
        od.fit_ratings_map(feats.head(10))
    out = od.from_ratings([1.8, 0.7, np.nan], [0.7, 1.8, 1.0], fitted)
    assert list(out.columns) == od.FEATURES
    assert out.loc[0, "odds_gf"] == pytest.approx(np.exp(0.1 + 0.9 * np.log(1.8)))
    assert out.loc[0, "odds_win"] > 0.6 > out.loc[1, "odds_win"]          # the stronger side wins more often
    assert out.loc[0, "odds_cs"] == pytest.approx(np.exp(-out.loc[0, "odds_ga"]))
    assert 0.1 < out.loc[0, "odds_draw"] < 0.35
    win_other = od.from_ratings([0.7], [1.8], ((0.05, 1.1), (0.1, 0.9)))    # the same match from the other side
    assert out.loc[0, "odds_win"] + out.loc[0, "odds_draw"] + win_other.loc[0, "odds_win"] == pytest.approx(1.0, abs=1e-6)
    assert out.loc[2].isna().all()


def test_input_variant_transforms():
    rows = pd.DataFrame({"minutes_r1": [90.0], "minutes_r3": [60.0], "minutes_r6": [30.0],
                         "total_points_r1": [12.0], "total_points_r3": [6.0], "total_points_r6": [4.0],
                         "team_gf": [1.5], "rt_xgf": [1.6], "rt_xga": [0.9],
                         **{c: [0.5] for c in od.FEATURES}})
    smooth = rows.copy()
    mrp._smooth_form(smooth, None)
    assert smooth.loc[0, ["minutes_r1", "minutes_r3", "minutes_r6"]].tolist() == [60.0, 30.0, 30.0]
    assert smooth.loc[0, ["total_points_r1", "total_points_r3"]].tolist() == [6.0, 4.0]
    assert smooth.loc[0, "team_gf"] == 1.5
    none = rows.copy()
    mrp._no_odds(none, None)
    assert none[od.FEATURES].isna().all().all() and none.loc[0, "minutes_r3"] == 60.0
    filled = rows.copy()
    mrp._rating_odds(filled, ((0.0, 1.0), (0.0, 1.0)))
    assert filled.loc[0, "odds_gf"] == pytest.approx(1.6) and filled.loc[0, "odds_ga"] == pytest.approx(0.9)
    assert set(mrp.INPUT_VARIANTS) == {"base", "noodds", "podds", "smooth", "podds_smooth"}


def test_input_variant_points_apply_the_steps_availability():
    rows = pd.DataFrame({"element": [1, 1, 2, 3], "value": [100.0, 100.0, 60.0, 80.0], "rt_xgf": 1.4, "rt_xga": 1.1,
                         "minutes_r1": 90.0, "minutes_r3": 80.0, "minutes_r6": 70.0,
                         **{c: [0.4] * 4 for c in od.FEATURES}})
    players = pd.DataFrame({"element": [1, 2, 3], "status": ["a", "s", "i"],
                            "chance_of_playing_next_round": [np.nan, 0.0, 0.0]})

    def predict(frame):          # 5 points a 10.0m fixture, one more when the odds are missing
        return frame["value"].to_numpy() / 20.0 + frame["odds_win"].isna().to_numpy()
    step1 = mrp.input_variant_points(rows, players, predict, ((0.0, 1.0), (0.0, 1.0)), 10, 10)
    assert step1["step"].tolist() == [1, 1, 1] and step1["element"].tolist() == [1, 2, 3]
    assert step1["pred_base"].tolist() == [10.0, 0.0, 0.0]            # a double summed; out players 0 this gameweek
    assert step1["pred_noodds"].tolist() == [12.0, 0.0, 0.0]
    step3 = mrp.input_variant_points(rows, players, predict, ((0.0, 1.0), (0.0, 1.0)), 10, 12)
    assert step3["pred_base"].tolist() == pytest.approx([10.0, 3.0, 4.0 * 0.35])   # suspended back, injured 0.35
    assert rows["odds_win"].notna().all()                              # the caller's rows are untouched


def test_input_variant_quality_compares_with_base():
    rng = np.random.default_rng(0)
    frames, actual = [], []
    for season in ("2023-24", "2024-25"):
        for deadline in range(1, 9):
            for step in (1, 2):
                truth = rng.gamma(2.0, 1.0, 60)
                frames.append(pd.DataFrame({"season": season, "deadline_gw": deadline, "gameweek": deadline + step - 1,
                                            "step": step, "element": range(60), "pred_base": truth + rng.normal(0, 1.0, 60),
                                            "pred_better": truth + rng.normal(0, 0.2, 60)}))
                if step == 1 or deadline == 8:
                    actual.append(pd.DataFrame({"season": season, "gameweek": deadline + step - 1, "element": range(60),
                                                "points": truth}))
    inputs = pd.concat(frames, ignore_index=True)
    # each (season, gameweek) has one truth: keep the first written
    truth = pd.concat(actual, ignore_index=True).drop_duplicates(["season", "gameweek", "element"])
    inputs = inputs.merge(truth, on=["season", "gameweek", "element"]).assign(
        pred_base=lambda d: d["points"] + (d["pred_base"] - d["pred_base"].mean()) * 0 + rng.normal(0, 1.0, len(d)),
        pred_better=lambda d: d["points"] + rng.normal(0, 0.2, len(d))).drop(columns="points")
    q = mrp.input_variant_quality(inputs, truth, top=10, n_boot=200).set_index(["step", "variant"])
    assert q.loc[(1, "base"), "deadlines"] == 16
    assert q.loc[(1, "better"), "rmse"] < q.loc[(1, "base"), "rmse"]
    assert q.loc[(1, "better"), "rmse_hi"] < 0 < q.loc[(1, "better"), "top_lo"]      # reliably better on both
    assert np.isnan(q.loc[(1, "base"), "rmse_vs_base"])


# ------------------------------------------------------------------ the first squad over the horizon (hstart)

def test_horizon_start_buys_for_the_coming_gameweeks():
    data = synthetic_season()
    gw = GWS[0]
    deadline = data["summary"]["deadlines"][str(gw)]
    preds = data["predictions"][data["predictions"]["deadline_gw"] == gw]
    proj = data["projections"][data["projections"]["deadline_gw"] == gw].drop(columns="deadline_gw")
    one_week = mrp.start_entry(preds, SEASON, gw, deadline, mrp.fm.DEFAULT_RULES)
    over_horizon = mrp.start_entry(preds, SEASON, gw, deadline, mrp.fm.DEFAULT_RULES, proj)
    names = lambda entry: {p["name"] for p in entry["squad"]}  # noqa: E731
    assert len(over_horizon["squad"]) == 15 and over_horizon["transfers"] == [] and over_horizon["chip"] is None
    assert over_horizon["bank_after"] >= 0
    # P25 is the one player projected well for the next four gameweeks (5.0 against 2.0)
    assert "P25" in names(over_horizon)
    captain = next(p for p in over_horizon["squad"] if p["captain"])
    assert captain["name"] == "P25" and captain["role"] == "starter"
    assert len({p["element"] for p in over_horizon["squad"]}) == 15
    assert one_week["gameweek"] == over_horizon["gameweek"] == gw


def test_hstart_plays_a_season_and_a_failed_build_keeps_the_pick(monkeypatch):
    data = synthetic_season()
    res = mrp.play_season(data, "hstart")
    assert [g["gameweek"] for g in res["gameweeks"] if not g.get("void")] == GWS
    assert not any(g["problems"] for g in res["gameweeks"])

    def broken(*args, **kwargs):
        raise ValueError("no projections")
    monkeypatch.setattr(planner, "start_squad", broken)
    assert mrp.play_season(data, "hstart")["gameweeks"][0]["points"] == mrp.play_season(data, "default")["gameweeks"][0]["points"]
