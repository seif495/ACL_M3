"""My team: a manager's own FPL squad, imported from FPL's public API by team ID, scored with the
site's predictions and projections, and planned with the ML manager's planner.

FPL publishes every team without a login:
- entry/{id}/: name, manager, points, rank, the first and the latest gameweek;
- entry/{id}/history/: each gameweek's points, bank, value, transfers and their cost, and the
  chips played;
- entry/{id}/transfers/: every transfer with its prices;
- entry/{id}/event/{gw}/picks/: the squad at a deadline.

Picks are only published once a deadline passes, so the squad is the one at the last deadline
(after a free hit, the one before it, since that squad comes back). Transfers FPL already lists
for a later gameweek are applied on top.

What FPL doesn't publish is worked out here:
- free transfers: replayed from the history with the game's rule (fpl_manager.next_free_transfers),
  and corrected by each gameweek's hit cost, which says how many free transfers were used;
- purchase prices (selling price = purchase + half the rise): each player's latest transfer in,
  skipping free-hit weeks (that squad reverts). A player kept from the first squad was bought at
  his price in the season file for that gameweek.

Nothing is stored. FPL's answers are cached in memory for a few minutes per team, so a page
refresh doesn't ask FPL again. The pure functions take plain dicts and are unit tested; the
`get_team`/`plan_team` entry points do the I/O.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from config.settings import CURRENT_SEASON
from services import fpl_manager as fm
from services import season_service as ss
from services import team_optimizer as opt

logger = logging.getLogger(__name__)

FPL_BASE_URL = "https://fantasy.premierleague.com/api/"
USER_AGENT = "PitchMe/1.0 (fan site; My team)"
TIMEOUT_SECONDS = 8.0
FPL_CACHE_SECONDS = 300          # a team's FPL answers, per team ID
PLAN_CACHE_SECONDS = 600         # a plan, per team, options and projections file
CACHE_MAX = 500                  # teams (and plans) kept in memory
MAX_ENTRY_ID = 99_999_999
PROJECTIONS_NAME = "projections.csv"
WEB_SOLVE_SECONDS = 8.0          # per solve for a visitor's plan (the nightly ML manager gets 30)
MAX_CONCURRENT_PLANS = 2         # solves at once: each takes a CPU core for a few seconds
PLAN_QUEUE_SECONDS = 20.0        # how long a request waits for a free slot before giving up
DEFAULT_HORIZON = 4

Fetch = Callable[[str], Any]


class MyTeamError(Exception):
    """Base for the errors the route turns into HTTP statuses."""


class TeamNotFound(MyTeamError):
    """FPL has no team with that ID (404)."""


class TeamNotReady(MyTeamError):
    """The team exists but has no squad at a deadline yet (409)."""


class FplUnavailable(MyTeamError):
    """FPL didn't answer (502). `updating`: FPL said it's updating the game after a deadline."""

    def __init__(self, message: str, updating: bool = False):
        super().__init__(message)
        self.updating = updating


class PlanUnavailable(MyTeamError):
    """No projections to plan from yet (503)."""


class PlanBusy(MyTeamError):
    """Too many plans are being solved right now (503)."""


# ------------------------------------------------------------------ FPL requests (cached)

def fetch_json(path: str) -> Any:
    """GET one public FPL endpoint. 404 -> TeamNotFound, anything else that fails -> FplUnavailable."""
    import httpx
    try:
        resp = httpx.get(FPL_BASE_URL + path, timeout=TIMEOUT_SECONDS, follow_redirects=True,
                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise FplUnavailable(f"FPL didn't answer: {exc}") from exc
    if resp.status_code == 404:
        raise TeamNotFound("No FPL team has that ID")
    if resp.status_code == 503 and "being updated" in resp.text[:200].lower():
        # For a while after every deadline FPL answers only "The game is being updated."
        raise FplUnavailable("FPL is updating the game after the deadline. Try again in a few minutes.", updating=True)
    if resp.status_code != 200:
        raise FplUnavailable(f"FPL answered HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FplUnavailable("FPL sent something that isn't JSON") from exc


class _TtlCache:
    """A small thread-safe cache: entries expire after `ttl` seconds, the oldest go past `size`."""

    def __init__(self, ttl: float, size: int = CACHE_MAX, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.size, self.clock = ttl, size, clock
        self._data: "OrderedDict[Tuple, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Tuple, loader: Callable[[], Any]) -> Any:
        now = self.clock()
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and now - hit[0] < self.ttl:
                return hit[1]
        value = loader()
        with self._lock:
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_fpl_cache = _TtlCache(FPL_CACHE_SECONDS)
_plan_cache = _TtlCache(PLAN_CACHE_SECONDS)
_plan_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PLANS)


def clear_cache() -> None:
    _fpl_cache.clear()
    _plan_cache.clear()


def load_entry(entry_id: int, fetch: Fetch = fetch_json) -> Dict[str, Any]:
    """FPL's raw answers for one team: entry, history, transfers and the picks the squad comes
    from ({..., "picks_gameweek"}). Raises TeamNotFound / TeamNotReady / FplUnavailable."""
    entry = fetch(f"entry/{int(entry_id)}/")
    last = _int(entry.get("current_event"))
    started = _int(entry.get("started_event"))
    if not last or (started and started > last):
        raise TeamNotReady("This team's squad shows once its first deadline has passed")
    history = fetch(f"entry/{int(entry_id)}/history/")
    chips = chips_by_gameweek(history.get("chips") or [])
    picks_gw = last - 1 if chips.get(last) == fm.FREEHIT and started and last - 1 >= started else last
    picks = fetch(f"entry/{int(entry_id)}/event/{picks_gw}/picks/")
    transfers = fetch(f"entry/{int(entry_id)}/transfers/")
    return {"entry": entry, "history": history, "transfers": transfers or [], "picks": picks,
            "picks_gameweek": picks_gw, "last_gameweek": last}


def cached_entry(entry_id: int, fetch: Fetch = fetch_json) -> Dict[str, Any]:
    return _fpl_cache.get(("entry", int(entry_id)), lambda: load_entry(entry_id, fetch))


# ------------------------------------------------------------------ pure: what FPL doesn't publish

def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and value == value else None
    except (TypeError, ValueError):
        return None


def chips_by_gameweek(chips: Iterable[Dict[str, Any]]) -> Dict[int, str]:
    """{gameweek: chip} from the history's chips ([{name, event}])."""
    out: Dict[int, str] = {}
    for c in chips:
        gw, name = _int(c.get("event")), str(c.get("name") or "").strip().lower()
        if gw and name in fm.CHIPS:
            out[gw] = name
    return out


