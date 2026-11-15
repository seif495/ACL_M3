"""
Regenerate next-gameweek point predictions from fresh data.

    python -m pipeline.predict_next_gw [--live-dir data/live] [--backtest GW] [--no-odds-fetch]

Inputs:
  * FPL API: the next gameweek (bootstrap event with `is_next`) and its fixtures.
  * LIVE_DATA_DIR (written by `python -m pipeline.current_season`): this season's
    finished matches (FPL_YYYY_YYYY.csv) and every current player's status/news, FPL code
    and this gameweek's transfers in/out, plus our pre-deadline element_snapshots archive.
  * LIVE_DATA_DIR/odds: football-data.co.uk's odds, refreshed at the start of every run
    (pipeline.current_season.refresh_odds: this season's file and fixtures.csv, earlier
    seasons once). A failed download only leaves the odds features NaN.
  * data/cleaned_merged_seasons_cleaned.csv: all historical seasons (the last 3 for the
    features, plus the small team-match table that team ratings start from).
  * The served points model, v4 (ml.points_model.load_model(): LIVE_DATA_DIR/models/points_v4.json
    from the weekly retrain, else the bundled ml/models/points_v4.json; v3 until a v4 exists).

Output: LIVE_DATA_DIR/next_gw_predictions.csv (+ .json), same schema as the old
ml/predictions/next_gw_predictions.csv plus a few transparency columns, written
atomically. services/predictions.py prefers this file over the bundled one. Before the
deadline it also saves the gameweek's squad pick (services/model_review.save_picks) for
the Team of the Week page's "Model vs actual" review, and every run writes the model's
inputs for each upcoming row (next_gw_features.csv), which services/explain.py turns into
SHAP and LIME explanations for the /model page.

Projections: the same run writes LIVE_DATA_DIR/projections.csv, every current player's points
for the next PROJECTION_HORIZON gameweeks (the ML manager's planner, services/fpl_manager_planner,
plans transfers over them). The first gameweek is next_gw_predictions itself; each later one is
predicted by the same model from the same pre-deadline history (one build_features call per
gameweek, with only that gameweek's fixtures, so form, team strength, price, ownership and
transfers all stay as they are now), with days_rest measured to the team's real previous
fixture and odds only where fixtures.csv already prices the match. FPL's flags are about the
next deadline, so later gameweeks are scored as if every player were available and then scaled
by horizon_availability: 0 until the return date in FPL's news ("Expected back 6 Dec",
"Suspended until 22 Nov"), else the share of their points flagged players really scored one to
three gameweeks on (ml.snapshots.HORIZON_POINTS). A failure there is logged and never touches
the predictions.

Shadow: the same run also scores the previous production model, v3 (LIVE_DATA_DIR/models/
points_v3.json if present, else the bundled one; no longer retrained), on the same feature rows
with the same chance-of-playing multiplier, and saves its squad pick before the deadline in
model_picks/<season>/shadow/ (same files and selection as the live pick). Only the live
predictions and pick reach the site; scripts/analysis/shadow_ledger.py compares the two picks
gameweek by gameweek, on gameweeks nobody selected on. A failing shadow never affects the live
predictions.

How a prediction is built: each player gets one row per fixture of the predicted
gameweek and goes through ml.points_model.build_features together with his real
match history, so features are computed by exactly the code used in training and
only from matches before the gameweek (rows from the predicted gameweek or later
are dropped before anything is computed).

  * Double gameweek: one row per fixture, predictions summed.
  * Blank gameweek: the player is kept with 0 points and opponent "No fixture".
  * Availability: status u/n, or chance_of_playing_next_round 0 -> 0 points; a 25/50/75%
    doubt -> points x 0.61/0.77/0.91 (availability_factor). v4 already lowers flagged
    players' predictions from FPL's availability before the deadline (snap_*, from the
    status file), so chance/100 on top counted the flags twice; those factors are fitted
    against v4's own predictions (ml.snapshots.DOUBT_POINTS).
  * Players are keyed by FPL code (the status file's `code`), so a renamed player keeps
    his history; the shadow predicts from the same rows.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from bisect import bisect_left
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ml import points_model as pm
from ml import snapshots as sn
from pipeline.current_season import (
    LIVE_DATA_DIR, next_event, odds_dir, refresh_odds, season_file_stem, season_label,
)
from pipeline.team_names import graph_team_name
from services.season_service import chance_of_playing

logger = logging.getLogger(__name__)

HISTORY_CSV = pm.HISTORY_CSV
OUTPUT_NAME = "next_gw_predictions.csv"
FEATURES_NAME = "next_gw_features.csv"   # model inputs per upcoming row, for services/explain.py
FEATURE_ID_COLUMNS = ["element", "name", "position", "team", "opponent", "fixture", "kickoff_time", "GW", "season"]
POSITIONS = ("GK", "DEF", "MID", "FWD")
MODEL_METHOD = f"xgboost_{pm.MODEL_VERSION}"
NO_FIXTURE_METHOD = "no_fixture"
NO_FIXTURE_OPPONENT = "No fixture"
RECENT_MATCHES = 5       # matches averaged by the backtest's naive baseline
HISTORY_SEASONS = 3      # past seasons fed to the features at prediction time
# Share of upcoming rows with each v4 input, reported per run: odds are NaN before
# football-data.co.uk's Friday/Tuesday collection, snapshots come from the status file
COVERAGE_COLUMNS = {"odds": "odds_win", "snapshot": "snap_age_days", "transfers": "tr_bal_rank",
                    "ratings": "rt_att"}
# Multi-gameweek projections for the ML manager's planner (services/fpl_manager_planner.py)
PROJECTIONS_NAME = "projections.csv"
PROJECTION_HORIZON = 4   # gameweeks, the next one included
PROJECTION_COLUMNS = [
    "element", "name", "web_name", "position", "team", "now_cost", "gameweek", "fixtures", "opponents",
    "predicted_points",
    # FPL's chance of playing that gameweek (horizon_availability with chance_of_playing: the planner's
    # chance for the armband; predicted_points carry availability_factor's multiplier) and FPL's status
    "availability", "status",
]

PredictFn = Callable[[pd.DataFrame], np.ndarray]

OUTPUT_COLUMNS = [
    "name", "position", "team", "predicted_points", "value", "opponent", "is_home",
    "gameweek", "kickoff_time", "prediction_method", "is_promoted_team",
    # transparency columns (consumers ignore unknown columns)
    "fixtures", "status", "chance_of_playing_next_round", "news", "element",
]


# ------------------------------------------------------------------ pure helpers

def next_gameweek(bootstrap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The upcoming gameweek (FPL's `is_next` event), or None after the last deadline."""
    return next_event(bootstrap)


