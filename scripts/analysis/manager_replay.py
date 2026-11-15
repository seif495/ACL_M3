"""
Season replay of the ML manager on past seasons: what the planner's transfers, hits, chips and
armband (services/fpl_manager_planner.py, run by pipeline/ml_manager.py) would have scored, with the
points model trained only on earlier seasons and its projections rebuilt at every deadline by the
live pipeline's own code (pipeline.predict_next_gw). Too heavy for a laptop: the "Manager replay"
workflow (.github/workflows/manager-replay.yml) runs

    python -m scripts.analysis.manager_replay project --season 2023-24 --start-gws 1,5,9,13 --out proj_2023-24
    python -m scripts.analysis.manager_replay plan --proj proj_2023-24 --variants default,hold \
        --start-gws 1,5,9,13 --out plan_2023-24.json
    python -m scripts.analysis.manager_replay report 'plan_*.json' --proj 'proj_*' --ref default --out report.md

project  trains the model (v4; --model v3 for the shadow) on the seasons before S as production
         trains it (ml.train_points_v3: early-stopped on S-1, refit on every earlier season with the
         tree count scaled by the rows). Then, for every deadline of S, it rebuilds what the live
         pipeline would have had and runs predict_next_gw's model_rows, score_rows, assemble_output
         and build_projections on it: the next gameweek's predictions and the planner's projections
         for the next PROJECTION_HORIZON gameweeks. Writes <out>.projections.parquet (deadline_gw +
         the projections.csv columns), <out>.predictions.parquet (each deadline's
         next_gw_predictions table), <out>.actual.parquet (gameweek, element, points, minutes),
         <out>.prices.parquet (gameweek, element, now_cost) and <out>.json (model, deadlines, coverage).
plan     plays the season from each of --start-gws. The first squad is the predicted Team of the Week
         (season_service.predicted_selection, as the ML manager's GW9 start). Then every gameweek the
         planner plans from that deadline's projections under a variant (VARIANTS: planner constants,
         horizon, chips; "hold" never transfers), services/fpl_manager validates and applies the
         entry, and score_entry scores it with the actual points (auto-subs, vice-captain, chips).
         Today's rules (fm.DEFAULT_RULES) in every season.
report   per variant and season: total points, the difference from --ref with a paired bootstrap CI
         over gameweeks, transfers, hits and chips; and how the projections' quality falls with the
         gameweeks ahead (RMSE, top-20 actual and bias by step), which is what DECAY discounts.

Paths: a season played from one start gameweek is one path. Decisions carry over, so where a season
starts changes everything after it: one start moved a variant's lead by up to 180 points (2026-10-30).
Playing each season from several starts (projected once, from the earliest) and pooling the paths
is what makes effects of a point or two a gameweek measurable. The report counts the paths a variant
is ahead on, which one lucky path can't fake.

What the live pipeline knew at deadline t, so all the replay uses:
  * matches: the HISTORY_SEASONS seasons before S and S's matches before t (predict_next_gw.load_history)
  * players: everyone with a GW t match, plus the players of a club without one (a blank gameweek)
    from their latest match; price, ownership and the gameweek's transfers in and out from the GW t
    row (FPL's values before the deadline, as in training)
  * availability: the latest FPL snapshot before the deadline (data/fpl_snapshots.csv, by
    ml.snapshots' rule, so none when it predates the previous deadline): status and chance of playing
  * odds: only prices football-data.co.uk had collected before the deadline (the next round's)
  * snapshot features: only captures before the deadline

Known differences from live:
  * the fixture list is the season as played: a postponed match sits in its final gameweek, so a
    double or blank gameweek is known PROJECTION_HORIZON gameweeks ahead (FPL usually announces
    them 2-6 weeks before)
  * the model is trained once per season (production retrains weekly)
  * snapshots are a day or two old at the deadline (the live status file is hours old), and about 3
    of 38 gameweeks a season have none, so their flagged players count as available
  * today's rules (two of each chip, one per half; five banked transfers) for every season, with
    each season's own points
"""
import argparse
import contextlib
import glob
import json
import os
import sys
import time
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from ml import odds as od  # noqa: E402
from ml import points_model as pm  # noqa: E402
from ml import snapshots as sn  # noqa: E402
from pipeline import predict_next_gw as pnp  # noqa: E402
from services import fpl_manager as fm  # noqa: E402
from services import fpl_manager_planner as planner  # noqa: E402
from services import model_review as mr  # noqa: E402
from services import season_service as ss  # noqa: E402

SEASONS = ["2022-23", "2023-24", "2024-25"]   # the seasons with an FPL snapshot before most deadlines
HORIZON = pnp.PROJECTION_HORIZON
MAX_HORIZON = 6             # the longest a variant plans over (project --horizon 6)
DEADLINE = timedelta(minutes=od.DEADLINE_MINUTES)