def replay_free_transfers(history: Sequence[Dict[str, Any]], chips: Dict[int, str],
                          started: int, last: int, rules: fm.Rules = fm.DEFAULT_RULES) -> int:
    """Free transfers for the gameweek after `last`.

    The first squad is free, so the gameweek after `started` has 1. Then each gameweek follows
    the game's rule (unused ones roll over, at most rules.max_free_transfers; a wildcard or free
    hit keeps them). A gameweek's hit cost says how many were really free: with a hit, every free
    transfer was used; with none, there were at least as many free transfers as transfers made.
    That corrects the replay when FPL handed out extra ones we can't see."""
    rows = {_int(r.get("event")): r for r in history if _int(r.get("event"))}
    ft = 1
    for gw in range(int(started) + 1, int(last) + 1):
        row = rows.get(gw) or {}
        chip = chips.get(gw)
        made = _int(row.get("event_transfers")) or 0
        cost = _int(row.get("event_transfers_cost")) or 0
        used = 0
        if chip not in fm.UNLIMITED_TRANSFERS:
            hits = cost // rules.hit_cost if rules.hit_cost else 0
            used = max(made - hits, 0)
            if hits:
                ft = used                 # all the free ones went before any hit
            elif made > ft:
                ft = made                 # more were free than the replay knew of
        ft = fm.next_free_transfers(ft, used, chip, rules)
    return ft


def free_hit_gameweeks(chips: Dict[int, str]) -> set:
    return {gw for gw, chip in chips.items() if chip == fm.FREEHIT}


