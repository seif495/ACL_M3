"""FPL decision tools built from data the site already has: captain options, differentials and
value, this gameweek's transfers and price changes, and set-piece takers.

Inputs (all optional; a missing file or column gives empty lists and a `note`, never an error):
- the predictions file (`services.predictions`): next-gameweek points, price, opponents;
- projections.csv (`services.my_team`): the next 4 gameweeks, used only from the same run;
- the FPL status file (`season_service.status_path`): ownership, transfers, price changes and
  set-piece orders.

The functions below are pure (plain lists of dicts); `register` adds the GET routes.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from services import my_team as mt
from services import predictions as preds
from services import season_service as ss
from services import team_optimizer as opt

logger = logging.getLogger(__name__)

DIFFERENTIAL_BELOW = 10.0       # % owned: under this is a differential, from here up is safe
TEMPLATE_FROM = 25.0            # % owned: the template picks
HORIZON = 4
CAPTAIN_POOL = 12               # candidates the armband pair is chosen from
MAX_LIMIT = 50
POSITIONS = ("GK", "DEF", "MID", "FWD")
# Cheapest price band per position, in tenths of a million
BUDGET_CAPS = {"GK": 45, "DEF": 45, "MID": 55, "FWD": 60}
BUDGET_PER_POSITION = 5

STATUS_COLUMNS = ("element", "code", "name", "web_name", "team", "position", "status",
                  "chance_of_playing_next_round", "news", "now_cost", "selected_by_percent",
                  "transfers_in_event", "transfers_out_event", "cost_change_event",
                  "penalties_order", "corners_and_indirect_freekicks_order", "direct_freekicks_order")
NO_PREDICTIONS = "No predictions yet: they appear after the next prediction run."
NO_STATUS = "FPL's player status isn't available yet."


# ------------------------------------------------------------------ small helpers

def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _int(value: Any) -> Optional[int]:
    f = _num(value)
    return None if f is None else int(f)


def _text(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip()
    return s or None


def clamp_limit(limit: int, default: int = 10) -> int:
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return default


def ownership_tag(selected: Optional[float]) -> Optional[str]:
    """differential (under 10%), safe (10-25%), template (25% and up); None when unknown."""
    if selected is None:
        return None
    if selected < DIFFERENTIAL_BELOW:
        return "differential"
    return "template" if selected >= TEMPLATE_FROM else "safe"


def _records(df: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
    return [] if df is None else df.to_dict("records")


# ------------------------------------------------------------------ pure builders

def player_table(prediction_rows: Iterable[Dict[str, Any]], status_rows: Iterable[Dict[str, Any]],
                 projections: Optional[Dict[int, List[Dict[str, Any]]]] = None
                 ) -> List[Dict[str, Any]]:
    """One row per player for the next gameweek: the predictions summed over a double
    gameweek, joined to the status file by element, plus the 4-week total when projected."""
    status = {e: r for r in status_rows if (e := _int(r.get("element"))) is not None}
    grouped: Dict[Any, Dict[str, Any]] = {}
    for r in prediction_rows:
        element = _int(r.get("element"))
        key = element if element is not None else r.get("name")
        pts = _num(r.get("predicted_points")) or 0.0
        opponent = _text(r.get("opponent"))
        fixture = {"opponent": opponent, "home": bool(r.get("is_home"))} if opponent else None
        g = grouped.get(key)
        if g is None:
            s = status.get(element, {}) if element is not None else {}
            chance_next = r.get("chance_of_playing_next_round")
            if _num(chance_next) is None:
                chance_next = s.get("chance_of_playing_next_round")
            st = _text(r.get("status")) or _text(s.get("status")) or "a"
            selected = _num(s.get("selected_by_percent"))
            cost = _num(r.get("value"))
            if cost is None:
                cost = _num(s.get("now_cost"))
            grouped[key] = g = {
                "element": element, "name": r.get("name"), "web_name": _text(s.get("web_name")) or r.get("name"),
                "team": r.get("team") or s.get("team"), "position": r.get("position") or s.get("position"),
                "predicted_points": 0.0, "fixtures": [],
                "chance_of_playing": round(ss.chance_of_playing(st, chance_next), 2),
                "status": st, "news": _text(r.get("news")) or _text(s.get("news")),
                "selected_by_percent": selected, "ownership": ownership_tag(selected),
                "price": round(cost / 10.0, 1) if cost else None,
                "_code": _int(s.get("code")),
            }
        g["predicted_points"] += pts
        if fixture:
            g["fixtures"].append(fixture)
    rows = list(grouped.values())
    for g in rows:
        g["predicted_points"] = round(g["predicted_points"], 2)
        proj = (projections or {}).get(g["element"]) if g["element"] is not None else None
        g["projected"] = proj or None
        g["points_4gw"] = round(sum(p["points"] for p in proj), 2) if proj else None
        g["points_per_m"] = _per_million(g["predicted_points"], g["price"])
        g["points_per_m_4gw"] = _per_million(g["points_4gw"], g["price"])
    return rows


def _per_million(points: Optional[float], price: Optional[float]) -> Optional[float]:
    if points is None or not price:
        return None
    return round(points / price, 2)


def _by_points(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (-r["predicted_points"], r["name"] or ""))


def captain_options(rows: List[Dict[str, Any]], limit: int = 10) -> Dict[str, Any]:
    """The best captain options by predicted points, and the model's captain and vice from
    pick_captains over the top candidates (E_c + (1 - p_c) * E_v: the vice counts when the
    captain may not play)."""
    ranked = _by_points(r for r in rows if r["predicted_points"] > 0)
    options = ranked[:limit]
    pool = ranked[:max(limit, CAPTAIN_POOL)]
    out: Dict[str, Any] = {"options": options, "captain": None, "vice": None, "expected_extra_points": None}
    if not pool:
        return out
    by_id = {r["name"]: r for r in pool}
    cands = [opt.Candidate(id=r["name"], position=r["position"] or "MID", team=r["team"] or "",
                           points=r["predicted_points"], p_play=r["chance_of_playing"],
                           opponents=tuple(f["opponent"] for f in r["fixtures"]))
             for r in pool]
    captain, vice = opt.pick_captains(cands)
    out["captain"] = captain.id
    out["vice"] = vice.id if vice else None
    out["expected_extra_points"] = round(opt.captain_bonus(captain, vice), 2)
    for r in options:
        r["role"] = "captain" if r["name"] == captain.id else "vice" if vice and r["name"] == vice.id else None
    # The pair can sit outside the top `limit` when a flagged star is backed by a nailed player
    for pick in (captain, vice):
        if pick and by_id[pick.id] not in options:
            options.append(dict(by_id[pick.id], role="captain" if pick is captain else "vice"))
    return out


def differential_rows(rows: List[Dict[str, Any]], position: Optional[str] = None,
                      max_ownership: float = DIFFERENTIAL_BELOW, limit: int = 20) -> List[Dict[str, Any]]:
    """Players owned by under `max_ownership`% with the most predicted points (next gameweek);
    unknown ownership is left out."""
    out = [r for r in rows
           if r["selected_by_percent"] is not None and r["selected_by_percent"] < max_ownership
           and r["predicted_points"] > 0 and (not position or r["position"] == position)]
    return _by_points(out)[:limit]


def budget_picks(rows: List[Dict[str, Any]], position: Optional[str] = None,
                 per_position: int = BUDGET_PER_POSITION) -> Dict[str, List[Dict[str, Any]]]:
    """Per position, the players priced within its cheapest band (GK/DEF up to 4.5, MID 5.5,
    FWD 6.0) with the most predicted points."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for pos in POSITIONS:
        if position and pos != position:
            continue
        cap = BUDGET_CAPS[pos] / 10.0
        pool = [r for r in rows if r["position"] == pos and r["price"] is not None
                and r["price"] <= cap + 1e-9 and r["predicted_points"] > 0]
        out[pos] = _by_points(pool)[:per_position]
    return out


