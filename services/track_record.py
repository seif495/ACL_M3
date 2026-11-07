"""
Track record: how good the model's forecasts were, gameweek by gameweek, this season.

For every finished gameweek that has a prediction saved BEFORE its deadline, this scores the
model's forecast against what happened, and against FPL's own forecast (`ep_next`):

  mae          mean absolute error over players who played (minutes > 0). With FPL's forecast
               available, both are scored on the same players, so the comparison is fair.
  top10        actual points of the model's 10 highest predicted players (who had a fixture),
               of FPL's 10 highest `ep_next`, and of the 10 who actually scored most.
  captain      the model's single top pick: his points, and whether he scored 6 or more.
  squad        the 15-man squad saved with the prediction, scored like FPL by
               model_review.score_picks (auto-subs, captain doubled), against the gameweek's
               average manager score.
  calibration  predicted vs actual mean points in bins (0-2, 2-4, 4-6, 6+), pooled over the
               season. It includes every player with a fixture, also those who didn't play,
               because the forecast is an expectation that already allows for that.

Sources (all read-only, under $LIVE_DATA_DIR):
  model_picks/<season>/gwNN.json              the saved pick; its `archived_at` and `deadline`
                                              decide whether the prediction is usable
  model_picks/<season>/gwNN_predictions.csv   every player's predicted points (summed over a
                                              double gameweek) as saved before the deadline
  <season file>.csv                           actual points and minutes (finished fixtures only)
  meta.json `events`                          which gameweeks are finished, deadlines, average
  element_snapshots/<season>/gwNN.csv         FPL's `ep_next`: the last capture at or before
                                              the deadline; else xp_snapshots.csv, same rule

No leakage: a gameweek's prediction counts only when its JSON says it was archived before the
deadline (the earlier of the pick's and meta.json's), and FPL's forecast only from a capture at or
before it. Anything else is listed in `missing` with the reason. Nothing here makes network
calls or writes files; the result is cached by the files' modification times.
"""
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.settings import CURRENT_SEASON
from services import model_review as mr
from services import season_service as ss

logger = logging.getLogger(__name__)

TOP_N = 10                 # players in the "top 10" comparison
CAPTAIN_HIT_POINTS = 6     # the top pick "hit" when he scored at least this
# (low, high, label); a prediction falls in the bin when low <= predicted < high
BINS: List[Tuple[float, Optional[float], str]] = [(0.0, 2.0, "0-2"), (2.0, 4.0, "2-4"), (4.0, 6.0, "4-6"), (6.0, None, "6+")]
SEASON_PATTERN = r"^\d{4}-\d{2}$"
NO_DATA_NOTE = "The track record starts once a gameweek with saved predictions has finished."

ElementPoints = Dict[int, Dict[str, int]]


# ------------------------------------------------------------------ small helpers

