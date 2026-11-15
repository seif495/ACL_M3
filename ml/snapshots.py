"""
FPL's availability and set-piece information as it stood before each deadline. The availability
features are part of the v4 points model (ml/points_model.py, since 2026-10-28); the set-piece
flags stay backtest candidates (2026-10-27; scripts/analysis/ml_backtest.py variants cop, avail,
avail_cop, sp, sp_cop and snap_cop).

Production multiplied the model's predictions by FPL's chance of playing until 2026-10-31
(pipeline.predict_next_gw.availability_factor); v3 never sees that information in training,
because the history has no FPL status. Variant cop replays that multiplication with these
snapshots; it is the reference the availability features had to beat. v4 sees them (snap_*), so
the chance itself counted the flags twice, and production now uses DOUBT_POINTS (points_factor).

  snap_unavailable   status i, s, u or n (injured, suspended, left / unavailable, not in squad)
  snap_doubtful      status d
  snap_chance        chance_of_playing_next_round, 100 when the status is a and none is given
  snap_news          the player has team news
  snap_age_days      the deadline minus the snapshot's capture time
  sp_pen1, sp_pen    first on penalties; first or second
  sp_corners1        first on corners and indirect free kicks
  sp_fk1             first on direct free kicks
  cop_factor         availability_factor of the snapshot, 1 without one (not a feature: the cop hook)

Sources (load_all_snapshots), all in load_snapshots' schema, one `sha` per capture:
  * data/fpl_snapshots.csv (python -m scripts.data.fetch_fpl_snapshots; MIT, committed so training
    in CI and on the VM needs no network), the git history of vaastav's players_raw.csv. It is
    committed about once a gameweek, usually a day or two after the gameweek ends.
  * our own pre-deadline archive, LIVE_DATA_DIR/element_snapshots/<season>/gwNN.csv
    (pipeline.current_season: every capture before each deadline since GW9 2026-27).
  * the live status file, captured at its fetched_at: what upcoming rows use, by the same rule.

Timing rule (tests/test_snapshots.py): gameweek N uses the latest snapshot captured strictly
before N's deadline (its first kickoff minus 90 minutes, as in ml/odds.py) and strictly after
N-1's. "Next round" in an older snapshot is N-1, not N, so N gets NaN then (stale in
snapshot_windows); the first gameweek of a season has no lower bound. Both fixtures of a double
gameweek share the snapshot. Capture time is the commit time: checked on 2026-10-27, every
in-season snapshot already includes the points of the last gameweek whose deadline had passed at
its commit time, so none was scraped before that deadline and committed after it.

Coverage: about 35 of 38 gameweeks a season from 2020-21 to 2024-25, but only 12 snapshots in
2025-26 and 4 in 2026-27 (GW1, GW2 and GW8), so the useful test folds are 2023-24 and 2024-25 plus
the untouched 2022-23. Our own archive covers 2026-27 from GW9 where it exists (on the VM).
"""
import glob
import os

import numpy as np
import pandas as pd

SNAPSHOTS_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fpl_snapshots.csv")
DEADLINE_MINUTES = 90
ORDERS = ["penalties_order", "corners_and_indirect_freekicks_order", "direct_freekicks_order"]
OUT_STATUSES = ["i", "s", "u", "n"]

AVAIL_FEATURES = ["snap_unavailable", "snap_doubtful", "snap_chance", "snap_news", "snap_age_days"]
SP_FEATURES = ["sp_pen1", "sp_pen", "sp_corners1", "sp_fk1"]
FEATURES = AVAIL_FEATURES + SP_FEATURES
COP = "cop_factor"

# Points multiplier per FPL doubt (chance of playing 25/50/75%) for a model that already sees the flags
# (v4's snap_* features), where chance / 100 counted them twice. Fitted against v4's own predictions on
# the validation season of each backtest fold (scripts/analysis/ml_backtest.py variant v4_fit, ML backtest
# run 41084780871, 2022-23..2024-25: 75%: 0.89-0.94, 50%: 0.75-0.79, 25%: 0.43-0.74), averaged. The
# snapshots behind the fit are a day or two old at the deadline; refit as our own archive grows.
DOUBT_POINTS = {0.25: 0.61, 0.5: 0.77, 0.75: 0.91}