# Planner variants. "set" overrides services/fpl_manager_planner constants for the whole season,
# "horizon" the gameweeks planned over (at most HORIZON: the projections stop there), "chips" False
# never plays one. hold keeps the first squad all season (the XI, bench and armband still follow
# the projections), so the others' gain over it is what transfers and chips earn.
VARIANTS: Dict[str, Dict[str, Any]] = {
    "default": {},
    "hold": {"set": {"TRANSFER_MARGIN": 1e6, "MAX_HITS_PER_GW": 0}, "chips": False},
    "nochips": {"chips": False},
    "h1": {"horizon": 1},
    "h2": {"horizon": 2},
    # Longer than production's 4: only with projections made by `project --horizon 6` (2026-11-15)
    "h4": {"horizon": 4},
    "h5": {"horizon": 5},
    "h6": {"horizon": 6},
    "decay08": {"set": {"DECAY": 0.8}},
    "decay1": {"set": {"DECAY": 1.0}},
    "ftv0": {"set": {"FT_END_VALUE": 0.0}},
    "ftv3": {"set": {"FT_END_VALUE": 3.0}},
    "margin05": {"set": {"TRANSFER_MARGIN": 0.5}},
    "margin1": {"set": {"TRANSFER_MARGIN": 1.0}},
    "margin15": {"set": {"TRANSFER_MARGIN": 1.5}},
    "margin2": {"set": {"TRANSFER_MARGIN": 2.0}},
    "margin1_hitm4": {"set": {"TRANSFER_MARGIN": 1.0, "HIT_MARGIN": 4.0}},
    "hitm2": {"set": {"HIT_MARGIN": 2.0}},
    "hitm4": {"set": {"HIT_MARGIN": 4.0}},
    "nohits": {"set": {"MAX_HITS_PER_GW": 0}},
    # 2026-10-31: the chip rules (services/fpl_manager_planner.chip_decision), one threshold at a
    # time each way; late_only plays a chip only when its window forces it
    "tc8": {"set": {"TRIPLE_CAPTAIN_MIN_POINTS": 8.0, "TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS": 6.0}},
    "tc10": {"set": {"TRIPLE_CAPTAIN_MIN_POINTS": 10.0, "TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS": 8.0}},
    "bb9": {"set": {"BENCH_BOOST_MIN_POINTS": 9.0}},
    "bb15": {"set": {"BENCH_BOOST_MIN_POINTS": 15.0}},
    "fh8": {"set": {"FREE_HIT_MIN_GAIN": 8.0}},
    "fh16": {"set": {"FREE_HIT_MIN_GAIN": 16.0}},
    "wc10": {"set": {"WILDCARD_MIN_GAIN": 10.0}},
    "wc25": {"set": {"WILDCARD_MIN_GAIN": 25.0}},
    "late_only": {"set": {k: 1e6 for k in ("TRIPLE_CAPTAIN_MIN_POINTS", "TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS",
                                           "BENCH_BOOST_MIN_POINTS", "FREE_HIT_MIN_GAIN", "WILDCARD_MIN_GAIN")}},
}


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_gws(text: str) -> List[int]:
    """'1,5,9,13' -> [1, 5, 9, 13] (sorted, without repeats)."""
    gws = sorted({int(x) for x in str(text).split(",") if x.strip()})
    if not gws or not all(1 <= g <= 38 for g in gws):
        raise argparse.ArgumentTypeError(f"gameweeks must be 1..38, got {text!r}")
    return gws


# ----------------------------------------------------------------------------- what was known at t

def api_fixtures(rows: pd.DataFrame) -> Tuple[List[Dict[str, Any]], Dict[int, str]]:
    """A season's matches (pm.load_history rows) as FPL's fixtures list, with team ids, for
    predict_next_gw's gameweek_fixtures / team_kickoffs / build_projections."""
    teams = {i: t for i, t in enumerate(sorted(rows["team"].dropna().unique()), start=1)}
    ids = {t: i for i, t in teams.items()}
    sides = rows.drop_duplicates(["fixture", "team"])
    home = sides[sides["was_home"]].set_index("fixture")
    away = sides[~sides["was_home"]].set_index("fixture")
    out = []
    for fixture in sorted(set(home.index) & set(away.index)):
        h, a = home.loc[fixture], away.loc[fixture]
        out.append({"id": int(fixture), "event": int(h["GW"]), "team_h": ids[h["team"]], "team_a": ids[a["team"]],
                    "kickoff_time": pd.Timestamp(h["kickoff_time"]).isoformat().replace("+00:00", "Z")})
    return out, teams


def deadlines(rows: pd.DataFrame) -> Dict[int, pd.Timestamp]:
    """{gameweek: its deadline}: the first kickoff minus 90 minutes, as FPL sets it."""
    first = rows.groupby("GW")["kickoff_time"].min()
    return {int(gw): ko - DEADLINE for gw, ko in first.items()}


def history_at(history: pd.DataFrame, season: str, gameweek: int, first_kickoff: pd.Timestamp,
               past_seasons: int = pnp.HISTORY_SEASONS) -> pd.DataFrame:
    """predict_next_gw.load_history at the deadline: the `past_seasons` seasons before `season` and
    its matches before `gameweek` that kicked off before the gameweek's first kickoff."""
    past = sorted(s for s in history["season"].unique() if s < season)[-past_seasons:] if past_seasons else []
    current = history["season"] == season
    early = current & (history["GW"] < gameweek) & (history["kickoff_time"] < first_kickoff)
    return history[history["season"].isin(past) | early].reset_index(drop=True)


