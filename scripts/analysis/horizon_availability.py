"""
What FPL's flag at one deadline says about the gameweeks after it. The projections for later
gameweeks (pipeline.predict_next_gw.build_projections) need a flagged player's availability two,
three and four gameweeks ahead; this measures it, and its result is ml.snapshots.HORIZON_POINTS.

    python -m scripts.analysis.horizon_availability [--boot 300] [--out res.json]

Rows: player-gameweeks with an FPL snapshot before the deadline (availability_calibration's
player_gameweeks: the flag and a stratum known before that deadline). For each flag and each
`ahead` (0 = the flagged gameweek itself, 1-3 = the gameweeks after it), the factor is the flagged
players' actual points in that later gameweek over what unflagged players (status a) of the same
stratum scored in it: position, minutes in the previous gameweek and in the previous three. So a
suspended regular is compared with available regulars one, two and three gameweeks on. A player
without a row in the later gameweek (a blank, or he left the game) is left out on both sides.

Flags: injured (i, FPL's chance 0 or none), suspended (s, the same), and the 25/50/75% doubts.
The snapshots carry no news text, so "Expected back 6 Dec" and "Unknown return date" are pooled:
production uses FPL's date when the news has one and these factors when it doesn't.
"""
import argparse
import json

import numpy as np
import pandas as pd

from ml import points_model as pm
from ml import snapshots as sn
from scripts.analysis.availability_calibration import player_gameweeks

FLAGS = ["injured", "suspended", "d25", "d50", "d75"]
AHEAD = (0, 1, 2, 3)
MEASURES = {"points": "total_points", "played": "played"}
# The split production's factors come from: players who were playing when the flag appeared. A
# long absentee's projection is already low from his recent minutes, and against unflagged players
# who weren't playing either his factor passes 1 (the "all" rows), which says nothing about a
# squad player who has just been flagged.
RECENT = "recent players (135+ minutes in the previous three gameweeks)"


def flag_rows(history_csv: str = pm.HISTORY_CSV, snapshots_csv: str = sn.SNAPSHOTS_CSV) -> pd.DataFrame:
    """player_gameweeks with the flag split by status (injured / suspended / doubts / a)."""
    rows = player_gameweeks(history_csv, snapshots_csv)
    status = rows["status"].fillna("a").astype(str).str.strip().str.lower()
    out = rows["flag"] == "out"
    rows["hflag"] = np.select(
        [out & (status == "i"), out & (status == "s"), rows["flag"] == "d25", rows["flag"] == "d50",
         rows["flag"] == "d75", rows["flag"] == "a"],
        ["injured", "suspended", "d25", "d50", "d75", "a"], default="other")
    return rows


def outcomes(history_csv: str = pm.HISTORY_CSV) -> pd.DataFrame:
    """Every player-gameweek's points and whether he played (a double gameweek summed)."""
    h = pd.read_csv(history_csv, low_memory=False, usecols=["season_x", "element", "GW", "minutes", "total_points"])
    h = h.rename(columns={"season_x": "season"})
    pg = h.groupby(["season", "GW", "element"]).agg(minutes=("minutes", "sum"),
                                                    total_points=("total_points", "sum")).reset_index()
    pg["played"] = (pg["minutes"] > 0).astype(float)
    return pg[["season", "GW", "element", "total_points", "played"]]


def later(rows: pd.DataFrame, pg: pd.DataFrame, ahead: int) -> pd.DataFrame:
    """`rows` (flag and stratum at their own gameweek) with the outcome `ahead` gameweeks later."""
    target = rows[["season", "GW", "element", "hflag", "stratum"]].copy()
    target["GW"] = target["GW"] + ahead
    return target.merge(pg, on=["season", "GW", "element"], how="inner")


def factors(joined: pd.DataFrame) -> dict:
    """{flag: {measure: actual / expected-if-unflagged, "n": rows}} for one `later` table."""
    cols = list(MEASURES.values())
    base = joined[joined["hflag"] == "a"].groupby("stratum")[cols].mean()
    out = {}
    for flag in FLAGS:
        f = joined[joined["hflag"] == flag]
        expected = base.reindex(f["stratum"]).to_numpy()
        keep = ~np.isnan(expected).any(axis=1)
        res = {"n": int(keep.sum())}
        for i, (name, col) in enumerate(MEASURES.items()):
            denominator = expected[keep, i].sum()
            res[name] = round(float(f[col].to_numpy()[keep].sum() / denominator), 3) if denominator > 0 else None
        out[flag] = res
    return out


def bootstrap(joined: pd.DataFrame, n_boot: int, seed: int = 0) -> dict:
    """95% CI of each flag's points factor, resampling (season, GW) clusters."""
    rng = np.random.default_rng(seed)
    groups = dict(tuple(joined.groupby(["season", "GW"])))
    keys = list(groups)
    draws = {flag: [] for flag in FLAGS}
    for _ in range(n_boot):
        sample = pd.concat([groups[keys[i]] for i in rng.integers(0, len(keys), len(keys))], ignore_index=True)
        for flag, res in factors(sample).items():
            if res["points"] is not None:
                draws[flag].append(res["points"])
    return {flag: [round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)] if d else None
            for flag, d in draws.items()}


def report(rows: pd.DataFrame, pg: pd.DataFrame, n_boot: int) -> dict:
    out = {}
    for name, part in (("all", rows), (RECENT, rows[rows["prev3"] >= 135]),
                       ("regulars (60+ the gameweek before)", rows[rows["prev1"] >= 60])):
        out[name] = {}
        for ahead in AHEAD:
            joined = later(part, pg, ahead)
            res = factors(joined)
            if n_boot and name == RECENT:
                for flag, ci in bootstrap(joined, n_boot).items():
                    res[flag]["ci_points"] = ci
            out[name][ahead] = res
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--boot", type=int, default=300, help="bootstrap resamples (0 to skip)")
    parser.add_argument("--out", help="also write the results as JSON")
    args = parser.parse_args()
    rows, pg = flag_rows(), outcomes()
    res = report(rows, pg, args.boot)
    print(f"{len(rows):,} player-gameweeks with a snapshot, {rows['season'].nunique()} seasons\n")
    for name, per_ahead in res.items():
        print(name)
        for flag in FLAGS:
            cells = []
            for ahead in AHEAD:
                r = per_ahead[ahead][flag]
                ci = r.get("ci_points")
                cells.append(f"+{ahead}: {r['points'] if r['points'] is not None else 'n/a'}"
                             + (f" [{ci[0]}, {ci[1]}]" if ci else "") + f" played {r['played']} n={r['n']}")
            print(f"  {flag:9} " + " | ".join(cells))
        print()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