# Share of his usual points a player flagged at one deadline scores one, two and three gameweeks after
# the flagged one, when FPL's news gives no return date (pipeline.predict_next_gw.horizon_availability,
# for the projections of later gameweeks). Measured on players who were playing when the flag appeared
# (135+ minutes in the previous three gameweeks) against unflagged players of the same position and
# recent minutes: python -m scripts.analysis.horizon_availability, 2020-21..2025-26, 2026-11-15, with
# 95% CIs resampling gameweeks:
#   injured    0.26 [0.20, 0.34]  0.36 [0.29, 0.44]  0.66 [0.49, 0.87]   (n 520-550)
#   suspended  0.83 [0.72, 0.95]  1.01 [0.85, 1.16]  1.07 [0.95, 1.20]   (n 320-330)
#   25% doubt  0.49 [0.30, 0.70]  0.55 [0.38, 0.73]  0.69 [0.52, 0.91]   (n 160-170)
#   50% doubt  0.68 [0.53, 0.87]  0.76 [0.55, 0.91]  0.99 [0.78, 1.20]   (n 200-220)
#   75% doubt  0.91 [0.84, 1.00]  0.92 [0.85, 1.00]  0.96 [0.87, 1.05]   (n 1240-1280)
# Rounded and capped at 1. Before this the projections left a flagged player's model inputs as
# they were (flagged), so a one-match ban cost him about 85% of all four gameweeks.
HORIZON_POINTS = {
    "injured": (0.25, 0.35, 0.65),
    "suspended": (0.85, 1.0, 1.0),
    0.25: (0.5, 0.55, 0.7),
    0.5: (0.7, 0.75, 1.0),
    0.75: (0.9, 0.9, 0.95),
}

_UTC = "datetime64[ns, UTC]"


COLUMNS = ["season", "captured_at", "sha", "element", "code", "status", "chance_of_playing_next_round",
           "news_flag"] + ORDERS


def empty_snapshots() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=_UTC if c == "captured_at" else object) for c in COLUMNS})


def load_snapshots(path: str = SNAPSHOTS_CSV) -> pd.DataFrame:
    """data/fpl_snapshots.csv with captured_at as UTC times; empty when the file is missing."""
    if not os.path.isfile(path):
        return empty_snapshots()
    snaps = pd.read_csv(path, dtype={"season": str, "sha": str, "status": str})
    snaps["captured_at"] = pd.to_datetime(snaps["captured_at"], utc=True).astype(_UTC)
    return snaps


def from_bootstrap(rows: pd.DataFrame, season: str, captured_at, source: str) -> pd.DataFrame:
    """FPL bootstrap players (pipeline.current_season's status file or element snapshot rows:
    element, code, status, chance_of_playing_next_round, news, set-piece orders) as snapshot
    rows captured at `captured_at` (a column name or a time), one sha per capture."""
    at = rows[captured_at] if isinstance(captured_at, str) and captured_at in rows else captured_at
    at = pd.Series(pd.to_datetime(at, utc=True), index=rows.index).astype(_UTC)
    news = rows["news"] if "news" in rows else pd.Series("", index=rows.index)
    out = pd.DataFrame({
        "season": str(season), "captured_at": at,
        "sha": source + "-" + at.dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "element": rows["element"], "code": rows.get("code"),
        "status": rows["status"],
        "chance_of_playing_next_round": rows.get("chance_of_playing_next_round"),
        "news_flag": news.fillna("").astype(str).str.strip().ne("").astype(int),
    }, index=rows.index)
    for c in ORDERS:
        out[c] = rows[c] if c in rows else np.nan
    return out[COLUMNS].reset_index(drop=True)


