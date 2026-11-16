"""Tests for pipeline/predict_next_gw.py and the live-file selection in services/predictions.py."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ml import odds as od
from ml import points_model as pm
from ml import snapshots as sn
from pipeline import predict_next_gw as png
from services import predictions as preds_service

TEAMS = {1: "Arsenal", 2: "Chelsea", 3: "Ipswich", 4: "Spurs", 5: "Hull City", 6: "Leeds"}


@pytest.fixture(autouse=True)
def hermetic_inputs(tmp_path, monkeypatch):
    """No real snapshot archive (vaastav's 2026-27 captures would join the test's elements),
    and odds only from the test's live dir."""
    monkeypatch.setattr(sn, "SNAPSHOTS_CSV", str(tmp_path / "no_snapshots.csv"))
    monkeypatch.delenv("ODDS_DIR", raising=False)


def fixture(fid, event, home, away, kickoff, finished=False):
    return {"id": fid, "event": event, "team_h": home, "team_a": away,
            "kickoff_time": kickoff, "finished": finished}


GW_FIXTURES = [
    fixture(1, 7, 1, 2, "2026-10-18T15:30:00Z"),
    fixture(2, 7, 3, 4, "2026-10-17T14:00:00Z"),
    fixture(3, 7, 1, 4, "2026-10-21T19:00:00Z"),   # Arsenal and Spurs double
    fixture(4, 8, 5, 6, "2026-10-25T14:00:00Z"),   # other gameweek
    fixture(5, None, 5, 2, None),                  # postponed / unscheduled
]


# ------------------------------------------------------------------ fixtures / gameweek

def test_next_gameweek_picks_is_next_event():
    bootstrap = {"events": [{"id": 6, "is_next": False}, {"id": 7, "is_next": True}]}
    assert png.next_gameweek(bootstrap)["id"] == 7
    assert png.next_gameweek({"events": [{"id": 38, "is_next": False}]}) is None


def test_gameweek_fixtures_single_double_and_blank():
    by_team = png.gameweek_fixtures(GW_FIXTURES, 7, TEAMS)

    assert [e["opponent"] for e in by_team["Arsenal"]] == ["Chelsea", "Spurs"]   # double, kickoff order
    assert [e["is_home"] for e in by_team["Arsenal"]] == [True, True]
    assert [e["opponent"] for e in by_team["Spurs"]] == ["Ipswich", "Arsenal"]
    assert [e["is_home"] for e in by_team["Spurs"]] == [False, False]
    assert by_team["Chelsea"] == [{"fixture_id": 1, "opponent": "Arsenal", "is_home": False,
                                   "kickoff_time": "2026-10-18T15:30:00Z"}]
    assert by_team["Ipswich"][0]["is_home"] is True and by_team["Ipswich"][0]["opponent"] == "Spurs"
    # blank: Hull City and Leeds only play in GW8 / an unscheduled fixture
    assert "Hull City" not in by_team and "Leeds" not in by_team


def test_describe_fixtures_joins_double_gameweek_opponents():
    by_team = png.gameweek_fixtures(GW_FIXTURES, 7, TEAMS)
    assert png.describe_fixtures(by_team["Arsenal"]) == ("Chelsea & Spurs", True, "2026-10-18T15:30:00Z")
    assert png.describe_fixtures(by_team["Chelsea"]) == ("Arsenal", False, "2026-10-18T15:30:00Z")


# ------------------------------------------------------------------ availability

@pytest.mark.parametrize("status, chance, expected", [
    ("a", np.nan, 1.0),
    ("a", None, 1.0),
    ("a", 100.0, 1.0),
    ("d", 75.0, 0.91),      # a doubt: the fitted factor, not the chance (v4 sees the flag already)
    ("d", 50.0, 0.77),
    ("d", 25.0, 0.61),
    ("i", 0.0, 0.0),
    ("i", np.nan, 0.0),     # injured without a chance value: out
    ("s", np.nan, 0.0),
    ("u", np.nan, 0.0),     # left the club / unavailable
    ("u", 100.0, 0.0),
    ("n", np.nan, 0.0),
])
def test_availability_factor(status, chance, expected):
    assert png.availability_factor(status, chance) == pytest.approx(expected)


def test_the_armbands_chance_of_playing_stays_fpls():
    from services.season_service import chance_of_playing
    assert chance_of_playing("d", 50.0) == 0.5 and chance_of_playing("d", 25.0) == 0.25
    assert chance_of_playing("i", np.nan) == 0.0 and chance_of_playing("a", None) == 1.0


def test_promoted_teams():
    assert png.promoted_teams(["Arsenal", "Chelsea"], ["Arsenal", "Hull City", "Chelsea", "Hull City"]) == ["Hull City"]
    assert png.promoted_teams([], ["Arsenal"]) == []


# ------------------------------------------------------------------ output assembly

def players_frame():
    return pd.DataFrame([
        {"element": 1, "name": "Fit Gunner", "team": "Arsenal", "position": "MID", "status": "a",
         "chance_of_playing_next_round": np.nan, "news": "", "now_cost": 100},
        {"element": 2, "name": "Doubtful Blue", "team": "Chelsea", "position": "DEF", "status": "d",
         "chance_of_playing_next_round": 50.0, "news": "Knock - 50% chance of playing", "now_cost": 50},
        {"element": 3, "name": "Injured Gunner", "team": "Arsenal", "position": "DEF", "status": "i",
         "chance_of_playing_next_round": 0.0, "news": "Back injury", "now_cost": 60},
        {"element": 4, "name": "Blank Tiger", "team": "Hull City", "position": "FWD", "status": "a",
         "chance_of_playing_next_round": np.nan, "news": "", "now_cost": 55},
        {"element": 5, "name": "Gone Gunner", "team": "Arsenal", "position": "GK", "status": "u",
         "chance_of_playing_next_round": np.nan, "news": "Joined X on loan", "now_cost": 40},
    ]).assign(
        # the status file's FPL code, transfers since the last deadline and capture time
        code=lambda d: d["element"] + 9000,
        transfers_in_event=[50000, 100, 10, 2000, 0], transfers_out_event=[1000, 8000, 30000, 500, 9000],
        fetched_at="2026-10-16T12:00:00Z",
    )


