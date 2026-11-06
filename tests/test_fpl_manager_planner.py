"""Tests for services/fpl_manager_planner.py: small synthetic pools, every solve well under a second."""
import dataclasses
from datetime import datetime, timezone

import pandas as pd
import pytest

from services import fpl_manager_planner as planner
from services import team_optimizer as opt

GWS = [10, 11, 12, 13]
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
ALL_CHIPS = [{"chip": c, "window_end": 19} for c in planner.CHIPS]

# element: (position, club, price, points per gameweek). Owned: a sensible squad whose XI is the
# 11 players with 3+ points (4-4-2), with four 1-point substitutes.
SQUAD = {
    1: ("GK", "A", 45, 4.0), 2: ("GK", "B", 40, 1.0),
    3: ("DEF", "A", 50, 4.0), 4: ("DEF", "B", 50, 4.0), 5: ("DEF", "C", 45, 3.5), 6: ("DEF", "D", 45, 3.0),
    7: ("DEF", "E", 40, 1.0),
    8: ("MID", "C", 80, 6.0), 9: ("MID", "D", 70, 5.0), 10: ("MID", "E", 65, 4.5), 11: ("MID", "F", 60, 4.0),
    12: ("MID", "G", 45, 1.0),
    13: ("FWD", "F", 75, 5.0), 14: ("FWD", "G", 65, 4.5), 15: ("FWD", "H", 45, 1.0),
}
# Alternatives no better than the substitutes
MARKET = {
    101: ("GK", "C", 45, 0.5), 102: ("GK", "H", 40, 0.5),
    103: ("DEF", "F", 45, 0.5), 104: ("DEF", "G", 40, 0.5), 105: ("DEF", "H", 45, 0.5),
    106: ("MID", "A", 45, 0.5), 107: ("MID", "B", 45, 0.5), 108: ("MID", "H", 45, 0.5),
    109: ("FWD", "A", 45, 0.5), 110: ("FWD", "B", 45, 0.5), 111: ("FWD", "C", 45, 0.5),
}


def projections(players, gws=GWS, fixtures=None, status=None, availability=None):
    """projections.csv rows. A player's points are one number (every gameweek) or one per gameweek;
    `fixtures`/`status`/`availability` override {element: per-gameweek list / value}."""
    rows = []
    for element, (pos, club, price, pts) in players.items():
        per_gw = pts if isinstance(pts, (list, tuple)) else [pts] * len(gws)
        for t, gw in enumerate(gws):
            n_fix = (fixtures or {}).get(element, [1] * len(gws))[t]
            rows.append({
                "element": element, "name": f"Player {element}", "web_name": f"P{element}", "position": pos,
                "team": club, "now_cost": price, "gameweek": gw, "fixtures": n_fix,
                "opponents": " & ".join(["X"] * n_fix) if n_fix else "No fixture",
                "predicted_points": float(per_gw[t]),
                "availability": (availability or {}).get(element, [1.0] * len(gws))[t],
                "status": (status or {}).get(element, "a"),
            })
    return pd.DataFrame(rows)


def current(squad=SQUAD, bank=0, free_transfers=1, prices=None):
    """`prices`: {element: (purchase_price, now_cost)}; the selling price is FPL's."""
    out = []
    for element, (pos, club, price, _) in squad.items():
        purchase, now = (prices or {}).get(element, (price, price))
        out.append({"element": element, "name": f"Player {element}", "web_name": f"P{element}", "position": pos,
                    "team": club, "purchase_price": purchase, "selling_price": planner.selling_price(purchase, now),
                    "now_cost": now})
    return {"squad": out, "bank": bank, "free_transfers": free_transfers}


def plan(players, cur=None, chips=(), gameweek=GWS[0], **kwargs):
    proj = players if isinstance(players, pd.DataFrame) else projections(players)
    return planner.plan(cur or current(), proj, None, gameweek, chips, now=NOW, season="2026-27", **kwargs)


def moves(entry):
    return [(t["out"]["element"], t["in"]["element"]) for t in entry["transfers"]]


