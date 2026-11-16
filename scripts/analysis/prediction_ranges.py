"""
What a predicted number has meant in practice: for each level of predicted points, where the
actual points fell. The site shows it next to a prediction (services/prediction_ranges.py), since
the model's output is an average and a single match can land far from it.

    python -m scripts.analysis.prediction_ranges <dir with a Manager replay run's artifacts> \\
        [--source "run 43736266241"] [--out ml/models/points_v4_ranges.json]

Input: the replay's proj_<season>.predictions.parquet and .actual.parquet (gh run download <id>,
all in one directory). They are the next-gameweek predictions the live pipeline would have
published at every deadline of those seasons (availability multiplier included, double gameweeks
summed), from a model trained only on earlier seasons, so the table is out of sample. Players
without a fixture are left out.

Per bin of predicted points: the players, their mean actual points, the 10th and 90th percentile
(the "usual range": four in five outcomes fall inside it), and the share who scored 6+ and 10+.
"""
import argparse
import glob
import json
import os
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

EDGES: Sequence[float] = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)   # the last bin is 6 and above
RETURN_POINTS = 6      # a goal or a clean sheet on top of playing
HAUL_POINTS = 10
DEFAULT_OUT = os.path.join("ml", "models", "points_v4_ranges.json")


def load_pairs(directory: str) -> pd.DataFrame:
    """(season, gameweek, element, predicted_points, points) for every player with a fixture."""
    frames = []
    for path in sorted(glob.glob(os.path.join(directory, "proj_*.predictions.parquet"))):
        season = os.path.basename(path).split(".")[0][len("proj_"):]
        pred = pd.read_parquet(path)
        actual = pd.read_parquet(path.replace(".predictions.", ".actual."))
        pred = pred[pred["fixtures"] > 0][["deadline_gw", "element", "predicted_points"]]
        d = pred.merge(actual[["gameweek", "element", "points"]], left_on=["deadline_gw", "element"],
                       right_on=["gameweek", "element"], how="left")
        d["points"] = d["points"].fillna(0.0)      # no row: he didn't play
        frames.append(d.assign(season=season)[["season", "gameweek", "element", "predicted_points", "points"]])
    if not frames:
        raise SystemExit(f"no proj_*.predictions.parquet in {directory}")
    return pd.concat(frames, ignore_index=True)


def range_table(pairs: pd.DataFrame, edges: Sequence[float] = EDGES) -> List[Dict[str, Any]]:
    """One row a bin of predicted points (see the module docstring)."""
    out = []
    bounds = list(edges) + [np.inf]
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        points = pairs.loc[(pairs["predicted_points"] >= lo) & (pairs["predicted_points"] < hi), "points"].to_numpy()
        if not len(points):
            continue
        out.append({
            "from": float(lo), "to": None if np.isinf(hi) else float(hi), "n": int(len(points)),
            "mean": round(float(points.mean()), 2),
            "low": int(np.percentile(points, 10)), "high": int(np.percentile(points, 90)),
            "p_return": round(float((points >= RETURN_POINTS).mean()), 3),
            "p_haul": round(float((points >= HAUL_POINTS).mean()), 3),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory")
    parser.add_argument("--source", default="a Manager replay run", help="what the table says it was built from")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    pairs = load_pairs(args.directory)
    table = {
        "source": args.source, "seasons": sorted(pairs["season"].unique()), "rows": int(len(pairs)),
        "return_points": RETURN_POINTS, "haul_points": HAUL_POINTS, "percentiles": [10, 90],
        "bins": range_table(pairs),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2)
        f.write("\n")
    for b in table["bins"]:
        top = "+" if b["to"] is None else f"-{b['to']:g}"
        print(f"{b['from']:g}{top:5} n={b['n']:6} mean {b['mean']:5.2f}  range {b['low']}-{b['high']:2}  "
              f"6+ {b['p_return']:.0%}  10+ {b['p_haul']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