def team_name_map(bootstrap: Dict[str, Any]) -> Dict[int, str]:
    return {t["id"]: graph_team_name(t["name"]) for t in bootstrap["teams"]}


def gameweek_fixtures(fixtures: Iterable[Dict[str, Any]], event_id: int,
                      teams: Dict[int, str]) -> Dict[str, List[Dict[str, Any]]]:
    """Each team's fixtures in gameweek `event_id`, in kickoff order.

    Teams with two fixtures (double gameweek) get two entries; teams without one
    (blank gameweek) are absent. Unscheduled fixtures (event None) are ignored.
    """
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for f in fixtures:
        if f.get("event") != event_id:
            continue
        home, away = teams[f["team_h"]], teams[f["team_a"]]
        for team, opponent, is_home in ((home, away, True), (away, home, False)):
            by_team.setdefault(team, []).append({
                "fixture_id": f["id"], "opponent": opponent, "is_home": is_home,
                "kickoff_time": f.get("kickoff_time"),
            })
    for entries in by_team.values():
        entries.sort(key=lambda e: (e["kickoff_time"] or "", e["fixture_id"]))
    return by_team


def availability_factor(status: Any, chance_next: Any) -> float:
    """Multiplier for a player's predicted points from his FPL status.

    0 when he's out: u (left / unavailable), n (not in squad), FPL's chance 0, or injured or
    suspended without a chance value. 1 without a flag. A 25/50/75% doubt gets
    ml.snapshots.DOUBT_POINTS (0.61/0.77/0.91), not the chance itself: v4 already lowers flagged
    players' predictions, so chance / 100 counted the flags twice. The chance itself
    (season_service.chance_of_playing) stays the chance of playing behind the armband.
    """
    return float(sn.points_factor(chance_of_playing(status, chance_next)))


# FPL's news for a player who is out: "Hamstring injury - Expected back 6 Dec", "Suspended until
# 22 Nov" (no year), or "Knee injury - Unknown return date"
_RETURN_DATE = re.compile(r"\b(?:expected back|until)\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*\b", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), start=1)}
# Share of his points an injured player gets in his first and second gameweek back, when FPL's
# news dates his return (then full). Not fitted: the snapshots keep no news text. It follows the
# shape of ml.snapshots.HORIZON_POINTS (a returning player starts on the bench or plays part of a
# match). A ban ends on its date, so a suspended player is back in full.
RETURN_RAMP = (0.5, 0.75)


def return_date(news: Any, today: date) -> Optional[date]:
    """The day FPL's news expects a player back, None when it gives none. The news has no year:
    it is the one that puts the day closest to `today`."""
    match = _RETURN_DATE.search(news) if isinstance(news, str) else None
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if month is None:
        return None
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, month, int(match.group(1))))
        except ValueError:
            continue
    return min(options, key=lambda d: abs((d - today).days)) if options else None


def horizon_availability(status: Any, chance_next: Any, step: int,
                         factor: Callable[[Any, Any], float] = availability_factor,
                         back: Optional[date] = None, dates: Sequence[Optional[date]] = ()) -> float:
    """Availability for the gameweek `step` gameweeks ahead (1 = the next one, `factor` itself:
    availability_factor's points multiplier, or chance_of_playing for the chance of playing).
    FPL's flag describes the next gameweek only, and later gameweeks are projected as if the player
    were fit (ml.snapshots.as_available), so this carries all of his availability there:

      * u (left / unavailable) and n (not in squad): 0 in every gameweek;
      * FPL's news dates his return (`back`, from return_date) after the next gameweek: 0 for
        gameweeks before that day, then RETURN_RAMP for an injured player's first two gameweeks
        back and full after (a ban ends on its day: full at once). `dates` are the days of the
        horizon's gameweeks for his club, the next one first (his club's first kickoff, else the
        deadline; None when unknown);
      * no date ("Unknown return date", a doubt, or a date that has already passed): the fitted
        ml.snapshots.HORIZON_POINTS for his flag, e.g. 0.25 / 0.35 / 0.65 when injured and
        0.85 / 1 / 1 when suspended.
    """
    now = factor(status, chance_next)
    if step <= 1:
        return now
    code = status.strip().lower() if isinstance(status, str) else "a"
    if code in ("u", "n"):
        return 0.0
    chance = chance_of_playing(status, chance_next)
    if not chance < 1.0:
        return 1.0
    dated = (back is not None and chance <= 0.0 and len(dates) >= step
             and dates[0] is not None and dates[step - 1] is not None and back > dates[0])
    if dated:
        if dates[step - 1] < back:
            return 0.0
        if code == "s":
            return 1.0
        played = sum(1 for d in dates[1:step] if d is not None and d >= back)    # this gameweek included
        return RETURN_RAMP[played - 1] if played <= len(RETURN_RAMP) else 1.0
    return sn.horizon_points(code, chance, step - 1)


def describe_fixtures(entries: List[Dict[str, Any]]) -> Tuple[str, bool, str]:
    """(opponent, is_home, kickoff_time) columns for one player's gameweek.

    A double gameweek joins the opponents ("Chelsea & Everton"); home/away and
    kickoff are those of the first fixture.
    """
    first = entries[0]
    opponent = " & ".join(e["opponent"] for e in entries)
    return opponent, bool(first["is_home"]), first["kickoff_time"]