def apply_pending(squad: Sequence[int], transfers: Iterable[Dict[str, Any]], after: int
                  ) -> Tuple[List[int], List[Dict[str, Any]]]:
    """The squad with the transfers FPL already lists for gameweeks after `after` applied, in
    the order made; a transfer that doesn't fit the squad (sold player missing) is skipped."""
    out = [int(e) for e in squad]
    applied: List[Dict[str, Any]] = []
    later = sorted((t for t in transfers if (_int(t.get("event")) or 0) > after), key=lambda t: str(t.get("time") or ""))
    for t in later:
        sold, bought = _int(t.get("element_out")), _int(t.get("element_in"))
        if sold in out and bought is not None and bought not in out:
            out[out.index(sold)] = bought
            applied.append(t)
    return out, applied


def purchase_prices(squad: Iterable[int], transfers: Iterable[Dict[str, Any]], free_hits: set,
                    start_prices: Dict[int, int], now_costs: Dict[int, int]) -> Dict[int, Tuple[int, str]]:
    """{element: (price paid in tenths, source)} where source is "transfer" (his latest transfer
    in, free-hit weeks skipped), "first squad" (his price the week the team started) or
    "estimate" (no record: today's price, so his selling price is today's price)."""
    latest: Dict[int, Dict[str, Any]] = {}
    for t in transfers:
        if (_int(t.get("event")) or 0) in free_hits:
            continue
        el = _int(t.get("element_in"))
        if el is None:
            continue
        if el not in latest or str(t.get("time") or "") > str(latest[el].get("time") or ""):
            latest[el] = t
    out: Dict[int, Tuple[int, str]] = {}
    for el in squad:
        el = int(el)
        if el in latest and _int(latest[el].get("element_in_cost")) is not None:
            out[el] = (int(latest[el]["element_in_cost"]), "transfer")
        elif el in start_prices:
            out[el] = (int(start_prices[el]), "first squad")
        else:
            out[el] = (int(now_costs.get(el) or 0), "estimate")
    return out


# ------------------------------------------------------------------ our data: players, predictions, projections

@dataclass(frozen=True)
class PlayerInfo:
    element: int
    code: Optional[int]
    name: str
    web_name: Optional[str]
    team: str
    position: str
    now_cost: int
    status: str
    chance: Optional[int]
    news: str


def _text(value: Any) -> str:
    return "" if value is None or value != value else str(value)


def players_by_element(season: str = CURRENT_SEASON) -> Dict[int, PlayerInfo]:
    """Every player in this season's game, from the FPL status file."""
    path = ss.status_path(season)

    def load() -> Dict[int, PlayerInfo]:
        if not os.path.exists(path):
            return {}
        df = pd.read_csv(path)
        out: Dict[int, PlayerInfo] = {}
        for r in df.to_dict("records"):
            el = _int(r.get("element"))
            if el is None:
                continue
            out[el] = PlayerInfo(
                element=el, code=_int(r.get("code")), name=_text(r.get("name")),
                web_name=_text(r.get("web_name")) or None, team=_text(r.get("team")),
                position=_text(r.get("position")).upper(), now_cost=_int(r.get("now_cost")) or 0,
                status=_text(r.get("status")) or "a", chance=_int(r.get("chance_of_playing_next_round")),
                news=_text(r.get("news")))
        return out

    return ss._cached(("my_team_players", path, ss._mtime(path)), load)


def season_csv_path(season: str = CURRENT_SEASON) -> str:
    name = (ss.read_meta(season).get("files") or {}).get("season") or f"FPL_{season[:4]}_{int(season[:4]) + 1}.csv"
    return os.path.join(ss.live_dir(), name)


def prices_in_gameweek(gameweek: int, season: str = CURRENT_SEASON) -> Dict[int, int]:
    """{element: price in tenths} in one gameweek, from the season file (a player with no
    fixture that week takes his nearest earlier week's price)."""
    path = season_csv_path(season)

    def load() -> Dict[int, int]:
        if not os.path.exists(path):
            return {}
        df = pd.read_csv(path, usecols=lambda c: c in ("element", "round", "value"))
        if not {"element", "round", "value"} <= set(df.columns):
            return {}
        df = df.dropna(subset=["element", "round", "value"])
        df = df[df["round"] <= gameweek].sort_values("round")
        return {int(el): int(v) for el, v in df.groupby("element")["value"].last().items()}

    return ss._cached(("my_team_prices", path, ss._mtime(path), int(gameweek)), load)


