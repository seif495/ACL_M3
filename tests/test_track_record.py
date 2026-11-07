"""Track record (services/track_record.py): pure scoring, no-leakage rules, missing files, the route."""
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import model_review as mr
from services import season_service as ss
from services import track_record as tr

SEASON = "2026-27"


# ------------------------------------------------------------------ pure scoring

def rows_for(pairs, fixtures=None):
    return tr.prediction_rows([
        {"element": e, "name": f"P{e}", "predicted_points": p, **({"fixtures": fixtures[e]} if fixtures else {})}
        for e, p in pairs
    ])


@pytest.fixture
def top3(monkeypatch):
    monkeypatch.setattr(tr, "TOP_N", 3)


def small_case():
    rows = rows_for([(1, 8), (2, 6), (3, 4), (4, 2), (5, 1), (6, 0)], fixtures={1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 0})
    actual = {1: {"points": 10, "minutes": 90}, 2: {"points": 2, "minutes": 60}, 3: {"points": 0, "minutes": 0},
              4: {"points": 6, "minutes": 90}, 5: {"points": 1, "minutes": 30}, 7: {"points": 13, "minutes": 90}}
    fpl = {1: 5.0, 2: 5.5, 3: 1.0, 4: 6.0, 5: 2.0, 6: 9.0}
    return rows, actual, fpl


def test_score_gameweek_against_fpl(top3):
    rows, actual, fpl = small_case()
    s = tr.score_gameweek(rows, actual, fpl)
    # played = 1, 2, 4, 5 (3 didn't play, 6 has no row, 7 has no prediction)
    assert s["mae"] == {"model": 2.5, "fpl": 2.375, "n": 4}
    # model's top 3 with a fixture: 1, 2, 3 -> 10 + 2 + 0; FPL's: 4, 2, 1 (6 has no fixture) -> 6 + 2 + 10
    assert s["top10"] == {"model": 12, "fpl": 18, "best": 29, "n": 3}
    assert s["captain"] == {"element": 1, "name": "P1", "predicted": 8.0, "points": 10, "hit": True}
    assert s["players"] == 5
    bins = tr.calibration(s["_bins"])
    assert [b["n"] for b in bins] == [1, 1, 1, 2]
    assert bins[3]["predicted"] == 7.0 and bins[3]["actual"] == 6.0
    assert bins[0]["predicted"] == 1.0 and bins[0]["actual"] == 1.0


def test_score_gameweek_without_fpl_forecast(top3):
    rows, actual, _ = small_case()
    s = tr.score_gameweek(rows, actual, None)
    assert s["mae"]["fpl"] is None and s["mae"]["model"] == 2.5 and s["mae"]["n"] == 4
    assert s["top10"]["fpl"] is None and s["top10"]["model"] == 12


def test_fpl_forecast_covering_too_few_players_gives_no_top10(top3):
    rows, actual, _ = small_case()
    s = tr.score_gameweek(rows, actual, {1: 5.0, 2: 4.0})   # only 2 of the 3 needed
    assert s["top10"]["fpl"] is None
    assert s["mae"]["fpl"] is not None   # MAE uses the players both cover


def test_captain_miss_and_ties_are_deterministic(top3):
    rows = rows_for([(9, 5), (4, 5), (7, 1)])
    s = tr.score_gameweek(rows, {4: {"points": 3, "minutes": 90}, 9: {"points": 12, "minutes": 90}}, None)
    assert s["captain"]["element"] == 4 and s["captain"]["hit"] is False   # tie -> lower element id


def test_blank_gameweek_has_nothing_to_rank():
    rows = rows_for([(1, 3.0), (2, 1.0)], fixtures={1: 0, 2: 0})
    s = tr.score_gameweek(rows, {}, None)
    assert s["top10"] is None and s["captain"] is None and s["players"] == 0
    assert s["mae"] == {"model": None, "fpl": None, "n": 0}


def test_partial_blank_excludes_players_without_a_fixture(top3):
    # element 1 is the highest prediction but his club blanks: he's neither top pick nor counted
    rows = rows_for([(1, 9.0), (2, 4.0), (3, 3.0)], fixtures={1: 0, 2: 1, 3: 1})
    actual = {2: {"points": 5, "minutes": 90}, 3: {"points": 1, "minutes": 90}}
    s = tr.score_gameweek(rows, actual, None)
    assert s["captain"]["element"] == 2
    assert s["top10"]["model"] == 6 and s["top10"]["n"] == 2