def _move(r: Dict[str, Any], key: str, count: Optional[float]) -> Dict[str, Any]:
    cost, selected = _num(r.get("now_cost")), _num(r.get("selected_by_percent"))
    return {"name": r.get("name"), "web_name": _text(r.get("web_name")) or r.get("name"),
            "team": r.get("team"), "position": r.get("position"), key: count,
            "price": round(cost / 10.0, 1) if cost else None, "selected_by_percent": selected,
            "_code": _int(r.get("code"))}


def transfer_summary(status_rows: Iterable[Dict[str, Any]], limit: int = 15) -> Dict[str, Any]:
    """This gameweek's most transferred in and out, and the price risers and fallers."""
    rows = list(status_rows)
    has = lambda col: any(_num(r.get(col)) is not None for r in rows)   # noqa: E731

    def top(col: str, key: str) -> List[Dict[str, Any]]:
        got = [(r, _num(r.get(col))) for r in rows]
        got = [(r, n) for r, n in got if n]
        got.sort(key=lambda t: (-t[1], t[0].get("name") or ""))
        return [_move(r, key, int(n)) for r, n in got[:limit]]

    def changes(sign: int) -> List[Dict[str, Any]]:
        got = [(r, _num(r.get("cost_change_event"))) for r in rows]
        got = [(r, n) for r, n in got if n and n * sign > 0]
        got.sort(key=lambda t: (-abs(t[1]), -(_num(t[0].get("selected_by_percent")) or 0), t[0].get("name") or ""))
        return [_move(r, "change", round(n / 10.0, 1)) for r, n in got[:limit]]

    return {"transfers_in": top("transfers_in_event", "transfers"),
            "transfers_out": top("transfers_out_event", "transfers"),
            "risers": changes(1), "fallers": changes(-1),
            "has_transfers": has("transfers_in_event") or has("transfers_out_event"),
            "has_prices": has("cost_change_event")}