def test_assemble_output_applies_availability_blank_and_schema():
    by_team = png.gameweek_fixtures(GW_FIXTURES, 7, TEAMS)
    predictions = {"1": {"points": 9.0, "method": "xgboost_v3"}, "2": {"points": 4.0, "method": "xgboost_v3"},
                   "3": {"points": 5.0, "method": "xgboost_v3"}, "5": {"points": 3.0, "method": "xgboost_v3"}}
    out = png.assemble_output(players_frame(), predictions, by_team, 7, "2026-10-17T10:00:00Z", ["Hull City"])
    rows = out.set_index("name")

    assert list(out.columns) == png.OUTPUT_COLUMNS
    assert out["predicted_points"].is_monotonic_decreasing
    assert rows.loc["Fit Gunner", "predicted_points"] == 9.0
    assert rows.loc["Fit Gunner", "opponent"] == "Chelsea & Spurs"
    assert rows.loc["Fit Gunner", "fixtures"] == 2
    assert rows.loc["Doubtful Blue", "predicted_points"] == 3.08         # 4 x 0.77 (a 50% doubt)
    assert rows.loc["Injured Gunner", "predicted_points"] == 0.0
    assert rows.loc["Injured Gunner", "news"] == "Back injury"
    assert rows.loc["Gone Gunner", "predicted_points"] == 0.0
    blank = rows.loc["Blank Tiger"]
    assert blank["predicted_points"] == 0.0 and blank["fixtures"] == 0
    assert blank["opponent"] == png.NO_FIXTURE_OPPONENT
    assert blank["kickoff_time"] == "2026-10-17T10:00:00Z"              # never NaN for API consumers
    assert blank["prediction_method"] == png.NO_FIXTURE_METHOD
    assert bool(blank["is_promoted_team"]) is True
    assert set(out["gameweek"]) == {7}
    assert out[["opponent", "kickoff_time", "value", "gameweek"]].notna().all().all()


# ------------------------------------------------------------------ history + model rows

LIVE_COLUMNS = ["name", "position", "team", "element", "kickoff_time", "GW", "was_home", "total_points",
                "value", "team_h_score", "team_a_score", "fixture", "minutes", "creativity", "ict_index",
                "influence", "threat", "clearances_blocks_interceptions", "defensive_contribution",
                "recoveries", "tackles"]


def live_rows(element, name, team, gws, minutes=90, points=6):
    return [{
        "name": name, "position": "MID", "team": team, "element": element,
        "kickoff_time": f"2026-09-{gw * 3:02d}T15:00:00Z", "GW": gw, "was_home": gw % 2 == 0,
        "total_points": points + gw, "value": 80, "team_h_score": 2, "team_a_score": 1, "fixture": gw * 10 + element,
        "minutes": minutes, "creativity": 20.0, "ict_index": 8.0, "influence": 30.0, "threat": 25.0,
        "clearances_blocks_interceptions": 1, "defensive_contribution": 2, "recoveries": 3, "tackles": 1,
    } for gw in gws]


@pytest.fixture
def live_dir(tmp_path):
    rows = (live_rows(1, "Fit Gunner", "Arsenal", [1, 2, 3, 4, 5, 6, 7])
            + live_rows(2, "Doubtful Blue", "Chelsea", [1, 2, 3])
            + live_rows(3, "Injured Gunner", "Arsenal", [1, 2]))
    pd.DataFrame(rows, columns=LIVE_COLUMNS).to_csv(tmp_path / "FPL_2026_2027.csv", index=False)
    players_frame().to_csv(tmp_path / "player_status_2026_27.csv", index=False)
    hist = pd.DataFrame([{
        "season_x": "2025-26", "name": "Fit Gunner", "position": "MID", "team_x": "Arsenal",
        "kickoff_time": "2026-05-10T15:00:00Z", "GW": 37, "was_home": True, "total_points": 2, "value": 75,
        "team_h_score": 1, "team_a_score": 1, "fixture": 370, "minutes": 90, "creativity": 1.0,
        "ict_index": 1.0, "influence": 1.0, "threat": 1.0, "expected_goals": 1.2,
    }, {
        "season_x": "2025-26", "name": "Someone Else", "position": "FWD", "team_x": "Leeds",
        "kickoff_time": "2026-05-10T15:00:00Z", "GW": 37, "was_home": False, "total_points": 2, "value": 60,
        "team_h_score": 1, "team_a_score": 1, "fixture": 371, "minutes": 90, "creativity": 1.0,
        "ict_index": 1.0, "influence": 1.0, "threat": 1.0, "expected_goals": 0.8,
    }])
    hist.to_csv(tmp_path / "history.csv", index=False)
    return tmp_path


def test_load_history_excludes_predicted_gameweek_and_later(live_dir):
    history = png.load_history(str(live_dir / "FPL_2026_2027.csv"), "2026-27", before_gw=6,
                               before_time="2026-10-17T10:00:00Z", history_csv=str(live_dir / "history.csv"))
    current = history[history["season"] == "2026-27"]
    assert current["GW"].max() == 5                     # GW6/GW7 rows never feed features
    assert len(history[history["season"] == "2025-26"]) == 2
    assert png.previous_season_teams(history, "2026-27") == ["Arsenal", "Leeds"]

    # kickoff cutoff also applies (a later-round match played early would be dropped)
    history = png.load_history(str(live_dir / "FPL_2026_2027.csv"), "2026-27", before_gw=8,
                               before_time="2026-09-10T00:00:00Z", history_csv=str(live_dir / "history.csv"))
    assert history.loc[history["season"] == "2026-27", "GW"].max() == 3


class RecordingModel:
    """Stands in for the v3 booster: constant points, keeps the feature rows it saw."""

    def __init__(self, value):
        self.value = value
        self.seen = []

    def __call__(self, frame):
        self.seen.append(frame.copy())
        return np.full(len(frame), self.value)