def load_live_snapshots(live_dir: str) -> pd.DataFrame:
    """Our own archive, live_dir/element_snapshots/<season>/gwNN.csv: every capture before each
    deadline (pipeline.current_season.update_element_snapshots), one sha per capture."""
    frames = []
    for path in sorted(glob.glob(os.path.join(live_dir, "element_snapshots", "*", "gw*.csv"))):
        raw = pd.read_csv(path)
        if len(raw):
            frames.append(from_bootstrap(raw, os.path.basename(os.path.dirname(path)), "captured_at", "live"))
    return pd.concat(frames, ignore_index=True) if frames else empty_snapshots()


def load_all_snapshots(path: str = SNAPSHOTS_CSV, live_dir: str = None, status: pd.DataFrame = None,
                       season: str = None) -> pd.DataFrame:
    """Every source: vaastav's history (`path`), our archive in `live_dir`, and the live status
    file (`status`, captured at its fetched_at, for `season`)."""
    frames = [load_snapshots(path)]
    if live_dir:
        frames.append(load_live_snapshots(live_dir))
    if status is not None and len(status) and "fetched_at" in status:
        frames.append(from_bootstrap(status, season, "fetched_at", "status"))
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else empty_snapshots()


def gameweek_deadlines(df: pd.DataFrame) -> pd.DataFrame:
    """(season, GW, deadline, prev_deadline) from build_features rows: a gameweek's first kickoff
    minus DEADLINE_MINUTES, and the previous gameweek's in the same season (NaT for the first)."""
    kickoff = pd.to_datetime(df["kickoff_time"], utc=True).astype(_UTC)
    d = (kickoff.groupby([df["season"], df["GW"]]).min() - pd.Timedelta(minutes=DEADLINE_MINUTES)).rename("deadline")
    d = d.reset_index().sort_values(["season", "GW"], kind="mergesort")
    d["prev_deadline"] = d.groupby("season")["deadline"].shift(1)
    return d.reset_index(drop=True)


def snapshot_windows(deadlines: pd.DataFrame, snaps: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, GW) of `deadlines`: the sha and captured_at of the snapshot it uses
    (NaN when none), and stale = the latest snapshot before the deadline predates the previous
    deadline, so it describes the previous gameweek and isn't used."""
    commits = snaps[["season", "sha", "captured_at"]].drop_duplicates(["season", "sha"])
    commits = commits.assign(season=commits["season"].astype(str),
                             captured_at=commits["captured_at"].astype(_UTC)).sort_values("captured_at")
    d = deadlines.assign(season=deadlines["season"].astype(str),
                         deadline=deadlines["deadline"].astype(_UTC)).sort_values("deadline")
    known = d["deadline"].notna()   # none for a gameweek without kickoff times yet
    if commits.empty or not known.any():
        got = d.assign(sha=np.nan, captured_at=pd.Series(pd.NaT, index=d.index, dtype=_UTC))
    else:
        # the latest snapshot captured strictly before the deadline, within the season
        got = pd.merge_asof(d[known], commits, left_on="deadline", right_on="captured_at", by="season",
                            allow_exact_matches=False, direction="backward")
        unknown = d[~known].assign(sha=np.nan, captured_at=pd.Series(pd.NaT, index=d.index[~known], dtype=_UTC))
        got = pd.concat([got, unknown], ignore_index=True) if len(unknown) else got
    stale = got["captured_at"].notna() & got["prev_deadline"].notna() & (got["captured_at"] <= got["prev_deadline"])
    got["stale"] = stale
    got.loc[stale, "sha"] = np.nan
    got.loc[stale, "captured_at"] = pd.NaT
    return got.sort_values(["season", "GW"], kind="mergesort").reset_index(drop=True)


def points_factor(chance) -> np.ndarray:
    """Production's points multiplier for a chance of playing on cop_factor's 0..1 scale: 0 and 1
    unchanged, a doubt's DOUBT_POINTS factor (interpolated between FPL's 25/50/75 steps)."""
    steps = sorted(DOUBT_POINTS)
    return np.interp(np.asarray(chance, dtype=float), [0.0] + steps + [1.0],
                     [0.0] + [DOUBT_POINTS[s] for s in steps] + [1.0])