SET_PIECE_COLUMNS = {"penalties": "penalties_order", "free_kicks": "direct_freekicks_order",
                     "corners": "corners_and_indirect_freekicks_order"}


def set_piece_takers(status_rows: Iterable[Dict[str, Any]], team: Optional[str] = None
                     ) -> List[Dict[str, Any]]:
    """Per club the penalty, direct free-kick and corner takers, first choice first. Clubs
    with no listed taker are left out."""
    wanted = team.strip().lower() if team else None
    clubs: Dict[str, Dict[str, Any]] = {}
    for r in status_rows:
        club = _text(r.get("team"))
        if not club or (wanted and club.lower() != wanted):
            continue
        entry = clubs.setdefault(club, {"team": club, "penalties": [], "free_kicks": [], "corners": []})
        for kind, col in SET_PIECE_COLUMNS.items():
            order = _int(r.get(col))
            if order and order > 0:
                entry[kind].append({"order": order, "name": r.get("name"),
                                    "web_name": _text(r.get("web_name")) or r.get("name"),
                                    "position": r.get("position")})
    out = []
    for club in sorted(clubs):
        entry = clubs[club]
        for kind in SET_PIECE_COLUMNS:
            entry[kind].sort(key=lambda t: (t["order"], t["name"] or ""))
        if any(entry[k] for k in SET_PIECE_COLUMNS):
            out.append(entry)
    return out


# ------------------------------------------------------------------ file access

def _read_status() -> List[Dict[str, Any]]:
    path = ss.status_path()
    if not os.path.exists(path):
        return []

    def load() -> List[Dict[str, Any]]:
        try:
            df = pd.read_csv(path, usecols=lambda c: c in STATUS_COLUMNS)
        except Exception:   # noqa: BLE001  an unreadable file is "no data", not a 500
            logger.warning("Could not read %s", path, exc_info=True)
            return []
        return _records(df)

    return ss._cached(("insights_status", path, ss._mtime(path)), load)


def _file_key() -> tuple:
    pred, status, proj = preds.predictions_path(), ss.status_path(), mt.projections_path()
    return (pred, ss._mtime(pred), status, ss._mtime(status), proj, ss._mtime(proj))