def test_prediction_rows_cleans_input():
    rows = tr.prediction_rows([
        {"element": 1, "name": "A", "predicted_points": 2.0, "fixtures": 1},
        {"element": 1, "name": "dup", "predicted_points": 9.0, "fixtures": 1},
        {"element": None, "predicted_points": 1.0},
        {"element": 2, "predicted_points": float("nan")},
        {"element": 3, "name": "C", "predicted_points": 1.0},   # no fixtures column: has a fixture
    ])
    assert [(r["element"], r["has_fixture"]) for r in rows] == [(1, True), (3, True)]


def gw(n, model_mae, fpl_mae, model_top, fpl_top, hit=True, squad=None, bins=None):
    return {
        "gameweek": n,
        "mae": {"model": model_mae, "fpl": fpl_mae, "n": 100},
        "top10": {"model": model_top, "fpl": fpl_top, "best": 80, "n": 10},
        "captain": {"element": n, "name": "X", "predicted": 6.0, "points": 8 if hit else 2, "hit": hit},
        "squad": squad, "_bins": bins or [{"n": 1, "predicted": 1.0, "actual": 2.0} for _ in tr.BINS],
    }


def test_summarize_pools_and_counts_wins():
    gws = [
        gw(1, 1.0, 1.2, 50, 40, True, {"points": 60, "average": 50}),
        gw(2, 1.4, 1.2, 30, 40, False, {"points": 40, "average": 50}),
        gw(3, 1.1, 1.3, 50, 50, True, {"points": 70, "average": None}),
        gw(4, 0.9, None, 45, None, True),   # no FPL forecast: left out of every comparison
    ]
    t = tr.summarize(gws)
    assert all("_bins" not in g for g in gws)
    assert t["gameweeks"] == 4 and t["compared_gameweeks"] == 3
    assert t["mae"] == {"model": 1.167, "fpl": 1.233, "n": 300, "gameweeks": 3}
    assert t["beating_fpl"] == {"mae": {"wins": 2, "of": 3}, "top10": {"wins": 1, "of": 3}}   # a tie isn't a win
    assert t["top10"] == {"model": 43.33, "fpl": 43.33, "best": 80.0, "gameweeks": 3}
    assert t["captain"]["hits"] == 3 and t["captain"]["of"] == 4 and t["captain"]["rate"] == 0.75
    assert t["squad"]["gameweeks"] == 3 and t["squad"]["of"] == 2 and t["squad"]["beat_average"] == 1
    assert t["squad"]["mean_average"] == 50.0
    assert [b["n"] for b in t["calibration"]] == [4, 4, 4, 4]
    assert t["calibration"][0]["predicted"] == 1.0 and t["calibration"][0]["actual"] == 2.0


def test_summarize_without_any_fpl_forecast_uses_the_models_own_numbers():
    t = tr.summarize([gw(1, 1.0, None, 50, None), gw(2, 2.0, None, 30, None)])
    assert t["compared_gameweeks"] == 0
    assert t["mae"]["model"] == 1.5 and t["mae"]["fpl"] is None
    assert t["top10"]["model"] == 40.0 and t["top10"]["fpl"] is None
    assert t["beating_fpl"] == {"mae": {"wins": 0, "of": 0}, "top10": {"wins": 0, "of": 0}}


# ------------------------------------------------------------------ files in a temp LIVE_DATA_DIR

DEADLINES = {n: f"2026-09-{10 + n:02d}T17:30:00Z" for n in range(1, 9)}
BEFORE = {n: f"2026-09-{10 + n:02d}T12:00:00+00:00" for n in DEADLINES}
AFTER = {n: f"2026-09-{10 + n:02d}T18:00:00+00:00" for n in DEADLINES}

# element -> (position, predicted)
SQUAD = ([(1, "GK"), (2, "DEF"), (3, "DEF"), (4, "DEF"), (5, "DEF"), (6, "MID"), (7, "MID"), (8, "MID"), (9, "MID"),
          (10, "FWD"), (11, "FWD")], [(12, "GK"), (13, "DEF"), (14, "MID"), (15, "FWD")])