def check_entry(entry):
    """The entry is a valid FPL team: 15 players, a valid XI, one captain and vice, bench 1..4."""
    squad = entry["squad"]
    assert len(squad) == 15 and len({p["element"] for p in squad}) == 15
    for pos, k in opt.SQUAD_SIZE.items():
        assert sum(p["position"] == pos for p in squad) == k
    xi = [p for p in squad if p["role"] == "starter"]
    assert len(xi) == 11
    for pos in opt.POSITIONS:
        assert opt.XI_MIN[pos] <= sum(p["position"] == pos for p in xi) <= opt.XI_MAX[pos]
    bench = sorted((p for p in squad if p["role"] == "bench"), key=lambda p: p["bench_order"])
    assert [p["bench_order"] for p in bench] == [1, 2, 3, 4] and bench[0]["position"] == "GK"
    assert sum(p["captain"] for p in xi) == 1 and sum(p["vice_captain"] for p in xi) == 1
    assert not any(p["captain"] or p["vice_captain"] for p in bench)
    clubs = pd.Series([p["team"] for p in squad]).value_counts()
    assert clubs.max() <= 3
    assert entry["status"] == "planned" and entry["result"] is None
    assert entry["transfers_made"] == len(entry["transfers"])
    assert entry["hit_cost"] == 4 * entry["hits"]
    assert entry["bank_after"] >= 0


# ------------------------------------------------------------------ helpers

def test_selling_price_keeps_half_the_rise():
    assert planner.selling_price(80, 90) == 85
    assert planner.selling_price(80, 83) == 81       # half of 0.3m, rounded down
    assert planner.selling_price(80, 75) == 75       # a fall is passed on in full
    assert planner.selling_price(80, 80) == 80


def test_chip_windows_accepts_the_usual_shapes():
    assert planner.chip_windows([{"chip": "3xc", "window": [1, 19]}, {"chip": "wildcard", "window": [20, 38]},
                                 {"chip": "bboost", "window": [1, 19], "available": False}, "freehit"], 12) \
        == {"3xc": 19, "freehit": None}
    assert planner.chip_windows({"wildcard": 19, "bboost": [1, 19], "nonsense": 19}, 19) == {"wildcard": 19, "bboost": 19}
    assert planner.chip_windows([("3xc", 19)], 20) == {}
    assert planner.chip_windows(None, 10) == {}


# ------------------------------------------------------------------ transfers