def _atomic_write(path: str, write) -> None:
    tmp = f"{path}.tmp"
    write(tmp)
    os.replace(tmp, path)


def peak_memory_mb() -> Optional[float]:
    """Peak resident memory of this process so far (Linux: ru_maxrss; else psutil if present)."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:
        pass
    try:
        import psutil
        info = psutil.Process().memory_info()
        return getattr(info, "peak_wset", info.rss) / (1024.0 * 1024.0)
    except ImportError:
        return None


# ------------------------------------------------------------------ data loading

def live_paths(live_dir: str, season: str) -> Tuple[str, str]:
    """(season matches CSV, player status CSV) in `live_dir`, preferring meta.json's names."""
    files = {}
    meta_path = os.path.join(live_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("season") == season:
            files = meta.get("files") or {}
    season_csv = files.get("season") or f"FPL_{season_file_stem(season)}.csv"
    status_csv = files.get("status") or f"player_status_{season.replace('-', '_')}.csv"
    return os.path.join(live_dir, season_csv), os.path.join(live_dir, status_csv)


def load_history(live_csv: str, season: str, before_gw: int, before_time: Optional[str],
                 history_csv: str = HISTORY_CSV, past_seasons: int = HISTORY_SEASONS) -> pd.DataFrame:
    """The last `past_seasons` historical seasons plus this season's matches from before the
    predicted gameweek. (Measured against using every season: correlation 0.9998, same
    top 20, about half the time and memory.) v4's team ratings still start from every season,
    through load_extras' team-match table.

    Leakage guard: of this season only rows with GW < `before_gw` that kicked off before
    `before_time` (the predicted gameweek's first kickoff) are kept.
    """
    history = pm.load_history(history_csv, live_csv=live_csv, live_season=season)
    current = history["season"] == season
    past = sorted(s for s in history["season"].unique() if s < season)[-past_seasons:]
    history = history[history["season"].isin(past) | current]
    current = history["season"] == season
    late = current & (history["GW"] >= before_gw)
    if before_time is not None:
        late |= current & (history["kickoff_time"] >= pd.to_datetime(before_time, utc=True))
    return history[~late].reset_index(drop=True)


def load_extras(live_dir: str, season: str, status_csv: str, history_csv: str = HISTORY_CSV,
                problems: Optional[Dict[str, str]] = None) -> pm.ExtraInputs:
    """v4's extra inputs (ml.points_model.load_extra_inputs): FPL codes, the odds in odds_dir,
    snapshots (vaastav's, our archive, and the status file as the upcoming gameweek's), and the
    full history's team-match table, so team ratings equal a build from every season.

    Never fails the run: if they don't load at once, each part is loaded on its own and a part that
    can't be read (an unreadable odds file, a malformed element_snapshots file, ...) is left None, so
    its features are NaN (without codes, players are keyed by name). Snapshots fall back to the
    status file alone, which is all the upcoming rows use. `problems` gets {part: what happened}."""
    from ml import fixture_strength as fs
    from ml import odds as od
    from ml import snapshots as sn

    odds = odds_dir(live_dir)
    try:
        return pm.load_extra_inputs(live_dir=live_dir, season=season, status_csv=status_csv,
                                    history_csv=history_csv, odds_dir=odds)
    except Exception:
        logger.warning("Couldn't load v4's extra inputs at once; loading them part by part", exc_info=True)

    def status() -> Optional[pd.DataFrame]:
        return pd.read_csv(status_csv) if os.path.exists(status_csv) else None

    attempts = {
        "codes": [lambda: pm.load_player_codes(status_csv, season)],
        "odds": [lambda: od.load_odds(odds)],
        "snapshots": [lambda: sn.load_all_snapshots(sn.SNAPSHOTS_CSV, live_dir, status=status(), season=season),
                      lambda: sn.load_all_snapshots("", None, status=status(), season=season)],
        "ratings_prior": [lambda: fs.team_match_history(history_csv=history_csv)],
    }
    parts: Dict[str, Any] = {}
    for part, loaders in attempts.items():
        parts[part] = None
        for k, load in enumerate(loaders):
            try:
                parts[part] = load()
            except Exception as exc:
                logger.warning("Couldn't load v4's %s (attempt %d)", part, k + 1, exc_info=True)
                if problems is not None:
                    problems[part] = f"failed: {exc}"[:200]
                continue
            if k and problems is not None:
                problems[part] = f"status file only; {problems[part]}"[:200]
            break
    return pm.ExtraInputs(**parts)


def previous_season_teams(history: pd.DataFrame, season: str) -> List[str]:
    past = sorted(s for s in history["season"].dropna().unique() if s < season)
    return sorted(history.loc[history["season"] == past[-1], "team"].dropna().unique()) if past else []


# ------------------------------------------------------------------ model rows

def _numeric(players: pd.DataFrame, col: str) -> pd.Series:
    if col not in players.columns:
        return pd.Series(np.nan, index=players.index)
    return pd.to_numeric(players[col], errors="coerce")


def upcoming_rows(players: pd.DataFrame, fixtures_by_team: Dict[str, List[Dict[str, Any]]],
                  season: str, gameweek: int) -> pd.DataFrame:
    """One row per player per fixture of the predicted gameweek, in the model's input format.

    Price and ownership are the current pre-deadline values; ownership becomes a rank
    within this gameweek, matching how training ranks `selected` per gameweek. Transfers in
    and out are FPL's counts since the last deadline (the status file's transfers_in_event /
    transfers_out_event), which v4 ranks within the gameweek like the history's counts.
    """
    rank = _numeric(players, "selected_by_percent").rank(pct=True)
    t_in, t_out = _numeric(players, "transfers_in_event"), _numeric(players, "transfers_out_event")
    rows = []
    for p, r, ti, to in zip(players.itertuples(index=False), rank, t_in, t_out):
        for e in fixtures_by_team.get(p.team) or []:
            rows.append({
                "season": season, "name": p.name, "position": p.position, "team": p.team,
                "opponent": e["opponent"], "fixture": e["fixture_id"],
                "kickoff_time": e["kickoff_time"], "GW": gameweek, "was_home": bool(e["is_home"]),
                "value": float(p.now_cost), "selected_rank": r, "element": int(p.element),
                "transfers_in": ti, "transfers_out": to,
            })
    return pd.DataFrame(rows)


def save_model_inputs(rows: pd.DataFrame, path: str) -> None:
    """Write the model's input row for every upcoming fixture, plus its raw output
    (model_points, before the floor at 0), atomically. Feature columns are
    ml.points_model.feature_columns() (v4's, which include v3's); services/explain.py checks
    them against the served model.
    Full precision on purpose: tree splits sit exactly on training values such as 1/3 (a
    3-match mean), and rounding to 6 digits moved 30% of players' predictions by up to 0.6."""
    cols = FEATURE_ID_COLUMNS + ["model_points"] + [c for c in pm.feature_columns() if c in rows.columns]
    out = rows[cols].sort_values(["element", "kickoff_time", "fixture"])
    _atomic_write(path, lambda p: out.to_csv(p, index=False))


def model_rows(players: pd.DataFrame, history: pd.DataFrame,
               fixtures_by_team: Dict[str, List[Dict[str, Any]]], season: str, gameweek: int,
               extras: Optional[pm.ExtraInputs] = None) -> pd.DataFrame:
    """The predicted gameweek's feature rows: one per player per fixture, with `element`.

    Features come from ml.points_model.build_features, the same code used in training;
    the upcoming rows have no outcomes, so their features only see earlier matches.
    `extras` (load_extras) adds v4's inputs and keys players by FPL code; without it the
    rows hold v3's features only.
    """
    upcoming = upcoming_rows(players, fixtures_by_team, season, gameweek)
    if upcoming.empty:
        return upcoming
    feats = pm.build_features(history, upcoming, extras=extras)
    return feats[feats["is_upcoming"]].reset_index(drop=True)


def input_coverage(rows: pd.DataFrame) -> Dict[str, float]:
    """Share of upcoming rows with each v4 input (COVERAGE_COLUMNS)."""
    return {k: round(float(rows[c].notna().mean()), 3) for k, c in COVERAGE_COLUMNS.items()
            if c in rows.columns and len(rows)}


def score_rows(rows: pd.DataFrame, predict_fn: PredictFn, method: str = MODEL_METHOD,
               shadow_fn: Optional[PredictFn] = None, features_path: Optional[str] = None,
               gameweek: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """element (str) -> {'points': model points summed over the gameweek's fixtures, 'method'},
    plus 'shadow_points' (the same from `shadow_fn`) when the shadow model ran.

    Each fixture's prediction is floored at 0 before a double gameweek is summed. With
    `features_path`, the model's inputs are saved there too (save_model_inputs). Neither a
    failure to save nor a failing shadow model is raised; both are logged.
    """
    if rows.empty:
        return {}
    rows = rows.copy()
    rows["model_points"] = predict_fn(rows)
    rows["points"] = np.clip(rows["model_points"], 0.0, None)
    if features_path:
        try:
            save_model_inputs(rows, features_path)
        except Exception:
            logger.exception("Couldn't save the model inputs for GW%s", gameweek)
    out = {str(int(k)): {"points": float(v), "method": method}
           for k, v in rows.groupby("element")["points"].sum().items()}
    if shadow_fn is not None:
        try:
            shadow = pd.Series(np.clip(np.asarray(shadow_fn(rows), dtype=float), 0.0, None), index=rows.index)
            for k, v in shadow.groupby(rows["element"]).sum().items():
                out[str(int(k))]["shadow_points"] = float(v)
        except Exception:
            logger.exception("The shadow model failed for GW%s; the live predictions are unaffected", gameweek)
    return out


def predict_players(players: pd.DataFrame, history: pd.DataFrame,
                    fixtures_by_team: Dict[str, List[Dict[str, Any]]], season: str, gameweek: int,
                    predict_fn: PredictFn, features_path: Optional[str] = None,
                    extras: Optional[pm.ExtraInputs] = None, shadow_fn: Optional[PredictFn] = None,
                    method: str = MODEL_METHOD) -> Dict[str, Dict[str, Any]]:
    """model_rows then score_rows: element (str) -> {'points', 'method'[, 'shadow_points']}.

    Players without any earlier matches still get a prediction from price, position,
    ownership and their team/opponent strength; blank-gameweek players get no entry.
    """
    rows = model_rows(players, history, fixtures_by_team, season, gameweek, extras)
    return score_rows(rows, predict_fn, method, shadow_fn, features_path, gameweek)


def load_model_fn(version: Optional[str] = None) -> Tuple[PredictFn, str]:
    """(predict function, model version): the served model (v4, or v3 until a v4 exists) by
    default, else `version`'s active copy (the retrained one in LIVE_DATA_DIR, else bundled).
    Each model reads its own feature list from its meta."""
    booster, meta = pm.load_model(version=version)
    return (lambda frame: pm.predict(booster, meta, frame)), str(meta.get("version") or version or pm.MODEL_VERSION)


def _shadow_fn(served_version: str) -> Tuple[Optional[PredictFn], str]:
    """(predict function, version) of the shadow model, or (None, why not): there's no v4
    model yet (the served model is the shadow), or the shadow's files don't load."""
    if served_version == pm.SHADOW_VERSION:
        return None, f"skipped: the served model is {served_version}"
    try:
        return load_model_fn(pm.SHADOW_VERSION)
    except Exception as exc:
        logger.exception("Couldn't load the shadow model %s", pm.SHADOW_VERSION)
        return None, f"failed to load: {exc}"[:200]


# ------------------------------------------------------------------ assembly

def assemble_output(players: pd.DataFrame, predictions: Dict[str, Dict[str, Any]],
                    fixtures_by_team: Dict[str, List[Dict[str, Any]]], gameweek: int,
                    deadline: Optional[str], promoted_teams: Iterable[str]) -> pd.DataFrame:
    """Final table in the next_gw_predictions schema, one row per current player."""
    promoted = set(promoted_teams)
    rows = []
    for p in players.itertuples(index=False):
        key = str(int(p.element))
        entries = fixtures_by_team.get(p.team) or []
        if entries:
            opponent, is_home, kickoff = describe_fixtures(entries)
            pred = predictions.get(key, {"points": 0.0, "method": MODEL_METHOD})
            points = pred["points"] * availability_factor(p.status, p.chance_of_playing_next_round)
            method = pred["method"]
        else:
            opponent, is_home, kickoff, points, method = NO_FIXTURE_OPPONENT, False, deadline, 0.0, NO_FIXTURE_METHOD
        rows.append({
            "name": p.name, "position": p.position, "team": p.team,
            "predicted_points": round(float(points), 2), "value": int(p.now_cost),
            "opponent": opponent, "is_home": is_home, "gameweek": int(gameweek),
            "kickoff_time": kickoff, "prediction_method": method,
            "is_promoted_team": p.team in promoted, "fixtures": len(entries),
            "status": p.status, "chance_of_playing_next_round": p.chance_of_playing_next_round,
            "news": p.news if isinstance(p.news, str) else "", "element": int(p.element),
        })
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return df.sort_values(["predicted_points", "name"], ascending=[False, True]).reset_index(drop=True)


def save_shadow_pick(live_dir: str, season: str, event: Dict[str, Any], players: pd.DataFrame,
                     predictions: Dict[str, Dict[str, Any]], fixtures_by_team: Dict[str, List[Dict[str, Any]]],
                     promoted: Iterable[str], version: str, now: Optional[datetime] = None) -> Optional[str]:
    """Assemble the shadow's predictions like the live ones (same availability multiplier and
    blank-gameweek rule) and save its squad pick next to the live pick (model_review.save_picks,
    variant SHADOW; `now` as for the live pick). Returns the JSON path, None after the deadline,
    or "failed"."""
    shadow = {k: {"points": v["shadow_points"], "method": f"xgboost_{version}"}
              for k, v in predictions.items() if "shadow_points" in v}
    if predictions and not shadow:
        return "failed"   # the shadow model raised (logged by score_rows)
    try:
        from services.model_review import SHADOW, save_picks
        gameweek, deadline = int(event["id"]), event["deadline_time"]
        out = assemble_output(players, shadow, fixtures_by_team, gameweek, deadline, promoted)
        return save_picks(live_dir, season, gameweek, deadline, out, fixtures_by_team, now=now,
                          variant=SHADOW, model=version)
    except Exception:
        logger.exception("Couldn't save the shadow model's pick for GW%s", event["id"])
        return "failed"


def promoted_teams(previous_teams: Iterable[str], teams: Iterable[str]) -> List[str]:
    """Teams this season that were not in the previous historical season."""
    previous = set(previous_teams)
    if not previous:
        return []
    return sorted(t for t in set(teams) if t not in previous)


# ------------------------------------------------------------------ projections (later gameweeks)

def horizon_events(bootstrap: Dict[str, Any], event: Dict[str, Any], horizon: int = PROJECTION_HORIZON
                   ) -> List[Dict[str, Any]]:
    """`event` and the gameweeks after it, `horizon` in all (fewer at the end of the season)."""
    first = int(event["id"])
    return sorted((e for e in bootstrap["events"] if first <= int(e["id"]) < first + horizon),
                  key=lambda e: int(e["id"]))


def team_kickoffs(fixtures: Iterable[Dict[str, Any]], teams: Dict[int, str]) -> Dict[str, List[pd.Timestamp]]:
    """Every kickoff of each team on FPL's fixture list (played or not, any gameweek), sorted."""
    out: Dict[str, List[pd.Timestamp]] = {}
    for f in fixtures:
        if not f.get("kickoff_time"):
            continue                    # unscheduled (postponed) fixtures
        kickoff = pd.to_datetime(f["kickoff_time"], utc=True)
        for team_id in (f["team_h"], f["team_a"]):
            out.setdefault(teams[team_id], []).append(kickoff)
    for kickoffs in out.values():
        kickoffs.sort()
    return out


def rest_days(team: str, kickoff: Any, kickoffs: Dict[str, List[pd.Timestamp]]) -> float:
    """Days from the team's previous fixture to `kickoff` (clipped at 30 like the feature), NaN
    when the list has none before it."""
    times = kickoffs.get(team) or []
    kickoff = pd.to_datetime(kickoff, utc=True)
    i = bisect_left(times, kickoff)
    if i == 0:
        return np.nan
    return min((kickoff - times[i - 1]).total_seconds() / 86400, 30.0)


def project_points(players: pd.DataFrame, history: pd.DataFrame,
                   fixtures_by_team: Dict[str, List[Dict[str, Any]]], season: str, gameweek: int,
                   predict_fn: PredictFn, extras: Optional[pm.ExtraInputs],
                   kickoffs: Dict[str, List[pd.Timestamp]], snapshot_age: Optional[float] = None
                   ) -> Dict[str, float]:
    """element (str) -> model points for a gameweek after the next one, summed over its fixtures
    (each floored at 0), before any availability multiplier.

    Built like the next gameweek's (model_rows: the same history, ending before the next
    gameweek, and only this gameweek's fixtures), so every input stays as it is now: form, team
    strength, price, ownership and transfer ranks. The FPL snapshot (the status file) is about the
    next deadline, so here every player is scored as available (ml.snapshots.as_available; the
    caller's horizon_availability scales a flagged player), and its age is held at the next
    gameweek's (`snapshot_age`): training never saw a snapshot older than the previous deadline. days_rest, which would otherwise count from the player's last
    match in the history (before the next gameweek), is the gap to his team's previous fixture on
    FPL's list (a player without any earlier match keeps NaN for his first, as in training). Odds are whatever fixtures.csv prices before
    this gameweek's deadline, usually none.
    """
    rows = model_rows(players, history, fixtures_by_team, season, gameweek, extras)
    if rows.empty:
        return {}
    sn.as_available(rows)
    gap = pd.Series([rest_days(t, k, kickoffs) for t, k in zip(rows["team"], rows["kickoff_time"])],
                    index=rows.index, dtype=float)
    rows["days_rest"] = gap.where(rows["days_rest"].notna() & gap.notna(), rows["days_rest"])
    if snapshot_age is not None and "snap_age_days" in rows.columns:
        rows.loc[rows["snap_age_days"].notna(), "snap_age_days"] = snapshot_age
    points = pd.Series(np.clip(np.asarray(predict_fn(rows), dtype=float), 0.0, None), index=rows.index)
    return {str(int(k)): float(v) for k, v in points.groupby(rows["element"]).sum().items()}


def gameweek_days(fixtures_by_team: Dict[str, List[Dict[str, Any]]], deadline: Any = None
                  ) -> Tuple[Dict[str, date], Optional[date]]:
    """({club: the day of its first kickoff this gameweek}, the deadline's day or None): what
    horizon_availability compares the return dates in FPL's news with."""
    def day(value: Any) -> Optional[date]:
        stamp = pd.to_datetime(value, utc=True, errors="coerce") if value else pd.NaT
        return None if pd.isna(stamp) else stamp.date()

    by_team = {}
    for team, entries in fixtures_by_team.items():
        first = min((d for d in (day(e.get("kickoff_time")) for e in entries) if d is not None), default=None)
        if first is not None:
            by_team[team] = first
    return by_team, day(deadline)


def projection_frame(players: pd.DataFrame, gameweek: int, fixtures_by_team: Dict[str, List[Dict[str, Any]]],
                     points: Dict[str, float], step: int,
                     days: Sequence[Tuple[Dict[str, date], Optional[date]]] = ()) -> pd.DataFrame:
    """PROJECTION_COLUMNS for one gameweek, one row per current player: model `points` times
    horizon_availability(step), and the chance of playing as `availability`; a blank gameweek
    gives 0 and opponent "No fixture", a double gameweek the sum and both opponents. `days` are
    gameweek_days of the horizon's gameweeks up to this one, the next gameweek first: with them a
    return date in a player's news ("Expected back 6 Dec") decides his later gameweeks."""
    rows = []
    for p in players.itertuples(index=False):
        entries = fixtures_by_team.get(p.team) or []
        dates = [by_team.get(p.team, default) for by_team, default in days]
        today = dates[0] if dates else None
        back = return_date(getattr(p, "news", None), today) if today is not None else None
        avail = horizon_availability(p.status, p.chance_of_playing_next_round, step, back=back, dates=dates)
        chance = horizon_availability(p.status, p.chance_of_playing_next_round, step, factor=chance_of_playing,
                                      back=back, dates=dates)
        pts = points.get(str(int(p.element)), 0.0) * avail if entries else 0.0
        web_name = getattr(p, "web_name", None)
        rows.append({
            "element": int(p.element), "name": p.name,
            "web_name": web_name if isinstance(web_name, str) and web_name else p.name,
            "position": p.position, "team": p.team, "now_cost": int(p.now_cost), "gameweek": int(gameweek),
            "fixtures": len(entries), "opponents": describe_fixtures(entries)[0] if entries else NO_FIXTURE_OPPONENT,
            "predicted_points": round(float(pts), 2), "availability": float(chance), "status": p.status,
        })
    return pd.DataFrame(rows, columns=PROJECTION_COLUMNS)


def build_projections(players: pd.DataFrame, history: pd.DataFrame, season: str,
                      events: List[Dict[str, Any]], fixtures: List[Dict[str, Any]], teams: Dict[int, str],
                      next_points: Dict[int, float], predict_fn: PredictFn,
                      extras: Optional[pm.ExtraInputs] = None, snapshot_age: Optional[float] = None
                      ) -> Tuple[pd.DataFrame, Dict[int, str]]:
    """(projections for `events`, {gameweek: why it's missing}). The first event's points are
    `next_points` (element -> the live predicted_points, so it equals next_gw_predictions
    exactly); later ones come from project_points, one gameweek at a time. A gameweek that fails
    is logged and left out."""
    kickoffs = team_kickoffs(fixtures, teams)
    frames, problems = [], {}
    days: List[Tuple[Dict[str, date], Optional[date]]] = []
    for step, event in enumerate(events, start=1):
        gameweek = int(event["id"])
        try:
            by_team = gameweek_fixtures(fixtures, gameweek, teams)
            days.append(gameweek_days(by_team, event.get("deadline_time")))
            if step == 1:
                frame = projection_frame(players, gameweek, by_team, {}, step, days)
                frame["predicted_points"] = frame["element"].map(next_points).fillna(0.0).astype(float)
            else:
                points = project_points(players, history, by_team, season, gameweek, predict_fn, extras,
                                        kickoffs, snapshot_age)
                frame = projection_frame(players, gameweek, by_team, points, step, days)
        except Exception as exc:
            logger.exception("Couldn't project GW%s; it is left out of %s", gameweek, PROJECTIONS_NAME)
            problems[gameweek] = f"failed: {exc}"[:200]
            continue
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PROJECTION_COLUMNS)
    return out.sort_values(["gameweek", "predicted_points", "element"], ascending=[True, False, True],
                           kind="mergesort").reset_index(drop=True), problems


def save_projections(live_dir: str, players: pd.DataFrame, history: pd.DataFrame, season: str,
                     event: Dict[str, Any], bootstrap: Dict[str, Any], fixtures: List[Dict[str, Any]],
                     out: pd.DataFrame, predict_fn: PredictFn, extras: Optional[pm.ExtraInputs] = None,
                     snapshot_age: Optional[float] = None, horizon: int = PROJECTION_HORIZON) -> Dict[str, Any]:
    """Write LIVE_DATA_DIR/projections.csv (build_projections) atomically. Never raises: the
    summary says which gameweeks it covers and which failed (on a total failure the previous
    file stays, and its gameweek column tells a reader it is stale)."""
    started = time.perf_counter()
    path = os.path.join(live_dir, PROJECTIONS_NAME)
    try:
        events = horizon_events(bootstrap, event, horizon)
        next_points = dict(zip(out["element"].astype(int), out["predicted_points"].astype(float)))
        frame, problems = build_projections(players, history, season, events, fixtures, team_name_map(bootstrap),
                                            next_points, predict_fn, extras, snapshot_age)
        _atomic_write(path, lambda p: frame.to_csv(p, index=False))
    except Exception as exc:
        logger.exception("Couldn't write %s; the predictions are unaffected", PROJECTIONS_NAME)
        return {"output": None, "horizon": horizon, "gameweeks": [], "failed": f"failed: {exc}"[:200],
                "seconds": round(time.perf_counter() - started, 1)}
    return {"output": path, "horizon": horizon,
            "gameweeks": sorted(int(g) for g in frame["gameweek"].unique()),
            "failed": problems or None, "seconds": round(time.perf_counter() - started, 1)}


def _snapshot_age(rows: pd.DataFrame) -> Optional[float]:
    """The next gameweek's snap_age_days (one snapshot per gameweek, so one value), if any."""
    if "snap_age_days" not in rows.columns:
        return None
    ages = rows["snap_age_days"].dropna()
    return float(ages.iloc[0]) if len(ages) else None


def _load_players(status_csv: str) -> pd.DataFrame:
    players = pd.read_csv(status_csv)
    players = players[players["position"].isin(POSITIONS)].copy()
    players["news"] = players["news"].fillna("")
    return players.reset_index(drop=True)


def _context(client, live_dir: str, gameweek: Optional[int] = None):
    return _context_from(client.bootstrap(), client.fixtures(), gameweek)


def _context_from(bootstrap: Dict[str, Any], fixtures: List[Dict[str, Any]], gameweek: Optional[int] = None):
    season = season_label(bootstrap)
    if gameweek is None:
        event = next_gameweek(bootstrap)
        if event is None:
            raise RuntimeError("No upcoming gameweek: the season is over")
    else:
        event = next(e for e in bootstrap["events"] if e["id"] == gameweek)
    fixtures_by_team = gameweek_fixtures(fixtures, event["id"], team_name_map(bootstrap))
    first_kickoff = min((e["kickoff_time"] for es in fixtures_by_team.values() for e in es if e["kickoff_time"]),
                        default=event["deadline_time"])
    return season, event, fixtures_by_team, first_kickoff


def predict_next_gameweek(live_dir: str = LIVE_DATA_DIR, client=None, history_csv: str = HISTORY_CSV,
                          predict_fn: Optional[PredictFn] = None, shadow_fn: Optional[PredictFn] = None,
                          fetch_odds: bool = True) -> Dict[str, Any]:
    """Predict every current player's points for the next gameweek and write the CSV/JSON.

    Without `predict_fn` the served model predicts (load_model_fn) and the shadow model runs
    alongside it; an injected `predict_fn` runs alone unless `shadow_fn` is given too.
    `fetch_odds` first refreshes the odds files (refresh_odds; failures are only logged).
    Returns a summary: gameweek, deadline, model, odds, input coverage, row counts, output
    paths, the live and shadow picks, the projections (save_projections: path, gameweeks
    covered, failures), timing, memory.
    """
    started = time.perf_counter()
    own_client = client is None
    if own_client:
        from pipeline.fpl_api import FPLClient
        client = FPLClient()
    try:
        bootstrap, fixtures = client.bootstrap(), client.fixtures()
    finally:
        if own_client:
            client.close()
    season, event, fixtures_by_team, first_kickoff = _context_from(bootstrap, fixtures)
    gameweek = int(event["id"])
    odds_status = refresh_odds(live_dir, season) if fetch_odds else "not fetched"

    live_csv, status_csv = live_paths(live_dir, season)
    players = _load_players(status_csv)
    # Leakage guard: nothing from the predicted gameweek or later feeds the features.
    history = load_history(live_csv, season, before_gw=gameweek, before_time=first_kickoff,
                           history_csv=history_csv)
    extras_problems: Dict[str, str] = {}
    extras = load_extras(live_dir, season, status_csv, history_csv, problems=extras_problems)
    if predict_fn is None:
        predict_fn, version = load_model_fn()
        if shadow_fn is None:
            shadow_fn, shadow_version = _shadow_fn(version)
        else:
            shadow_version = pm.SHADOW_VERSION
    else:
        version = pm.MODEL_VERSION
        shadow_version = pm.SHADOW_VERSION if shadow_fn is not None else "skipped: no shadow model given"

    os.makedirs(live_dir, exist_ok=True)
    features_path = os.path.join(live_dir, FEATURES_NAME)
    rows = model_rows(players, history, fixtures_by_team, season, gameweek, extras)
    coverage = input_coverage(rows)
    snapshot_age = _snapshot_age(rows)
    logger.info("GW%s: %d upcoming rows, v4 input coverage %s", gameweek, len(rows), coverage)
    predictions = score_rows(rows, predict_fn, f"xgboost_{version}", shadow_fn, features_path, gameweek)
    del rows
    promoted = promoted_teams(previous_season_teams(history, season), players["team"])
    out = assemble_output(players, predictions, fixtures_by_team, gameweek, event["deadline_time"], promoted)

    csv_path = os.path.join(live_dir, OUTPUT_NAME)
    json_path = csv_path[:-4] + ".json"
    _atomic_write(json_path, lambda p: out.to_json(p, orient="records", indent=2))
    _atomic_write(csv_path, lambda p: out.to_csv(p, index=False))  # CSV last: it is what the API watches

    # Keep this gameweek's pick: the last run before the deadline leaves the final one.
    # A failure here mustn't cost the predictions themselves. Both picks are saved as of the same
    # moment: one deadline check, and the same archived_at, which marks them as one run's pair.
    saved_at = datetime.now(timezone.utc)
    try:
        from services.model_review import save_picks
        picks_path = save_picks(live_dir, season, gameweek, event["deadline_time"], csv_path, fixtures_by_team,
                                now=saved_at, model=version)
    except Exception:
        logger.exception("Couldn't save the model's pick for GW%s", gameweek)
        picks_path = "failed"
    if shadow_fn is not None:
        shadow = {"model": shadow_version,
                  "pick": save_shadow_pick(live_dir, season, event, players, predictions, fixtures_by_team,
                                           promoted, shadow_version, now=saved_at)}
    else:
        shadow = {"model": None, "pick": shadow_version}

    # The next PROJECTION_HORIZON gameweeks for the ML manager's planner, after everything above is
    # saved, one gameweek's features at a time (never raises)
    projections = save_projections(live_dir, players, history, season, event, bootstrap, fixtures, out,
                                   predict_fn, extras, snapshot_age)
    del extras

    has_fixture = out["fixtures"] > 0
    return {
        "season": season,
        "gameweek": gameweek,
        "deadline": event["deadline_time"],
        "model": version,
        "odds": odds_status,
        "extra_inputs": extras_problems or "ok",
        "input_coverage": coverage,
        "players": int(len(out)),
        "players_with_fixture": int(has_fixture.sum()),
        "players_unavailable": int((has_fixture & (out["predicted_points"] == 0)
                                    & out["status"].isin(["i", "s", "u", "n"])).sum()),
        "double_gameweek_players": int((out["fixtures"] > 1).sum()),
        "blank_teams": sorted(set(players["team"]) - set(fixtures_by_team)),
        "promoted_teams": promoted,
        "history_rows": int(len(history)),
        "history_last_kickoff": str(history["kickoff_time"].max()) if len(history) else None,
        "output": csv_path,
        "output_json": json_path,
        "model_inputs": features_path if os.path.exists(features_path) else None,
        "model_pick": picks_path,
        "shadow": shadow,
        "projections": projections,
        "seconds": round(time.perf_counter() - started, 1),
        "peak_memory_mb": (round(peak_memory_mb(), 1) if peak_memory_mb() is not None else None),
    }


def backtest(gameweek: int, live_dir: str = LIVE_DATA_DIR, client=None, history_csv: str = HISTORY_CSV,
             predict_fn: Optional[PredictFn] = None) -> Dict[str, Any]:
    """Predict an already-played gameweek of this season from earlier data only and score it.

    No availability multiplier. v4's inputs follow training's rules: the gameweek's transfer
    counts come from its rows in the season CSV, and odds and snapshots count only if captured
    before its deadline (so never the current status file). Compares against the naive
    "average of the last RECENT_MATCHES matches" baseline.
    """
    own_client = client is None
    if own_client:
        from pipeline.fpl_api import FPLClient
        client = FPLClient()
    try:
        season, event, fixtures_by_team, first_kickoff = _context(client, live_dir, gameweek)
    finally:
        if own_client:
            client.close()
    live_csv, status_csv = live_paths(live_dir, season)
    players = _load_players(status_csv)
    history = load_history(live_csv, season, before_gw=gameweek, before_time=first_kickoff,
                           history_csv=history_csv)
    # The gameweek's transfers as its history rows carry them, not today's
    played = pd.read_csv(live_csv, usecols=["element", "GW", "transfers_in", "transfers_out"])
    played = played[played["GW"] == gameweek].drop_duplicates("element").set_index("element")
    for col in ("in", "out"):
        players[f"transfers_{col}_event"] = players["element"].map(played[f"transfers_{col}"])
    preds = predict_players(players, history, fixtures_by_team, season, gameweek,
                            predict_fn or load_model_fn()[0],
                            extras=load_extras(live_dir, season, status_csv, history_csv))
    live = pd.read_csv(live_csv, usecols=["element", "GW", "total_points"])
    actual = live[live["GW"] == gameweek].groupby("element")["total_points"].sum()
    actual.index = actual.index.astype(str)
    by_name = history.sort_values("kickoff_time").groupby("name").tail(RECENT_MATCHES)
    naive_by_name = by_name.groupby("name")["total_points"].mean()
    naive = players.set_index(players["element"].astype(int).astype(str))["name"].map(naive_by_name)
    df = pd.DataFrame({"pred": pd.Series({k: v["points"] for k, v in preds.items()})})
    df["actual"] = actual
    df["naive"] = naive
    df = df.dropna(subset=["actual"]).fillna({"naive": 0.0})
    top = lambda col: float(df.nlargest(30, col)["actual"].mean())
    return {
        "gameweek": gameweek,
        "players": int(len(df)),
        "history_last_kickoff": str(history.loc[history["season"] == season, "kickoff_time"].max()),
        "first_kickoff": first_kickoff,
        "model_mae": round(float((df.pred - df.actual).abs().mean()), 3),
        "naive_mae": round(float((df.naive - df.actual).abs().mean()), 3),
        "model_spearman": round(float(df.pred.corr(df.actual, method="spearman")), 3),
        "naive_spearman": round(float(df.naive.corr(df.actual, method="spearman")), 3),
        "model_top30_actual": round(top("pred"), 3),
        "naive_top30_actual": round(top("naive"), 3),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate next-gameweek predictions from fresh data")
    parser.add_argument("--live-dir", default=LIVE_DATA_DIR)
    parser.add_argument("--backtest", type=int, metavar="GW",
                        help="Score a played gameweek of this season instead of writing predictions")
    parser.add_argument("--no-odds-fetch", action="store_true",
                        help="Use the odds files already in LIVE_DATA_DIR/odds without downloading")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backtest:
        result = backtest(args.backtest, args.live_dir)
    else:
        result = predict_next_gameweek(args.live_dir, fetch_odds=not args.no_odds_fetch)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