def pick_json(gameweek: int, archived_at: str, deadline: Optional[str] = None, source: str = "pipeline") -> Dict[str, Any]:
    def player(el: int, pos: str, role: str, captain: bool = False, vice: bool = False) -> Dict[str, Any]:
        return {"element": el, "name": f"P{el}", "position": pos, "team": f"T{el}", "cost": 5.0,
                "predicted_points": 5.0 - el * 0.2, "role": role, "captain": captain, "vice_captain": vice,
                "fixtures": [el], "opponents": []}
    players = [player(el, pos, "starter", captain=el == 10, vice=el == 9) for el, pos in SQUAD[0]]
    players += [player(el, pos, "bench") for el, pos in SQUAD[1]]
    return {"season": SEASON, "gameweek": gameweek, "deadline": deadline or DEADLINES[gameweek],
            "archived_at": archived_at, "source": source, "note": None, "model": "v4", "players": players}


def predictions_csv(fixtures: int = 1, elements: int = 20) -> pd.DataFrame:
    # Element e is predicted 10 - 0.5e points; elements 1..elements
    return pd.DataFrame([{"name": f"P{e}", "position": "MID", "team": f"T{e}", "predicted_points": 10 - 0.5 * e,
                          "fixtures": fixtures, "element": e} for e in range(1, elements + 1)])


class World:
    """Builds a LIVE_DATA_DIR with the files the track record reads."""

    def __init__(self, root: str):
        self.root = root
        self.events: List[Dict[str, Any]] = []
        self.results: List[Dict[str, int]] = []

    def event(self, n: int, finished: bool = True, average: int = 50, data_checked: Optional[bool] = None) -> None:
        self.events.append({"id": n, "deadline_time": DEADLINES[n], "finished": finished,
                            "data_checked": finished if data_checked is None else data_checked,
                            "average_entry_score": average, "highest_score": 120})

    def result(self, n: int, element: int, points: int, minutes: int = 90) -> None:
        self.results.append({"element": element, "GW": n, "minutes": minutes, "total_points": points})

    def pick(self, n: int, archived_at: Optional[str] = None, csv: Optional[pd.DataFrame] = None,
             source: str = "pipeline", write_csv: bool = True) -> None:
        d = os.path.join(self.root, "model_picks", SEASON)
        os.makedirs(d, exist_ok=True)
        if write_csv:
            (predictions_csv() if csv is None else csv).to_csv(os.path.join(d, f"gw{n:02d}_predictions.csv"), index=False)
        with open(os.path.join(d, f"gw{n:02d}.json"), "w", encoding="utf-8") as f:
            json.dump(pick_json(n, archived_at or BEFORE[n], source=source), f)

    def element_capture(self, n: int, captured_at: str, ep: Dict[int, float]) -> None:
        d = os.path.join(self.root, "element_snapshots", SEASON)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"gw{n:02d}.csv")
        new = pd.DataFrame([{"element": e, "GW": n, "captured_at": captured_at, "ep_next": v} for e, v in ep.items()])
        if os.path.exists(path):
            new = pd.concat([pd.read_csv(path), new], ignore_index=True)
        new.to_csv(path, index=False)

    def xp(self, rows: List[Dict[str, Any]]) -> None:
        pd.DataFrame(rows).to_csv(os.path.join(self.root, "xp_snapshots.csv"), index=False)

    def write(self) -> None:
        with open(os.path.join(self.root, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"season": SEASON, "finished_fixtures": list(range(1, 100)), "events": self.events,
                       "files": {"season": "FPL_2026_2027.csv", "xp": "xp_snapshots.csv"}}, f)
        pd.DataFrame(self.results, columns=["element", "GW", "minutes", "total_points"]).to_csv(
            os.path.join(self.root, "FPL_2026_2027.csv"), index=False)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mr, "COMMITTED_DIR", str(tmp_path / "no-committed-picks"))
    ss.clear_cache()
    yield World(str(tmp_path))
    ss.clear_cache()


def season_of_results(w: World, n: int) -> None:
    """Element e scores 12 - e points in gameweek n (everyone plays), so the model is nearly right."""
    for e in range(1, 21):
        w.result(n, e, 12 - e)