def test_no_transfer_when_nothing_beats_the_squad():
    entry = plan({**SQUAD, **MARKET}, chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["transfers"] == [] and entry["chip"] is None and entry["hits"] == 0
    assert entry["bank_before"] == entry["bank_after"] == 0
    assert entry["free_transfers"] == 1
    assert [h["free_transfers"] for h in entry["horizon"]] == [1, 2, 3, 4]      # banked, one a week
    assert entry["reason"].startswith("No transfers") and "2 free transfers next gameweek" in entry["reason"]
    # 4-4-2 of the 3+ point players, the 6-point midfielder captain
    xi = {p["element"] for p in entry["squad"] if p["role"] == "starter"}
    assert xi == {1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14}
    captain = next(p for p in entry["squad"] if p["captain"])
    assert captain["element"] == 8
    assert entry["projected_points"] == pytest.approx(sum(SQUAD[e][3] for e in xi) + 6.0)
    assert entry["season"] == "2026-27" and entry["gameweek"] == 10 and entry["planned_at"] == "2026-11-02T12:00:00Z"


def test_a_solve_that_stops_at_its_time_limit_is_reported(monkeypatch):
    assert plan({**SQUAD, **MARKET}, chips=ALL_CHIPS)["optimal"] is True
    solve = planner.solve_plan

    def capped(pool, bank, free_transfers, rules, chip, time_limit, max_hits=planner.MAX_HITS_PER_GW):
        sol = solve(pool, bank, free_transfers, rules, chip, time_limit, max_hits)
        return dataclasses.replace(sol, optimal=False) if chip is None else sol

    monkeypatch.setattr(planner, "solve_plan", capped)
    entry = plan({**SQUAD, **MARKET}, chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["optimal"] is False


def test_a_tiny_gain_keeps_the_free_transfer():
    # +0.3 a gameweek on the bench isn't worth giving up a banked free transfer
    entry = plan({**SQUAD, **MARKET, 120: ("MID", "A", 45, 1.3)})
    assert entry["transfers"] == []


def test_single_upgrade_with_one_free_transfer():
    entry = plan({**SQUAD, **MARKET, 120: ("MID", "A", 45, 4.0)})
    check_entry(entry)
    assert moves(entry) == [(12, 120)]
    assert entry["hits"] == 0 and entry["hit_cost"] == 0
    assert entry["transfers"][0]["out"]["selling_price"] == 45 and entry["transfers"][0]["in"]["price"] == 45
    assert entry["bank_after"] == 0
    bought = next(p for p in entry["squad"] if p["element"] == 120)
    assert bought["purchase_price"] == bought["selling_price"] == bought["now_cost"] == 45
    assert entry["horizon"][1]["free_transfers"] == 1
    assert entry["reason"].startswith("P12 out for P120 (+12.0) projected over the next 4 gameweeks, no hit. Captain")


def test_banks_the_free_transfer_for_two_moves_next_week():
    # Two starters fall away after this gameweek; their replacements blank now and shine after
    players = {**SQUAD, **MARKET, 130: ("MID", "H", 70, [0.0, 7.0, 7.0, 7.0]), 131: ("FWD", "A", 75, [0.0, 7.0, 7.0, 7.0])}
    players[9] = ("MID", "D", 70, [5.0, 2.0, 2.0, 2.0])
    players[13] = ("FWD", "F", 75, [5.0, 2.0, 2.0, 2.0])
    entry = plan(projections(players, fixtures={130: [0, 1, 1, 1], 131: [0, 1, 1, 1]}))
    check_entry(entry)
    assert entry["transfers"] == []
    assert entry["horizon"][1] == {"gameweek": 11, "projected": entry["horizon"][1]["projected"],
                                   "transfers": 2, "hits": 0, "free_transfers": 2}
    assert "No transfers" in entry["reason"] and "2 free transfers next gameweek" in entry["reason"]


def test_a_hit_only_when_it_pays():
    # A big upgrade takes the free transfer; a second one worth +0.5 a gameweek isn't worth -4
    small = plan({**SQUAD, **MARKET, 120: ("MID", "A", 45, 9.0), 140: ("FWD", "B", 45, 1.5)})
    check_entry(small)
    assert moves(small) == [(12, 120)] and small["hits"] == 0
    # Two big upgrades: the second pays for its hit this gameweek
    big = plan({**SQUAD, **MARKET, 120: ("MID", "A", 45, 9.0), 141: ("FWD", "B", 45, 9.0)})
    check_entry(big)
    assert sorted(moves(big)) == [(12, 120), (15, 141)]
    assert big["hits"] == 1 and big["hit_cost"] == 4
    assert "1 hit taken (-4)" in big["reason"]
    xi = [p for p in big["squad"] if p["role"] == "starter"]
    captain = next(p for p in xi if p["captain"])
    assert big["projected_points"] == pytest.approx(sum(p["predicted_points"] for p in xi)
                                                    + captain["predicted_points"] - 4)


def test_max_hits_zero_keeps_to_the_free_transfers():
    # The same two big upgrades as above, but no hits allowed (My team's "free transfers only"):
    # the better one takes the free transfer and nothing is paid for
    big = {**SQUAD, **MARKET, 120: ("MID", "A", 45, 9.0), 141: ("FWD", "B", 45, 9.0)}
    entry = plan(big, max_hits=0)
    check_entry(entry)
    assert len(entry["transfers"]) == 1 and entry["hits"] == 0 and entry["hit_cost"] == 0
    assert all(row["hits"] == 0 for row in entry["horizon"])


def test_margins_hold_back_transfers_and_hits(monkeypatch):
    upgrade = {**SQUAD, **MARKET, 120: ("MID", "A", 45, 4.0)}
    monkeypatch.setattr(planner, "TRANSFER_MARGIN", 50.0)       # far more than the +12 projected
    assert plan(upgrade)["transfers"] == []
    monkeypatch.setattr(planner, "TRANSFER_MARGIN", 0.0)
    monkeypatch.setattr(planner, "HIT_MARGIN", 50.0)
    big = plan({**SQUAD, **MARKET, 120: ("MID", "A", 45, 9.0), 141: ("FWD", "B", 45, 9.0)})
    check_entry(big)
    assert len(big["transfers"]) == 1 and big["hits"] == 0 and big["hit_cost"] == 0


def test_budget_uses_selling_prices():
    # Bought at 8.0m, now 9.0m: sells for 8.5m, so a 8.7m midfielder is out of reach with no money
    # in the bank (the price now would afford him), and in reach with 0.2m
    squad = {**SQUAD, 8: ("MID", "C", 90, 6.0)}
    players = {**squad, 150: ("MID", "A", 87, 10.0)}
    prices = {8: (80, 90)}
    broke = plan(players, current(squad, bank=0, prices=prices))
    check_entry(broke)
    assert broke["transfers"] == []
    owned = next(p for p in broke["squad"] if p["element"] == 8)
    assert (owned["purchase_price"], owned["selling_price"], owned["now_cost"]) == (80, 85, 90)

    rich = plan(players, current(squad, bank=2, prices=prices))
    check_entry(rich)
    assert moves(rich) == [(8, 150)]
    assert rich["transfers"][0]["out"]["selling_price"] == 85 and rich["transfers"][0]["in"]["price"] == 87
    assert rich["bank_before"] == 2 and rich["bank_after"] == 0

    # Without a selling price, FPL's rule fills it in
    cur = current(squad, bank=0, prices=prices)
    for p in cur["squad"]:
        del p["selling_price"]
    assert plan(players, cur)["transfers"] == []


def test_a_club_over_the_limit_after_a_move_gets_no_new_players():
    moved = {**SQUAD, 5: ("DEF", "A", 45, 3.5), 8: ("MID", "A", 80, 6.0)}   # A: 1, 3, 5 and 8
    star = {120: ("MID", "A", 45, 9.0)}                                       # cheap, and the best MID
    entry = plan({**moved, **MARKET, **star}, cur=current(moved))
    squad = {p["element"]: p for p in entry["squad"]}
    assert 120 not in squad
    assert all(e in moved for e, p in squad.items() if p["team"] == "A")
    other = plan({**SQUAD, **MARKET, 120: ("MID", "I", 45, 9.0)})            # elsewhere he's bought
    assert 120 in {p["element"] for p in other["squad"]}


def test_three_players_per_club():
    # Club A already has three players; its star midfielder can only replace the A midfielder,
    # not the weaker G one
    squad = dict(SQUAD)
    squad[11] = ("MID", "A", 60, 4.0)
    entry = plan({**squad, 160: ("MID", "A", 60, 9.0)}, current(squad, bank=20))
    check_entry(entry)
    assert moves(entry) == [(11, 160)]
    assert sum(p["team"] == "A" for p in entry["squad"]) == 3


# ------------------------------------------------------------------ chips

def test_no_chip_on_an_ordinary_week():
    entry = plan({**SQUAD, **MARKET}, chips=ALL_CHIPS)
    assert entry["chip"] is None


def test_triple_captain_on_a_big_double():
    players = {**SQUAD, **MARKET}
    players[8] = ("MID", "C", 80, [14.0, 6.0, 6.0, 6.0])
    entry = plan(projections(players, fixtures={8: [2, 1, 1, 1]}), chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["chip"] == "3xc" and entry["transfers"] == []
    captain = next(p for p in entry["squad"] if p["captain"])
    assert captain["element"] == 8
    xi = [p for p in entry["squad"] if p["role"] == "starter"]
    assert entry["projected_points"] == pytest.approx(sum(p["predicted_points"] for p in xi) + 2 * 14.0)
    assert "Triple captain: P8 projects 14.0 in a double gameweek" in entry["reason"]


def test_triple_captain_waits_for_a_better_week_in_the_horizon():
    players = {**SQUAD, **MARKET}
    players[8] = ("MID", "C", 80, [10.0, 6.0, 15.0, 6.0])
    entry = plan(projections(players, fixtures={8: [1, 1, 2, 1]}), chips=ALL_CHIPS)
    assert entry["chip"] is None


def test_bench_boost_on_a_strong_bench():
    players = {**SQUAD, **MARKET}
    for e in (2, 7, 12, 15):
        pos, club, price, _ = players[e]
        players[e] = (pos, club, price, [3.5, 1.0, 1.0, 1.0])
    players[6] = ("DEF", "D", 45, 4.0)
    entry = plan(players, chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["chip"] == "bboost"
    bench = [p for p in entry["squad"] if p["role"] == "bench"]
    assert sum(p["predicted_points"] for p in bench) == pytest.approx(14.0)
    xi = [p for p in entry["squad"] if p["role"] == "starter"]
    assert entry["projected_points"] == pytest.approx(sum(p["predicted_points"] for p in entry["squad"]) + 6.0)
    assert len(xi) == 11 and "Bench boost: the bench projects 14.0" in entry["reason"]


def test_wildcard_when_the_squad_is_bad():
    squad = {e: (pos, club, price, 1.0) for e, (pos, club, price, _) in SQUAD.items()}
    market, e = {}, 200
    for pos, k, price, pts in (("GK", 2, 45, 5.0), ("DEF", 5, 50, 5.0), ("MID", 5, 60, 6.0), ("FWD", 3, 60, 6.0)):
        for _ in range(k):
            e += 1
            market[e] = (pos, "ABCDEFGH"[e % 8], price, pts)
    entry = plan({**squad, **market}, current(squad), chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["chip"] == "wildcard"
    assert entry["transfers_made"] == 15 and entry["hits"] == 0 and entry["hit_cost"] == 0
    assert {p["element"] for p in entry["squad"]} == set(market)
    assert entry["bank_after"] == 0
    assert [h["free_transfers"] for h in entry["horizon"][:2]] == [1, 2]   # the banked transfer is kept
    assert entry["reason"].startswith("Wildcard: +")


def test_free_hit_for_a_blank_gameweek():
    # Six starters blank this gameweek only: a one-week squad, then the old one returns
    players = {**SQUAD, **MARKET}
    blanks = (3, 4, 8, 9, 13, 14)
    for e in blanks:
        pos, club, price, pts = players[e]
        players[e] = (pos, club, price, [0.0] + [pts] * 3)
    stand_ins = {301: ("DEF", "E", 45, [5.0, 0.5, 0.5, 0.5]), 302: ("DEF", "F", 45, [5.0, 0.5, 0.5, 0.5]),
                 303: ("MID", "G", 60, [6.0, 0.5, 0.5, 0.5]), 304: ("MID", "H", 60, [6.0, 0.5, 0.5, 0.5]),
                 305: ("FWD", "E", 60, [6.0, 0.5, 0.5, 0.5]), 306: ("FWD", "H", 60, [6.0, 0.5, 0.5, 0.5])}
    fixtures = {e: [0, 1, 1, 1] for e in blanks}
    entry = plan(projections({**players, **stand_ins}, fixtures=fixtures), chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["chip"] == "freehit" and entry["hits"] == 0
    assert set(stand_ins) <= {p["element"] for p in entry["squad"]}
    assert entry["horizon"][1]["free_transfers"] == 2 and entry["horizon"][1]["transfers"] == 0
    assert "squad returns next gameweek" in entry["reason"]


def test_chips_about_to_expire_are_played_if_they_help():
    gws = [19, 20, 21, 22]
    entry = plan(projections({**SQUAD, **MARKET}, gws=gws), gameweek=19, chips=ALL_CHIPS)
    check_entry(entry)
    assert entry["chip"] == "3xc"            # gains the most of the chips that help
    assert "before it expires (its window ends GW19)" in entry["reason"]


# ------------------------------------------------------------------ determinism, inputs

def test_plans_are_deterministic_and_ignore_row_order():
    players = {**SQUAD, **MARKET, 120: ("MID", "A", 45, 4.0), 121: ("MID", "B", 45, 4.0)}   # a tie
    proj = projections(players)
    first = plan(proj, chips=ALL_CHIPS)
    assert plan(proj, chips=ALL_CHIPS) == first
    shuffled = proj.sample(frac=1.0, random_state=3).reset_index(drop=True)
    assert plan(shuffled, chips=ALL_CHIPS) == first
    assert moves(first) == [(12, 120)]      # ties go to the lower element


def test_unavailable_squad_player_is_sold_and_explained():
    players = {**SQUAD, **MARKET, 120: ("MID", "A", 70, 5.0)}
    players[9] = ("MID", "D", 70, [0.0, 0.0, 5.0, 5.0])
    proj = projections(players, status={9: "i"}, availability={9: [0.0, 0.5, 1.0, 1.0]})
    entry = plan(proj)
    check_entry(entry)
    assert moves(entry) == [(9, 120)]
    assert entry["reason"].startswith("P9 (injured, 0%) out for P120")


def test_shorter_horizon_at_the_end_of_the_season():
    entry = plan(projections({**SQUAD, **MARKET}, gws=[37, 38]), gameweek=37)
    assert [h["gameweek"] for h in entry["horizon"]] == [37, 38]
    with pytest.raises(ValueError):
        plan(projections({**SQUAD, **MARKET}), gameweek=9)
    bad = current()
    bad["squad"] = bad["squad"][:14]
    with pytest.raises(ValueError):
        plan({**SQUAD, **MARKET}, bad)


def test_pool_keeps_the_squad_the_best_and_cheap_fillers():
    proj = projections({**SQUAD, **MARKET, **{400 + i: ("MID", "Z", 50 + i, 2.0 + i / 10) for i in range(40)}})
    pool = planner.build_pool(current(), proj, GWS, per_position=5, cheap_per_position=2)
    elements = {p.element for p in pool}
    assert set(SQUAD) <= elements
    assert {435, 436, 437, 438, 439} <= elements                     # best midfielders
    assert {106, 107} <= elements                                    # cheapest midfielders (4.5m)
    assert sum(p.position == "MID" for p in pool) == 5 + 5 + 2
