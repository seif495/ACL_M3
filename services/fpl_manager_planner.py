"""
The ML manager's planner: one gameweek's transfers, chip, captain and bench, chosen by looking
a few gameweeks ahead with the points model's projections.

    plan(current, projections, rules, gameweek, chips_available, horizon=4) -> the gwNN.json entry

Pure: a plain dict (the squad, bank and free transfers), a DataFrame of LIVE_DATA_DIR/
projections.csv (pipeline.predict_next_gw) and a rules dict in, the planned entry out. No files,
no network, and none of the game engine's types (services/fpl_manager.py).

How it decides
  * Pool: the current squad, the best POOL_PER_POSITION players per position by decayed horizon
    points, and CHEAP_PER_POSITION cheap fillers per position.
  * One multi-period MILP (scipy.optimize.milp / HiGHS, like team_optimizer.best_squad) over the
    horizon: per gameweek a squad (2/5/5/3, at most `team_limit` per club), an XI (1 GK, 3-5 DEF,
    2-5 MID, 1-3 FWD), a captain, the first two outfield substitutes, and players bought (at
    now_cost) and sold (at the current squad's selling prices; a player bought within the horizon
    sells at now_cost), with the bank carried and never negative. Free transfers follow FPL: the
    ones not used are banked up to `max_free_transfers`, and each transfer beyond them costs
    `hit_cost` points (at most MAX_HITS_PER_GW a gameweek). The objective is each gameweek's XI
    points, the captain's points again and the bench weighted like team_optimizer.best_squad
    (BENCH_WEIGHTS / BENCH_GK_WEIGHT; after this gameweek every outfield substitute gets their
    mean), minus hits, discounted by DECAY per gameweek ahead, plus FT_END_VALUE per free transfer still
    banked after the horizon (so a free transfer isn't spent on a tiny gain just because the
    horizon ends) and tiny tie-breaks (fewer transfers, more money in the bank, then the pool's
    order), so a plan is the same on every run.
  * Speed: the budget is what makes the MILP hard (without it a solve takes 0.1 s); a realistic
    pool of ~125 players takes about 5-25 s per solve on a laptop. A solve that reaches
    SOLVE_TIME_LIMIT keeps its best plan so far (Solution.optimal False). Chip solves that the
    policy can't pick are skipped (chip_may_play).
  * Only the first gameweek's moves are returned: the pipeline replans every run.
  * Chips, one per gameweek and only this gameweek's: the same MILP is solved without a chip and
    with each available one, and chip_decision (below) compares them.
  * Captain and vice: team_optimizer.pick_captains on the chosen XI, with each player's chance of
    playing (the projections' `availability` for this gameweek, else 1). Bench: the substitute
    goalkeeper first, then outfield players by projected points (team_optimizer.order_bench).

Prices and the bank are integers in tenths (1000 = 100.0m).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from services import team_optimizer as opt

DEFAULT_HORIZON = 4
DEFAULT_RULES: Dict[str, int] = {"budget": 1000, "team_limit": 3, "max_free_transfers": 5, "hit_cost": 4,
                                 "squad_size": 15}
CHIPS: Tuple[str, ...] = ("wildcard", "freehit", "bboost", "3xc")   # also the order ties are broken in
CHIP_LABELS = {"wildcard": "Wildcard", "freehit": "Free hit", "bboost": "Bench boost", "3xc": "Triple captain"}

# ---- objective
DECAY = 0.9                # a gameweek t ahead (0 = this one) counts DECAY ** t
FT_END_VALUE = 1.5         # points per free transfer still banked after the horizon
MAX_HITS_PER_GW = 2        # at most 2 transfers beyond the free ones per gameweek (-8): bigger rebuilds
                           # rest on projections too noisy to bet on; that is the wildcard's job
TRANSFER_TIEBREAK = 1e-3   # points per transfer: between equal plans, make fewer moves
# A transfer's projected gain is the difference of two noisy predictions, so the optimizer's curse
# inflates it. Margins the gain must clear (scripts/analysis/manager_replay.py tunes them): every
# transfer outside a wildcard or free hit, and each hit on top of its cost. 0 = off. A 1-point
# transfer margin was ahead in the 12-path replay of 2022-23..2024-25 (+0.70 a gameweek, not
# reliably) but behind on 2025-26 (-1.34), as were 0.5, 1.5 and 2 (CLAUDE.md, Season replay).
TRANSFER_MARGIN = 0.0
HIT_MARGIN = 0.0
BANK_TIEBREAK = 1e-5       # points per tenth in the bank after the horizon: then keep more money
RANK_TIEBREAK = 1e-4       # up to this per player per gameweek: then prefer players earlier in the pool's
                           # order (higher value, cheaper, lower element), so ties have one answer

# ---- pool and solver
POOL_PER_POSITION = 25     # best players per position by decayed horizon points
CHEAP_PER_POSITION = 3     # plus the cheapest players with points, per position (bench fillers)
SOLVE_TIME_LIMIT = 30.0    # seconds per solve; a solve that hits it keeps its best plan so far
MIP_REL_GAP = 0.0          # solve to optimality (the tie-breaks above then decide between equal plans)
# Gameweeks (from this one) whose bench is weighted slot by slot like best_squad (first outfield
# substitute BENCH_WEIGHTS[0], ...); later ones weight every outfield substitute by their mean,
# which keeps the MILP smaller
ORDERED_BENCH_GWS = 1

# ---- chip policy (see chip_decision)
TRIPLE_CAPTAIN_MIN_POINTS = 9.0          # the captain's projection this gameweek
TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS = 7.0   # ... or this much in a double gameweek
BENCH_BOOST_MIN_POINTS = 12.0            # the four substitutes' projections this gameweek
FREE_HIT_MIN_GAIN = 12.0                 # free-hit squad over the normal team, this gameweek
WILDCARD_MIN_GAIN = 15.0                 # wildcard plan over the normal one, over the horizon (discounted)
WILDCARD_LATE_GWS = 2                    # forced in the window's last 2 gameweeks if it helps
FREE_HIT_LATE_GWS = 1                    # forced in the window's last gameweek if it helps

STATUS_WORDS = {"i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad", "d": "doubtful"}


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class PoolPlayer:
    element: int
    name: str
    web_name: str
    position: str
    team: str
    now_cost: int              # buy price (tenths)
    sell: int                  # what selling him raises: his selling price if owned, else now_cost
    purchase_price: int        # owned: what was paid; else now_cost
    owned: bool
    points: Tuple[float, ...]  # projected points per horizon gameweek
    p_play: float = 1.0        # chance of playing this gameweek (projections' availability)
    fixtures: int = 1                 # this gameweek's fixtures (2 in a double, 0 in a blank)
    opponents: Tuple[str, ...] = ()   # this gameweek's opponents
    status: str = "a"

    @property
    def horizon_points(self) -> float:
        return float(sum(self.points))

    @property
    def value(self) -> float:
        """Decayed horizon points: what the pool is pruned by."""
        return float(sum(p * DECAY ** t for t, p in enumerate(self.points)))


def selling_price(purchase: int, now: int) -> int:
    """FPL: half the rise (rounded down) when the price went up, else the current price."""
    purchase, now = int(purchase), int(now)
    return purchase + (now - purchase) // 2 if now > purchase else now


def _rules(rules: Optional[Dict[str, Any]]) -> Dict[str, int]:
    out = dict(DEFAULT_RULES)
    for k, v in (rules or {}).items():
        if k in out and v is not None:
            out[k] = int(v)
    return out


def chip_windows(chips_available: Any, gameweek: int) -> Dict[str, Optional[int]]:
    """{chip: last gameweek of its window (None when unknown)} for the chips usable this gameweek.

    Accepts a dict {chip: end | [start, end]}, or a list of names, (name, end) pairs or dicts
    with "chip"/"name" and "window_end"/"end"/"window" (and "available": False to skip one).
    A chip whose window doesn't contain `gameweek` is dropped."""
    if not chips_available:
        return {}
    items: List[Tuple[Any, Any]] = []
    if isinstance(chips_available, dict):
        items = list(chips_available.items())
    else:
        for item in chips_available:
            if isinstance(item, str):
                items.append((item, None))
            elif isinstance(item, dict):
                if item.get("available") is False:
                    continue
                end = item.get("window_end", item.get("end", item.get("window")))
                items.append((item.get("chip") or item.get("name"), end))
            else:
                item = list(item)
                items.append((item[0], item[1] if len(item) > 1 else None))
    out: Dict[str, Optional[int]] = {}
    for name, end in items:
        chip = str(name or "").strip().lower()
        if chip not in CHIPS:
            continue
        start = None
        if isinstance(end, (list, tuple)):
            start, end = (end[0], end[-1]) if len(end) else (None, None)
        if start is not None and int(start) > gameweek or end is not None and int(end) < gameweek:
            continue
        out[chip] = int(end) if end is not None else None
    return out