def snapshot_status(rows: pd.DataFrame, snaps: pd.DataFrame) -> pd.DataFrame:
    """(GW, element, status, chance_of_playing_next_round) from the snapshot each gameweek uses
    (ml.snapshots.snapshot_windows: the latest captured before its deadline, none if stale)."""
    win = sn.snapshot_windows(sn.gameweek_deadlines(rows), snaps).dropna(subset=["sha"])
    cols = ["season", "sha", "element", "status", "chance_of_playing_next_round"]
    got = win[["season", "GW", "sha"]].merge(snaps[cols].drop_duplicates(["season", "sha", "element"]),
                                             on=["season", "sha"])
    got["element"] = pd.to_numeric(got["element"], errors="coerce")
    return got.dropna(subset=["element"]).astype({"element": int})[
        ["GW", "element", "status", "chance_of_playing_next_round"]]


def players_at(rows: pd.DataFrame, gameweek: int, status: pd.DataFrame) -> pd.DataFrame:
    """The player status table (pipeline.current_season's columns that predict_next_gw reads) at
    `gameweek`'s deadline. Players with a GW `gameweek` match, plus those whose club has none (from
    their latest earlier match); a player missing from both has left the league."""
    this = rows[rows["GW"] == gameweek].sort_values("kickoff_time", kind="mergesort").drop_duplicates("element")
    playing = set(this["team"])
    earlier = rows[rows["GW"] < gameweek].sort_values(["GW", "kickoff_time"], kind="mergesort")
    last = earlier.drop_duplicates("element", keep="last")
    blank = last[~last["team"].isin(playing) & ~last["element"].isin(this["element"])]
    blank = blank.assign(transfers_in=np.nan, transfers_out=np.nan)
    p = pd.concat([this, blank], ignore_index=True)
    out = pd.DataFrame({
        "element": p["element"].astype(int), "name": p["name"], "web_name": p["name"], "position": p["position"],
        "team": p["team"], "now_cost": p["value"].astype(int), "selected_by_percent": p["selected"],
        "transfers_in_event": p["transfers_in"], "transfers_out_event": p["transfers_out"], "news": "",
    })
    flags = status[status["GW"] == gameweek].drop(columns="GW").drop_duplicates("element")
    out = out.merge(flags, on="element", how="left")
    out["status"] = out["status"].fillna("a")
    return out.sort_values("element").reset_index(drop=True)


