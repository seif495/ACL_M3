"""
The ML manager: a season-long FPL team run by the points model under FPL's rules.

This module is the game engine (pure functions over plain dicts) plus its files. FPL's
rules (Rules, read from bootstrap-static's game_settings, element_types and chips, with the
2026-27 values as defaults):

  squad      15 players (2 GK, 5 DEF, 5 MID, 3 FWD), at most 3 per club, 100.0m (a club over
             the limit because an owned player moved there may stay so, but never grow). The XI
             is 1 GK, 3-5 DEF, 2-5 MID and 1-3 FWD. Bench order 1 is the substitute
             goalkeeper, 2-4 the outfield subs in priority order (FPL's layout).
  prices     a player is bought at now_cost and sold at purchase + half the rise, rounded
             down, or at now_cost when the price fell (selling_price). Money is in tenths.
  transfers  the first squad is free. After that each gameweek adds a free transfer, and
             unused ones are banked up to 5 (next_free_transfers). Each transfer beyond the
             free ones costs 4 points. Wildcard and free hit allow unlimited transfers, and
             the banked free transfers are kept.
  chips      wildcard, free hit, bench boost and triple captain, each once per half (the
             bootstrap's "chips" windows: GW2-19 / GW20-38 for wildcard and free hit,
             GW1-19 / GW20-38 for the others), at most one a gameweek. A first-half chip
             not used by GW19 expires. After a free hit the squad, its purchase prices and
             the bank revert to what they were before it.
  scoring    the XI after FPL's auto-subs (team_optimizer.apply_auto_subs); the captain's
             points x2, or x3 with triple captain, going to the vice-captain when the
             captain didn't play; with bench boost all 15 count; minus hits (score_entry).

Files, in $LIVE_DATA_DIR/ml_manager/<season>/:
  gwNN.json          one entry per gameweek played: squad, transfers, chip, plan and result.
                     Status "planned" (the plan is saved again on every run before the
                     deadline), then "locked" once the deadline passes (its decisions can't
                     change: save_entry refuses), then "scored" with `result` filled in.
  state.json         replay() of the locked and scored entries: squad with purchase prices,
                     bank, free transfers for the next gameweek, chips used, history and
                     totals. Derived and rebuilt by rebuild_state; never the source of truth.
  rules.json         the Rules last read from bootstrap-static (save_rules).
  league_cache.json  ranks in FPL's "Gameweek 9" league (services/fpl_manager_league).

The manager starts in ML_MANAGER_START_GW (GW9 2026-27) with the v4 squad the pipeline saved
before that deadline (model_picks/<season>/gw09.json, start_entry_from_pick). The plans come
from services/fpl_manager_planner and the daily run is pipeline/ml_manager.
get_manager_view builds the /api/ml-manager payload (one gameweek at a time, by default the one
being played). Nothing here makes network calls.
"""
import copy
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config.settings import CURRENT_SEASON, ML_MANAGER_SEASON, ML_MANAGER_START_GW
from services import live_points as lp
from services import model_review as mr
from services import season_service as ss
from services import team_optimizer as opt

logger = logging.getLogger(__name__)

MANAGER_DIR = "ml_manager"
STATE_FILE = "state.json"
RULES_FILE = "rules.json"
MODEL = "v4"

PLANNED, LOCKED, SCORED = "planned", "locked", "scored"
STATUSES = (PLANNED, LOCKED, SCORED)

WILDCARD, FREEHIT, BBOOST, TRIPLE_CAPTAIN = "wildcard", "freehit", "bboost", "3xc"
CHIPS: Tuple[str, ...] = (WILDCARD, FREEHIT, BBOOST, TRIPLE_CAPTAIN)     # FPL's API names
CHIP_LABELS = {WILDCARD: "Wildcard", FREEHIT: "Free Hit", BBOOST: "Bench Boost", TRIPLE_CAPTAIN: "Triple Captain"}
UNLIMITED_TRANSFERS = (WILDCARD, FREEHIT)
# Conservative: never two free hits in a row (only possible across GW19/GW20). Whether FPL
# allows it isn't verified, and a manager that never tries it stays legal either way.
FREE_HIT_BACK_TO_BACK = False

FPL_POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}    # element_types ids (5 = assistant manager)


# ------------------------------------------------------------------ rules

@dataclass(frozen=True)
class ChipWindow:
    chip: str
    start: int        # first gameweek it can be played
    stop: int         # last gameweek
    half: int         # 1-based place among this chip's windows

    def contains(self, gameweek: int) -> bool:
        return self.start <= gameweek <= self.stop


def _default_windows() -> Tuple[ChipWindow, ...]:
    first = {WILDCARD: 2, FREEHIT: 2, BBOOST: 1, TRIPLE_CAPTAIN: 1}
    return tuple(w for chip in CHIPS
                 for w in (ChipWindow(chip, first[chip], 19, 1), ChipWindow(chip, 20, 38, 2)))