def test_full_gameweek_with_fpl_forecast(world):
    world.event(1, average=40)
    season_of_results(world, 1)
    world.pick(1)
    world.element_capture(1, "2026-09-10T08:00:00+00:00", {e: 3.0 for e in range(1, 21)})
    world.element_capture(1, "2026-09-11T16:00:00+00:00", {e: 12.0 - e for e in range(1, 21)})   # the last before the deadline: spot on
    world.write()
    r = tr.get_track_record(SEASON)
    assert r["note"] is None and r["missing"] == []
    (g,) = r["gameweeks"]
    assert g["gameweek"] == 1 and g["fpl_source"] == "element_snapshots" and g["model"] == "v4"
    assert g["mae"]["fpl"] == 0.0    # FPL's last capture was exact
    assert g["mae"]["model"] == pytest.approx(3.55)   # |10 - .5e - (12 - e)| = |.5e - 2|, mean over 1..20 = 71 / 20
    assert g["top10"]["fpl"] == g["top10"]["best"] == sum(12 - e for e in range(1, 11))
    assert g["top10"]["model"] == g["top10"]["fpl"]    # same order
    assert g["captain"]["element"] == 1 and g["captain"]["points"] == 11 and g["captain"]["hit"] is True
    assert g["squad"]["average"] == 40 and g["squad"]["points"] > 0 and g["squad"]["formation"] == "4-4-2"
    assert r["totals"]["gameweeks"] == 1
    assert r["totals"]["beating_fpl"]["mae"] == {"wins": 0, "of": 1}   # FPL was exact
    assert [b["label"] for b in r["calibration"]] == ["0-2", "2-4", "4-6", "6+"]
    assert sum(b["n"] for b in r["calibration"]) == 20


def test_fpl_capture_after_the_deadline_is_ignored(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1)
    world.element_capture(1, "2026-09-11T18:30:00+00:00", {e: 12.0 - e for e in range(1, 21)})   # after the 17:30 deadline
    world.write()
    (g,) = tr.get_track_record(SEASON)["gameweeks"]
    assert g["fpl_source"] is None and g["mae"]["fpl"] is None and g["top10"]["fpl"] is None
    assert g["mae"]["model"] is not None   # the model's own numbers remain


def test_falls_back_to_xp_snapshots_for_the_right_gameweek(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1)
    world.xp([{"element": e, "GW": 1, "xP": 12.0 - e, "captured_at": "2026-09-11T10:00:00+00:00"} for e in range(1, 21)]
             + [{"element": e, "GW": 2, "xP": 99.0, "captured_at": "2026-09-11T10:00:00+00:00"} for e in range(1, 21)])
    world.write()
    (g,) = tr.get_track_record(SEASON)["gameweeks"]
    assert g["fpl_source"] == "xp_snapshots" and g["mae"]["fpl"] == 0.0


def test_missing_ep_next_still_scores_the_model(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1)
    world.element_capture(1, "2026-09-11T10:00:00+00:00", {})   # a capture file with no rows
    world.write()
    r = tr.get_track_record(SEASON)
    (g,) = r["gameweeks"]
    assert g["fpl_source"] is None and g["mae"]["fpl"] is None
    assert r["totals"]["mae"]["fpl"] is None and r["totals"]["compared_gameweeks"] == 0
    assert r["totals"]["beating_fpl"]["top10"] == {"wins": 0, "of": 0}


def test_prediction_saved_after_the_deadline_is_not_used(world):
    for n in (1, 2):
        world.event(n)
        season_of_results(world, n)
    world.pick(1)
    world.pick(2, archived_at=AFTER[2], csv=predictions_csv().assign(predicted_points=lambda d: 12 - d.element))   # would be spot on
    world.write()
    r = tr.get_track_record(SEASON)
    assert [g["gameweek"] for g in r["gameweeks"]] == [1]
    assert r["missing"] == [{"gameweek": 2, "reason": "The prediction was saved after the deadline"}]


def test_the_earlier_of_the_two_deadlines_applies(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1, archived_at="2026-09-11T17:00:00+00:00")   # before meta's 17:30 ...
    path = os.path.join(world.root, "model_picks", SEASON, "gw01.json")
    world.write()
    with open(path, encoding="utf-8") as f:
        picks = json.load(f)
    picks["deadline"] = "2026-09-11T16:00:00Z"   # ... but after the pick's own, earlier one
    with open(path, "w", encoding="utf-8") as f:
        json.dump(picks, f)
    r = tr.get_track_record(SEASON)
    assert r["gameweeks"] == [] and r["missing"][0]["reason"] == "The prediction was saved after the deadline"