def horizon_points(status, chance: float, ahead: int) -> float:
    """HORIZON_POINTS for one player: `chance` is his chance of playing the flagged gameweek on
    cop_factor's 0..1 scale, `ahead` the gameweeks after it (1 = the one right after; past the
    table its last value). 1 without a flag, 0 for a player who left (u) or isn't in the squad (n)."""
    status = str(status or "a").strip().lower() or "a"
    if status in ("u", "n"):
        return 0.0
    if not chance < 1.0:                       # no flag (or NaN)
        return 1.0
    if chance <= 0.0:
        key = "suspended" if status == "s" else "injured"
    else:
        key = min((k for k in HORIZON_POINTS if not isinstance(k, str)), key=lambda k: abs(k - chance))
    table = HORIZON_POINTS[key]
    return float(table[min(max(int(ahead), 1), len(table)) - 1])


def as_available(rows: pd.DataFrame) -> pd.DataFrame:
    """`rows` (a build_features table) with the availability features of an unflagged player, where
    a snapshot gave them: how a gameweek after the next one is projected, since FPL's flag is about
    the next deadline only (the horizon factor then says how likely he is to be back). In place."""
    if "snap_unavailable" not in rows.columns:
        return rows
    has = rows["snap_unavailable"].notna()
    for col, value in (("snap_unavailable", 0.0), ("snap_doubtful", 0.0), ("snap_chance", 100.0), ("snap_news", 0.0)):
        if col in rows.columns:
            rows.loc[has, col] = value
    return rows


def cop_factor(status, chance) -> np.ndarray:
    """services.season_service.chance_of_playing on arrays: 0 for u and n; else the chance / 100
    when given (clipped to 0..1); 0 for i and s without a chance; 1 otherwise, a missing status
    counting as a. Production's points multiplier is points_factor of it."""
    status = pd.Series(np.asarray(status, dtype=object)).fillna("").astype(str).str.strip().str.lower()
    status = status.replace("", "a").to_numpy()
    chance = pd.to_numeric(pd.Series(np.asarray(chance, dtype=object)), errors="coerce").to_numpy(dtype=float)
    without = np.where(np.isin(status, ["i", "s"]), 0.0, 1.0)
    out = np.where(np.isnan(chance), without, np.clip(chance / 100.0, 0.0, 1.0))
    return np.where(np.isin(status, ["u", "n"]), 0.0, out)