@dataclass(frozen=True)
class Rules:
    """FPL's rules for the season. Money in tenths. Defaults are the 2026-27 values."""
    budget: int = 1000                    # squad_total_spend
    squad_size: int = 15                  # squad_squadsize
    xi_size: int = 11                     # squad_squadplay
    max_per_club: int = 3                 # squad_team_limit
    max_free_transfers: int = 5           # 1 + max_extra_free_transfers
    hit_cost: int = 4                     # points per extra transfer (not in bootstrap)
    sell_on_fee: float = 0.5              # transfers_sell_on_fee: share of a rise FPL keeps
    vice_captain: bool = True             # sys_vice_captain_enabled
    transfers_cap: int = 20               # most transfers in a gameweek without a chip
    squad_positions: Dict[str, int] = field(default_factory=lambda: dict(opt.SQUAD_SIZE))
    xi_min: Dict[str, int] = field(default_factory=lambda: dict(opt.XI_MIN))
    xi_max: Dict[str, int] = field(default_factory=lambda: dict(opt.XI_MAX))
    chip_windows: Tuple[ChipWindow, ...] = field(default_factory=_default_windows)

    @classmethod
    def from_bootstrap(cls, bootstrap: Dict[str, Any]) -> "Rules":
        """Rules from FPL's bootstrap-static; anything missing keeps its default."""
        base = cls()
        gs = bootstrap.get("game_settings") or {}

        def num(key: str, default: Any, cast=int) -> Any:
            value = gs.get(key)
            try:
                return default if value is None else cast(value)
            except (TypeError, ValueError):
                return default

        squad, xi_min, xi_max = dict(base.squad_positions), dict(base.xi_min), dict(base.xi_max)
        for et in bootstrap.get("element_types") or []:
            pos = FPL_POSITIONS.get(et.get("id"))
            if pos is None:
                continue
            for target, key in ((squad, "squad_select"), (xi_min, "squad_min_play"), (xi_max, "squad_max_play")):
                if et.get(key) is not None:
                    target[pos] = int(et[key])
        extra = num("max_extra_free_transfers", base.max_free_transfers - 1)
        return cls(
            budget=num("squad_total_spend", base.budget),
            squad_size=num("squad_squadsize", base.squad_size),
            xi_size=num("squad_squadplay", base.xi_size),
            max_per_club=num("squad_team_limit", base.max_per_club),
            max_free_transfers=1 + extra,
            sell_on_fee=num("transfers_sell_on_fee", base.sell_on_fee, float),
            vice_captain=bool(gs.get("sys_vice_captain_enabled", base.vice_captain)),
            transfers_cap=num("transfers_cap", base.transfers_cap),
            squad_positions=squad, xi_min=xi_min, xi_max=xi_max,
            chip_windows=chip_windows_from(bootstrap.get("chips") or []) or base.chip_windows,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget": self.budget, "squad_size": self.squad_size, "xi_size": self.xi_size,
            "max_per_club": self.max_per_club, "team_limit": self.max_per_club,   # the planner's name
            "max_free_transfers": self.max_free_transfers,
            "hit_cost": self.hit_cost, "sell_on_fee": self.sell_on_fee, "vice_captain": self.vice_captain,
            "transfers_cap": self.transfers_cap, "squad_positions": dict(self.squad_positions),
            "xi_min": dict(self.xi_min), "xi_max": dict(self.xi_max),
            "chip_windows": [{"chip": w.chip, "start": w.start, "stop": w.stop, "half": w.half}
                             for w in self.chip_windows],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rules":
        base = cls()
        kwargs = {k: data[k] for k in ("budget", "squad_size", "xi_size", "max_per_club", "max_free_transfers",
                                       "hit_cost", "sell_on_fee", "vice_captain", "transfers_cap",
                                       "squad_positions", "xi_min", "xi_max") if data.get(k) is not None}
        windows = tuple(ChipWindow(w["chip"], int(w["start"]), int(w["stop"]), int(w["half"]))
                        for w in data.get("chip_windows") or [] if w.get("chip") in CHIPS)
        return cls(**kwargs, chip_windows=windows or base.chip_windows)


def chip_windows_from(chips: Iterable[Dict[str, Any]]) -> Tuple[ChipWindow, ...]:
    """bootstrap-static "chips" -> windows; a chip's halves are numbered by start gameweek."""
    by_chip: Dict[str, List[Tuple[int, int]]] = {}
    for c in chips:
        name = c.get("name")
        if name in CHIPS and c.get("start_event") is not None and c.get("stop_event") is not None:
            by_chip.setdefault(name, []).append((int(c["start_event"]), int(c["stop_event"])))
    return tuple(ChipWindow(chip, start, stop, half)
                 for chip in CHIPS for half, (start, stop) in enumerate(sorted(by_chip.get(chip, [])), start=1))


DEFAULT_RULES = Rules()


# ------------------------------------------------------------------ prices, transfers, chips

def selling_price(purchase: int, now: int, sell_on_fee: float = 0.5) -> int:
    """FPL's selling price in tenths: half of a rise is kept (rounded down), a fall is passed on."""
    purchase, now = int(purchase), int(now)
    if now <= purchase:
        return now
    return purchase + int(math.floor((now - purchase) * (1.0 - sell_on_fee) + 1e-9))


def next_free_transfers(free_transfers: int, used_free: int, chip: Optional[str] = None,
                        rules: Rules = DEFAULT_RULES) -> int:
    """Free transfers for the next gameweek: the unused ones plus one, at most 5 and at
    least 1. A wildcard or free hit keeps every banked free transfer."""
    left = int(free_transfers) if chip in UNLIMITED_TRANSFERS else max(int(free_transfers) - int(used_free), 0)
    return max(1, min(left + 1, rules.max_free_transfers))


def transfer_cost(transfers: int, free_transfers: int, chip: Optional[str] = None,
                  rules: Rules = DEFAULT_RULES) -> Tuple[int, int, int]:
    """(free transfers used, hits, points deducted) for a gameweek's transfers."""
    if chip in UNLIMITED_TRANSFERS:
        return 0, 0, 0
    used = min(int(transfers), int(free_transfers))
    hits = max(int(transfers) - int(free_transfers), 0)
    return used, hits, hits * rules.hit_cost


def chip_window(rules: Rules, chip: str, gameweek: int) -> Optional[ChipWindow]:
    return next((w for w in rules.chip_windows if w.chip == chip and w.contains(gameweek)), None)


def _uses_window(use: Dict[str, Any], window: ChipWindow) -> bool:
    if use.get("chip") != window.chip:
        return False
    if use.get("half") is not None:
        return int(use["half"]) == window.half
    return window.contains(int(use["gameweek"]))


def chips_available(rules: Rules, used: Iterable[Dict[str, Any]], gameweek: int) -> List[str]:
    """Chips that can be played in `gameweek`, given the chips already played
    ([{chip, gameweek, half}]): one of each per window, one chip a gameweek."""
    used = list(used)
    if any(int(u["gameweek"]) == gameweek for u in used):
        return []
    out = []
    for chip in CHIPS:
        window = chip_window(rules, chip, gameweek)
        if window is None or any(_uses_window(u, window) for u in used):
            continue
        if chip == FREEHIT and not FREE_HIT_BACK_TO_BACK and any(
                u["chip"] == FREEHIT and int(u["gameweek"]) == gameweek - 1 for u in used):
            continue
        out.append(chip)
    return out


def chip_deadlines(rules: Rules, used: Iterable[Dict[str, Any]], gameweek: int) -> Dict[str, int]:
    """{chip: the last gameweek of its window} for the chips playable in `gameweek` (the
    planner's chips_available)."""
    return {chip: chip_window(rules, chip, gameweek).stop for chip in chips_available(rules, used, gameweek)}


def chip_status(rules: Rules, used: Iterable[Dict[str, Any]], gameweek: int) -> List[Dict[str, Any]]:
    """One row per chip window for the page. `available`: not used and its window hasn't
    ended by `gameweek` (it may not have started yet); `active`: playable in `gameweek`."""
    used = list(used)
    playable = set(chips_available(rules, used, gameweek))
    rows = []
    for w in sorted(rules.chip_windows, key=lambda w: (CHIPS.index(w.chip), w.half)):
        use = next((u for u in used if _uses_window(u, w)), None)
        rows.append({
            "chip": w.chip, "label": CHIP_LABELS[w.chip], "half": w.half, "window": [w.start, w.stop],
            "used_gameweek": int(use["gameweek"]) if use else None, "planned": bool(use and use.get("planned")),
            "available": use is None and gameweek <= w.stop,
            "active": use is None and w.contains(gameweek) and w.chip in playable,
        })
    return rows


# ------------------------------------------------------------------ state

def new_state(season: str = CURRENT_SEASON, rules: Rules = DEFAULT_RULES,
              start_gameweek: int = ML_MANAGER_START_GW) -> Dict[str, Any]:
    """The state before the first squad: no players, the whole budget, nothing banked."""
    return {
        "season": season, "start_gameweek": start_gameweek, "gameweek": None, "next_gameweek": start_gameweek,
        "squad": [], "bank": rules.budget, "free_transfers": 0, "team_value": 0, "chips_used": [],
        "history": [], "totals": _totals([]), "problems": [],
    }


def _int(value: Any) -> Optional[int]:
    return ss._int_or_none(value)


def _bench_key(p: Dict[str, Any]) -> Tuple[int, int]:
    order = _int(p.get("bench_order"))
    return (order if order is not None else 99, p.get("position") != "GK")


def _state_player(p: Dict[str, Any]) -> Dict[str, Any]:
    """What the state keeps of a squad player: identity, purchase price and the lineup."""
    return {
        "element": int(p["element"]), "name": p.get("name"), "web_name": p.get("web_name"),
        "position": p.get("position"), "team": p.get("team"),
        "purchase_price": int(p["purchase_price"]), "now_cost": _int(p.get("now_cost")),
        "role": p.get("role"), "bench_order": _int(p.get("bench_order")),
        "captain": bool(p.get("captain")), "vice_captain": bool(p.get("vice_captain")),
    }


def squad_at_prices(state: Dict[str, Any], prices: Dict[int, int], rules: Rules = DEFAULT_RULES) -> Dict[str, Any]:
    """The state's squad at today's prices ({element: now_cost}, tenths): each player with
    now_cost and selling_price, plus the bank and free transfers (the planner's `current`).
    A player missing from `prices` keeps his last known price."""
    squad = []
    for p in state.get("squad") or []:
        now = int(prices.get(int(p["element"]), _int(p.get("now_cost")) or p["purchase_price"]))
        squad.append({**p, "now_cost": now,
                      "selling_price": selling_price(int(p["purchase_price"]), now, rules.sell_on_fee)})
    return {"squad": squad, "bank": int(state.get("bank") or 0),
            "free_transfers": int(state.get("free_transfers") or 0)}


def team_value(squad: Iterable[Dict[str, Any]]) -> int:
    """FPL's team value: the squad at current prices (tenths), without the bank."""
    return sum(_int(p.get("now_cost")) or int(p.get("purchase_price") or 0) for p in squad)


def apply_entry(state: Dict[str, Any], entry: Dict[str, Any], rules: Rules = DEFAULT_RULES) -> Dict[str, Any]:
    """The state after `entry`'s deadline: squad and purchase prices, bank, free transfers
    for the next gameweek and chips played. A free hit leaves the squad and bank as they
    were. Gameweeks skipped since the state's gameweek each add a free transfer (FPL rolls
    the team over). Doesn't validate (validate_entry) and doesn't change its inputs."""
    gw = int(entry["gameweek"])
    chip = entry.get("chip")
    out = copy.deepcopy(state)
    ft = int(state.get("free_transfers") or 0)
    if state.get("gameweek") is not None and state.get("squad"):
        for _ in range(gw - int(state["gameweek"]) - 1):
            ft = next_free_transfers(ft, 0, None, rules)

    transfers = entry.get("transfers") or []
    used_free, _, _ = transfer_cost(len(transfers), ft, chip, rules)
    if not state.get("squad"):
        bank = rules.budget - sum(int(p["purchase_price"]) for p in entry["squad"])
    else:
        bank = (int(state["bank"]) + sum(int(t["out"]["selling_price"]) for t in transfers)
                - sum(int(t["in"]["price"]) for t in transfers))
    if entry.get("bank_after") is not None and int(entry["bank_after"]) != bank:
        logger.warning("GW%s: bank_after %s doesn't match the transfers (%s)", gw, entry["bank_after"], bank)

    if chip != FREEHIT:
        out["squad"] = [_state_player(p) for p in entry["squad"]]
        out["bank"] = bank
    if chip:
        window = chip_window(rules, chip, gw)
        out["chips_used"] = list(state.get("chips_used") or []) + [
            {"chip": chip, "gameweek": gw, "half": window.half if window else None}]
    out["free_transfers"] = next_free_transfers(ft, used_free, chip, rules)
    out["gameweek"], out["next_gameweek"] = gw, gw + 1
    out["team_value"] = team_value(out["squad"])
    return out


# ------------------------------------------------------------------ validation

def _check_squad(squad: List[Dict[str, Any]], rules: Rules, kept: Optional[set] = None) -> List[str]:
    """Rule violations of a squad. `kept`: the elements it keeps from the previous squad; a club
    may have as many of them as it holds (a player moved there), but no more than that."""
    errors: List[str] = []
    if len(squad) != rules.squad_size:
        errors.append(f"Squad has {len(squad)} players, not {rules.squad_size}")
    elements = [_int(p.get("element")) for p in squad]
    if None in elements:
        errors.append("A squad player has no element id")
    dupes = sorted({e for e in elements if e is not None and elements.count(e) > 1})
    if dupes:
        errors.append(f"Duplicate players: {dupes}")
    for pos, n in rules.squad_positions.items():
        k = sum(p.get("position") == pos for p in squad)
        if k != n:
            errors.append(f"Squad has {k} {pos}, not {n}")
    clubs: Dict[str, int] = {}
    kept_clubs: Dict[str, int] = {}
    for p in squad:
        clubs[p.get("team")] = clubs.get(p.get("team"), 0) + 1
        if kept and _int(p.get("element")) in kept:
            kept_clubs[p.get("team")] = kept_clubs.get(p.get("team"), 0) + 1
    for club, k in sorted(clubs.items(), key=lambda kv: str(kv[0])):
        allowed = max(rules.max_per_club, kept_clubs.get(club, 0))
        if k > allowed:
            errors.append(f"{k} players from {club} (at most {allowed}"
                          + (" after a player moved there)" if allowed > rules.max_per_club else ")"))

    roles = {p.get("role") for p in squad} - {"starter", "bench"}
    if roles:
        errors.append(f"Unknown role(s): {sorted(map(str, roles))}")
    starters = [p for p in squad if p.get("role") == "starter"]
    bench = [p for p in squad if p.get("role") == "bench"]
    if len(starters) != rules.xi_size:
        errors.append(f"XI has {len(starters)} players, not {rules.xi_size}")
    for pos in rules.xi_min:
        k = sum(p.get("position") == pos for p in starters)
        if not rules.xi_min[pos] <= k <= rules.xi_max.get(pos, k):
            errors.append(f"XI has {k} {pos}")
    orders = sorted(_int(p.get("bench_order")) or 0 for p in bench)
    if orders != list(range(1, len(bench) + 1)):
        errors.append(f"Bench order should be 1..{len(bench)}, got {orders}")
    gk_orders = sorted(_int(p.get("bench_order")) or 0 for p in bench if p.get("position") == "GK")
    if gk_orders != list(range(1, len(gk_orders) + 1)):
        errors.append("The substitute goalkeeper must be first on the bench")

    captains = [p for p in squad if p.get("captain")]
    vices = [p for p in squad if p.get("vice_captain")]
    if len(captains) != 1 or captains[0].get("role") != "starter":
        errors.append("Need exactly one captain, in the XI")
    if rules.vice_captain:
        if len(vices) != 1 or vices[0].get("role") != "starter":
            errors.append("Need exactly one vice-captain, in the XI")
        elif captains and vices[0] is captains[0]:
            errors.append("Captain and vice-captain are the same player")

    for p in squad:
        purchase, now = _int(p.get("purchase_price")), _int(p.get("now_cost"))
        if purchase is None:
            errors.append(f"{p.get('name')} has no purchase price")
        elif now is not None and p.get("selling_price") is not None and \
                int(p["selling_price"]) != selling_price(purchase, now, rules.sell_on_fee):
            errors.append(f"{p.get('name')}: selling price {p['selling_price']} should be "
                          f"{selling_price(purchase, now, rules.sell_on_fee)}")
    return errors


def _check_money(entry: Dict[str, Any], bank_before: int, bank_after: int) -> List[str]:
    errors = []
    if _int(entry.get("bank_before")) != bank_before:
        errors.append(f"bank_before {entry.get('bank_before')} should be {bank_before}")
    if bank_after < 0:
        errors.append(f"Over budget by {-bank_after / 10:.1f}m")
    if _int(entry.get("bank_after")) != bank_after:
        errors.append(f"bank_after {entry.get('bank_after')} should be {bank_after}")
    return errors


def validate_entry(prev_state: Dict[str, Any], entry: Dict[str, Any], rules: Rules = DEFAULT_RULES,
                   prices: Optional[Dict[int, int]] = None) -> List[str]:
    """Rule violations of `entry` played from `prev_state` (the state after the previous
    gameweek; new_state() for the first squad). Empty list = valid. `prices`
    ({element: now_cost}, tenths) adds checks that the entry's prices are today's."""
    gw = _int(entry.get("gameweek"))
    if gw is None:
        return ["Entry has no gameweek"]
    errors: List[str] = []
    last = prev_state.get("gameweek")
    if last is not None and gw != int(last) + 1:
        errors.append(f"Gameweek {gw} doesn't follow gameweek {last}: save the gameweeks between "
                      "(rollover_entry) first")
    squad = entry.get("squad") or []
    kept = {int(p["element"]) for p in prev_state.get("squad") or []}
    errors += _check_squad(squad, rules, kept)

    chip = entry.get("chip")
    if chip is not None:
        if chip not in CHIPS:
            errors.append(f"Unknown chip {chip!r}")
        elif chip not in chips_available(rules, prev_state.get("chips_used") or [], gw):
            errors.append(f"{CHIP_LABELS[chip]} can't be played in gameweek {gw}")

    transfers = entry.get("transfers") or []
    if _int(entry.get("transfers_made")) != len(transfers):
        errors.append(f"transfers_made {entry.get('transfers_made')} should be {len(transfers)}")
    by_element = {_int(p.get("element")): p for p in squad}

    if not prev_state.get("squad"):                       # the first squad: picked for free
        if transfers:
            errors.append("The first squad has no transfers")
        if chip in UNLIMITED_TRANSFERS:
            errors.append(f"{CHIP_LABELS[chip]} can't be played with the first squad")
        if _int(entry.get("free_transfers")) not in (0, None):
            errors.append("The first squad has no free transfers")
        if _int(entry.get("hits")) or _int(entry.get("hit_cost")):
            errors.append("The first squad has no hits")
        spent = sum(_int(p.get("purchase_price")) or 0 for p in squad)
        errors += _check_money(entry, rules.budget, rules.budget - spent)
    else:
        prev = {int(p["element"]): p for p in prev_state["squad"]}
        outs = [_int((t.get("out") or {}).get("element")) for t in transfers]
        ins = [_int((t.get("in") or {}).get("element")) for t in transfers]
        for t in transfers:
            if (t.get("out") or {}).get("position") != (t.get("in") or {}).get("position"):
                errors.append(f"Transfer {t.get('out', {}).get('name')} -> {t.get('in', {}).get('name')} "
                              "changes position")
        if len(set(outs)) != len(outs) or len(set(ins)) != len(ins):
            errors.append("A player is transferred twice")
        if set(outs) & set(ins):
            errors.append("A player is both sold and bought")
        for e in outs:
            if e not in prev:
                errors.append(f"Sold player {e} isn't in the squad")
        for e in ins:
            if e in prev:
                errors.append(f"Bought player {e} is already in the squad")
        expected = (set(prev) - set(outs)) | set(ins)
        if set(by_element) != expected:
            errors.append("The squad doesn't match the previous squad and the transfers")
        for e, p in by_element.items():
            if e in prev and _int(p.get("purchase_price")) != int(prev[e]["purchase_price"]):
                errors.append(f"{p.get('name')}: purchase price changed from {prev[e]['purchase_price']}")
        for t in transfers:
            t_in, t_out = t.get("in") or {}, t.get("out") or {}
            p = by_element.get(_int(t_in.get("element")))
            if p is not None and _int(p.get("purchase_price")) != _int(t_in.get("price")):
                errors.append(f"{t_in.get('name')}: bought at {t_in.get('price')}, "
                              f"purchase price {p.get('purchase_price')}")
            out_el = _int(t_out.get("element"))
            if prices is not None and out_el in prev and out_el in prices:
                want = selling_price(int(prev[out_el]["purchase_price"]), int(prices[out_el]), rules.sell_on_fee)
                if _int(t_out.get("selling_price")) != want:
                    errors.append(f"{t_out.get('name')}: sells for {want}, not {t_out.get('selling_price')}")
        ft = int(prev_state.get("free_transfers") or 0)
        if _int(entry.get("free_transfers")) != ft:
            errors.append(f"free_transfers {entry.get('free_transfers')} should be {ft}")
        _, hits, hit_cost = transfer_cost(len(transfers), ft, chip, rules)
        if (_int(entry.get("hits")), _int(entry.get("hit_cost"))) != (hits, hit_cost):
            errors.append(f"hits/hit_cost {entry.get('hits')}/{entry.get('hit_cost')} should be {hits}/{hit_cost}")
        if chip not in UNLIMITED_TRANSFERS and len(transfers) > rules.transfers_cap:
            errors.append(f"{len(transfers)} transfers (at most {rules.transfers_cap} without a chip)")
        bank_before = int(prev_state["bank"])
        sells = sum(_int((t.get("out") or {}).get("selling_price")) or 0 for t in transfers)
        buys = sum(_int((t.get("in") or {}).get("price")) or 0 for t in transfers)
        errors += _check_money(entry, bank_before, bank_before + sells - buys)

    if prices is not None:
        for p in squad:
            e = _int(p.get("element"))
            if e in prices and _int(p.get("now_cost")) != int(prices[e]):
                errors.append(f"{p.get('name')}: now_cost {p.get('now_cost')} is not today's {prices[e]}")
        for t in transfers:
            t_in = t.get("in") or {}
            e = _int(t_in.get("element"))
            if e not in prices:
                errors.append(f"Bought player {e} isn't in the game")
            elif _int(t_in.get("price")) != int(prices[e]):
                errors.append(f"{t_in.get('name')}: costs {prices[e]}, not {t_in.get('price')}")
    return errors


# ------------------------------------------------------------------ building entries

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def deadline_passed(entry: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    deadline = ss.to_datetime(entry.get("deadline"))
    return deadline is not None and (now or _utcnow()) >= deadline


def _transfer_side(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"element": int(p["element"]), "name": p.get("name"), "web_name": p.get("web_name"),
            "position": p.get("position"), "team": p.get("team")}


def build_entry(state: Dict[str, Any], gameweek: int, deadline: Optional[str], starters: Sequence[int],
                bench: Sequence[int], captain: int, vice_captain: Optional[int],
                players: Dict[int, Dict[str, Any]], chip: Optional[str] = None, rules: Rules = DEFAULT_RULES,
                predicted: Optional[Dict[int, float]] = None, horizon: Optional[List[Dict[str, Any]]] = None,
                reason: str = "", model: str = MODEL, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The gwNN.json entry for a lineup played from `state` (the state after the previous
    gameweek, or new_state() for the first squad).

    starters: 11 elements; bench: 4 elements in priority order (a goalkeeper is moved to
    slot 1, as FPL lays the bench out). players: {element: {name, web_name, position, team,
    now_cost (tenths), optional fixtures/opponents/predicted_points}} for the new squad; a
    player already owned may be left out (the state's details are used). Transfers are the
    difference from the state's squad, paired out/in by position. Players are bought at
    now_cost and sold at selling_price; free transfers, hits and the bank follow the rules.
    projected_points = XI + the captain's extra (x2, or x3 with triple captain) + the bench
    with bench boost - hit_cost, from `predicted` (else the players' predicted_points).
    Status is "planned", or "locked" when the deadline has passed. Check the result with
    validate_entry before saving."""
    now = now or _utcnow()
    players = {int(k): v for k, v in (players or {}).items()}
    predicted = {int(k): float(v) for k, v in (predicted or {}).items()}
    prev = {int(p["element"]): p for p in state.get("squad") or []}
    initial = not prev
    starters, bench = [int(e) for e in starters], [int(e) for e in bench]
    captain, vice_captain = int(captain), (int(vice_captain) if vice_captain is not None else None)

    def details(e: int) -> Dict[str, Any]:
        d = {**prev.get(e, {}), **players.get(e, {})}
        if d.get("position") is None:
            raise ValueError(f"No details for player {e}")
        return d

    def cost(e: int) -> int:
        d = details(e)
        for key in ("now_cost", "purchase_price"):
            if _int(d.get(key)) is not None:
                return int(d[key])
        raise ValueError(f"No price for player {e}")

    def points(e: int) -> Optional[float]:
        if e in predicted:
            return predicted[e]
        value = players.get(e, {}).get("predicted_points")
        return float(value) if value is not None else None

    new_ids = starters + bench
    outs = [] if initial else [e for e in prev if e not in set(new_ids)]
    ins = [] if initial else [e for e in new_ids if e not in prev]
    rank = {pos: i for i, pos in enumerate(opt.POSITIONS)}
    outs.sort(key=lambda e: rank.get(details(e)["position"], 9))
    ins.sort(key=lambda e: rank.get(details(e)["position"], 9))
    transfers = []
    for o, i in zip(outs, ins):
        sell = selling_price(int(prev[o]["purchase_price"]), cost(o), rules.sell_on_fee)
        transfers.append({"out": {**_transfer_side({**details(o), "element": o}), "selling_price": sell},
                          "in": {**_transfer_side({**details(i), "element": i}), "price": cost(i)}})

    ft = 0 if initial else int(state.get("free_transfers") or 0)
    _, hits, hit_cost = (0, 0, 0) if initial else transfer_cost(len(transfers), ft, chip, rules)
    if initial:
        bank_before = rules.budget
        bank_after = rules.budget - sum(cost(e) for e in new_ids)
    else:
        bank_before = int(state["bank"])
        bank_after = (bank_before + sum(t["out"]["selling_price"] for t in transfers)
                      - sum(t["in"]["price"] for t in transfers))

    bench_sorted = sorted(range(len(bench)), key=lambda k: (details(bench[k])["position"] != "GK", k))
    bench_order = {bench[k]: n for n, k in enumerate(bench_sorted, start=1)}
    starters_sorted = sorted(starters, key=lambda e: (rank.get(details(e)["position"], 9), starters.index(e)))

    def squad_player(e: int, role: str) -> Dict[str, Any]:
        d = details(e)
        purchase = int(prev[e]["purchase_price"]) if e in prev else cost(e)
        now_cost = cost(e)
        row = {
            "element": e, "name": d.get("name"), "web_name": d.get("web_name"), "position": d["position"],
            "team": d.get("team"), "purchase_price": purchase,
            "selling_price": selling_price(purchase, now_cost, rules.sell_on_fee), "now_cost": now_cost,
            "predicted_points": round(points(e), 2) if points(e) is not None else None,
            "role": role, "bench_order": bench_order.get(e) if role == "bench" else None,
            "captain": e == captain, "vice_captain": e == vice_captain,
        }
        for key in ("fixtures", "opponents"):
            if players.get(e, {}).get(key) is not None:
                row[key] = players[e][key]
        return row

    squad = [squad_player(e, "starter") for e in starters_sorted] + \
            [squad_player(bench[k], "bench") for k in bench_sorted]
    projected = None
    if any(points(e) is not None for e in new_ids):
        mult = 3 if chip == TRIPLE_CAPTAIN else 2
        total = sum(points(e) or 0.0 for e in starters) + (mult - 1) * (points(captain) or 0.0)
        if chip == BBOOST:
            total += sum(points(e) or 0.0 for e in bench)
        projected = round(total - hit_cost, 2)
    entry = {
        "season": state.get("season") or CURRENT_SEASON, "gameweek": int(gameweek), "deadline": deadline,
        "status": PLANNED, "planned_at": _iso(now), "model": model, "chip": chip,
        "transfers": transfers, "free_transfers": ft, "transfers_made": len(transfers),
        "hits": hits, "hit_cost": hit_cost, "bank_before": bank_before, "bank_after": bank_after,
        "squad": squad, "projected_points": projected,
        "horizon": horizon if horizon is not None else
        ([{"gameweek": int(gameweek), "projected": projected}] if projected is not None else []),
        "reason": reason, "result": None,
    }
    if deadline_passed(entry, now):
        entry["status"] = LOCKED
    return entry


def rollover_entry(state: Dict[str, Any], gameweek: int, deadline: Optional[str],
                   prices: Optional[Dict[int, int]] = None, rules: Rules = DEFAULT_RULES,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """The entry for a gameweek nobody planned before its deadline: FPL plays the last
    team again (the squad before a free hit), with no transfers or chip, and banks the
    free transfer."""
    squad = state.get("squad") or []
    if not squad:
        raise ValueError("No squad to roll over")
    prices = prices or {}
    starters = [p for p in squad if p.get("role") == "starter"]
    bench = sorted((p for p in squad if p.get("role") == "bench"), key=_bench_key)
    captain = next((p for p in starters if p.get("captain")), starters[0])
    vice = next((p for p in starters if p.get("vice_captain")), None)
    players = {int(p["element"]): {**p, "now_cost": prices.get(int(p["element"]), p.get("now_cost"))}
               for p in squad}
    return build_entry(state, gameweek, deadline, [p["element"] for p in starters], [p["element"] for p in bench],
                       captain["element"], vice["element"] if vice else None, players, rules=rules,
                       reason="No plan was saved before the deadline, so the previous team played again",
                       now=now)


def start_entry_from_pick(pick: Dict[str, Any], deadline: Optional[str] = None, now: Optional[datetime] = None,
                          web_names: Optional[Dict[str, str]] = None, rules: Rules = DEFAULT_RULES,
                          start_gameweek: Optional[int] = None) -> Dict[str, Any]:
    """The first entry from the model's saved pick (model_picks/<season>/gwNN.json): its 15
    players, roles and armband, bought at the pick's cost (in m: purchase price = cost x 10);
    no transfers or chip and no free transfers (the first squad is free). The bench keeps
    the file's order with the goalkeeper first. Locked once the deadline has passed."""
    web_names = web_names or {}
    gameweek = int(start_gameweek or pick["gameweek"])
    rows = pick["players"]
    players = {}
    for p in rows:
        players[int(p["element"])] = {
            "name": p.get("name"), "web_name": p.get("web_name") or web_names.get(p.get("name")),
            "position": ss.normalize_position(p.get("position")), "team": p.get("team"),
            "now_cost": int(round(float(p["cost"]) * 10)), "predicted_points": p.get("predicted_points"),
            "fixtures": p.get("fixtures") or [], "opponents": p.get("opponents") or [],
        }
    starters = [int(p["element"]) for p in rows if p.get("role") == "starter"]
    bench = [int(p["element"]) for p in rows if p.get("role") == "bench"]
    captain = next(int(p["element"]) for p in rows if p.get("captain"))
    vice = next((int(p["element"]) for p in rows if p.get("vice_captain")), None)
    state = new_state(pick.get("season") or CURRENT_SEASON, rules, gameweek)
    model = pick.get("model") or MODEL
    entry = build_entry(state, gameweek, deadline or pick.get("deadline"), starters, bench, captain, vice, players,
                        rules=rules, model=model, now=now)
    spent = sum(p["purchase_price"] for p in entry["squad"])
    entry["planned_at"] = pick.get("archived_at") or entry["planned_at"]
    entry["reason"] = (f"Starting squad: the {model} model's GW{gameweek} pick "
                       f"({spent / 10:.1f}m, {entry['bank_after'] / 10:.1f}m in the bank)")
    return entry


# ------------------------------------------------------------------ scoring

def ready_to_score(event: Dict[str, Any]) -> bool:
    """FPL has finished the gameweek and checked its data (bonus points are final)."""
    return bool(event.get("finished") and event.get("data_checked"))


def _display(p: Optional[Dict[str, Any]]) -> Optional[str]:
    return (p.get("web_name") or p.get("name")) if p else None


def score_entry(entry: Dict[str, Any], actual: Dict[int, Dict[str, int]], event: Dict[str, Any],
                finished_fixtures: Iterable[int] = (), previous_total: int = 0,
                league: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The `result` of a locked entry. `actual` is {element: {points, minutes}} for the
    gameweek (model_review.actual_by_element; double gameweeks summed), `event` its
    meta.json event (finished, average_entry_score, highest_score). A player whose fixtures
    aren't all finished (in `finished_fixtures`, or the whole gameweek) is undecided."""
    finished = set(finished_fixtures)
    gw_finished = bool(event.get("finished"))
    chip = entry.get("chip")
    rows = []
    for p in entry["squad"]:
        element = _int(p.get("element"))
        a = actual.get(element) if element is not None else None
        fixtures = p.get("fixtures")
        done = gw_finished or (fixtures is not None and all(f in finished for f in fixtures))
        minutes = a["minutes"] if a else (0 if done else None)
        rows.append({
            "element": element, "name": p.get("name"), "web_name": p.get("web_name"), "position": p["position"],
            "role": p.get("role"), "bench_order": p.get("bench_order"),
            "captain": bool(p.get("captain")), "vice_captain": bool(p.get("vice_captain")),
            "points": a["points"] if a else (0 if done else None), "minutes": minutes,
            "played": True if minutes else (False if done else None),
        })

    starters = [r for r in rows if r["role"] == "starter"]
    bench = sorted((r for r in rows if r["role"] == "bench"), key=_bench_key)
    if chip == BBOOST:
        xi, subs = starters + bench, []
    else:
        xi, _, subs = opt.apply_auto_subs(starters, bench)
    counted = {id(r) for r in xi}
    captain = next((r for r in starters if r["captain"]), None)
    vice = next((r for r in starters if r["vice_captain"]), None)
    armband = captain
    if captain is None or captain["played"] is False:
        armband = vice if vice is not None and vice["played"] is not False else None
    multiplier = 3 if chip == TRIPLE_CAPTAIN else 2
    out_names, in_names = {o for o, _ in subs}, {i for _, i in subs}

    players = []
    for r in rows:
        mult = (multiplier if r is armband else 1) if id(r) in counted else 0
        players.append({**r, "multiplier": mult, "counted": mult > 0,
                        "subbed_in": r["name"] in in_names and r["role"] == "bench",
                        "subbed_out": r["name"] in out_names and r["role"] == "starter"})
    gross = sum((p["points"] or 0) * p["multiplier"] for p in players)
    hit_cost = int(entry.get("hit_cost") or 0)
    return {
        "points": gross - hit_cost, "gross": gross, "hit_cost": hit_cost, "chip": chip,
        "captain": _display(captain), "armband": _display(armband),
        "vice_captain_used": armband is not None and armband is not captain,
        "auto_subs": [[o, i] for o, i in subs], "players": players,
        "bench_points": sum(p["points"] or 0 for p in players if not p["counted"]),
        "gw_average": event.get("average_entry_score") or None,
        "gw_highest": event.get("highest_score") or None,
        "total": int(previous_total) + gross - hit_cost,
        "league": league, "finished": gw_finished,
    }


def score_live(entry: Dict[str, Any], season: str = CURRENT_SEASON, previous_total: int = 0,
               league: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """score_entry from the live files, as the Model vs actual review reads them: the season
    CSV's points (model_review.actual_by_element), meta.json's event and finished fixtures."""
    gw = int(entry["gameweek"])
    return score_entry(entry, mr.actual_by_element(gw, season), mr.event_info(gw, season),
                       ss.read_meta(season).get("finished_fixtures") or [], previous_total, league)


# ------------------------------------------------------------------ replay

def _totals(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    with_avg = [h for h in history if h.get("gw_average") is not None]
    last_league = next((h for h in reversed(history) if h.get("league_rank") is not None), None)
    return {
        "points": sum(h["points"] for h in history),
        "gameweeks": len(history),
        "vs_average": sum(h["points"] - h["gw_average"] for h in with_avg) if with_avg else None,
        "league": {"id": last_league.get("league_id"), "name": last_league.get("league_name"),
                   "gameweek": last_league["gameweek"], "rank": last_league["league_rank"],
                   "size": last_league["league_size"], "percentile": last_league["percentile"]}
        if last_league else None,
    }


def history_row(entry: Dict[str, Any], total: int) -> Dict[str, Any]:
    result = entry.get("result") or {}
    league = result.get("league") or {}
    return {
        "gameweek": int(entry["gameweek"]), "points": result.get("points"), "gross": result.get("gross"),
        "hit_cost": int(entry.get("hit_cost") or 0), "chip": entry.get("chip"), "captain": result.get("captain"),
        "transfers": int(entry.get("transfers_made") or 0), "total": total,
        "team_value": team_value(entry.get("squad") or []), "bank": entry.get("bank_after"),
        "gw_average": result.get("gw_average"), "gw_highest": result.get("gw_highest"),
        "league_id": league.get("id"), "league_name": league.get("name"),
        "league_rank": league.get("rank"), "league_size": league.get("size"), "percentile": league.get("percentile"),
    }


def replay(entries: Iterable[Dict[str, Any]], rules: Rules = DEFAULT_RULES, season: Optional[str] = None,
           start_gameweek: int = ML_MANAGER_START_GW, include_planned: bool = False) -> Dict[str, Any]:
    """The state after every locked and scored entry (planned ones too with
    include_planned), in gameweek order, plus the history of scored gameweeks and totals.
    Rule problems found on the way are listed under `problems`, not raised."""
    entries = sorted(entries, key=lambda e: int(e["gameweek"]))
    state = new_state(season or (entries[0].get("season") if entries else None) or CURRENT_SEASON,
                      rules, start_gameweek)
    problems, history, total = [], [], 0
    for e in entries:
        if e.get("status") == PLANNED and not include_planned:
            continue
        errors = validate_entry(state, e, rules)
        if errors:
            problems.append({"gameweek": int(e["gameweek"]), "errors": errors})
        state = apply_entry(state, e, rules)
        if e.get("status") == SCORED and e.get("result"):
            total += int(e["result"]["points"])
            history.append(history_row(e, total))
    state.update(history=history, totals=_totals(history), problems=problems)
    return state


# ------------------------------------------------------------------ files

def season_dir(live_dir: str, season: str = CURRENT_SEASON) -> str:
    return os.path.join(live_dir, MANAGER_DIR, season)


def entry_name(gameweek: int) -> str:
    return f"gw{int(gameweek):02d}.json"


def entry_path(live_dir: str, season: str, gameweek: int) -> str:
    return os.path.join(season_dir(live_dir, season), entry_name(gameweek))


def _is_entry_name(name: str) -> bool:
    return name.startswith("gw") and name.endswith(".json") and name[2:-5].isdigit()


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path: str, data: Any) -> None:
    """Write atomically: a temporary file, then a rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_entry(live_dir: str, season: str, gameweek: int) -> Optional[Dict[str, Any]]:
    return _read_json(entry_path(live_dir, season, gameweek))


def load_entries(live_dir: str, season: str = CURRENT_SEASON) -> List[Dict[str, Any]]:
    """Every saved entry of the season, by gameweek (unreadable files are skipped)."""
    d = season_dir(live_dir, season)
    try:
        names = sorted(n for n in os.listdir(d) if _is_entry_name(n))
    except OSError:
        return []
    out = []
    for name in names:
        entry = _read_json(os.path.join(d, name))
        if entry is None:
            logger.warning("Skipping unreadable ML manager entry %s", name)
            continue
        out.append(entry)
    return sorted(out, key=lambda e: int(e["gameweek"]))


class EntryLockedError(ValueError):
    """The gameweek's deadline has passed, so its decisions can't change."""


DECISION_KEYS = ("gameweek", "chip", "transfers", "free_transfers", "transfers_made", "hits", "hit_cost",
                 "bank_before", "bank_after")


def _decisions(entry: Dict[str, Any]) -> str:
    squad = sorted(((_int(p.get("element")), p.get("role"), _int(p.get("bench_order")), bool(p.get("captain")),
                     bool(p.get("vice_captain")), _int(p.get("purchase_price"))) for p in entry.get("squad") or []),
                   key=lambda r: (r[0] is None, r[0] or 0))
    return json.dumps([{k: entry.get(k) for k in DECISION_KEYS}, squad], sort_keys=True, default=str)


def save_entry(live_dir: str, entry: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Save an entry atomically. After the deadline nothing the manager decided may change:
    a planned entry can no longer be saved, and a locked or scored one only changes its
    status, result and league (EntryLockedError otherwise). Returns the path."""
    if entry.get("status") not in STATUSES:
        raise ValueError(f"Unknown status {entry.get('status')!r}")
    path = entry_path(live_dir, entry.get("season") or CURRENT_SEASON, entry["gameweek"])
    if entry["status"] == PLANNED and deadline_passed(entry, now):
        raise EntryLockedError(f"GW{entry['gameweek']}'s deadline has passed; the plan can't be saved")
    existing = _read_json(path)
    if existing is not None and existing.get("status") in (LOCKED, SCORED):
        if entry["status"] == PLANNED:
            raise EntryLockedError(f"GW{entry['gameweek']} is already {existing['status']}")
        if _decisions(existing) != _decisions(entry):
            raise EntryLockedError(f"GW{entry['gameweek']} is {existing['status']}; its team can't change")
    write_json(path, entry)
    return path


def lock_entry(entry: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """A planned entry whose deadline has passed, marked locked (a copy); else unchanged."""
    if entry.get("status") == PLANNED and deadline_passed(entry, now):
        return {**entry, "status": LOCKED}
    return entry


def save_rules(live_dir: str, season: str, rules: Rules) -> str:
    path = os.path.join(season_dir(live_dir, season), RULES_FILE)
    write_json(path, rules.to_dict())
    return path


def load_rules(live_dir: str, season: str = CURRENT_SEASON) -> Rules:
    data = _read_json(os.path.join(season_dir(live_dir, season), RULES_FILE))
    try:
        return Rules.from_dict(data) if data else Rules()
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Unreadable %s, using the default rules: %s", RULES_FILE, exc)
        return Rules()


def rebuild_state(live_dir: str, season: str = CURRENT_SEASON, rules: Optional[Rules] = None) -> Dict[str, Any]:
    """Replay the saved entries and write state.json (the same files give the same bytes)."""
    rules = rules or load_rules(live_dir, season)
    start = ML_MANAGER_START_GW if season == ML_MANAGER_SEASON else None
    entries = load_entries(live_dir, season)
    state = replay(entries, rules, season, start or (int(entries[0]["gameweek"]) if entries else ML_MANAGER_START_GW))
    write_json(os.path.join(season_dir(live_dir, season), STATE_FILE), state)
    return state


def read_pick(live_dir: str, season: str, gameweek: int) -> Optional[Dict[str, Any]]:
    """The model's saved pick for a gameweek: LIVE_DATA_DIR's, else a committed one."""
    for d in (os.path.join(live_dir, mr.PICKS_DIR, season), os.path.join(mr.COMMITTED_DIR, season)):
        pick = _read_json(os.path.join(d, f"gw{int(gameweek):02d}.json"))
        if pick is not None:
            return pick
    return None


def ensure_start_entry(live_dir: str, season: str = ML_MANAGER_SEASON, start_gameweek: int = ML_MANAGER_START_GW,
                       now: Optional[datetime] = None, web_names: Optional[Dict[str, str]] = None,
                       rules: Rules = DEFAULT_RULES) -> Optional[Dict[str, Any]]:
    """Create or refresh the first entry from the model's saved pick: "planned" while the
    deadline is ahead (the pick may still change), "locked" after it. A locked or scored
    start entry is returned as it is. None when there's no pick yet."""
    existing = load_entry(live_dir, season, start_gameweek)
    if existing is not None and existing.get("status") != PLANNED:
        return existing
    pick = read_pick(live_dir, season, start_gameweek)
    if pick is None or not pick.get("players"):
        return existing
    entry = start_entry_from_pick(pick, now=now, web_names=web_names, rules=rules, start_gameweek=start_gameweek)
    entry["season"] = season
    save_entry(live_dir, entry, now=now)
    return entry


# ------------------------------------------------------------------ endpoint payload

def _cached_entries(live: str, season: str) -> List[Dict[str, Any]]:
    d = season_dir(live, season)
    try:
        names = sorted(n for n in os.listdir(d) if _is_entry_name(n))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(d, name)
        entry = ss._cached(("ml-manager", path, ss._mtime(path)), lambda p=path: _read_json(p))
        if entry is not None:
            out.append(entry)
    return sorted(out, key=lambda e: int(e["gameweek"]))


class NoEntryError(ss.NotFoundError):
    """The manager is running but has no entry for the gameweek asked for."""


def _status_at(entry: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """The entry's status as the page shows it: a plan past its deadline counts as locked."""
    return lock_entry(entry, now)["status"]


def _being_played(entry: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Its deadline has passed and the pipeline hasn't scored it yet."""
    return _status_at(entry, now) == LOCKED and not entry.get("result")


def default_gameweek(entries: List[Dict[str, Any]], now: Optional[datetime] = None) -> int:
    """The gameweek the page opens on: the one being played (deadline passed, not scored yet),
    even when the next one is already planned; else the latest entry."""
    playing = [e for e in entries if _being_played(e, now)]
    return int((playing or entries)[-1]["gameweek"])


def gameweek_list(entries: List[Dict[str, Any]], season: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Every gameweek the manager has an entry for, for the page's picker: status as shown,
    `live` while FPL plays it (deadline passed, not finished), and the points once scored."""
    out = []
    for e in entries:
        gw = int(e["gameweek"])
        status = _status_at(e, now)
        result = e.get("result") or {}
        out.append({"gameweek": gw, "status": status,
                    "live": status == LOCKED and not result
                    and lp.is_live(mr.event_info(gw, season), e.get("deadline"), now),
                    "points": result.get("points") if status == SCORED else None})
    return out


def provisional_result(entry: Dict[str, Any], season: str, previous_total: int,
                       live: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None) -> Optional[Dict[str, Any]]:
    """The `result` of a locked gameweek the pipeline hasn't scored yet: FPL's live points
    (live_points.gameweek_live) over the season CSV's finished matches, scored like
    score_entry. `live` says when FPL's feed was read (null when only the CSV had points),
    `provisional` that the pipeline still scores it for good. None when neither has anything
    (no feed and no CSV rows for the gameweek), so the page shows the team as locked."""
    gw = int(entry["gameweek"])
    feed = live(gw) if live is not None else None
    csv_actual = mr.actual_by_element(gw, season)
    if not feed and not csv_actual:
        return None
    finished = set(ss.read_meta(season).get("finished_fixtures") or []) | set((feed or {}).get("finished") or [])
    result = score_entry(entry, lp.merge(csv_actual, feed), mr.event_info(gw, season), sorted(finished),
                         previous_total=previous_total)
    return {**result, "live": {"fetched_at": feed["fetched_at"]} if feed else None, "provisional": True}


def get_manager_view(season: str = CURRENT_SEASON, photo_for: Optional[ss.PhotoFn] = None,
                     now: Optional[datetime] = None,
                     live: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
                     gameweek: Optional[int] = None) -> Dict[str, Any]:
    """The /api/ml-manager payload: one gameweek's entry (`current`), chips, history, totals and
    the list of gameweeks. NotFoundError until the first entry exists, and for a `gameweek` the
    manager has no entry for. Players get a `photo` like the other team-of-the-week payloads.

    Without `gameweek` it's default_gameweek: the gameweek being played while FPL plays it
    (the next one's plan is in `gameweeks`), else the latest entry (the planned next gameweek,
    or the last locked/scored one).

    A locked gameweek the pipeline hasn't scored yet gets a provisional `result`
    (provisional_result: with `live`, from FPL's live points, `result.live` saying when they
    were fetched); the entry stays locked, and the pipeline scores it once FPL has checked
    the gameweek."""
    live_dir = ss.live_dir()
    entries = _cached_entries(live_dir, season)
    if not entries:
        raise ss.NotFoundError("The ML manager hasn't picked its first squad yet")
    rules = load_rules(live_dir, season)
    start = ML_MANAGER_START_GW if season == ML_MANAGER_SEASON else int(entries[0]["gameweek"])
    state = replay(entries, rules, season, start)
    by_gw = {int(e["gameweek"]): e for e in entries}
    default = default_gameweek(entries, now)
    shown = default if gameweek is None else int(gameweek)
    if shown not in by_gw:
        raise NoEntryError(f"The ML manager has no team for gameweek {shown}")
    current = lock_entry(copy.deepcopy(by_gw[shown]), now)   # past its deadline it counts, even before the next run locks it
    latest = lock_entry(entries[-1], now)

    lookup = ss.status_lookup(season)
    for p in current.get("squad") or []:
        p["web_name"] = p.get("web_name") or lookup["web_name"].get(p.get("name"))
        p["_code"] = lookup["code"].get(_int(p.get("element")))
    for t in current.get("transfers") or []:
        for side in (t.get("out") or {}, t.get("in") or {}):
            side["web_name"] = side.get("web_name") or lookup["web_name"].get(side.get("name"))
            side["_code"] = lookup["code"].get(_int(side.get("element")))
    current["team_value"] = team_value(current.get("squad") or [])
    if current.get("status") == LOCKED and not current.get("result") and deadline_passed(current, now):
        before = sum(int(h["points"]) for h in state["history"] if int(h["gameweek"]) < shown)
        current["result"] = provisional_result(current, season, before, live)
    # The chips as of the latest entry (a chip planned for the next gameweek shows as planned).
    used = list(state["chips_used"])
    if latest.get("status") == PLANNED:
        chip_gw = int(latest["gameweek"])
        if latest.get("chip"):
            window = chip_window(rules, latest["chip"], chip_gw)
            used.append({"chip": latest["chip"], "gameweek": chip_gw, "half": window.half if window else None,
                         "planned": True})
    else:
        chip_gw = int(state["next_gameweek"])

    history = [{**h, "captain": lookup["web_name"].get(h["captain"], h["captain"]) if h.get("captain") else None}
               for h in state["history"]]
    payload = {
        "season": season, "start_gameweek": start, "model": current.get("model") or MODEL,
        "rules": {"budget": rules.budget, "max_free_transfers": rules.max_free_transfers, "hit_cost": rules.hit_cost},
        "current": current,
        "gameweeks": gameweek_list(entries, season, now),
        "default_gameweek": default,
        "state": {"gameweek": state["gameweek"], "next_gameweek": state["next_gameweek"], "bank": state["bank"],
                  "free_transfers": state["free_transfers"], "team_value": state["team_value"]},
        "chips": chip_status(rules, used, chip_gw),
        "history": history,
        "totals": state["totals"],
        "next_deadline": ss.read_meta(season).get("next_deadline"),
    }
    return ss.with_photos(payload, photo_for)