def test_missing_reasons_and_unfinished_gameweeks(world):
    world.event(1)                       # no pick at all
    world.event(2)                       # pick without results
    world.event(3)                       # pick without a predictions file
    world.event(4)                       # recorded by hand
    world.event(5)                       # no save time
    world.event(6, finished=False)       # not finished: not listed at all
    season_of_results(world, 1)
    for n in (2, 3, 4, 5):
        world.pick(n, write_csv=n not in (3, 4), source="manual" if n == 4 else "pipeline")
    path = os.path.join(world.root, "model_picks", SEASON, "gw05.json")
    world.write()
    with open(path, encoding="utf-8") as f:
        picks = json.load(f)
    picks["archived_at"] = None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(picks, f)
    r = tr.get_track_record(SEASON)
    reasons = {m["gameweek"]: m["reason"] for m in r["missing"]}
    assert set(reasons) == {1, 2, 3, 4, 5}
    assert "saved before this deadline" in reasons[1]
    assert "No results" in reasons[2]
    assert "missing" in reasons[3]
    assert "by hand" in reasons[4]
    assert "No save time" in reasons[5]
    assert r["gameweeks"] == [] and r["totals"] is None and r["note"] == tr.NO_DATA_NOTE


def test_corrupt_predictions_file_is_a_missing_gameweek(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1)
    world.write()
    with open(os.path.join(world.root, "model_picks", SEASON, "gw01_predictions.csv"), "w", encoding="utf-8") as f:
        f.write("not,a\x00,csv\n1")
    r = tr.get_track_record(SEASON)
    assert r["gameweeks"] == [] and r["missing"][0]["gameweek"] == 1


def test_blank_gameweek_is_listed_not_scored(world):
    world.event(1)
    world.pick(1, csv=predictions_csv(fixtures=0))   # nobody has a fixture and nothing was played
    world.write()
    r = tr.get_track_record(SEASON)
    assert r["gameweeks"] == [] and "No results" in r["missing"][0]["reason"]


def test_blank_for_some_clubs_scores_the_rest(world):
    world.event(1)
    season_of_results(world, 1)
    csv = predictions_csv()
    csv.loc[csv.element <= 3, "fixtures"] = 0    # the three best-predicted players' clubs blank
    world.pick(1, csv=csv)
    world.write()
    (g,) = tr.get_track_record(SEASON)["gameweeks"]
    assert g["captain"]["element"] == 4 and g["players"] == 17


def test_empty_directory_gives_an_empty_payload_with_a_note(world):
    r = tr.get_track_record(SEASON)
    assert r == {"season": SEASON, "gameweeks": [], "missing": [], "totals": None, "calibration": [], "note": tr.NO_DATA_NOTE}


def test_cache_follows_the_files(world):
    world.event(1)
    season_of_results(world, 1)
    world.write()
    assert tr.get_track_record(SEASON)["gameweeks"] == []
    world.pick(1)   # new files change the signature
    assert len(tr.get_track_record(SEASON)["gameweeks"]) == 1


def test_a_season_the_live_files_dont_cover_is_empty(world):
    world.event(1)
    world.write()
    assert tr.get_track_record("2025-26")["gameweeks"] == []


# ------------------------------------------------------------------ the route

def client() -> TestClient:
    app = FastAPI()
    tr.register(app)
    return TestClient(app)


def test_route_returns_the_payload(world):
    world.event(1)
    season_of_results(world, 1)
    world.pick(1)
    world.write()
    r = client().get("/api/ml/track-record")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == SEASON and len(body["gameweeks"]) == 1 and body["totals"]["gameweeks"] == 1
    assert "_bins" not in json.dumps(body)


def test_route_never_fails_on_missing_files(world):
    r = client().get("/api/ml/track-record", params={"season": SEASON})
    assert r.status_code == 200
    assert r.json()["gameweeks"] == [] and r.json()["note"] == tr.NO_DATA_NOTE


def test_route_survives_an_unexpected_error(world, monkeypatch):
    def boom(season):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(tr, "get_track_record", boom)
    r = client().get("/api/ml/track-record")
    assert r.status_code == 200 and r.json()["gameweeks"] == [] and "couldn" in r.json()["note"]


@pytest.mark.parametrize("bad", ["../etc", "2026", "2026-2027", ""])
def test_route_rejects_a_malformed_season(world, bad):
    assert client().get("/api/ml/track-record", params={"season": bad}).status_code == 422