def odds_collection(odds: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """The odds table with `collected_at`: when football-data.co.uk collected each match's
    pre-closing prices (ml.odds.collected_at of its kickoff in the history); NaT without one."""
    if odds.empty:
        return odds.assign(collected_at=pd.Series(dtype="datetime64[ns, UTC]"))
    sides = history.drop_duplicates(["season", "fixture", "team"])
    home = sides[sides["was_home"]][["season", "fixture", "team", "kickoff_time"]]
    away = sides[~sides["was_home"]][["season", "fixture", "team"]]
    matches = home.merge(away, on=["season", "fixture"], suffixes=("_h", "_a"))
    matches = matches.rename(columns={"team_h": "home", "team_a": "away"}).drop_duplicates(["season", "home", "away"])
    out = odds.merge(matches[["season", "home", "away", "kickoff_time"]], on=["season", "home", "away"], how="left")
    out["collected_at"] = od.collected_at(out["kickoff_time"])
    return out.drop(columns="kickoff_time")


def extras_at(base: pm.ExtraInputs, odds: pd.DataFrame, deadline: pd.Timestamp) -> pm.ExtraInputs:
    """v4's inputs as they stood at `deadline`: prices collected by then, snapshots captured before."""
    known = odds[odds["collected_at"].notna() & (odds["collected_at"] <= deadline)].drop(columns="collected_at")
    snaps = base.snapshots
    if snaps is not None and len(snaps):
        snaps = snaps[pd.to_datetime(snaps["captured_at"], utc=True) < deadline]
    return pm.ExtraInputs(codes=base.codes, odds=known.reset_index(drop=True), snapshots=snaps,
                          ratings_prior=base.ratings_prior)


# ----------------------------------------------------------------------------- the model

def train_model(feats: pd.DataFrame, season: str, version: str) -> Tuple[Any, Dict[str, Any]]:
    """(predict function, info): `version`'s columns, fit like ml.train_points_v3 on the seasons
    before `season`: early-stopped on the last of them, then production's ensemble refit on all
    (pm.fit_ensemble, each member with pm.ensemble_trees of the early-stopped count)."""
    cols = pm.feature_columns(version)
    seasons = sorted(s for s in feats["season"].unique() if s < season)
    if len(seasons) < 2:
        raise SystemExit(f"{season}: needs two earlier seasons to train on, has {seasons}")
    train = feats[feats["season"].isin(seasons[:-1])]
    valid = feats[feats["season"] == seasons[-1]]
    model = pm.fit(train, valid, cols=cols)
    best = int(model.best_iteration) + 1
    both = feats[feats["season"].isin(seasons)]
    n_trees = pm.ensemble_trees(best, len(both), len(train))
    final = pm.fit_ensemble(both, n_trees, cols=cols)
    info = {"version": version, "seasons": seasons, "best_trees": best, "n_trees": n_trees, "members": len(final),
            "rows": len(both)}
    return (lambda frame: pm.predict(final, {"features": cols}, frame)), info


# ----------------------------------------------------------------------------- input variants

def _no_odds(rows: pd.DataFrame, coef: Any) -> None:
    """A run before the bookmakers' prices are collected (early in the week)."""
    rows[od.FEATURES] = np.nan


def _rating_odds(rows: pd.DataFrame, coef: Any) -> None:
    """The odds features as the model's own team ratings imply them (ml.odds.from_ratings)."""
    rows[od.FEATURES] = od.from_ratings(rows["rt_xgf"], rows["rt_xga"], coef).to_numpy()


def _smooth_form(rows: pd.DataFrame, coef: Any) -> None:
    """Steadier form for a gameweek further off: the 3-match averages become the 6-match ones and
    the last match's values the 3-match averages."""
    cols = set(rows.columns)
    r3 = {c: rows[c].copy() for c in cols if c.endswith("_r3")}
    for c in r3:
        if c[:-1] + "6" in cols:
            rows[c] = rows[c[:-1] + "6"]
    for c in cols:
        if c.endswith("_r1") and c[:-1] + "3" in r3:
            rows[c] = r3[c[:-1] + "3"]


# What the model is given for a gameweek, scored against `base` (production's inputs) by gameweeks
# ahead (project --input-variants, then report). Step 1 is the next gameweek: there `noodds` is a
# prediction made before the prices are in and `podds` one that fills them from the ratings.
INPUT_VARIANTS: Dict[str, Tuple[Any, ...]] = {
    "base": (),
    "noodds": (_no_odds,),
    "podds": (_rating_odds,),
    "smooth": (_smooth_form,),
    "podds_smooth": (_rating_odds, _smooth_form),
}


def input_variant_points(rows: pd.DataFrame, players: pd.DataFrame, predict_fn, coef: Any, deadline_gw: int,
                         gameweek: int) -> pd.DataFrame:
    """One row per player with a fixture in `gameweek`: his points under every INPUT_VARIANTS entry
    (pred_<variant>), with the availability multiplier production applies at that step."""
    step = gameweek - deadline_gw + 1
    flags = players.set_index(players["element"].astype(int))[["status", "chance_of_playing_next_round"]]
    out: Dict[str, Dict[int, float]] = {}
    for name, transforms in INPUT_VARIANTS.items():
        frame = rows.copy()
        for transform in transforms:
            transform(frame, coef)
        out[name] = {int(k): v for k, v in pnp.points_by_element(frame, predict_fn).items()}
    elements = sorted(out["base"])
    mult = np.array([pnp.horizon_availability(flags.at[e, "status"], flags.at[e, "chance_of_playing_next_round"], step)
                     if e in flags.index else 1.0 for e in elements])
    table = pd.DataFrame({"deadline_gw": deadline_gw, "gameweek": gameweek, "step": step, "element": elements})
    for name, pts in out.items():
        table[f"pred_{name}"] = np.array([pts.get(e, 0.0) for e in elements]) * mult
    return table


def input_variant_quality(inputs: pd.DataFrame, actual: pd.DataFrame, top: int = 20, n_boot: int = 1000,
                          seed: int = 0) -> pd.DataFrame:
    """By gameweeks ahead and variant: RMSE, correlation and the top `top` picks' actual points a
    player, and the last two against `base` with a 95% CI resampling deadlines (season, deadline_gw).
    `inputs`: input_variant_points tables with a `season`; `actual`: season, gameweek, element, points."""
    d = inputs.merge(actual[["season", "gameweek", "element", "points"]], on=["season", "gameweek", "element"],
                     how="left")
    d["points"] = d["points"].fillna(0.0)
    names = [c[5:] for c in d.columns if c.startswith("pred_")]
    rng = np.random.default_rng(seed)
    rows = []
    for step, g in d.groupby("step"):
        groups = [x for _, x in g.groupby(["season", "deadline_gw"])]
        n = np.array([len(x) for x in groups], dtype=float)
        sse, tops = {}, {}
        for name in names:
            sse[name] = np.array([((x[f"pred_{name}"] - x["points"]) ** 2).sum() for x in groups])
            tops[name] = np.array([x.nlargest(top, f"pred_{name}")["points"].mean() for x in groups])
        picks = rng.integers(0, len(groups), (n_boot, len(groups)))
        for name in names:
            rmse = float(np.sqrt(sse[name].sum() / n.sum()))
            row = {"step": int(step), "variant": name, "deadlines": len(groups), "rmse": rmse,
                   "corr": float(g[f"pred_{name}"].corr(g["points"])), f"top{top}": float(tops[name].mean())}
            if name != "base":
                d_rmse = np.sqrt(sse[name][picks].sum(axis=1) / n[picks].sum(axis=1)) \
                    - np.sqrt(sse["base"][picks].sum(axis=1) / n[picks].sum(axis=1))
                d_top = (tops[name] - tops["base"])[picks].mean(axis=1)
                row.update(rmse_vs_base=rmse - float(np.sqrt(sse["base"].sum() / n.sum())),
                           rmse_lo=float(np.percentile(d_rmse, 2.5)), rmse_hi=float(np.percentile(d_rmse, 97.5)),
                           top_vs_base=float((tops[name] - tops["base"]).mean()),
                           top_lo=float(np.percentile(d_top, 2.5)), top_hi=float(np.percentile(d_top, 97.5)))
            rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- project

_JOB: Dict[str, Any] = {}   # the season's inputs for project_deadline (cmd_project sets them)


def project_deadline(gameweek: int) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """(projections, next_gw_predictions table, info) as the live pipeline would have made them at
    `gameweek`'s deadline."""
    j = _JOB
    season, deadline = j["season"], j["deadlines"][gameweek]
    history = history_at(j["history"], season, gameweek, deadline + DEADLINE)
    players = players_at(j["rows"], gameweek, j["status"])
    extras = extras_at(j["extras"], j["odds"], deadline) if j["extras"] is not None else None
    by_team = pnp.gameweek_fixtures(j["fixtures"], gameweek, j["teams"])
    rows = pnp.model_rows(players, history, by_team, season, gameweek, extras)
    preds = pnp.score_rows(rows, j["predict_fn"], f"xgboost_{j['version']}")
    out = pnp.assemble_output(players, preds, by_team, gameweek, deadline.isoformat(), [])
    next_points = dict(zip(out["element"].astype(int), out["predicted_points"].astype(float)))
    events = [{"id": g} for g in range(gameweek, gameweek + j.get("horizon", HORIZON)) if g in j["deadlines"]]
    variants: List[pd.DataFrame] = []
    on_rows = None
    if j.get("input_variants"):
        def on_rows(step_rows: pd.DataFrame, step_gw: int) -> None:
            variants.append(input_variant_points(step_rows, players, j["predict_fn"], j["rating_odds"], gameweek, step_gw))
        if len(rows):
            on_rows(rows, gameweek)
    proj, problems = pnp.build_projections(players, history, season, events, j["fixtures"], j["teams"],
                                           next_points, j["predict_fn"], extras, pnp._snapshot_age(rows), on_rows)
    info = {"gameweek": gameweek, "players": len(players), "rows": len(rows),
            "flagged": int((players["status"] != "a").sum()),
            "snapshot": bool(len(j["status"]) and (j["status"]["GW"] == gameweek).any()),
            "coverage": pnp.input_coverage(rows) if len(rows) else {}, "problems": problems}
    if variants:
        info["input_variants"] = pd.concat(variants, ignore_index=True)
    return proj.assign(deadline_gw=gameweek), out.assign(deadline_gw=gameweek), info


def cmd_project(args) -> None:
    t0 = time.time()
    history = pm.load_history()
    if args.season not in set(history["season"]):
        raise SystemExit(f"{args.season} isn't in {pm.HISTORY_CSV}")
    extras = None if args.model == "v3" else pm.load_extra_inputs(ratings_prior=False)
    feats = pm.build_features(history, extras=extras)
    feats = feats[feats["total_points"].notna()]
    log(f"training features: {len(feats)} rows in {time.time() - t0:.0f}s")
    predict_fn, model_info = train_model(feats, args.season, args.model)
    rating_odds = None
    if args.input_variants:
        if args.model == "v3":
            raise SystemExit("--input-variants needs v4's ratings and odds features")
        # Fitted on the seasons the model trained on, never the replayed one
        rating_odds = od.fit_ratings_map(feats[feats["season"] < args.season])
        model_info["rating_odds"] = rating_odds
        log(f"rating-implied odds: log(goals) = a + b log(rating xG), (for, against) = {rating_odds}")
    del feats
    log(f"model {args.model}: {model_info}")

    rows = history[history["season"] == args.season].copy()
    rows["element"] = rows["element"].astype(int)
    fixtures, teams = api_fixtures(rows)
    dl = deadlines(rows)
    base = None if args.model == "v3" else pm.load_extra_inputs(ratings_prior=True)
    snaps = base.snapshots if base is not None else sn.load_snapshots()
    _JOB.update(season=args.season, history=history, rows=rows, fixtures=fixtures, teams=teams, deadlines=dl,
                status=snapshot_status(rows, snaps), extras=base, version=args.model, predict_fn=predict_fn,
                horizon=args.horizon, input_variants=args.input_variants, rating_odds=rating_odds,
                odds=odds_collection(base.odds, history) if base is not None else None)
    gws = [g for g in sorted(dl) if min(args.start_gws) <= g <= (args.end_gw or 38)]
    results = {}
    inputs = []
    for gw in gws:
        started = time.time()
        results[gw] = project_deadline(gw)
        if "input_variants" in results[gw][2]:
            inputs.append(results[gw][2].pop("input_variants"))
        log(f"  GW{gw} ({time.time() - started:.0f}s): {results[gw][2]}")
    if inputs:
        pd.concat(inputs, ignore_index=True).to_parquet(f"{args.out}.inputs.parquet", index=False)

    proj = pd.concat([r[0] for r in results.values()], ignore_index=True)
    preds = pd.concat([r[1] for r in results.values()], ignore_index=True)
    proj.to_parquet(f"{args.out}.projections.parquet", index=False)
    preds.to_parquet(f"{args.out}.predictions.parquet", index=False)
    actual = rows.groupby(["GW", "element"], as_index=False)[["total_points", "minutes"]].sum()
    actual.rename(columns={"GW": "gameweek", "total_points": "points"}).to_parquet(
        f"{args.out}.actual.parquet", index=False)
    prices = pd.concat([players_at(rows, g, _JOB["status"])[["element", "now_cost"]].assign(gameweek=g) for g in gws],
                       ignore_index=True)
    prices.to_parquet(f"{args.out}.prices.parquet", index=False)
    summary = {"season": args.season, "model": model_info, "gameweeks": gws, "horizon": args.horizon,
               "deadlines": {str(g): dl[g].isoformat() for g in gws},
               "per_gameweek": [results[g][2] for g in gws], "seconds": round(time.time() - t0, 1)}
    with open(f"{args.out}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    log(f"projected {len(gws)} gameweeks of {args.season} in {time.time() - t0:.0f}s")


# ----------------------------------------------------------------------------- plan

@contextlib.contextmanager
def planner_constants(overrides: Dict[str, Any]):
    """Set services/fpl_manager_planner constants for the duration (restored after)."""
    old = {k: getattr(planner, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(planner, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(planner, k, v)


def load_projected(prefix: str) -> Dict[str, Any]:
    with open(f"{prefix}.json", encoding="utf-8") as f:
        summary = json.load(f)
    return {"summary": summary, "season": summary["season"],
            "projections": pd.read_parquet(f"{prefix}.projections.parquet"),
            "predictions": pd.read_parquet(f"{prefix}.predictions.parquet"),
            "actual": pd.read_parquet(f"{prefix}.actual.parquet"),
            "prices": pd.read_parquet(f"{prefix}.prices.parquet")}


def start_entry(predictions: pd.DataFrame, season: str, gameweek: int, deadline: str,
                rules: fm.Rules) -> Dict[str, Any]:
    """The first entry: the predicted Team of the Week at `gameweek` (as the ML manager's start)."""
    selection, details = ss.predicted_selection(predictions.to_dict("records"))
    pick = mr.build_picks(selection, details, season, gameweek, deadline, {}, archived_at=deadline,
                          model="replay")
    return fm.start_entry_from_pick(pick, deadline, now=pd.Timestamp(deadline) - timedelta(hours=2),
                                    rules=rules, start_gameweek=gameweek)


def play_season(data: Dict[str, Any], variant: str, start_gw: int = 1, rules: fm.Rules = fm.DEFAULT_RULES,
                time_limit: float = planner.SOLVE_TIME_LIMIT) -> Dict[str, Any]:
    """One season under `variant` (VARIANTS): {"season", "variant", "total", "gameweeks": [...]}."""
    spec = VARIANTS[variant]
    season = data["season"]
    deadline_of = data["summary"]["deadlines"]
    proj, preds = data["projections"], data["predictions"]
    actual = {int(g): {int(e): {"points": int(p), "minutes": int(m)} for e, p, m in
                       zip(d["element"], d["points"], d["minutes"])}
              for g, d in data["actual"].groupby("gameweek")}
    prices = {int(g): dict(zip(d["element"].astype(int), d["now_cost"].astype(int)))
              for g, d in data["prices"].groupby("gameweek")}
    have = [g for g in data["summary"]["gameweeks"] if g >= start_gw]
    gws = list(range(have[0], have[-1] + 1)) if have else []
    state, total, out, t0 = None, 0, [], time.time()
    with planner_constants(spec.get("set", {})):
        for gw in gws:
            if str(gw) not in deadline_of:     # no matches at all (postponed): FPL rolls the team over
                if state is not None:
                    state = fm.apply_entry(state, fm.rollover_entry(state, gw, None, rules=rules), rules)
                out.append({"gameweek": gw, "points": 0, "gross": 0, "hit_cost": 0, "chip": None, "transfers": 0,
                            "hits": 0, "captain": None, "armband": None, "bench_points": 0, "projected": None,
                            "optimal": None, "free_transfers": None, "bank": None, "team_value": None,
                            "problems": [], "seconds": 0.0, "void": True})
                continue
            deadline = deadline_of[str(gw)]
            now = pd.Timestamp(deadline) - timedelta(hours=2)
            started, problems = time.perf_counter(), []
            if state is None:
                state = fm.new_state(season, rules, gw)
                entry = start_entry(preds[preds["deadline_gw"] == gw], season, gw, deadline, rules)
            else:
                current = fm.squad_at_prices(state, prices[gw], rules)
                chips = fm.chip_deadlines(rules, state["chips_used"], gw) if spec.get("chips", True) else {}
                try:
                    entry = planner.plan(current, proj[proj["deadline_gw"] == gw].drop(columns="deadline_gw"),
                                         rules.to_dict(), gw, chips, horizon=spec.get("horizon", HORIZON),
                                         season=season, deadline=deadline, now=now, time_limit=time_limit)
                    entry["season"] = season
                    problems = fm.validate_entry(state, entry, rules, prices[gw])
                except Exception as exc:  # noqa: BLE001 - a failed plan rolls the team over, as live
                    problems = [f"plan failed: {exc}"[:300]]
                if problems:
                    entry = fm.rollover_entry(state, gw, deadline, prices=prices[gw], rules=rules, now=now)
            result = fm.score_entry(entry, actual.get(gw, {}), {"finished": True}, [], total)
            total = int(result["total"])
            state = fm.apply_entry(state, entry, rules)
            out.append({"gameweek": gw, "points": result["points"], "gross": result["gross"],
                        "hit_cost": result["hit_cost"], "chip": entry.get("chip"),
                        "transfers": int(entry.get("transfers_made") or 0), "hits": int(entry.get("hits") or 0),
                        "captain": result["captain"], "armband": result["armband"],
                        "bench_points": result["bench_points"], "projected": entry.get("projected_points"),
                        "optimal": entry.get("optimal"), "free_transfers": entry.get("free_transfers"),
                        "bank": entry.get("bank_after"), "team_value": fm.team_value(entry.get("squad") or []),
                        "problems": problems, "seconds": round(time.perf_counter() - started, 1)})
            log(f"  {variant} {season} GW{gw}: {result['points']} pts (total {total}), "
                f"{out[-1]['transfers']} transfers, chip {entry.get('chip')}, {out[-1]['seconds']}s")
    return {"season": season, "variant": variant, "start_gw": start_gw, "model": data["summary"]["model"],
            "total": total, "gameweeks": out, "seconds": round(time.time() - t0, 1)}


def cmd_plan(args) -> None:
    data = load_projected(args.proj)
    names = args.variants.split(",")
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variants {unknown}; known: {', '.join(VARIANTS)}")
    res = [play_season(data, n, start, time_limit=args.time_limit) for start in args.start_gws for n in names]
    version = data["summary"]["model"].get("version")
    for r in res:      # another model's projections (project --model v3) report as "<variant>@v3"
        if version and version != pm.MODEL_VERSION:
            r["variant"] = f"{r['variant']}@{version}"
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)


# ----------------------------------------------------------------------------- report

def paired_ci(diffs: Dict[str, np.ndarray], n_boot: int = 2000, seed: int = 0) -> Tuple[float, float, float]:
    """(mean per gameweek, 2.5%, 97.5%) of the variant-minus-reference points, gameweeks resampled
    within each season and pooled. Decisions carry over between gameweeks, so the interval is a
    rough guide, not an exact one."""
    rng = np.random.default_rng(seed)
    parts = [np.asarray(d, dtype=float) for d in diffs.values() if len(d)]
    if not parts:
        return float("nan"), float("nan"), float("nan")
    allv = np.concatenate(parts)
    boots = [np.concatenate([d[rng.integers(0, len(d), len(d))] for d in parts]).mean() for _ in range(n_boot)]
    return float(allv.mean()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def projection_quality(proj: pd.DataFrame, actual: pd.DataFrame, top: int = 20) -> pd.DataFrame:
    """By gameweeks ahead (step 1 = the deadline's own gameweek): RMSE, correlation, and per gameweek
    the top `top` by projection's actual mean and bias (projection - actual), averaged."""
    d = proj.merge(actual, on=["gameweek", "element"], how="left")
    d["points"] = d["points"].fillna(0.0)
    d["step"] = d["gameweek"] - d["deadline_gw"] + 1
    rows = []
    for step, g in d.groupby("step"):
        tops = g.sort_values("predicted_points", ascending=False).groupby("deadline_gw").head(top)
        rows.append({"step": int(step), "rows": len(g),
                     "rmse": float(np.sqrt(((g["predicted_points"] - g["points"]) ** 2).mean())),
                     "corr": float(g["predicted_points"].corr(g["points"])),
                     f"top{top}_actual": float(tops.groupby("deadline_gw")["points"].mean().mean()),
                     f"top{top}_bias": float((tops["predicted_points"] - tops["points"]).mean())})
    return pd.DataFrame(rows)


def cmd_report(args) -> None:
    results = [r for path in sorted(glob.glob(args.paths)) for r in json.load(open(path, encoding="utf-8"))]
    by = {(r["variant"], r["season"], int(r.get("start_gw") or 1)): r for r in results}
    variants = list(dict.fromkeys(r["variant"] for r in results))
    seasons = sorted({r["season"] for r in results})
    starts = sorted({int(r.get("start_gw") or 1) for r in results})
    multi = len(starts) > 1
    lines = ["# Manager replay", "",
             f"Seasons {', '.join(seasons)}, each from GW{', '.join(map(str, starts))} "
             f"({len(seasons) * len(starts)} paths); reference `{args.ref}`. Points after hits, today's rules. "
             "Season cells: total over the starts (difference from the reference"
             + (", paths ahead" if multi else "") + "). The CI resamples gameweeks within each path; decisions "
             "carry over between gameweeks, so treat it as a guide. Paths ahead counts the paths where the "
             "variant beat the reference.", "",
             "| variant | " + " | ".join(seasons)
             + " | total | vs ref per GW [95% CI] | paths ahead | transfers | hits | chips |",
             "|---|" + "---|" * len(seasons) + "---|---|---|---|---|---|"]
    for v in variants:
        cells, diffs, total, transfers, hits, chips = [], {}, 0, 0, 0, []
        ahead = paths = 0
        for s in seasons:
            season_total, season_delta, s_ahead, s_paths, have = 0, 0, 0, 0, False
            for start in starts:
                r = by.get((v, s, start))
                if r is None:
                    continue
                have = True
                season_total += r["total"]
                transfers += sum(g["transfers"] for g in r["gameweeks"])
                hits += sum(g["hits"] for g in r["gameweeks"])
                chips += [f"{g['chip']} {s[2:4]}/GW{g['gameweek']}" for g in r["gameweeks"] if g["chip"]]
                ref = by.get((args.ref, s, start))
                if ref is None or v == args.ref:
                    continue
                season_delta += r["total"] - ref["total"]
                s_paths += 1
                s_ahead += r["total"] > ref["total"]
                mine = {g["gameweek"]: g["points"] for g in r["gameweeks"]}
                theirs = {g["gameweek"]: g["points"] for g in ref["gameweeks"]}
                common = sorted(set(mine) & set(theirs))
                diffs[(s, start)] = np.array([mine[g] - theirs[g] for g in common])
            total += season_total
            ahead, paths = ahead + s_ahead, paths + s_paths
            if not have:
                cells.append("-")
            elif s_paths:
                cells.append(f"{season_total} ({season_delta:+d}" + (f", {s_ahead}/{s_paths}" if multi else "") + ")")
            else:
                cells.append(str(season_total))
        mean, lo, hi = paired_ci(diffs) if v != args.ref else (0.0, 0.0, 0.0)
        chip_cell = (f"{len(chips)} played" if multi else ", ".join(chips)) if chips else "-"
        lines.append(f"| {v} | " + " | ".join(cells) + f" | {total} | {mean:+.2f} [{lo:+.2f}, {hi:+.2f}] | "
                     f"{f'{ahead}/{paths}' if paths else '-'} | {transfers} | {hits} | {chip_cell} |")
    failed = [(r["variant"], r["season"], r.get("start_gw"), g["gameweek"], g["problems"])
              for r in results for g in r["gameweeks"] if g["problems"]]
    if failed:
        lines += ["", f"## Gameweeks rolled over after a failed or invalid plan ({len(failed)})", ""]
        lines += [f"- {v} {s} from GW{st}, GW{gw}: {p}" for v, s, st, gw, p in failed[:50]]
    capped = sum(g.get("optimal") is False for r in results for g in r["gameweeks"])
    lines += ["", f"Solves stopped at the time limit: {capped} gameweeks."]
    if args.proj:
        lines += ["", "## Projection quality by gameweeks ahead", ""]
        for prefix in sorted(glob.glob(f"{args.proj}.json")):
            prefix = prefix[:-5]
            data = load_projected(prefix)
            q = projection_quality(data["projections"], data["actual"]).round(3)
            model = data["summary"]["model"]
            lines += [f"**{data['season']}** (model {model.get('version')}, {model.get('members', 1)} x "
                      f"{model.get('n_trees')} trees)", "",
                      "| " + " | ".join(q.columns) + " |", "|" + "---|" * len(q.columns)]
            lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in q.itertuples(index=False)] + [""]
    if args.proj:
        inputs, actual = [], []
        for path in sorted(glob.glob(f"{args.proj}.inputs.parquet")):
            season = json.load(open(path[:-len(".inputs.parquet")] + ".json", encoding="utf-8"))["season"]
            inputs.append(pd.read_parquet(path).assign(season=season))
            actual.append(pd.read_parquet(path.replace(".inputs.", ".actual.")).assign(season=season))
        if inputs:
            q = input_variant_quality(pd.concat(inputs, ignore_index=True), pd.concat(actual, ignore_index=True))
            lines += ["", "## Input variants by gameweeks ahead", "",
                      "What the model is given for each gameweek (INPUT_VARIANTS), against production's inputs "
                      "(`base`), pooled over the seasons; step 1 is the deadline's own gameweek. The CIs resample "
                      "deadlines. Lower RMSE and higher top-20 points are better.", "",
                      "| step | variant | rmse | corr | top20 | rmse vs base [95% CI] | top20 vs base [95% CI] |",
                      "|---|---|---|---|---|---|---|"]
            for r in q.to_dict("records"):
                vs = ("-", "-") if r["variant"] == "base" else (
                    f"{r['rmse_vs_base']:+.4f} [{r['rmse_lo']:+.4f}, {r['rmse_hi']:+.4f}]",
                    f"{r['top_vs_base']:+.3f} [{r['top_lo']:+.3f}, {r['top_hi']:+.3f}]")
                lines.append(f"| {r['step']} | {r['variant']} | {r['rmse']:.4f} | {r['corr']:.4f} | {r['top20']:.3f} | "
                             f"{vs[0]} | {vs[1]} |")
    text = "\n".join(lines) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


# ----------------------------------------------------------------------------- cli

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("project", help="train on earlier seasons and rebuild every deadline's projections")
    p.add_argument("--season", required=True)
    p.add_argument("--model", default=pm.MODEL_VERSION, choices=["v3", "v4"])
    p.add_argument("--start-gws", "--start-gw", dest="start_gws", type=parse_gws, default=[1],
                   help="the plan's start gameweeks, e.g. 1,5,9,13 (projected from the earliest)")
    p.add_argument("--end-gw", type=int, default=None)
    p.add_argument("--horizon", type=int, default=HORIZON,
                   help="gameweeks projected at each deadline (production: %(default)s; more for the h5/h6 variants)")
    p.add_argument("--input-variants", action="store_true",
                   help="also score INPUT_VARIANTS at every step (<out>.inputs.parquet, for the report)")
    p.add_argument("--out", required=True, help="output prefix")
    p.set_defaults(func=cmd_project)
    p = sub.add_parser("plan", help="play the season under planner variants")
    p.add_argument("--proj", required=True, help="the project step's output prefix")
    p.add_argument("--variants", default="default")
    p.add_argument("--start-gws", "--start-gw", dest="start_gws", type=parse_gws, default=[1],
                   help="play each season from each of these gameweeks, e.g. 1,5,9,13")
    p.add_argument("--time-limit", type=float, default=planner.SOLVE_TIME_LIMIT)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("report", help="compare variants")
    p.add_argument("paths", help="glob of plan results")
    p.add_argument("--proj", default=None, help="glob of project prefixes, for projection quality")
    p.add_argument("--ref", default="default")
    p.add_argument("--out", default="report.md")
    p.set_defaults(func=cmd_report)
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