def _predictions_by_element(df: Optional[pd.DataFrame]) -> Tuple[Dict[int, Dict[str, Any]], Optional[int]]:
    """{element: {points, opponents}} for the next gameweek (doubles summed), and its gameweek."""
    if df is None or "element" not in df.columns or not len(df):
        return {}, None
    out: Dict[int, Dict[str, Any]] = {}
    for r in df.to_dict("records"):
        el = _int(r.get("element"))
        if el is None:
            continue
        row = out.setdefault(el, {"points": 0.0, "opponents": []})
        row["points"] += float(r.get("predicted_points") or 0.0)
        if _text(r.get("opponent")):
            row["opponents"].append({"team": _text(r.get("opponent")), "home": bool(r.get("is_home"))})
    gw = _int(df["gameweek"].iloc[0]) if "gameweek" in df.columns else None
    return out, gw


def projections_path() -> str:
    return os.path.join(ss.live_dir(), PROJECTIONS_NAME)


def load_projections() -> Optional[pd.DataFrame]:
    path = projections_path()
    if not os.path.exists(path):
        return None
    return ss._cached(("my_team_projections", path, ss._mtime(path)), lambda: pd.read_csv(path))


def projected_by_element(proj: Optional[pd.DataFrame], first_gw: Optional[int], horizon: int
                          ) -> Dict[int, List[Dict[str, Any]]]:
    """{element: [{gameweek, points}]} for the next `horizon` gameweeks of projections.csv, used
    only when it comes from the same run as the predictions: it starts at their gameweek (an
    older file's weeks were projected from older team news)."""
    if proj is None or first_gw is None or not {"element", "gameweek", "predicted_points"} <= set(proj.columns):
        return {}
    gws = sorted(pd.to_numeric(proj["gameweek"], errors="coerce").dropna().astype(int).unique())[:horizon]
    if not gws or gws[0] != first_gw:
        return {}
    rows = proj[pd.to_numeric(proj["gameweek"], errors="coerce").isin(gws)]
    sums = rows.groupby(["element", "gameweek"])["predicted_points"].sum()
    out: Dict[int, List[Dict[str, Any]]] = {}
    for (el, gw), pts in sums.items():
        out.setdefault(int(el), []).append({"gameweek": int(gw), "points": round(float(pts), 2)})
    for el in out:
        have = {r["gameweek"] for r in out[el]}
        out[el] += [{"gameweek": g, "points": 0.0} for g in gws if g not in have]   # a blank
        out[el].sort(key=lambda r: r["gameweek"])
    return out


# ------------------------------------------------------------------ the team