def _num(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int(value: Any) -> Optional[int]:
    x = _num(value)
    return int(x) if x is not None else None


def _r(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _mean(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    return sum(values) / len(values) if values else None


def _bin_index(predicted: float) -> int:
    for i, (_, high, _) in enumerate(BINS):
        if high is None or predicted < high:
            return i
    return len(BINS) - 1


# ------------------------------------------------------------------ pure: scoring

def prediction_rows(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean rows from the saved predictions CSV: one per player (first wins), with `has_fixture`
    from the `fixtures` column (all players when the column is missing)."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in records:
        element, predicted = _int(r.get("element")), _num(r.get("predicted_points"))
        if element is None or predicted is None or element in seen:
            continue
        seen.add(element)
        fixtures = _num(r.get("fixtures")) if "fixtures" in r else None
        out.append({"element": element, "name": str(r.get("name") or element), "predicted": predicted,
                    "has_fixture": True if fixtures is None else fixtures > 0})
    return out


def score_gameweek(rows: List[Dict[str, Any]], actual: ElementPoints,
                   fpl: Optional[Dict[int, float]] = None) -> Dict[str, Any]:
    """One gameweek's metrics (see the module doc). `rows` are prediction_rows, `actual` is
    model_review.actual_by_element's {element: {points, minutes}}, `fpl` is {element: ep_next}.
    The result carries `_bins` (calibration sums) for summarize(), which strips it."""
    def pts(element: int) -> int:
        return (actual.get(element) or {}).get("points", 0)

    def mins(element: int) -> int:
        return (actual.get(element) or {}).get("minutes", 0)

    fpl = fpl or {}
    candidates = [r for r in rows if r["has_fixture"]]
    ranked = sorted(candidates, key=lambda r: (-r["predicted"], r["element"]))

    # MAE: players who played; on the players both forecasts cover when FPL's is available
    played = [r for r in rows if mins(r["element"]) > 0]
    shared = [r for r in played if r["element"] in fpl]
    scored = shared or played
    mae = {
        "model": _r(_mean(abs(r["predicted"] - pts(r["element"])) for r in scored)),
        "fpl": _r(_mean(abs(fpl[r["element"]] - pts(r["element"])) for r in shared)) if shared else None,
        "n": len(scored),
    }

    # Top 10 by points: the model's, FPL's and the best there was
    model_top = ranked[:TOP_N]
    fpl_ranked = sorted((r for r in candidates if r["element"] in fpl), key=lambda r: (-fpl[r["element"]], r["element"]))
    fpl_top = fpl_ranked[:TOP_N]
    best = sorted((v.get("points", 0) for v in actual.values()), reverse=True)[:TOP_N]
    top10 = None
    if model_top:
        top10 = {
            "model": sum(pts(r["element"]) for r in model_top),
            "fpl": sum(pts(r["element"]) for r in fpl_top) if len(fpl_top) == len(model_top) else None,
            "best": sum(best),
            "n": len(model_top),
        }

    captain = None
    if ranked:
        c = ranked[0]
        captain = {"element": c["element"], "name": c["name"], "predicted": _r(c["predicted"], 2),
                   "points": pts(c["element"]), "hit": pts(c["element"]) >= CAPTAIN_HIT_POINTS}

    bins = [{"n": 0, "predicted": 0.0, "actual": 0.0} for _ in BINS]
    for r in candidates:
        b = bins[_bin_index(r["predicted"])]
        b["n"] += 1
        b["predicted"] += r["predicted"]
        b["actual"] += pts(r["element"])

    return {"mae": mae, "top10": top10, "captain": captain, "players": len(candidates), "_bins": bins}


def calibration(bins: List[Dict[str, float]]) -> List[Dict[str, Any]]:
    """The pooled calibration table from summed bins."""
    out = []
    for (low, high, label), b in zip(BINS, bins):
        n = int(b["n"])
        out.append({"label": label, "low": low, "high": high, "n": n,
                    "predicted": _r(b["predicted"] / n) if n else None,
                    "actual": _r(b["actual"] / n) if n else None})
    return out


def summarize(gws: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Season totals over scored gameweeks (strips each one's `_bins`, returns the calibration
    under `calibration`). Comparisons with FPL pool only the gameweeks that have its forecast,
    so both sides cover the same matches; with none, the model's own numbers use all gameweeks."""
    pooled = [{"n": 0, "predicted": 0.0, "actual": 0.0} for _ in BINS]
    for g in gws:
        for acc, b in zip(pooled, g.pop("_bins", [])):
            for k in acc:
                acc[k] += b[k]

    mae_pairs = [g for g in gws if g["mae"]["fpl"] is not None]
    mae_scope = mae_pairs or [g for g in gws if g["mae"]["model"] is not None]
    n_mae = sum(g["mae"]["n"] for g in mae_scope)
    mae = {
        "model": _r(sum(g["mae"]["model"] * g["mae"]["n"] for g in mae_scope) / n_mae) if n_mae else None,
        "fpl": _r(sum(g["mae"]["fpl"] * g["mae"]["n"] for g in mae_pairs) / n_mae) if mae_pairs and n_mae else None,
        "n": n_mae, "gameweeks": len(mae_scope),
    }

    top_pairs = [g for g in gws if g["top10"] and g["top10"]["fpl"] is not None]
    top_scope = top_pairs or [g for g in gws if g["top10"]]
    top10 = {
        "model": _r(_mean(g["top10"]["model"] for g in top_scope), 2),
        "fpl": _r(_mean(g["top10"]["fpl"] for g in top_pairs), 2) if top_pairs else None,
        "best": _r(_mean(g["top10"]["best"] for g in top_scope), 2),
        "gameweeks": len(top_scope),
    }

    captains = [g["captain"] for g in gws if g["captain"]]
    hits = sum(1 for c in captains if c["hit"])
    captain = {"hits": hits, "of": len(captains), "rate": _r(hits / len(captains)) if captains else None,
               "mean_points": _r(_mean(c["points"] for c in captains), 2), "threshold": CAPTAIN_HIT_POINTS}

    squads = [g["squad"] for g in gws if g.get("squad")]
    rated = [s for s in squads if s.get("average")]
    squad = {
        "mean_points": _r(_mean(s["points"] for s in squads), 2),
        "mean_average": _r(_mean(s["average"] for s in rated), 2) if rated else None,
        "beat_average": sum(1 for s in rated if s["points"] > s["average"]),
        "of": len(rated), "gameweeks": len(squads),
    }

    mae_wins = sum(1 for g in mae_pairs if g["mae"]["model"] < g["mae"]["fpl"])
    top_wins = sum(1 for g in top_pairs if g["top10"]["model"] > g["top10"]["fpl"])
    return {
        "gameweeks": len(gws),
        "compared_gameweeks": len({g["gameweek"] for g in mae_pairs} | {g["gameweek"] for g in top_pairs}),
        "mae": mae, "top10": top10, "captain": captain, "squad": squad,
        "beating_fpl": {"mae": {"wins": mae_wins, "of": len(mae_pairs)}, "top10": {"wins": top_wins, "of": len(top_pairs)}},
        "calibration": calibration(pooled),
    }


# ------------------------------------------------------------------ files

def _read_csv(path: str, columns: Iterable[str]) -> Optional[Any]:
    """A CSV's wanted columns as a DataFrame, or None when missing/unreadable."""
    if not os.path.exists(path):
        return None
    try:
        import pandas as pd
        wanted = set(columns)
        return pd.read_csv(path, usecols=lambda c: c in wanted)
    except Exception:  # corrupt or empty file: treat as absent
        logger.warning("Track record: could not read %s", path, exc_info=True)
        return None


def _last_capture_forecast(df: Any, value_column: str, deadline: datetime) -> Dict[int, float]:
    """{element: forecast} from the latest capture at or before the deadline (a DataFrame with
    element, captured_at and `value_column`; rows without a usable time or value are dropped)."""
    import pandas as pd
    if df is None or not len(df) or not {"element", "captured_at", value_column} <= set(df.columns):
        return {}
    stamps = pd.to_datetime(df["captured_at"], utc=True, errors="coerce")
    ok = stamps.notna() & (stamps <= pd.Timestamp(deadline))
    if not ok.any():
        return {}
    df, stamps = df[ok], stamps[ok]
    df = df[stamps == stamps.max()]
    out: Dict[int, float] = {}
    for element, value in zip(df["element"], df[value_column]):
        e, v = _int(element), _num(value)
        if e is not None and v is not None:
            out[e] = v
    return out


def fpl_forecast(season: str, gameweek: int, deadline: datetime) -> Tuple[Dict[int, float], Optional[str]]:
    """FPL's `ep_next` for a gameweek as known at its deadline, and where it came from: our
    element_snapshots archive (the last capture at or before the deadline), else xp_snapshots.csv
    (one capture per gameweek, the last before the deadline). ({}, None) when neither has one."""
    root = ss.live_dir()
    snap = _read_csv(os.path.join(root, "element_snapshots", season, f"gw{int(gameweek):02d}.csv"),
                     ("element", "captured_at", "ep_next"))
    forecast = _last_capture_forecast(snap, "ep_next", deadline)
    if forecast:
        return forecast, "element_snapshots"
    xp_name = (ss.read_meta(season).get("files") or {}).get("xp") or "xp_snapshots.csv"
    xp = _read_csv(os.path.join(root, xp_name), ("element", "GW", "xP", "captured_at"))
    xp = xp[xp["GW"] == int(gameweek)] if xp is not None and "GW" in xp.columns else None
    forecast = _last_capture_forecast(xp, "xP", deadline)
    return (forecast, "xp_snapshots") if forecast else ({}, None)


def _saved_pick(season: str, gameweek: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The saved pick JSON and the path of its predictions CSV (same folder), highest-priority folder first."""
    for d in mr._dirs(season):
        path = os.path.join(d, mr._pick_name(gameweek))
        picks = mr._read_json(path)
        if picks is not None:
            return picks, path[:-5] + "_predictions.csv"
    return None, None


def _signature(season: str) -> Tuple:
    """Every file the result depends on with its mtime, for the cache key."""
    root = ss.live_dir()
    files = ss.read_meta(season).get("files") or {}
    paths = [ss.meta_path(),
             os.path.join(root, files.get("season") or f"FPL_{season[:4]}_{int(season[:4]) + 1}.csv"),
             os.path.join(root, files.get("xp") or "xp_snapshots.csv")]
    for d in mr._dirs(season) + [os.path.join(root, "element_snapshots", season)]:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        paths += [os.path.join(d, n) for n in names if n.startswith("gw")]
    return (root, tuple((p, ss._mtime(p)) for p in paths))


# ------------------------------------------------------------------ payload

def empty_payload(season: str, note: str = NO_DATA_NOTE, missing: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"season": season, "gameweeks": [], "missing": missing or [], "totals": None,
            "calibration": [], "note": note}


def _score_one(season: str, gameweek: int, event: Dict[str, Any], finished_fixtures: List[int]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(scored gameweek, None) or (None, the reason it has no track record)."""
    picks, csv_path = _saved_pick(season, gameweek)
    if picks is None:
        return None, "No prediction was saved before this deadline"
    if not csv_path or not os.path.exists(csv_path):
        if picks.get("source") == "manual":
            return None, "Squad recorded by hand; no predictions were saved"
        return None, "The saved predictions file is missing"
    deadlines = [t for t in (mr._parse_time(picks.get("deadline")), mr._parse_time(event.get("deadline_time"))) if t]
    archived = mr._parse_time(picks.get("archived_at"))
    if not deadlines:
        return None, "The deadline isn't known, so the prediction can't be shown to be earlier"
    if archived is None:
        return None, "No save time was recorded for the prediction"
    deadline = min(deadlines)
    if archived >= deadline:
        return None, "The prediction was saved after the deadline"
    try:
        import pandas as pd
        records = pd.read_csv(csv_path).to_dict("records")
    except Exception:
        logger.warning("Track record: could not read %s", csv_path, exc_info=True)
        return None, "The saved predictions file couldn't be read"
    rows = prediction_rows(records)
    if not rows:
        return None, "The saved predictions have no player ids"
    actual = mr.actual_by_element(gameweek, season)
    if not actual:
        return None, "No results for this gameweek in the season file yet"

    forecast, source = fpl_forecast(season, gameweek, deadline)
    scored = score_gameweek(rows, actual, forecast)
    squad = None
    try:
        review = mr.score_picks(picks, actual, event, finished_fixtures)
        squad = {"points": review["points"], "bench_points": review["bench_points"],
                 "predicted": review["predicted_points"], "average": review["fpl_average"],
                 "highest": review["fpl_highest"], "formation": review["formation"]}
    except Exception:
        logger.warning("Track record: could not score the saved squad for GW%s", gameweek, exc_info=True)
    return {"gameweek": int(gameweek), "deadline": deadline.isoformat(), "archived_at": picks.get("archived_at"),
            "model": picks.get("model"), "fpl_source": source, **scored, "squad": squad}, None


def build_track_record(season: str = CURRENT_SEASON) -> Dict[str, Any]:
    meta = ss.read_meta(season)
    events = {e["id"]: e for e in meta.get("events") or [] if isinstance(e, dict) and isinstance(e.get("id"), int)}
    finished = sorted(i for i, e in events.items() if e.get("finished") or e.get("data_checked"))
    if not finished:
        return empty_payload(season)
    done = list(meta.get("finished_fixtures") or [])
    gws: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for gw in finished:
        scored, reason = _score_one(season, gw, events[gw], done)
        if scored is not None:
            gws.append(scored)
        else:
            missing.append({"gameweek": gw, "reason": reason})
    if not gws:
        return empty_payload(season, missing=missing)
    totals = summarize(gws)
    return {"season": season, "gameweeks": gws, "missing": missing, "totals": totals,
            "calibration": totals.pop("calibration"), "note": None}


def get_track_record(season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """The cached payload (key: every input file's mtime)."""
    return ss._cached(("track-record", season, _signature(season)), lambda: build_track_record(season))


# ------------------------------------------------------------------ route

def register(app: Any) -> None:
    """Add GET /api/ml/track-record?season=. A plain `def` route (it reads files). It never
    fails on missing or unreadable files: the payload is then empty, with a `note`."""
    from fastapi import Query

    @app.get("/api/ml/track-record")
    def ml_track_record(season: str = Query(CURRENT_SEASON, pattern=SEASON_PATTERN)) -> Dict[str, Any]:
        try:
            return get_track_record(season)
        except Exception:
            logger.exception("Track record failed for %s", season)
            return empty_payload(season, note="The track record couldn't be worked out just now. Try again later.")