def snapshot_features(df: pd.DataFrame, snaps: pd.DataFrame, with_status: bool = False) -> pd.DataFrame:
    """FEATURES and COP for every row of a build_features table `df` (season, GW, element,
    kickoff_time), on the same index. NaN (COP 1) where the gameweek has no valid snapshot or the
    player isn't in it. `with_status` adds the snapshot's status letter (snap_status, for
    timing_check)."""
    out = pd.DataFrame(np.nan, index=df.index, columns=FEATURES)
    out[COP] = 1.0
    if snaps.empty:
        return out.assign(snap_status=None) if with_status else out
    win = snapshot_windows(gameweek_deadlines(df), snaps)
    snap = snaps[["season", "sha", "element", "status", "chance_of_playing_next_round", "news_flag"] + ORDERS].copy()
    snap = snap.drop_duplicates(["season", "sha", "element"])
    snap["element"] = snap["element"].astype(float)
    # Files from before FPL published set-piece orders (pre-season 2020-21) say nothing about them
    snap["sp_known"] = snap.groupby(["season", "sha"])[ORDERS].transform("count").sum(axis=1) > 0
    snap["_in"] = True

    rows = df[["season", "GW"]].assign(element=pd.to_numeric(df["element"], errors="coerce").astype(float))
    rows = rows.merge(win[["season", "GW", "sha", "deadline", "captured_at"]], on=["season", "GW"], how="left",
                      validate="many_to_one")
    rows = rows.merge(snap, on=["season", "sha", "element"], how="left", validate="many_to_one")
    rows.index = df.index
    has = rows["_in"].eq(True).to_numpy()

    status = rows["status"].fillna("").astype(str).str.strip().str.lower()
    chance = pd.to_numeric(rows["chance_of_playing_next_round"], errors="coerce").astype(float)
    out["snap_unavailable"] = status.isin(OUT_STATUSES).astype(float)
    out["snap_doubtful"] = (status == "d").astype(float)
    out["snap_chance"] = chance.where(chance.notna() | (status != "a"), 100.0)
    out["snap_news"] = rows["news_flag"].astype(float)
    out["snap_age_days"] = (rows["deadline"] - rows["captured_at"]).dt.total_seconds() / 86400
    pen = rows["penalties_order"].astype(float)
    sp = {"sp_pen1": pen == 1, "sp_pen": pen.isin([1, 2]),
          "sp_corners1": rows["corners_and_indirect_freekicks_order"].astype(float) == 1,
          "sp_fk1": rows["direct_freekicks_order"].astype(float) == 1}
    sp_known = has & rows["sp_known"].eq(True).to_numpy()
    for c, v in sp.items():
        out[c] = v.astype(float).where(sp_known)
    out[AVAIL_FEATURES] = out[AVAIL_FEATURES].where(pd.Series(has, index=df.index), axis=0)
    out[COP] = np.where(has, cop_factor(status.where(has, ""), chance), 1.0)
    out = out.astype(float)
    if with_status:
        out["snap_status"] = status.where(has)
    return out


# --------------------------------------------------------------------------- checks

def coverage(df: pd.DataFrame, snaps: pd.DataFrame, feats: pd.DataFrame) -> dict:
    """Per season: gameweeks, those with a snapshot, those left NaN because the latest snapshot
    predates the previous deadline (stale), and the share of rows with snapshot features."""
    win = snapshot_windows(gameweek_deadlines(df), snaps) if len(snaps) else gameweek_deadlines(df).assign(
        sha=np.nan, stale=False)
    per = win.groupby("season").agg(gws=("GW", "size"), with_snapshot=("sha", "count"), stale=("stale", "sum"))
    rows = feats["snap_age_days"].notna().groupby(df["season"]).mean().round(3).rename("rows")
    return per.join(rows).astype({"gws": int, "with_snapshot": int, "stale": int}).to_dict(orient="index")


def timing_check(df: pd.DataFrame, feats: pd.DataFrame) -> dict:
    """Share of player-gameweeks with minutes > 0 by what the snapshot said before the deadline:
    {group: (player-gameweeks, share who played)}, from snapshot_features(..., with_status=True).
    In the data, i/s/u/n always come with chance 0 and d with 25/50/75. If the snapshots had been
    captured after the deadline, or described the previous gameweek, "out" players would play often."""
    d = pd.DataFrame({"season": df["season"], "GW": df["GW"], "element": df["element"], "minutes": df["minutes"],
                      "chance": feats["snap_chance"], "status": feats["snap_status"]})
    d = d[d["status"].notna() & d["minutes"].notna()]
    g = d.groupby(["season", "GW", "element"]).agg(minutes=("minutes", "sum"), chance=("chance", "first"),
                                                    status=("status", "first"))
    played = g["minutes"] > 0
    groups = {"out (i/s/u/n or chance 0)": g["status"].isin(OUT_STATUSES) | (g["chance"] == 0),
              "injured or suspended (i/s)": g["status"].isin(["i", "s"]),
              "left or not in squad (u/n)": g["status"].isin(["u", "n"])}
    for c in (25, 50, 75):
        groups[f"chance {c}"] = g["chance"] == c
    groups["available (a)"] = g["status"] == "a"
    return {k: (int(m.sum()), round(float(played[m].mean()), 3) if m.any() else None) for k, m in groups.items()}