def suggested_lineup(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The best XI from the squad by next-gameweek predicted points, the bench in the order to
    set it, and the armband (pick_captains: the vice counts when the captain may not play)."""
    cands = [opt.Candidate(id=str(r["element"]), position=r["position"], team=r["team"],
                           points=float(r.get("predicted_points") or 0.0), cost=int(r.get("now_cost") or 0),
                           p_play=ss.chance_of_playing(r.get("status"), r.get("chance")),
                           opponents=tuple(o["team"] for o in r.get("opponents") or []))
             for r in rows]
    xi = opt.best_xi(cands)
    starters = {c.id for c in xi.starters}
    bench = opt.order_bench(c for c in cands if c.id not in starters)
    projected = sum(c.points for c in xi.starters) + opt.captain_bonus(xi.captain, xi.vice_captain)
    return {
        "starters": [int(c.id) for c in xi.starters],
        "bench": [int(c.id) for c in bench],
        "captain": int(xi.captain.id),
        "vice_captain": int(xi.vice_captain.id) if xi.vice_captain else None,
        "formation": xi.formation,
        "projected_points": round(projected, 2),
    }


def build_team(raw: Dict[str, Any], players: Dict[int, PlayerInfo], predictions: Optional[pd.DataFrame],
               projections: Optional[pd.DataFrame], start_prices: Dict[int, int], rules: fm.Rules,
               meta: Dict[str, Any], horizon: int = DEFAULT_HORIZON) -> Dict[str, Any]:
    """The My team payload from FPL's raw answers and our data (pure)."""
    entry, history, transfers = raw["entry"], raw["history"], raw["transfers"]
    picks_gw, last = int(raw["picks_gameweek"]), int(raw["last_gameweek"])
    started = _int(entry.get("started_event")) or 1
    chips = chips_by_gameweek(history.get("chips") or [])
    # The gameweek being planned: never before the one after the team's last deadline, even if
    # meta.json lags behind FPL
    next_gw = max(_int(meta.get("next_gameweek")) or 0, last + 1)

    picked = [int(p["element"]) for p in sorted(raw["picks"].get("picks") or [], key=lambda p: p.get("position") or 0)]
    squad, pending = apply_pending(picked, transfers, last)
    now_costs = {el: info.now_cost for el, info in players.items()}
    paid = purchase_prices(squad, transfers, free_hit_gameweeks(chips), start_prices, now_costs)

    pred, pred_gw = _predictions_by_element(predictions)
    projected = projected_by_element(projections, pred_gw, horizon)
    rows: List[Dict[str, Any]] = []
    for el in squad:
        info = players.get(el)
        now = info.now_cost if info else 0
        price, source = paid[el]
        p = pred.get(el) or {"points": 0.0, "opponents": []}
        weeks = projected.get(el) or []
        rows.append({
            "element": el, "name": info.name if info else f"Player {el}", "web_name": info.web_name if info else None,
            "team": info.team if info else "", "position": info.position if info else "",
            "now_cost": now, "purchase_price": price, "selling_price": selling_price(price, now),
            "purchase_source": source,
            "status": info.status if info else "a", "chance": info.chance if info else None,
            "news": info.news if info else "",
            "predicted_points": round(float(p["points"]), 2), "opponents": p["opponents"],
            "projected": weeks, "projected_total": round(sum(w["points"] for w in weeks), 2) if weeks else None,
            "_code": info.code if info else None,
        })
    lineup = suggested_lineup(rows) if len(rows) == 15 and _valid_counts(rows) else None

    picks_history = (raw["picks"].get("entry_history") or {})
    rows_by_gw = {_int(r.get("event")): r for r in history.get("current") or []}
    averages = {_int(e.get("id")): _int(e.get("average_entry_score")) for e in meta.get("events") or []}
    used = [{"chip": chip, "gameweek": gw} for gw, chip in sorted(chips.items())]
    return {
        "entry": {
            "id": _int(entry.get("id")), "name": _text(entry.get("name")),
            "manager": " ".join(x for x in (_text(entry.get("player_first_name")), _text(entry.get("player_last_name"))) if x),
            "overall_points": _int(entry.get("summary_overall_points")),
            "overall_rank": _int(entry.get("summary_overall_rank")),
            "event_points": _int(entry.get("summary_event_points")),
            "last_gameweek": last, "started_gameweek": started,
        },
        "gameweek": next_gw,
        "deadline": meta.get("next_deadline"),
        "predictions_gameweek": pred_gw,
        "based_on_gameweek": picks_gw,
        "pending_transfers": [{"out": _int(t.get("element_out")), "in": _int(t.get("element_in")),
                               "gameweek": _int(t.get("event"))} for t in pending],
        "bank": _int(picks_history.get("bank")) if picks_history.get("bank") is not None
        else _int(entry.get("last_deadline_bank")) or 0,
        "team_value": _int(picks_history.get("value")) or _int(entry.get("last_deadline_value")),
        "free_transfers": replay_free_transfers(history.get("current") or [], chips, started, last, rules),
        "chips": fm.chip_status(rules, used, next_gw),
        "squad": rows,
        "lineup": lineup,
        "history": [{"gameweek": gw, "points": _int(r.get("points")),
                     "transfer_cost": _int(r.get("event_transfers_cost")) or 0,
                     "overall_rank": _int(r.get("overall_rank")), "average": averages.get(gw),
                     "chip": chips.get(gw)}
                    for gw, r in sorted(rows_by_gw.items()) if gw],
    }


def selling_price(purchase: int, now: int) -> int:
    """FPL: half the rise (rounded down) when the price went up, else today's price."""
    purchase, now = int(purchase), int(now)
    return purchase + (now - purchase) // 2 if now > purchase else now


def _valid_counts(rows: Sequence[Dict[str, Any]]) -> bool:
    counts = {pos: sum(r["position"] == pos for r in rows) for pos in opt.POSITIONS}
    return counts == opt.SQUAD_SIZE


def get_team(entry_id: int, fetch: Fetch = fetch_json, season: str = CURRENT_SEASON) -> Dict[str, Any]:
    """The My team payload for one FPL team ID (with `_code` keys for photos)."""
    from services.predictions import get_predictions_df

    raw = cached_entry(entry_id, fetch)
    started = _int(raw["entry"].get("started_event")) or 1
    return build_team(raw, players_by_element(season), get_predictions_df(), load_projections(),
                      prices_in_gameweek(started, season), fm.load_rules(ss.live_dir(), season),
                      ss.read_meta(season))


# ------------------------------------------------------------------ transfer plan

def plan_team(entry_id: int, free_transfers: Optional[int] = None, max_hits: int = 0,
              horizon: int = DEFAULT_HORIZON, chips: bool = False, fetch: Fetch = fetch_json,
              season: str = CURRENT_SEASON, planner: Optional[Callable[..., Dict[str, Any]]] = None
              ) -> Dict[str, Any]:
    """The planner's best moves for this team over `horizon` gameweeks (fpl_manager_planner.plan):
    `free_transfers` overrides the replayed count, `max_hits` caps paid transfers per gameweek
    (0: free ones only) and `chips` lets it consider the chips the team still has."""
    projections = load_projections()
    if projections is None:
        raise PlanUnavailable("Transfer plans appear after the next prediction run")
    team = get_team(entry_id, fetch, season)
    if team["lineup"] is None:
        raise TeamNotReady("This squad isn't a full 15 we can plan with")
    ft = team["free_transfers"] if free_transfers is None else int(free_transfers)
    rules = fm.load_rules(ss.live_dir(), season)
    ft = min(max(ft, 0), rules.max_free_transfers)
    max_hits = min(max(int(max_hits), 0), 2)
    horizon = min(max(int(horizon), 1), DEFAULT_HORIZON)
    used = [{"chip": c["chip"], "gameweek": c["used_gameweek"]} for c in team["chips"] if c.get("used_gameweek")]
    chips_available = fm.chip_deadlines(rules, used, team["gameweek"]) if chips else {}
    current = {
        "squad": [{"element": r["element"], "name": r["name"], "web_name": r["web_name"] or r["name"],
                   "position": r["position"], "team": r["team"], "purchase_price": r["purchase_price"],
                   "selling_price": r["selling_price"], "now_cost": r["now_cost"]} for r in team["squad"]],
        "bank": team["bank"], "free_transfers": ft,
    }
    key = ("plan", int(entry_id), team["based_on_gameweek"], ft, max_hits, horizon, bool(chips),
           ss._mtime(projections_path()), tuple(sorted(chips_available)))

    def solve() -> Dict[str, Any]:
        if planner is None:
            from services.fpl_manager_planner import plan as run_plan
        else:
            run_plan = planner
        if not _plan_slots.acquire(timeout=PLAN_QUEUE_SECONDS):
            raise PlanBusy("Lots of people are planning right now; try again in a minute")
        try:
            started = time.perf_counter()
            entry = run_plan(current, projections, rules.to_dict(), team["gameweek"], chips_available, horizon,
                             season=season, deadline=team["deadline"], time_limit=WEB_SOLVE_SECONDS,
                             max_hits=max_hits)
        finally:
            _plan_slots.release()
        logger.info("My team plan for %s: %.1fs, %s transfers, chip %s", entry_id,
                    time.perf_counter() - started, len(entry.get("transfers") or []), entry.get("chip"))
        return entry

    entry = _plan_cache.get(key, solve)
    codes = {r["element"]: r["_code"] for r in team["squad"]}
    players = players_by_element(season)
    out = dict(entry)
    out["squad"] = [{**p, "_code": codes.get(p["element"]) or (players[p["element"]].code if p["element"] in players else None)}
                    for p in entry.get("squad") or []]
    out["inputs"] = {"free_transfers": ft, "max_hits": max_hits, "horizon": horizon, "chips": bool(chips)}
    out["current_projected"] = team["lineup"]["projected_points"]
    return out