def _position(value: Any) -> str:
    pos = str(value or "").strip().upper()
    return "GK" if pos == "GKP" else pos


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


def _number(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(out) else out


def horizon_gameweeks(projections: pd.DataFrame, gameweek: int, horizon: int) -> List[int]:
    """gameweek, gameweek + 1, ... while the projections have them (at most `horizon`)."""
    have = set(pd.to_numeric(projections["gameweek"], errors="coerce").dropna().astype(int))
    gws = []
    for gw in range(gameweek, gameweek + max(1, horizon)):
        if gw not in have:
            break
        gws.append(gw)
    if not gws:
        raise ValueError(f"The projections have no rows for GW{gameweek}")
    return gws


def build_pool(current: Dict[str, Any], projections: pd.DataFrame, gws: Sequence[int],
               per_position: int = POOL_PER_POSITION, cheap_per_position: int = CHEAP_PER_POSITION
               ) -> List[PoolPlayer]:
    """The players the MILP chooses from, in a fixed order (position, value desc, price, element)."""
    proj = projections.copy()
    proj["element"] = pd.to_numeric(proj["element"], errors="coerce")
    proj["gameweek"] = pd.to_numeric(proj["gameweek"], errors="coerce")
    proj = proj.dropna(subset=["element", "gameweek"])
    proj["element"] = proj["element"].astype(int)
    proj["gameweek"] = proj["gameweek"].astype(int)
    proj = proj[proj["gameweek"].isin(gws)]
    proj["predicted_points"] = pd.to_numeric(proj["predicted_points"], errors="coerce").fillna(0.0)
    points = proj.pivot_table(index="element", columns="gameweek", values="predicted_points",
                              aggfunc="sum").reindex(columns=list(gws)).fillna(0.0)
    # Player details from his row this gameweek (else his first one)
    proj["_first"] = proj["gameweek"] != gws[0]
    info = proj.sort_values(["element", "_first", "gameweek"], kind="mergesort").drop_duplicates("element")
    info = info.set_index("element")

    def from_projection(element: int, owned: Optional[Dict[str, Any]]) -> Optional[PoolPlayer]:
        row = info.loc[element] if element in info.index else None
        if row is None and owned is None:
            return None
        get = (lambda k, d=None: row[k] if row is not None and k in row.index else d)
        position = _position(_text(get("position")) or (owned or {}).get("position"))
        if position not in opt.POSITIONS:
            return None
        now_cost = int(_number(get("now_cost"), _number((owned or {}).get("now_cost"), 0.0)))
        pts = tuple(float(points.loc[element, gw]) if element in points.index else 0.0 for gw in gws)
        this_gw = row is not None and int(get("gameweek")) == gws[0]
        fixtures = int(_number(get("fixtures"), 1.0)) if this_gw else 0
        opponents = _text(get("opponents")) if this_gw else ""
        name = _text(get("name"), _text((owned or {}).get("name"), str(element)))
        if owned is not None:
            purchase = int(_number(owned.get("purchase_price"), now_cost))
            sell = int(_number(owned.get("selling_price"), selling_price(purchase, now_cost)))
        else:
            purchase = sell = now_cost
        return PoolPlayer(
            element=element, name=name,
            web_name=_text(get("web_name"), _text((owned or {}).get("web_name"), name)),
            position=position, team=_text(get("team"), _text((owned or {}).get("team"))),
            now_cost=now_cost, sell=sell, purchase_price=purchase, owned=owned is not None, points=pts,
            p_play=min(max(_number(get("availability"), 1.0), 0.0), 1.0) if this_gw else 1.0,
            fixtures=fixtures,
            opponents=tuple(o.strip() for o in opponents.split(" & ") if o.strip() and fixtures),
            status=_text(get("status"), "a").strip().lower(),
        )

    squad = {int(p["element"]): p for p in current.get("squad") or []}
    pool: Dict[int, PoolPlayer] = {}
    for element, owned in squad.items():
        player = from_projection(element, owned)
        if player is None:
            raise ValueError(f"Squad player {element} has no usable position")
        pool[element] = player
    others = [p for p in (from_projection(int(e), None) for e in info.index if int(e) not in squad) if p is not None]
    for pos in opt.POSITIONS:
        mine = [p for p in others if p.position == pos]
        for p in sorted(mine, key=lambda p: (-p.value, p.now_cost, p.element))[:per_position]:
            pool.setdefault(p.element, p)
        cheap = sorted(mine, key=lambda p: (p.value <= 0, p.now_cost, -p.value, p.element))
        for p in cheap[:cheap_per_position]:
            pool.setdefault(p.element, p)
    return sorted(pool.values(), key=lambda p: (opt.POSITIONS.index(p.position), -p.value, p.now_cost, p.element))


# ------------------------------------------------------------------ the MILP

class _Milp:
    """A small builder over scipy.optimize.milp: variables in blocks, rows as sums of terms."""

    def __init__(self) -> None:
        self.n = 0
        self._lb: List[np.ndarray] = []
        self._ub: List[np.ndarray] = []
        self._int: List[np.ndarray] = []
        self._gain: List[np.ndarray] = []
        self._r: List[np.ndarray] = []
        self._c: List[np.ndarray] = []
        self._v: List[np.ndarray] = []
        self._lo: List[np.ndarray] = []
        self._hi: List[np.ndarray] = []
        self.rows = 0

    def var(self, size: int, lb: Any = 0.0, ub: Any = 1.0, integer: bool = True, gain: Any = 0.0) -> np.ndarray:
        idx = np.arange(self.n, self.n + size)
        self.n += size
        for store, value in ((self._lb, lb), (self._ub, ub), (self._gain, gain)):
            store.append(np.broadcast_to(np.asarray(value, dtype=float), (size,)).copy())
        self._int.append(np.full(size, 1.0 if integer else 0.0))
        return idx

    def gain(self, idx: np.ndarray, value: Any) -> None:
        """Add `value` to the objective coefficients of variables `idx`."""
        flat = np.concatenate(self._gain)
        np.add.at(flat, np.asarray(idx), np.broadcast_to(np.asarray(value, dtype=float), np.shape(idx)))
        self._gain = [flat]

    def set_ub(self, idx: np.ndarray, value: Any) -> None:
        flat = np.concatenate(self._ub)
        flat[np.asarray(idx)] = value
        self._ub = [flat]

    def add_rows(self, terms: Iterable[Tuple[np.ndarray, Any]], lo: Any, hi: Any, count: int) -> None:
        """`count` rows, row r = sum of coef[r] * x[idx[r]] over `terms` (idx arrays of `count`)."""
        base = np.arange(self.rows, self.rows + count)
        for idx, coef in terms:
            idx = np.asarray(idx)
            self._r.append(base)
            self._c.append(idx)
            self._v.append(np.broadcast_to(np.asarray(coef, dtype=float), idx.shape).copy())
        self._lo.append(np.broadcast_to(np.asarray(lo, dtype=float), (count,)).copy())
        self._hi.append(np.broadcast_to(np.asarray(hi, dtype=float), (count,)).copy())
        self.rows += count

    def add_row(self, idx: Sequence[int], coef: Any, lo: float, hi: float) -> None:
        """One row: sum of coef * x[idx]."""
        idx = np.asarray(idx, dtype=int)
        self._r.append(np.full(len(idx), self.rows))
        self._c.append(idx)
        self._v.append(np.broadcast_to(np.asarray(coef, dtype=float), idx.shape).copy())
        self._lo.append(np.array([lo], dtype=float))
        self._hi.append(np.array([hi], dtype=float))
        self.rows += 1

    def solve(self, time_limit: float):
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_matrix

        a = coo_matrix((np.concatenate(self._v), (np.concatenate(self._r), np.concatenate(self._c))),
                       shape=(self.rows, self.n)).tocsr()
        return milp(-np.concatenate(self._gain),
                    constraints=LinearConstraint(a, np.concatenate(self._lo), np.concatenate(self._hi)),
                    integrality=np.concatenate(self._int),
                    bounds=Bounds(np.concatenate(self._lb), np.concatenate(self._ub)),
                    options={"mip_rel_gap": MIP_REL_GAP, "time_limit": time_limit, "presolve": True})


@dataclass
class Solution:
    """One solve's plan. Lists are per horizon gameweek; players are pool indices."""
    chip: Optional[str]
    objective: float
    squads: List[List[int]]           # the squad that plays each gameweek (the free-hit squad in its week)
    starters: List[List[int]]
    captains: List[int]
    bought: List[List[int]]           # transfers in (the free hit's are against the current squad)
    sold: List[List[int]]
    hits: List[int]
    free_transfers: List[int]         # available before each gameweek's transfers
    banks: List[int]                  # after each gameweek's transfers
    projected: List[float] = field(default_factory=list)   # XI + captain (+ chip) - hits, by the MILP's captain
    optimal: bool = True              # False when the solve stopped at its time limit with its best plan so far


def solve_plan(pool: Sequence[PoolPlayer], bank: int, free_transfers: int, rules: Dict[str, int],
               chip: Optional[str] = None, time_limit: float = SOLVE_TIME_LIMIT,
               max_hits: int = MAX_HITS_PER_GW) -> Solution:
    """The best plan over len(pool[0].points) gameweeks, with `chip` played this gameweek.

    wildcard / freehit: this gameweek's transfers are free and unlimited, and the free transfers
    banked are kept (+1 next gameweek, capped). The free hit's squad plays this gameweek only:
    the current squad, bank and prices return next gameweek. bboost: every squad player's points
    count this gameweek. 3xc: the captain's points count three times this gameweek."""
    n, horizon = len(pool), len(pool[0].points)
    bank0, ft0 = float(bank), float(free_transfers)
    max_ft, hit_cost, limit = rules["max_free_transfers"], float(rules["hit_cost"]), rules["team_limit"]
    pts = np.array([p.points for p in pool], dtype=float)                   # n x horizon
    cost = np.array([p.now_cost for p in pool], dtype=float)
    sell = np.array([p.sell for p in pool], dtype=float)
    x0 = np.array([1.0 if p.owned else 0.0 for p in pool])
    is_gk = np.array([p.position == "GK" for p in pool])
    by_pos = {pos: np.flatnonzero([p.position == pos for p in pool]) for pos in opt.POSITIONS}
    clubs = sorted({p.team for p in pool})
    by_club = {c: np.flatnonzero([p.team == c for p in pool]) for c in clubs}
    # A squad over the club limit (a player moved clubs) may stay so, but FPL won't add to that club:
    # none of its other players can be bought (fpl_manager.validate_entry checks the same)
    club_cap = {c: max(limit, int(x0[idx].sum())) for c, idx in by_club.items()}
    no_buys = [idx[x0[idx] == 0] for c, idx in by_club.items() if club_cap[c] > limit]
    w1, w2, w_rest = opt.BENCH_WEIGHTS
    flat_bench = np.where(is_gk, opt.BENCH_GK_WEIGHT, sum(opt.BENCH_WEIGHTS) / len(opt.BENCH_WEIGHTS))
    free_week = chip in ("wildcard", "freehit")
    rank_pref = RANK_TIEBREAK * (1.0 - np.arange(n) / n)       # the pool is in its fixed order

    m = _Milp()
    ft_vars: List[Optional[np.ndarray]] = [None]          # ft[0] is the given number
    blocks = []
    for t in range(horizon):
        w = DECAY ** t
        p = pts[:, t]
        boost, triple = chip == "bboost" and t == 0, chip == "3xc" and t == 0
        ordered = t < ORDERED_BENCH_GWS
        w_bench = np.where(is_gk, opt.BENCH_GK_WEIGHT, w_rest) if ordered else flat_bench
        x = m.var(n, gain=w * rank_pref)
        margin = 0.0 if free_week and t == 0 else TRANSFER_MARGIN
        bi = m.var(n, gain=-w * (TRANSFER_TIEBREAK + margin))
        bo = m.var(n, integer=False)   # integral anyway: previous squad + bought - squad
        fh = (m.var(n, gain=w * rank_pref - w * TRANSFER_TIEBREAK * (1.0 - x0))
              if chip == "freehit" and t == 0 else None)
        plays = fh if fh is not None else x
        # With one bench weight for every outfield substitute (later gameweeks) the XI and captain
        # can be continuous: for an integral squad their LP already reaches the best XI (the
        # position-count constraints are totally unimodular) and its best player as captain, so
        # only the squad and the transfers are branched on
        s = m.var(n, integer=ordered, gain=0.0 if boost else w * (1.0 - w_bench) * p)
        k = m.var(n, integer=ordered, gain=w * p * (2.0 if triple else 1.0))
        f = g = None
        if ordered:   # the first and second outfield substitutes, weighted like best_squad
            f = m.var(n, ub=np.where(is_gk, 0.0, 1.0), gain=0.0 if boost else w * (w1 - w_rest) * p)
            g = m.var(n, ub=np.where(is_gk, 0.0, 1.0), gain=0.0 if boost else w * (w2 - w_rest) * p)
        m.gain(plays, w * (1.0 if boost else w_bench) * p)
        money = m.var(1, lb=0.0, ub=np.inf, integer=False, gain=BANK_TIEBREAK if t == horizon - 1 else 0.0)
        u = m.var(1, ub=0.0 if free_week and t == 0 else max_ft)
        hits = m.var(1, ub=0.0 if free_week and t == 0 else max_hits, gain=-w * (hit_cost + HIT_MARGIN))
        ft_next = m.var(1, lb=1.0, ub=max_ft, gain=FT_END_VALUE * DECAY ** horizon if t == horizon - 1 else 0.0)
        blocks.append((x, bi, bo, fh, s, k, f, g, money, u, hits))

        # squad: x = previous squad + bought - sold; the free hit leaves the squad itself alone
        if t == 0:
            m.add_rows([(x, 1.0), (bi, -1.0), (bo, 1.0)], x0, x0, n)
            if fh is not None:
                m.set_ub(bi, 0.0)
                m.set_ub(bo, 0.0)
        else:
            m.add_rows([(x, 1.0), (bi, -1.0), (bo, 1.0), (blocks[t - 1][0], -1.0)], 0.0, 0.0, n)
        m.add_rows([(bi, 1.0), (bo, 1.0)], -np.inf, 1.0, n)
        squads = [x] + ([fh] if fh is not None else [])
        for sq in squads:
            for pos, idx in by_pos.items():
                m.add_row(sq[idx], 1.0, opt.SQUAD_SIZE[pos], opt.SQUAD_SIZE[pos])
            for c, idx in by_club.items():
                if len(idx) > club_cap[c]:
                    m.add_row(sq[idx], 1.0, -np.inf, club_cap[c])
            for idx in no_buys:
                if len(idx):
                    m.add_row(sq[idx], 1.0, -np.inf, 0.0)
        # the XI, captain (and first two outfield substitutes), from the squad that plays
        m.add_rows([(s, 1.0), (plays, -1.0)], -np.inf, 0.0, n)
        m.add_rows([(k, 1.0), (s, -1.0)], -np.inf, 0.0, n)
        m.add_row(s, 1.0, opt.XI_SIZE, opt.XI_SIZE)
        for pos, idx in by_pos.items():
            m.add_row(s[idx], 1.0, opt.XI_MIN[pos], opt.XI_MAX[pos])
        m.add_row(k, 1.0, 1.0, 1.0)
        if ordered:
            m.add_rows([(f, 1.0), (g, 1.0), (s, 1.0), (plays, -1.0)], -np.inf, 0.0, n)
            m.add_row(f, 1.0, 1.0, 1.0)
            m.add_row(g, 1.0, 1.0, 1.0)
        # bank: previous bank + sales - purchases (the free hit: its own budget, against today's squad)
        if t:
            m.add_row(np.concatenate([money, bo, bi, blocks[t - 1][8]]),
                      np.concatenate([[1.0], -sell, cost, [-1.0]]), 0.0, 0.0)
        else:
            m.add_row(np.concatenate([money, bo, bi]), np.concatenate([[1.0], -sell, cost]), bank0, bank0)
        if fh is not None:
            # the free-hit squad, owned players at their selling price and the rest at now_cost,
            # within the bank plus what today's squad would sell for
            m.add_row(fh, np.where(x0 > 0, sell, cost), -np.inf, bank0 + float((sell * x0).sum()))
        # free transfers: u used (at most those available and those made), hits for the rest,
        # ft_next = ft - u + 1, capped; a wildcard / free hit week uses none and pays none
        ft_prev = ft_vars[t]
        made = bi
        if not (free_week and t == 0):
            if ft_prev is None:
                m.add_row(u, 1.0, -np.inf, ft0)
            else:
                m.add_row(np.concatenate([u, ft_prev]), [1.0, -1.0], -np.inf, 0.0)
            m.add_row(np.concatenate([u, made]), np.concatenate([[1.0], -np.ones(len(made))]), -np.inf, 0.0)
            m.add_row(np.concatenate([hits, u, made]), np.concatenate([[1.0, 1.0], -np.ones(len(made))]),
                      0.0, np.inf)
        if ft_prev is None:
            m.add_row(np.concatenate([ft_next, u]), [1.0, 1.0], -np.inf, ft0 + 1.0)
        else:
            m.add_row(np.concatenate([ft_next, u, ft_prev]), [1.0, 1.0, -1.0], -np.inf, 1.0)
        ft_vars.append(ft_next)

    res = m.solve(time_limit)
    if res.x is None:
        raise opt.InfeasibleError(f"No valid plan ({chip or 'no chip'}): {res.message}")
    val = np.round(res.x).astype(int)

    sol = Solution(chip=chip, objective=float(-res.fun), squads=[], starters=[], captains=[], bought=[], sold=[],
                   hits=[], free_transfers=[], banks=[], optimal=res.status == 0)
    ft = int(free_transfers)
    prev = x0.astype(int)
    for t, (x, bi, bo, fh, s, k, f, g, money, u, hits) in enumerate(blocks):
        plays = fh if fh is not None else x
        squad = [i for i in range(n) if val[plays[i]]]
        if fh is not None:
            bought = [i for i in squad if not prev[i]]
            sold = [i for i in range(n) if prev[i] and not val[fh[i]]]
        else:
            bought = [i for i in range(n) if val[bi[i]]]
            sold = [i for i in range(n) if val[bo[i]]]
        free = chip in ("wildcard", "freehit") and t == 0
        hit_count = 0 if free else max(0, len(bought) - ft)
        sol.free_transfers.append(ft)
        sol.squads.append(squad)
        if t < ORDERED_BENCH_GWS and not (chip == "bboost" and t == 0):
            sol.starters.append([i for i in range(n) if val[s[i]]])
            sol.captains.append(int(next(i for i in range(n) if val[k[i]])))
        else:
            # The XI and captain the LP valued, made integral (ties broken like best_xi). A bench
            # boost counts everyone, so its XI is just the best 11.
            xi = opt.best_xi(opt.Candidate(id=str(i), position=pool[i].position, team=pool[i].team,
                                           points=float(pts[i, t])) for i in squad)
            sol.starters.append(sorted(int(c.id) for c in xi.starters))
            sol.captains.append(int(xi.captain.id))
        sol.bought.append(bought)
        sol.sold.append(sold)
        sol.hits.append(hit_count)
        sol.banks.append(int(round(res.x[money[0]])))
        mult = 3 if chip == "3xc" and t == 0 else 2
        counted = squad if chip == "bboost" and t == 0 else sol.starters[-1]
        sol.projected.append(float(pts[counted, t].sum() + (mult - 1) * pts[sol.captains[-1], t]
                                   - hit_cost * hit_count))
        ft = min(ft + 1 if free else max(ft - len(bought), 0) + 1, max_ft)
        prev = np.array([1 if val[x[i]] else 0 for i in range(n)])
    return sol


# ------------------------------------------------------------------ chips

def _captain_points(pool: Sequence[PoolPlayer], sol: Solution, t: int) -> float:
    return float(pool[sol.captains[t]].points[t])


def _bench_points(pool: Sequence[PoolPlayer], sol: Solution, t: int) -> float:
    starters = set(sol.starters[t])
    return float(sum(pool[i].points[t] for i in sol.squads[t] if i not in starters))


@dataclass
class _Timing:
    later: List[int]     # horizon gameweeks after this one still inside the chip's window
    within: bool         # the window ends within the horizon
    crowded: bool        # no more gameweeks left in the window than chips due by its end
    best_now: bool       # 3xc / bboost: this gameweek's captain / bench is the best left (plan without chips)
    note: str


def _timing(chip: str, pool: Sequence[PoolPlayer], gws: Sequence[int], base: Solution,
            windows: Dict[str, Optional[int]]) -> _Timing:
    end = windows.get(chip)
    later = [t for t in range(1, len(gws)) if end is None or gws[t] <= end]
    crowded = end is not None and end - gws[0] + 1 <= sum(1 for e in windows.values() if e is not None and e <= end)
    measure = {"3xc": _captain_points, "bboost": _bench_points}.get(chip)
    best_now = measure is not None and measure(pool, base, 0) >= max((measure(pool, base, t) for t in later),
                                                                     default=0.0)
    return _Timing(later, end is not None and end <= gws[-1], crowded, best_now,
                   f" (its window ends GW{end})" if end is not None else "")


def chip_may_play(chip: str, pool: Sequence[PoolPlayer], gws: Sequence[int], base: Solution,
                  windows: Dict[str, Optional[int]]) -> bool:
    """False when chip_decision can't pick `chip` whatever its own solve finds, so that solve is
    skipped: 3xc and bboost when a later gameweek of the window is better (and nothing forces
    them), 3xc also when nobody in the pool projects enough to qualify."""
    if chip not in ("3xc", "bboost"):
        return True
    timing = _timing(chip, pool, gws, base, windows)
    if timing.crowded or timing.best_now and timing.within:
        return True
    if not timing.best_now:
        return False
    if chip == "3xc":
        return any(p.points[0] >= TRIPLE_CAPTAIN_MIN_POINTS
                   or p.fixtures >= 2 and p.points[0] >= TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS for p in pool)
    return True


def chip_decision(pool: Sequence[PoolPlayer], gws: Sequence[int], base: Solution, options: Dict[str, Solution],
                  windows: Dict[str, Optional[int]]) -> Tuple[Optional[str], str]:
    """(chip to play this gameweek or None, why). Each chip is compared with the plan without
    one (`base`); `options` holds a solve per available chip, `windows` each chip's last
    gameweek. A chip is played when it qualifies:

      3xc       the captain projects TRIPLE_CAPTAIN_MIN_POINTS, or TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS
                in a double gameweek, and no later gameweek of the horizon (within the window)
                has a better captain in the plan without chips;
      bboost    the four substitutes project BENCH_BOOST_MIN_POINTS, and no later gameweek of the
                horizon (within the window) has a better bench;
      freehit   the free-hit squad beats the normal team by FREE_HIT_MIN_GAIN this gameweek;
      wildcard  the wildcard plan beats the normal one by WILDCARD_MIN_GAIN over the horizon
                (discounted objective);

    or when it is forced and helps (its plan beats the one without chips): 3xc and bboost when
    the window ends within the horizon and this gameweek is their best one left in it; the
    wildcard in the window's last WILDCARD_LATE_GWS gameweeks; the free hit in its last
    FREE_HIT_LATE_GWS; and any chip once the gameweeks left in its window are no more than the
    chips that must still be played by then (one chip per gameweek). Of the chips that qualify or
    are forced, the one whose plan gains most is played (ties in CHIPS order)."""
    gameweek = gws[0]
    candidates: List[Tuple[float, int, str, str]] = []
    for chip, sol in options.items():
        end = windows.get(chip)
        gain = sol.objective - base.objective
        if gain <= 1e-6:
            continue
        timing = _timing(chip, pool, gws, base, windows)
        why = None
        if chip == "3xc":
            captain = pool[sol.captains[0]]
            pts, doubles = captain.points[0], captain.fixtures >= 2
            if timing.best_now and (pts >= TRIPLE_CAPTAIN_MIN_POINTS
                                    or doubles and pts >= TRIPLE_CAPTAIN_DOUBLE_MIN_POINTS):
                why = f"{captain.web_name} projects {pts:.1f}" + (" in a double gameweek" if doubles else "")
            elif timing.within and timing.best_now or timing.crowded:
                why = f"{captain.web_name} ({pts:.1f}) is the best captain left before it expires{timing.note}"
        elif chip == "bboost":
            bench = _bench_points(pool, sol, 0)
            if timing.best_now and bench >= BENCH_BOOST_MIN_POINTS:
                why = f"the bench projects {bench:.1f}"
            elif timing.within and timing.best_now or timing.crowded:
                why = f"the bench projects {bench:.1f}, the best left before it expires{timing.note}"
        elif chip == "freehit":
            this_week = sol.projected[0] - base.projected[0]
            if this_week >= FREE_HIT_MIN_GAIN:
                why = f"{this_week:+.1f} this gameweek over the normal team"
            elif this_week > 0 and (end is not None and gameweek > end - FREE_HIT_LATE_GWS or timing.crowded):
                why = f"{this_week:+.1f} this gameweek, before it expires{timing.note}"
        elif chip == "wildcard":
            if gain >= WILDCARD_MIN_GAIN:
                why = f"{gain:+.1f} over the next {len(gws)} gameweeks against the plan without it"
            elif end is not None and gameweek > end - WILDCARD_LATE_GWS or timing.crowded:
                why = f"{gain:+.1f} over the next {len(gws)} gameweeks, before it expires{timing.note}"
        if why is not None:
            candidates.append((-gain, CHIPS.index(chip), chip, why))
    if not candidates:
        return None, ""
    _, _, chip, why = min(candidates)
    return chip, why


# ------------------------------------------------------------------ the entry

def _iso(now: Optional[datetime]) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidate(p: PoolPlayer) -> opt.Candidate:
    return opt.Candidate(id=str(p.element), position=p.position, team=p.team, points=float(p.points[0]),
                         cost=p.now_cost, p_play=p.p_play, opponents=p.opponents)


def _pair_transfers(pool: Sequence[PoolPlayer], sold: List[int], bought: List[int]) -> List[Tuple[int, int]]:
    """(out, in) pairs within each position: the weakest sale with the strongest buy, and so on."""
    pairs = []
    for pos in opt.POSITIONS:
        outs = sorted((i for i in sold if pool[i].position == pos),
                      key=lambda i: (pool[i].horizon_points, pool[i].element))
        ins = sorted((i for i in bought if pool[i].position == pos),
                     key=lambda i: (-pool[i].horizon_points, pool[i].element))
        pairs.extend(zip(outs, ins))
    return pairs


def _flag(p: PoolPlayer) -> str:
    word = STATUS_WORDS.get(p.status)
    return f" ({word}, {round(p.p_play * 100)}%)" if word else ""


def _reason(pool: Sequence[PoolPlayer], gws: Sequence[int], sol: Solution, pairs: List[Tuple[int, int]],
            chip: Optional[str], chip_why: str, captain: opt.Candidate,
            vice: Optional[opt.Candidate], rules: Dict[str, int]) -> str:
    n_gws = len(gws)
    span = f"the next {n_gws} gameweeks" if n_gws > 1 else "this gameweek"
    parts = []
    if chip in ("wildcard", "freehit"):
        parts.append(f"{CHIP_LABELS[chip]}: {chip_why}, {len(pairs)} change{'s' if len(pairs) != 1 else ''}"
                     + (" for this gameweek only; the squad returns next gameweek" if chip == "freehit" else ""))
    elif pairs:
        moves = [f"{pool[o].web_name}{_flag(pool[o])} out for {pool[i].web_name} "
                 f"({pool[i].horizon_points - pool[o].horizon_points:+.1f})" for o, i in pairs]
        hits = sol.hits[0]
        hit_text = f"{hits} hit{'s' if hits != 1 else ''} taken (-{hits * rules['hit_cost']})" if hits else "no hit"
        parts.append(f"{', '.join(moves)} projected over {span}, {hit_text}")
    else:
        nxt = min(sol.free_transfers[0] + 1, rules["max_free_transfers"])
        parts.append(f"No transfers: no move gains enough over {span}; "
                     f"{nxt} free transfer{'s' if nxt != 1 else ''} next gameweek")
    if chip in ("3xc", "bboost"):
        parts.append(f"{CHIP_LABELS[chip]}: {chip_why}")
    by_id = {str(p.element): p for p in pool}
    cap = f"Captain {by_id[captain.id].web_name} ({captain.points:.1f})"
    if vice is not None:
        cap += f", vice {by_id[vice.id].web_name}"
    parts.append(cap)
    return ". ".join(parts)


def _player_entry(p: PoolPlayer, role: str, bench_order: Optional[int], captain: bool, vice: bool) -> Dict[str, Any]:
    return {
        "element": p.element, "name": p.name, "web_name": p.web_name, "position": p.position, "team": p.team,
        "purchase_price": p.purchase_price, "selling_price": p.sell if p.owned else p.now_cost,
        "now_cost": p.now_cost, "predicted_points": round(float(p.points[0]), 2), "role": role,
        "bench_order": bench_order, "captain": captain, "vice_captain": vice,
    }


def plan(current: Dict[str, Any], projections: pd.DataFrame, rules: Optional[Dict[str, Any]], gameweek: int,
         chips_available: Any = (), horizon: int = DEFAULT_HORIZON, *, season: Optional[str] = None,
         deadline: Optional[str] = None, now: Optional[datetime] = None, model: Optional[str] = None,
         time_limit: float = SOLVE_TIME_LIMIT, max_hits: int = MAX_HITS_PER_GW) -> Dict[str, Any]:
    """The planned gwNN.json entry (status "planned") for `gameweek`.

    current      {"squad": [{element, name, web_name, position, team, purchase_price,
                 selling_price, now_cost}] (15), "bank": tenths, "free_transfers": 1..5}
    projections  projections.csv: element, name, web_name, position, team, now_cost, gameweek,
                 fixtures, opponents, predicted_points (and availability, status)
    rules        {budget, team_limit, max_free_transfers, hit_cost, squad_size} (DEFAULT_RULES fill gaps)
    chips_available  the chips usable this gameweek with their window's last gameweek (chip_windows)
    horizon      gameweeks planned over, this one included (fewer when the projections stop)
    max_hits     transfers beyond the free ones allowed per gameweek (0: free transfers only; the
                 My team page lets a visitor choose; the ML manager keeps MAX_HITS_PER_GW)

    The entry also carries, per horizon gameweek, the plan's projected points, transfers, hits and
    free transfers, though only this gameweek's moves count, and `optimal`: False when a solve (with
    or without a chip) stopped at its time limit with its best plan so far. Raises ValueError for an invalid
    squad or missing projections, team_optimizer.InfeasibleError when no plan fits.
    """
    rules = _rules(rules)
    squad = list(current.get("squad") or [])
    counts = {pos: sum(_position(p.get("position")) == pos for p in squad) for pos in opt.POSITIONS}
    if len(squad) != sum(opt.SQUAD_SIZE.values()) or counts != opt.SQUAD_SIZE:
        raise ValueError(f"The current squad must be 2 GK, 5 DEF, 5 MID, 3 FWD; got {counts}")
    bank = int(current.get("bank") or 0)
    free_transfers = int(current.get("free_transfers") if current.get("free_transfers") is not None else 1)
    free_transfers = min(max(free_transfers, 0), rules["max_free_transfers"])
    gws = horizon_gameweeks(projections, gameweek, horizon)
    pool = build_pool(current, projections, gws)
    windows = chip_windows(chips_available, gameweek)

    base = solve_plan(pool, bank, free_transfers, rules, None, time_limit, max_hits)
    options = {chip: solve_plan(pool, bank, free_transfers, rules, chip, time_limit, max_hits)
               for chip in CHIPS if chip in windows and chip_may_play(chip, pool, gws, base, windows)}
    chip, chip_why = chip_decision(pool, gws, base, options, windows)
    sol = options[chip] if chip else base
    optimal = base.optimal and all(o.optimal for o in options.values())

    starters = opt.order_starters(_candidate(pool[i]) for i in sol.starters[0])
    bench = opt.order_bench(_candidate(pool[i]) for i in sol.squads[0] if i not in set(sol.starters[0]))
    captain, vice = opt.pick_captains(starters)
    by_id = {str(pool[i].element): pool[i] for i in sol.squads[0]}
    entries = [_player_entry(by_id[c.id], "starter", None, c.id == captain.id,
                             vice is not None and c.id == vice.id) for c in starters]
    entries += [_player_entry(by_id[c.id], "bench", k, False, False) for k, c in enumerate(bench, start=1)]

    pairs = _pair_transfers(pool, sol.sold[0], sol.bought[0])
    transfers = [{
        "out": {"element": pool[o].element, "name": pool[o].name, "web_name": pool[o].web_name,
                "position": pool[o].position, "team": pool[o].team, "selling_price": pool[o].sell},
        "in": {"element": pool[i].element, "name": pool[i].name, "web_name": pool[i].web_name,
               "position": pool[i].position, "team": pool[i].team, "price": pool[i].now_cost},
    } for o, i in pairs]
    hits = sol.hits[0]
    hit_cost = hits * rules["hit_cost"]
    multiplier = 2.0 if chip == "3xc" else 1.0
    projected = (sum(c.points for c in starters) + multiplier * opt.captain_bonus(captain, vice)
                 + (sum(c.points for c in bench) if chip == "bboost" else 0.0) - hit_cost)
    horizon_rows = [{"gameweek": gw, "projected": round(projected if t == 0 else sol.projected[t], 2),
                     "transfers": len(sol.bought[t]), "hits": sol.hits[t],
                     "free_transfers": sol.free_transfers[t]} for t, gw in enumerate(gws)]
    bank_after = bank + sum(pool[o].sell for o in sol.sold[0]) - sum(pool[i].now_cost for i in sol.bought[0])

    return {
        "season": season, "gameweek": int(gameweek), "deadline": deadline, "status": "planned",
        "planned_at": _iso(now), "model": model or _model_version(),
        "chip": chip,
        "transfers": transfers,
        "free_transfers": free_transfers,
        "transfers_made": len(transfers), "hits": hits, "hit_cost": hit_cost,
        "bank_before": bank, "bank_after": int(bank_after),
        "squad": entries,
        "projected_points": round(projected, 2),
        "horizon": horizon_rows,
        "reason": _reason(pool, gws, sol, pairs, chip, chip_why, captain, vice, rules),
        "optimal": optimal,
        "result": None,
    }


def _model_version() -> str:
    from ml import points_model as pm
    return pm.MODEL_VERSION