def test_predict_players_sums_double_gameweek_and_uses_prior_matches_only(live_dir):
    players = players_frame()
    history = png.load_history(str(live_dir / "FPL_2026_2027.csv"), "2026-27", before_gw=7,
                               before_time=None, history_csv=str(live_dir / "history.csv"))
    by_team = png.gameweek_fixtures(GW_FIXTURES, 7, TEAMS)
    model = RecordingModel(2.5)
    out = png.predict_players(players, history, by_team, "2026-27", 7, model)

    assert out["1"]["points"] == pytest.approx(5.0)      # Arsenal double: 2 x 2.5
    assert out["2"]["points"] == pytest.approx(2.5)
    assert "4" not in out                                 # blank (Hull City): no model row
    assert out["5"]["points"] == pytest.approx(5.0)      # no match rows at all: still predicted
    assert {v["method"] for v in out.values()} == {png.MODEL_METHOD}

    rows = pd.concat(model.seen)
    fit = rows[rows["name"] == "Fit Gunner"].sort_values("kickoff_time")
    assert list(fit["was_home"]) == [1.0, 1.0]
    assert set(fit["fixtures_in_gw"]) == {2.0}
    assert set(fit["GW"]) == {7}
    # Form only from matches before GW7 (GW4-6 points are 10, 11, 12), identical for both
    # fixtures of the double gameweek
    assert list(fit["total_points_r3"]) == pytest.approx([11.0, 11.0])
    assert list(fit["opponent"]) == ["Chelsea", "Spurs"]


class FakeClient:
    def bootstrap(self):
        return {
            "events": [{"id": 6, "is_next": False, "deadline_time": "2026-08-21T17:30:00Z"},
                       {"id": 7, "is_next": True, "deadline_time": "2026-10-17T10:00:00Z"}],
            "teams": [{"id": i, "name": "Ipswich Town" if n == "Ipswich" else n} for i, n in TEAMS.items()],
        }

    def fixtures(self):
        return GW_FIXTURES

    def close(self):
        pass


def test_predict_next_gameweek_end_to_end(live_dir):
    # Real served model file (v4, or v3 until one exists), end to end; no odds download
    summary = png.predict_next_gameweek(str(live_dir), client=FakeClient(),
                                        history_csv=str(live_dir / "history.csv"), fetch_odds=False)
    out = pd.read_csv(live_dir / "next_gw_predictions.csv")
    records = json.loads((live_dir / "next_gw_predictions.json").read_text(encoding="utf-8"))

    assert summary["gameweek"] == 7 and summary["players"] == 5 == len(out) == len(records)
    assert summary["extra_inputs"] == "ok"
    assert summary["blank_teams"] == ["Hull City"]
    assert summary["double_gameweek_players"] == 3        # the three Arsenal players
    assert summary["history_last_kickoff"] < "2026-10-17"
    assert not any(p.endswith(".tmp") for p in os.listdir(live_dir))
    assert summary["model_pick"] is None                  # GW7's deadline has passed: its pick is final
    for col in ["name", "team", "position", "opponent", "is_home", "kickoff_time", "gameweek",
                "predicted_points", "value", "prediction_method"]:
        assert col in out.columns
    rows = out.set_index("name")
    assert rows.loc["Injured Gunner", "predicted_points"] == 0
    assert rows.loc["Gone Gunner", "predicted_points"] == 0
    assert rows.loc["Fit Gunner", "predicted_points"] > 0
    assert rows.loc["Fit Gunner", "opponent"] == "Chelsea & Spurs"

    # The model's inputs, for the /model page's explanations (services/explain.py)
    assert summary["model_inputs"] == str(live_dir / png.FEATURES_NAME)
    inputs = pd.read_csv(live_dir / png.FEATURES_NAME)
    assert set(png.FEATURE_ID_COLUMNS + ["model_points"] + pm.feature_columns()) <= set(inputs.columns)
    fit = inputs[inputs["name"] == "Fit Gunner"]
    assert list(fit["opponent"]) == ["Chelsea", "Spurs"]
    assert fit["model_points"].clip(lower=0).sum() == pytest.approx(rows.loc["Fit Gunner", "predicted_points"], abs=0.01)


# ------------------------------------------------------------------ v4 inputs, odds, shadow

# football-data.co.uk's fixtures.csv for GW7 (Tottenham is Spurs in the graph)
ODDS_FIXTURES_CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5\n"
    "E0,17/10/2026,15:00,Ipswich,Tottenham,3.40,3.50,2.10,1.90,1.90\n"
    "E0,18/10/2026,16:30,Arsenal,Chelsea,1.80,3.80,4.50,1.70,2.10\n"
    "E0,21/10/2026,20:00,Arsenal,Tottenham,1.60,4.20,5.00,1.60,2.30\n"
)


def write_odds(live_dir, text=ODDS_FIXTURES_CSV):
    d = live_dir / "odds"
    d.mkdir(exist_ok=True)
    (d / od.FIXTURES_FILE).write_text(text, encoding="utf-8")
    return d


def test_model_rows_carry_every_v4_input(live_dir):
    write_odds(live_dir)
    status_csv = str(live_dir / "player_status_2026_27.csv")
    players = png._load_players(status_csv)
    history = png.load_history(str(live_dir / "FPL_2026_2027.csv"), "2026-27", before_gw=7,
                               before_time="2026-10-17T14:00:00Z", history_csv=str(live_dir / "history.csv"))
    extras = png.load_extras(str(live_dir), "2026-27", status_csv, str(live_dir / "history.csv"))
    rows = png.model_rows(players, history, png.gameweek_fixtures(GW_FIXTURES, 7, TEAMS), "2026-27", 7, extras)

    assert set(pm.feature_columns("v4")) <= set(rows.columns)
    assert len(rows) == 7 and set(rows["element"]) == {1, 2, 3, 5}      # Hull City blank
    r = rows.set_index(["name", "opponent"])
    # Odds count only if priced before the deadline (here Sunday's first kickoff - 90 min):
    # Sunday's match on Friday, the Wednesday match only on Tuesday
    assert 0.45 < r.loc[("Fit Gunner", "Chelsea"), "odds_win"] < 0.6
    assert r.loc[("Doubtful Blue", "Arsenal"), "odds_win"] < 0.3
    assert np.isnan(r.loc[("Fit Gunner", "Spurs"), "odds_win"])
    # The status file is the upcoming gameweek's availability snapshot
    assert r.loc[("Doubtful Blue", "Arsenal"), "snap_doubtful"] == 1
    assert r.loc[("Doubtful Blue", "Arsenal"), "snap_chance"] == 50
    assert r.loc[("Injured Gunner", "Chelsea"), "snap_unavailable"] == 1
    assert r.loc[("Fit Gunner", "Chelsea"), "snap_age_days"] == pytest.approx(2 + 2 / 24, abs=0.01)
    # Transfers since the last deadline, ranked within the gameweek; team ratings for everyone
    assert r.loc[("Fit Gunner", "Chelsea"), "tr_in_rank"] == 1.0
    assert rows[["tr_bal_rank", "rt_att", "rt_opp_def"]].notna().all().all()
    assert png.input_coverage(rows) == {"odds": round(4 / 7, 3), "snapshot": 1.0, "transfers": 1.0, "ratings": 1.0}