def _players() -> tuple:
    """(rows, gameweek) for the next gameweek; ([], None) without predictions."""
    df = preds.get_predictions_df()
    if df is None or df.empty or "predicted_points" not in df.columns:
        return [], None
    gws = pd.to_numeric(df["gameweek"], errors="coerce").dropna() if "gameweek" in df.columns else pd.Series(dtype=float)
    first_gw = int(gws.min()) if len(gws) else None
    projected = mt.projected_by_element(mt.load_projections(), first_gw, HORIZON)
    return player_table(_records(df), _read_status(), projected), first_gw


def _payload(builder: Callable[[], Dict[str, Any]], photo_for: Optional[ss.PhotoFn]) -> Dict[str, Any]:
    return ss.with_photos(builder(), photo_for)


# ------------------------------------------------------------------ routes

def register(app, photo_for: Optional[ss.PhotoFn] = None) -> None:
    """Adds the /api/insights/* routes (plain def: FastAPI runs them in its threadpool)."""
    from fastapi import Query

    @app.get("/api/insights/captains")
    def insights_captains(limit: int = Query(10, ge=1, le=MAX_LIMIT)):
        def build() -> Dict[str, Any]:
            def make() -> Dict[str, Any]:
                rows, gw = _players()
                res = captain_options(rows, limit) if rows else {"options": [], "captain": None, "vice": None,
                                                                 "expected_extra_points": None}
                res["gameweek"] = gw
                res["note"] = None if rows else NO_PREDICTIONS
                return res
            return ss._cached(("insights_captains", limit, _file_key()), make)
        return _payload(build, photo_for)

    @app.get("/api/insights/differentials")
    def insights_differentials(position: Optional[str] = None, max_ownership: float = Query(DIFFERENTIAL_BELOW, gt=0, le=100),
                               limit: int = Query(20, ge=1, le=MAX_LIMIT)):
        pos = ss.normalize_position(position) if position else None
        if position and pos not in POSITIONS:
            pos = None

        def build() -> Dict[str, Any]:
            def make() -> Dict[str, Any]:
                rows, gw = _players()
                note = None if rows else NO_PREDICTIONS
                if rows and not any(r["selected_by_percent"] is not None for r in rows):
                    note = "Ownership isn't available yet, so differentials can't be listed."
                return {"gameweek": gw, "max_ownership": max_ownership, "position": pos,
                        "has_projections": any(r["points_4gw"] is not None for r in rows),
                        "differentials": differential_rows(rows, pos, max_ownership, limit),
                        "budget": budget_picks(rows, pos),
                        "budget_caps": {k: v / 10.0 for k, v in BUDGET_CAPS.items()}, "note": note}
            return ss._cached(("insights_differentials", pos, max_ownership, limit, _file_key()), make)
        return _payload(build, photo_for)

    @app.get("/api/insights/transfers")
    def insights_transfers(limit: int = Query(15, ge=1, le=MAX_LIMIT)):
        def build() -> Dict[str, Any]:
            def make() -> Dict[str, Any]:
                status = _read_status()
                res = transfer_summary(status, limit)
                note = None
                if not status:
                    note = NO_STATUS
                elif not (res["has_transfers"] or res["has_prices"]):
                    note = "This gameweek's transfer and price data isn't in the player file yet."
                res["note"] = note
                return res
            return ss._cached(("insights_transfers", limit, _file_key()), make)
        return _payload(build, photo_for)

    @app.get("/api/insights/set-pieces")
    def insights_set_pieces(team: Optional[str] = None):
        def build() -> Dict[str, Any]:
            def make() -> Dict[str, Any]:
                status = _read_status()
                teams = set_piece_takers(status, team)
                note = None
                if not status:
                    note = NO_STATUS
                elif not teams:
                    note = ("No set-piece takers are listed for that club." if team
                            else "FPL hasn't listed set-piece takers in the player file yet.")
                return {"teams": teams, "note": note}
            return ss._cached(("insights_set_pieces", (team or "").lower(), _file_key()), make)
        return _payload(build, photo_for)