def test_odds_download_failure_degrades_to_nan_odds(live_dir, monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("network is unreachable")
    monkeypatch.setattr(od, "fetch_season", offline)
    monkeypatch.setattr(od, "fetch_fixtures", offline)
    # An unreadable leftover (an error page saved by hand) is skipped too, with a warning
    odds = write_odds(live_dir, "<html>Service unavailable</html>\n")
    old = (datetime.now() - timedelta(days=1)).timestamp()
    os.utime(odds / od.FIXTURES_FILE, (old, old))

    model = RecordingModel(2.0)
    summary = png.predict_next_gameweek(str(live_dir), client=FakeClient(),
                                        history_csv=str(live_dir / "history.csv"), predict_fn=model)
    assert summary["odds"]["fixtures.csv"].startswith("failed")
    assert summary["odds"]["E0_2627.csv"].startswith("failed")
    assert summary["input_coverage"]["odds"] == 0.0 and summary["input_coverage"]["snapshot"] == 1.0
    assert summary["shadow"] == {"model": None, "pick": "skipped: no shadow model given"}
    assert len(pd.read_csv(live_dir / png.OUTPUT_NAME)) == 5
    seen = pd.concat(model.seen)
    assert set(pm.feature_columns("v4")) <= set(seen.columns) and seen["odds_win"].isna().all()


def test_unreadable_extra_input_never_stops_the_run(live_dir, monkeypatch):
    """A malformed element_snapshots file (here without status / captured_at) and failing FPL codes:
    the run still predicts, snapshots fall back to the status file (all the upcoming rows use), and
    the summary says what happened."""
    archive = live_dir / "element_snapshots" / "2026-27"
    archive.mkdir(parents=True)
    (archive / "gw07.csv").write_text("element,code,GW\n1,9001,7\n", encoding="utf-8")

    def broken(*args, **kwargs):
        raise ValueError("codes file is corrupt")
    monkeypatch.setattr(pm, "load_player_codes", broken)

    model = RecordingModel(2.0)
    summary = png.predict_next_gameweek(str(live_dir), client=FakeClient(), history_csv=str(live_dir / "history.csv"),
                                        predict_fn=model, fetch_odds=False)
    assert set(summary["extra_inputs"]) == {"codes", "snapshots"}
    assert summary["extra_inputs"]["codes"] == "failed: codes file is corrupt"
    assert summary["extra_inputs"]["snapshots"].startswith("status file only; failed: ")
    assert summary["input_coverage"]["snapshot"] == 1.0 and summary["input_coverage"]["ratings"] == 1.0
    seen = pd.concat(model.seen).set_index(["name", "opponent"])
    assert seen.loc[("Doubtful Blue", "Arsenal"), "snap_chance"] == 50
    assert len(pd.read_csv(live_dir / png.OUTPUT_NAME)) == 5


SQUAD_CLUBS = ["Arsenal", "Chelsea", "Ipswich", "Spurs", "Hull City", "Leeds"]


def squad_frame():
    """Three players per club (GK or FWD, DEF, MID), enough for a 15-man squad, plus an injured keeper."""
    rows = []
    for k, team in enumerate(SQUAD_CLUBS):
        for j, pos in enumerate(["GK" if k < 3 else "FWD", "DEF", "MID"]):
            e = 100 + 3 * k + j
            rows.append({"element": e, "name": f"{team} {pos}", "team": team, "position": pos, "status": "a",
                         "chance_of_playing_next_round": np.nan, "news": "", "now_cost": 45 + (7 * e) % 20})
    rows.append({"element": 200, "name": "Crocked Keeper", "team": "Leeds", "position": "GK", "status": "i",
                 "chance_of_playing_next_round": 0.0, "news": "Knee injury", "now_cost": 40})
    fetched = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return pd.DataFrame(rows).assign(code=lambda d: d["element"] + 9000, transfers_in_event=100,
                                     transfers_out_event=50, fetched_at=fetched)


class UpcomingClient:
    """GW9, with every club playing, two days from now (its deadline is ahead)."""

    def __init__(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.deadline = iso(now + timedelta(days=2))
        kickoff = iso(now + timedelta(days=2, hours=2))
        self._fixtures = [fixture(90 + i, 9, h, a, kickoff) for i, (h, a) in enumerate([(1, 2), (3, 4), (5, 6)])]

    def bootstrap(self):
        return {
            "events": [{"id": 8, "is_next": False, "deadline_time": "2026-10-24T10:00:00Z"},
                       {"id": 9, "is_next": True, "deadline_time": self.deadline}],
            "teams": [{"id": i, "name": "Ipswich Town" if n == "Ipswich" else n} for i, n in TEAMS.items()],
        }

    def fixtures(self):
        return self._fixtures

    def close(self):
        pass


def test_shadow_pick_is_saved_next_to_the_live_pick(live_dir, monkeypatch):
    from services import model_review as mr

    class TickingClock(datetime):
        """model_review's clock moves a second per reading: the pair must still share one time."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        reads = 0

        @classmethod
        def now(cls, tz=None):
            cls.reads += 1
            return cls.start + timedelta(seconds=cls.reads)
    monkeypatch.setattr(mr, "datetime", TickingClock)
    squad_frame().to_csv(live_dir / "player_status_2026_27.csv", index=False)
    summary = png.predict_next_gameweek(
        str(live_dir), client=UpcomingClient(), history_csv=str(live_dir / "history.csv"), fetch_odds=False,
        predict_fn=lambda f: f["value"].to_numpy() / 20.0,          # the live model likes pricey players
        shadow_fn=lambda f: 6.0 - f["value"].to_numpy() / 20.0)     # the shadow likes cheap ones
    picks_dir = live_dir / "model_picks" / "2026-27"
    assert summary["model_pick"] == str(picks_dir / "gw09.json")
    assert summary["shadow"] == {"model": pm.SHADOW_VERSION, "pick": str(picks_dir / "shadow" / "gw09.json")}

    live_pick = json.loads((picks_dir / "gw09.json").read_text(encoding="utf-8"))
    shadow_pick = json.loads((picks_dir / "shadow" / "gw09.json").read_text(encoding="utf-8"))
    assert set(live_pick) == set(shadow_pick)
    assert [set(p) for p in live_pick["players"]] == [set(p) for p in shadow_pick["players"]]
    assert (live_pick["model"], shadow_pick["model"]) == (pm.MODEL_VERSION, pm.SHADOW_VERSION)
    assert live_pick["archived_at"] == shadow_pick["archived_at"]      # one run's pair, for the ledger
    assert len(shadow_pick["players"]) == 15
    assert {p["name"] for p in live_pick["players"]} != {p["name"] for p in shadow_pick["players"]}
    # Same availability multiplier for the shadow; the site keeps reading the live predictions
    shadow_preds = pd.read_csv(picks_dir / "shadow" / "gw09_predictions.csv").set_index("name")
    assert shadow_preds.loc["Crocked Keeper", "predicted_points"] == 0
    assert set(shadow_preds["prediction_method"]) == {f"xgboost_{pm.SHADOW_VERSION}"}
    assert set(pd.read_csv(live_dir / png.OUTPUT_NAME)["prediction_method"]) == {png.MODEL_METHOD}


def test_failing_shadow_never_touches_the_live_predictions(live_dir):
    squad_frame().to_csv(live_dir / "player_status_2026_27.csv", index=False)

    def broken(frame):
        raise RuntimeError("shadow model exploded")
    summary = png.predict_next_gameweek(str(live_dir), client=UpcomingClient(),
                                        history_csv=str(live_dir / "history.csv"), fetch_odds=False,
                                        predict_fn=lambda f: f["value"].to_numpy() / 20.0, shadow_fn=broken)
    assert summary["shadow"]["pick"] == "failed"
    assert os.path.exists(summary["model_pick"])
    assert not (live_dir / "model_picks" / "2026-27" / "shadow").exists()
    assert len(pd.read_csv(live_dir / png.OUTPUT_NAME)) == 19


# ------------------------------------------------------------------ projections (next 4 gameweeks)

@pytest.mark.parametrize("status, chance, expected", [
    ("a", np.nan, [1.0, 1.0, 1.0, 1.0]),
    # Fitted on flagged players' next gameweeks (ml.snapshots.HORIZON_POINTS): a doubt lingers,
    ("d", 50.0, [0.77, 0.7, 0.75, 1.0]),
    ("d", 25.0, [0.61, 0.5, 0.55, 0.7]),
    ("d", 75.0, [0.91, 0.9, 0.9, 0.95]),
    # an injury with no return date keeps most of its cost,
    ("i", 0.0, [0.0, 0.25, 0.35, 0.65]),
    ("i", np.nan, [0.0, 0.25, 0.35, 0.65]),
    # and a ban is usually one match
    ("s", 0.0, [0.0, 0.85, 1.0, 1.0]),
    ("u", np.nan, [0.0, 0.0, 0.0, 0.0]),    # left the club: never
    ("u", 100.0, [0.0, 0.0, 0.0, 0.0]),
    ("n", np.nan, [0.0, 0.0, 0.0, 0.0]),
])
def test_horizon_availability(status, chance, expected):
    assert [png.horizon_availability(status, chance, step) for step in (1, 2, 3, 4)] == pytest.approx(expected)
    assert png.horizon_availability(status, chance, 1) == png.availability_factor(status, chance)
    assert png.horizon_availability(status, chance, 7) == pytest.approx(expected[-1])   # past the table: its last value


def test_horizon_chance_of_playing_is_fpls():
    """The armband's chance is FPL's own for the next gameweek; later gameweeks share the fitted factor."""
    from services.season_service import chance_of_playing
    chances = [png.horizon_availability("d", 50.0, step, factor=chance_of_playing) for step in (1, 2, 3)]
    assert chances == [0.5, 0.7, 0.75]
    assert [png.horizon_availability("i", 0.0, step, factor=chance_of_playing) for step in (1, 2, 3)] == [0.0, 0.25, 0.35]


@pytest.mark.parametrize("news, expected", [
    ("Hamstring injury - Expected back 6 Dec", date(2026, 12, 6)),
    ("Suspended until 22 Nov", date(2026, 11, 22)),
    ("Knee injury - Expected back 03 January", date(2027, 1, 3)),     # the year nearest to today
    ("Unspecified injury - Expected back 31 Oct", date(2026, 10, 31)),  # already past: still this year
    ("Knee injury - Unknown return date", None),
    ("Has joined Oxford United on loan until January", None),         # no day
    ("Knock - 75% chance of playing", None),
    ("Expected back 31 Feb", None),                                   # not a date
    ("", None), (None, None), (float("nan"), None),
])
def test_return_date_from_fpl_news(news, expected):
    assert png.return_date(news, date(2026, 11, 15)) == expected


def test_horizon_availability_follows_the_return_date_in_the_news():
    days = [date(2026, 11, 21), date(2026, 11, 28), date(2026, 12, 2), date(2026, 12, 5)]

    def horizon(status, back, dates=days):
        return [png.horizon_availability(status, 0.0, step, back=back, dates=dates) for step in (1, 2, 3, 4)]

    # Injured until 1 Dec: out for the first two gameweeks, then half and three quarters
    assert horizon("i", date(2026, 12, 1)) == [0.0, 0.0, 0.5, 0.75]
    # Back before the second gameweek: half, three quarters, then full
    assert horizon("i", date(2026, 11, 25)) == [0.0, 0.5, 0.75, 1.0]
    # A long injury: nothing in the whole horizon
    assert horizon("i", date(2027, 1, 20)) == [0.0, 0.0, 0.0, 0.0]
    # A ban ends on its day: full from then on
    assert horizon("s", date(2026, 11, 22)) == [0.0, 1.0, 1.0, 1.0]
    assert horizon("s", date(2026, 12, 1)) == [0.0, 0.0, 1.0, 1.0]
    # A date that has already passed while he is still flagged says nothing: the fitted factors
    assert horizon("i", date(2026, 11, 14)) == [0.0, 0.25, 0.35, 0.65]
    assert horizon("i", date(2026, 11, 21)) == [0.0, 0.25, 0.35, 0.65]
    # No dates for his club's gameweeks: the fitted factors too
    assert horizon("i", date(2026, 12, 1), dates=[]) == [0.0, 0.25, 0.35, 0.65]
    assert horizon("i", date(2026, 12, 1), dates=[days[0], None, None, None]) == [0.0, 0.25, 0.35, 0.65]
    # A doubt with a date keeps its fitted factors (the date belongs to players who are out)
    assert [png.horizon_availability("d", 75.0, step, back=date(2026, 12, 1), dates=days)
            for step in (1, 2, 3)] == pytest.approx([0.91, 0.9, 0.9])
    # A player who left never returns, whatever the news says
    assert [png.horizon_availability("u", None, step, back=date(2026, 11, 25), dates=days)
            for step in (1, 2, 3)] == [0.0, 0.0, 0.0]


def test_gameweek_days_are_each_clubs_first_kickoff():
    by_team = {"Arsenal": [{"kickoff_time": "2026-11-03T19:45:00Z"}, {"kickoff_time": "2026-10-31T15:00:00Z"}],
               "Leeds": [{"kickoff_time": None}]}
    days, deadline = png.gameweek_days(by_team, "2026-10-31T13:30:00Z")
    assert days == {"Arsenal": date(2026, 10, 31)} and deadline == date(2026, 10, 31)
    assert png.gameweek_days({}, None) == ({}, None)


def test_later_gameweeks_are_scored_as_available():
    """A gameweek after the next one is predicted with an unflagged player's availability features:
    the flag is about the next deadline, and horizon_availability carries the rest."""
    rows = pd.DataFrame({"snap_unavailable": [1.0, 0.0, np.nan], "snap_doubtful": [0.0, 1.0, np.nan],
                         "snap_chance": [0.0, 50.0, np.nan], "snap_news": [1.0, 1.0, np.nan],
                         "snap_age_days": [2.0, 2.0, np.nan], "minutes_r3": [90.0, 60.0, 0.0]})
    out = sn.as_available(rows.copy())
    assert out["snap_unavailable"].tolist()[:2] == [0.0, 0.0] and out["snap_doubtful"].tolist()[:2] == [0.0, 0.0]
    assert out["snap_chance"].tolist()[:2] == [100.0, 100.0] and out["snap_news"].tolist()[:2] == [0.0, 0.0]
    assert out.iloc[2].drop("minutes_r3").isna().all()                  # no snapshot: still none
    assert out["snap_age_days"].tolist()[:2] == [2.0, 2.0] and out["minutes_r3"].tolist() == [90.0, 60.0, 0.0]
    assert sn.as_available(pd.DataFrame({"value": [1.0]})).columns.tolist() == ["value"]   # v3 rows: untouched


def test_horizon_events_stop_at_the_season_end():
    bootstrap = {"events": [{"id": g} for g in range(30, 39)]}
    assert [e["id"] for e in png.horizon_events(bootstrap, {"id": 32})] == [32, 33, 34, 35]
    assert [e["id"] for e in png.horizon_events(bootstrap, {"id": 37})] == [37, 38]


# GW8 played; GW9 next (deadline passed, so no pick is saved); GW10 Arsenal and Chelsea double
# while Hull City and Leeds blank; GW12 after an international break
HORIZON_FIXTURES = [
    fixture(80, 8, 1, 5, "2026-10-17T15:00:00Z", finished=True),
    fixture(81, 8, 2, 3, "2026-10-17T15:00:00Z", finished=True),
    fixture(82, 8, 4, 6, "2026-10-18T15:00:00Z", finished=True),
    fixture(90, 9, 1, 2, "2026-10-24T15:00:00Z"),
    fixture(91, 9, 3, 4, "2026-10-24T15:00:00Z"),
    fixture(92, 9, 5, 6, "2026-10-25T14:00:00Z"),
    fixture(100, 10, 4, 1, "2026-10-31T15:00:00Z"),
    fixture(101, 10, 2, 3, "2026-10-31T15:00:00Z"),
    fixture(102, 10, 1, 2, "2026-11-03T19:45:00Z"),
    fixture(110, 11, 1, 3, "2026-11-07T15:00:00Z"),
    fixture(111, 11, 2, 4, "2026-11-07T15:00:00Z"),
    fixture(112, 11, 6, 5, "2026-11-08T14:00:00Z"),
    fixture(120, 12, 3, 1, "2026-11-21T15:00:00Z"),
    fixture(121, 12, 4, 2, "2026-11-21T15:00:00Z"),
    fixture(122, 12, 5, 6, "2026-11-21T15:00:00Z"),
    fixture(130, 13, 1, 6, "2026-11-28T15:00:00Z"),
]


HORIZON_DEADLINES = {8: "2026-10-17T13:30:00Z", 9: "2026-10-24T13:30:00Z", 10: "2026-10-31T13:30:00Z",
                     11: "2026-11-07T13:30:00Z", 12: "2026-11-21T13:30:00Z", 13: "2026-11-28T13:30:00Z"}


class HorizonClient:
    def bootstrap(self):
        return {
            "events": [{"id": g, "is_next": g == 9, "deadline_time": d} for g, d in HORIZON_DEADLINES.items()],
            "teams": [{"id": i, "name": "Ipswich Town" if n == "Ipswich" else n} for i, n in TEAMS.items()],
        }

    def fixtures(self):
        return HORIZON_FIXTURES

    def close(self):
        pass


def test_rest_days_from_the_fixture_list():
    kickoffs = png.team_kickoffs(HORIZON_FIXTURES, TEAMS)
    assert png.rest_days("Arsenal", "2026-10-31T15:00:00Z", kickoffs) == pytest.approx(7.0)
    assert png.rest_days("Arsenal", "2026-11-03T19:45:00Z", kickoffs) == pytest.approx(3 + 4.75 / 24)
    assert png.rest_days("Arsenal", "2026-11-21T15:00:00Z", kickoffs) == pytest.approx(14.0)
    assert np.isnan(png.rest_days("Arsenal", "2026-10-17T15:00:00Z", kickoffs))     # nothing before
    assert png.rest_days("Leeds", "2027-01-01T15:00:00Z", kickoffs) == 30.0          # clipped like the feature


def test_projections_cover_four_gameweeks_and_start_with_the_live_predictions(live_dir):
    write_odds(live_dir)       # prices the GW7 matches only: every later gameweek predicts without odds
    seen = []

    def by_price(frame):               # 5 points a fixture for a 10.0m player
        seen.append(frame.copy())
        return frame["value"].to_numpy() / 20.0
    summary = png.predict_next_gameweek(str(live_dir), client=HorizonClient(), history_csv=str(live_dir / "history.csv"),
                                        predict_fn=by_price, fetch_odds=False)
    path = live_dir / png.PROJECTIONS_NAME
    assert summary["projections"]["seconds"] >= 0
    assert {k: v for k, v in summary["projections"].items() if k != "seconds"} == {
        "output": str(path), "horizon": 4, "gameweeks": [9, 10, 11, 12], "failed": None}
    proj = pd.read_csv(path)
    assert list(proj.columns) == png.PROJECTION_COLUMNS
    assert len(proj) == 4 * 5 and not any(p.endswith(".tmp") for p in os.listdir(live_dir))

    # GW+1 is exactly next_gw_predictions
    live = pd.read_csv(live_dir / png.OUTPUT_NAME).set_index("element")
    gw9 = proj[proj["gameweek"] == 9].set_index("element")
    assert gw9["predicted_points"].to_dict() == live.loc[gw9.index, "predicted_points"].to_dict()

    pts = proj.pivot(index="name", columns="gameweek", values="predicted_points")
    assert pts.loc["Fit Gunner"].tolist() == [5.0, 10.0, 5.0, 5.0]          # GW10 double summed
    # A 50% doubt lingers: x0.7 of the GW10 double, x0.75, then full
    assert pts.loc["Doubtful Blue"].tolist() == pytest.approx([round(2.5 * 0.77, 2), 3.5, 1.88, 2.5], abs=0.006)
    # Out with no return date in his news: x0.25 of the double, x0.35, x0.65
    assert pts.loc["Injured Gunner"].tolist() == pytest.approx([0.0, 1.5, 1.05, 1.95])
    assert pts.loc["Gone Gunner"].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert pts.loc["Blank Tiger"].tolist() == [2.75, 0.0, 2.75, 2.75]       # Hull City blank in GW10
    rows = proj.set_index(["name", "gameweek"])
    assert rows.loc[("Fit Gunner", 10), "opponents"] == "Spurs & Chelsea"
    assert rows.loc[("Fit Gunner", 10), "fixtures"] == 2
    assert rows.loc[("Blank Tiger", 10), "opponents"] == png.NO_FIXTURE_OPPONENT
    assert rows.loc[("Blank Tiger", 10), "fixtures"] == 0
    assert rows.loc[("Injured Gunner", 10), "availability"] == 0.25
    assert rows.loc[("Doubtful Blue", 9), "availability"] == 0.5          # the chance itself, for the armband
    assert rows.loc[("Fit Gunner", 11), "now_cost"] == 100 and rows.loc[("Fit Gunner", 11), "web_name"] == "Fit Gunner"

    # One feature build per gameweek, each with only its own fixtures
    frames = {int(f["GW"].iloc[0]): f for f in seen}
    assert sorted(frames) == [9, 10, 11, 12] and all(f["GW"].nunique() == 1 for f in seen)
    fit = {gw: f[f["name"] == "Fit Gunner"].sort_values("kickoff_time") for gw, f in frames.items()}
    # Form and the rest stay as they were before GW9; days_rest is the gap to the team's previous fixture
    for gw in (10, 11, 12):
        assert fit[gw]["total_points_r3"].tolist() == pytest.approx([fit[9]["total_points_r3"].iloc[0]] * len(fit[gw]))
        assert fit[gw]["season_matches_before"].iloc[0] == fit[9]["season_matches_before"].iloc[0]
    assert fit[10]["days_rest"].tolist() == pytest.approx([7.0, 3 + 4.75 / 24])
    assert fit[10]["fixtures_in_gw"].tolist() == [2.0, 2.0]
    assert fit[11]["days_rest"].tolist() == pytest.approx([3 + 19.25 / 24])
    assert fit[12]["days_rest"].tolist() == pytest.approx([14.0])
    # No earlier match at all: NaN for his first row, as in training (the double's second counts from the first)
    gone = frames[10][frames[10]["name"] == "Gone Gunner"].sort_values("kickoff_time")
    assert np.isnan(gone["days_rest"].iloc[0]) and gone["days_rest"].iloc[1] == pytest.approx(3 + 4.75 / 24)
    # The status file stays the snapshot, as old as it was for GW9; no odds for later gameweeks
    age = frames[9]["snap_age_days"].dropna().unique()
    assert len(age) == 1
    for gw in (10, 11, 12):
        assert frames[gw]["snap_age_days"].dropna().unique().tolist() == pytest.approx(age.tolist())
        assert frames[gw]["snap_unavailable"].notna().all() and frames[gw]["odds_win"].isna().all()
        # ... but its flags are about GW9: later gameweeks are scored as if everyone were available
        assert (frames[gw]["snap_unavailable"] == 0).all() and (frames[gw]["snap_chance"] == 100).all()
    assert frames[9].set_index("name").loc["Doubtful Blue", "snap_chance"] == 50
    assert frames[11].set_index("name").loc["Fit Gunner", "tr_in_rank"] == 1.0


def test_projections_use_the_return_date_in_fpls_news(live_dir):
    """GW9 kicks off on 24 Oct, GW10 on 31 Oct (Arsenal also 3 Nov), GW11 on 7 Nov, GW12 on 21 Nov."""
    status_path = next(live_dir.glob("player_status*.csv"))
    status = pd.read_csv(status_path)
    status.loc[status["name"] == "Injured Gunner", "news"] = "Back injury - Expected back 5 Nov"
    row = status["name"] == "Doubtful Blue"
    status.loc[row, "status"], status.loc[row, "chance_of_playing_next_round"] = "s", 0.0
    status.loc[row, "news"] = "Suspended until 26 Oct"
    status.to_csv(status_path, index=False)
    png.predict_next_gameweek(str(live_dir), client=HorizonClient(), history_csv=str(live_dir / "history.csv"),
                              predict_fn=lambda f: f["value"].to_numpy() / 20.0, fetch_odds=False)
    proj = pd.read_csv(live_dir / png.PROJECTIONS_NAME)
    pts = proj.pivot(index="name", columns="gameweek", values="predicted_points")
    # Out for GW9 and for GW10 (which starts before 5 Nov), half in his first gameweek back, then 0.75
    assert pts.loc["Injured Gunner"].tolist() == pytest.approx([0.0, 0.0, 1.5, 2.25])
    # A one-match ban: back in full for the GW10 double
    assert pts.loc["Doubtful Blue"].tolist() == pytest.approx([0.0, 5.0, 2.5, 2.5])


def test_failing_projections_never_touch_the_predictions(live_dir, monkeypatch):
    def next_gameweek_only(frame):
        if (frame["GW"] != 9).any():
            raise RuntimeError("model exploded on a later gameweek")
        return frame["value"].to_numpy() / 20.0
    summary = png.predict_next_gameweek(str(live_dir), client=HorizonClient(), history_csv=str(live_dir / "history.csv"),
                                        predict_fn=next_gameweek_only, fetch_odds=False)
    assert summary["projections"]["gameweeks"] == [9]
    assert set(summary["projections"]["failed"]) == {10, 11, 12}
    assert set(pd.read_csv(live_dir / png.PROJECTIONS_NAME)["gameweek"]) == {9}
    assert len(pd.read_csv(live_dir / png.OUTPUT_NAME)) == 5

    def broken(*args, **kwargs):
        raise ValueError("no projections today")
    monkeypatch.setattr(png, "build_projections", broken)
    summary = png.predict_next_gameweek(str(live_dir), client=HorizonClient(), history_csv=str(live_dir / "history.csv"),
                                        predict_fn=lambda f: f["value"].to_numpy() / 20.0, fetch_odds=False)
    assert summary["projections"]["output"] is None
    assert summary["projections"]["failed"] == "failed: no projections today"
    assert pd.read_csv(live_dir / png.OUTPUT_NAME)["predicted_points"].max() == 5.0


# ------------------------------------------------------------------ services/predictions.py

@pytest.fixture
def prediction_files(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled.csv"
    pd.DataFrame([{"name": "Old Player", "predicted_points": 1.0}]).to_csv(bundled, index=False)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setattr(preds_service, "PREDICTIONS_CSV", str(bundled))
    monkeypatch.setenv("LIVE_DATA_DIR", str(live_dir))
    monkeypatch.setattr(preds_service, "_cached", None)
    return bundled, live_dir / "next_gw_predictions.csv"


def test_predictions_fall_back_to_bundled_file(prediction_files):
    bundled, live = prediction_files
    assert preds_service.predictions_path() == str(bundled)
    df = preds_service.get_predictions_df()
    assert list(df["name_lower"]) == ["old player"]


def test_predictions_prefer_live_file_once_written(prediction_files):
    bundled, live = prediction_files
    assert list(preds_service.get_predictions_df()["name"]) == ["Old Player"]

    pd.DataFrame([{"name": "New Player", "predicted_points": 7.0}]).to_csv(live, index=False)
    assert preds_service.predictions_path() == str(live)
    assert list(preds_service.get_predictions_df()["name_lower"]) == ["new player"]   # no restart needed

    live.unlink()
    assert list(preds_service.get_predictions_df()["name"]) == ["Old Player"]


def test_predictions_cache_reuses_frame_until_mtime_changes(prediction_files):
    bundled, live = prediction_files
    pd.DataFrame([{"name": "A", "predicted_points": 1.0}]).to_csv(live, index=False)
    first = preds_service.get_predictions_df()
    assert preds_service.get_predictions_df() is first

    pd.DataFrame([{"name": "B", "predicted_points": 2.0}]).to_csv(live, index=False)
    os.utime(live, (os.path.getmtime(live) + 10, os.path.getmtime(live) + 10))
    assert list(preds_service.get_predictions_df()["name"]) == ["B"]


def test_predictions_missing_everywhere_returns_none(prediction_files):
    bundled, live = prediction_files
    bundled.unlink()
    assert preds_service.get_predictions_df() is None


# ------------------------------------------------------------------ what a run had (prediction_run.json)

def test_run_file_says_when_prices_were_missing(live_dir, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(live_dir))
    assert preds_service.run_info() == {} and preds_service.odds_pending() is False    # no run yet
    png.predict_next_gameweek(str(live_dir), client=HorizonClient(), history_csv=str(live_dir / "history.csv"),
                              predict_fn=lambda f: f["value"].to_numpy() / 20.0, fetch_odds=False)
    info = preds_service.run_info()
    assert info["gameweek"] == 9 and info["model"] == pm.MODEL_VERSION and info["odds"] == "not fetched"
    assert set(info["input_coverage"]) >= {"odds", "snapshot"}
    # No prices for GW9 in this directory: the run was made without them
    assert info["input_coverage"]["odds"] == 0.0 and preds_service.odds_pending() is True
    assert preds_service.odds_pending({"input_coverage": {"odds": 0.9}}) is False
    assert preds_service.odds_pending({"input_coverage": {}}) is False
    assert preds_service.odds_pending({"input_coverage": {"odds": "x"}}) is False


def test_sources_note_pending_prices():
    from services import answer_sources

    used = {"predictions": True}
    assert answer_sources.build_sources(used, current_season="2026-27", predictions_gameweek=11,
                                        odds_pending=True) == ["GW11 predictions (bookmakers' prices not in yet)"]
    assert answer_sources.build_sources(used, current_season="2026-27", predictions_gameweek=11) == ["GW11 predictions"]
