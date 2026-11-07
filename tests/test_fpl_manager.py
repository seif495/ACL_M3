"""Tests for services.fpl_manager, the ML manager's FPL game engine: prices, free
transfers and hits, chips, the free hit's revert, scoring, validation, replay, the GW9 start
from a saved pick, the files and the endpoint payload. All data is synthetic."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services import fpl_manager as fm
from services import season_service as ss

SEASON = "2026-27"
TEAMS = ["ARS", "AVL", "BOU", "BRE", "BHA", "CHE", "CRY", "EVE", "FUL", "LIV"]
POSITIONS = ["GK"] * 4 + ["DEF"] * 8 + ["MID"] * 8 + ["FWD"] * 5     # elements 1..25
STARTERS = [1, 5, 6, 7, 8, 13, 14, 15, 16, 21, 22]                    # 4-4-2
BENCH = [2, 9, 17, 23]                                                # GK, DEF, MID, FWD
CAPTAIN, VICE = 21, 13
GW9_DEADLINE = datetime(2026, 10, 30, 18, 30, tzinfo=timezone.utc)


def deadline(gw):
    return (GW9_DEADLINE + timedelta(days=7 * (gw - 9))).isoformat().replace("+00:00", "Z")


def before(gw):
    return GW9_DEADLINE + timedelta(days=7 * (gw - 9) - 1)


def after(gw):
    return GW9_DEADLINE + timedelta(days=7 * (gw - 9), hours=1)


def pool(prices=None, teams=None):
    """25 players, 60 (6.0m) each unless `prices` says otherwise; at most 3 per club."""
    prices, teams = prices or {}, teams or {}
    return {k: {"name": f"Player {k}", "web_name": f"P{k}", "position": pos,
                "team": teams.get(k, TEAMS[(k - 1) % len(TEAMS)]), "now_cost": prices.get(k, 60),
                "predicted_points": float(k % 7 + 1), "fixtures": [100 + (k - 1) % len(TEAMS)]}
            for k, pos in enumerate(POSITIONS, start=1)}


def start_entry(prices=None):
    return fm.build_entry(fm.new_state(SEASON), 9, deadline(9), STARTERS, BENCH, CAPTAIN, VICE, pool(prices),
                          now=before(9))


def lineup(state):
    squad = state["squad"]
    starters = [p["element"] for p in squad if p["role"] == "starter"]
    bench = [p["element"] for p in sorted((p for p in squad if p["role"] == "bench"), key=lambda p: p["bench_order"])]
    return starters, bench


def next_entry(state, gw, swaps=None, chip=None, prices=None, teams=None, captain=CAPTAIN, vice=VICE):
    """The state's lineup with `swaps` ({out: in}) made, as an entry for `gw`."""
    swaps = swaps or {}
    starters, bench = lineup(state)
    return fm.build_entry(state, gw, deadline(gw), [swaps.get(e, e) for e in starters],
                          [swaps.get(e, e) for e in bench], swaps.get(captain, captain), swaps.get(vice, vice),
                          pool(prices, teams), chip=chip, now=before(gw))


def lock(entry):
    return {**entry, "status": fm.LOCKED}


def after_start(prices=None):
    return fm.apply_entry(fm.new_state(SEASON), lock(start_entry(prices)))


class TestRules(unittest.TestCase):
    def test_defaults_are_the_2026_27_rules(self):
        r = fm.Rules()
        self.assertEqual((r.budget, r.squad_size, r.xi_size, r.max_per_club), (1000, 15, 11, 3))
        self.assertEqual((r.max_free_transfers, r.hit_cost, r.sell_on_fee, r.transfers_cap), (5, 4, 0.5, 20))
        self.assertEqual(r.squad_positions, {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3})
        windows = {(w.chip, w.half): (w.start, w.stop) for w in r.chip_windows}
        self.assertEqual(windows[("wildcard", 1)], (2, 19))
        self.assertEqual(windows[("freehit", 2)], (20, 38))
        self.assertEqual(windows[("bboost", 1)], (1, 19))
        self.assertEqual(windows[("3xc", 2)], (20, 38))
        self.assertEqual(len(r.chip_windows), 8)

    def test_from_bootstrap(self):
        bootstrap = {
            "game_settings": {"squad_squadsize": 15, "squad_squadplay": 11, "squad_team_limit": 3,
                              "squad_total_spend": 1000, "max_extra_free_transfers": 4,
                              "transfers_sell_on_fee": 0.5, "sys_vice_captain_enabled": True, "transfers_cap": 20},
            "element_types": [{"id": 1, "squad_select": 2, "squad_min_play": 1, "squad_max_play": 1},
                              {"id": 2, "squad_select": 5, "squad_min_play": 3, "squad_max_play": 5},
                              {"id": 5, "squad_select": 0, "squad_min_play": 0, "squad_max_play": 1}],
            "chips": [{"name": "wildcard", "start_event": 20, "stop_event": 38},
                      {"name": "wildcard", "start_event": 2, "stop_event": 19},
                      {"name": "bboost", "start_event": 1, "stop_event": 19},
                      {"name": "manager", "start_event": 23, "stop_event": 38}],
        }
        r = fm.Rules.from_bootstrap(bootstrap)
        self.assertEqual(r.max_free_transfers, 5)
        self.assertEqual(r.xi_min["DEF"], 3)
        self.assertEqual(sorted((w.chip, w.half, w.start, w.stop) for w in r.chip_windows),
                         [("bboost", 1, 1, 19), ("wildcard", 1, 2, 19), ("wildcard", 2, 20, 38)])
        self.assertEqual(fm.Rules.from_dict(r.to_dict()), r)
        # No chips listed: the default windows
        self.assertEqual(fm.Rules.from_bootstrap({}).chip_windows, fm.Rules().chip_windows)

    def test_rules_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fm.load_rules(tmp, SEASON), fm.Rules())
            rules = fm.Rules(budget=1005)
            fm.save_rules(tmp, SEASON, rules)
            self.assertEqual(fm.load_rules(tmp, SEASON), rules)


class TestSellingPrice(unittest.TestCase):
    def test_rises_keep_half_rounded_down(self):
        self.assertEqual(fm.selling_price(50, 50), 50)
        self.assertEqual(fm.selling_price(50, 51), 50)
        self.assertEqual(fm.selling_price(50, 52), 51)
        self.assertEqual(fm.selling_price(50, 53), 51)
        self.assertEqual(fm.selling_price(45, 50), 47)
        self.assertEqual(fm.selling_price(77, 80), 78)

    def test_falls_are_passed_on(self):
        self.assertEqual(fm.selling_price(50, 49), 49)
        self.assertEqual(fm.selling_price(50, 45), 45)

    def test_sale_uses_the_selling_price(self):
        state = after_start()                         # everyone bought at 60, bank 100
        self.assertEqual(state["bank"], 100)
        e = next_entry(state, 10, swaps={9: 10}, prices={9: 63, 10: 55})
        self.assertEqual(e["transfers"][0]["out"]["selling_price"], 61)
        self.assertEqual(e["transfers"][0]["in"]["price"], 55)
        self.assertEqual((e["bank_before"], e["bank_after"]), (100, 106))
        kept = next(p for p in e["squad"] if p["element"] == 5)
        self.assertEqual((kept["purchase_price"], kept["now_cost"], kept["selling_price"]), (60, 60, 60))
        self.assertEqual(fm.apply_entry(state, lock(e))["bank"], 106)
        current = fm.squad_at_prices(state, {9: 63, 5: 57})
        by = {p["element"]: p for p in current["squad"]}
        self.assertEqual((by[9]["selling_price"], by[5]["selling_price"], by[6]["selling_price"]), (61, 57, 60))
        self.assertEqual((current["bank"], current["free_transfers"]), (100, 1))


class TestFreeTransfers(unittest.TestCase):
    def test_next_free_transfers(self):
        self.assertEqual(fm.next_free_transfers(0, 0), 1)       # after the free first squad
        self.assertEqual(fm.next_free_transfers(1, 0), 2)
        self.assertEqual(fm.next_free_transfers(1, 1), 1)
        self.assertEqual(fm.next_free_transfers(4, 0), 5)
        self.assertEqual(fm.next_free_transfers(5, 0), 5)       # capped at 5
        self.assertEqual(fm.next_free_transfers(2, 2), 1)       # never below 1
        self.assertEqual(fm.next_free_transfers(3, 0, "wildcard"), 4)
        self.assertEqual(fm.next_free_transfers(5, 0, "freehit"), 5)

    def test_banking_over_gameweeks(self):
        state = after_start()
        seen = []
        for gw in range(10, 16):
            e = next_entry(state, gw)
            seen.append(e["free_transfers"])
            state = fm.apply_entry(state, lock(e))
        self.assertEqual(seen, [1, 2, 3, 4, 5, 5])

    def test_wildcard_keeps_banked_transfers(self):
        state = after_start()
        for gw in (10, 11):
            state = fm.apply_entry(state, lock(next_entry(state, gw)))
        self.assertEqual(state["free_transfers"], 3)
        e = next_entry(state, 12, swaps={5: 10, 6: 11, 13: 18, 14: 19, 21: 24}, chip="wildcard")
        self.assertEqual((e["transfers_made"], e["hits"], e["hit_cost"]), (5, 0, 0))
        self.assertEqual(fm.validate_entry(state, e), [])
        self.assertEqual(fm.apply_entry(state, lock(e))["free_transfers"], 4)

    def test_skipped_gameweeks_bank_a_transfer_each(self):
        state = after_start()                         # GW9 played, 1 free transfer for GW10
        e = next_entry(state, 12)
        self.assertTrue(any("doesn't follow" in m for m in fm.validate_entry(state, e)))
        self.assertEqual(fm.apply_entry(state, lock(e))["free_transfers"], 4)   # 1 -> 3 at GW12, +1


class TestHits(unittest.TestCase):
    def test_transfer_cost(self):
        self.assertEqual(fm.transfer_cost(3, 1), (1, 2, 8))
        self.assertEqual(fm.transfer_cost(2, 2), (2, 0, 0))
        self.assertEqual(fm.transfer_cost(0, 1), (0, 0, 0))
        self.assertEqual(fm.transfer_cost(6, 1, "wildcard"), (0, 0, 0))
        self.assertEqual(fm.transfer_cost(6, 1, "freehit"), (0, 0, 0))

    def test_hit_is_charged_and_resets_free_transfers(self):
        state = after_start()
        e = next_entry(state, 10, swaps={9: 10, 17: 18})
        self.assertEqual((e["free_transfers"], e["transfers_made"], e["hits"], e["hit_cost"]), (1, 2, 1, 4))
        self.assertEqual(fm.validate_entry(state, e), [])
        no_hit = next_entry(state, 10)                    # both swaps are on the bench: only the hit differs
        self.assertAlmostEqual(no_hit["projected_points"] - e["projected_points"], 4)
        self.assertEqual(fm.apply_entry(state, lock(e))["free_transfers"], 1)
        actual = {k: {"points": 2, "minutes": 90} for k in range(1, 26)}
        r = fm.score_entry(lock(e), actual, {"finished": True}, previous_total=50)
        self.assertEqual((r["gross"], r["hit_cost"], r["points"], r["total"]), (24, 4, 20, 70))


class TestChips(unittest.TestCase):
    def setUp(self):
        self.rules = fm.Rules()

    def test_windows(self):
        self.assertEqual(fm.chips_available(self.rules, [], 1), ["bboost", "3xc"])
        self.assertEqual(fm.chips_available(self.rules, [], 2), ["wildcard", "freehit", "bboost", "3xc"])
        self.assertEqual(fm.chips_available(self.rules, [], 38), ["wildcard", "freehit", "bboost", "3xc"])
        self.assertEqual(fm.chips_available(self.rules, [], 39), [])

    def test_one_of_each_per_half(self):
        used = [{"chip": "wildcard", "gameweek": 12, "half": 1}]
        self.assertNotIn("wildcard", fm.chips_available(self.rules, used, 15))
        self.assertIn("wildcard", fm.chips_available(self.rules, used, 20))
        # Without a recorded half the gameweek decides it
        self.assertNotIn("wildcard", fm.chips_available(self.rules, [{"chip": "wildcard", "gameweek": 12}], 19))

    def test_one_chip_per_gameweek(self):
        self.assertEqual(fm.chips_available(self.rules, [{"chip": "bboost", "gameweek": 10, "half": 1}], 10), [])

    def test_no_free_hit_twice_in_a_row(self):
        used = [{"chip": "freehit", "gameweek": 19, "half": 1}]
        self.assertNotIn("freehit", fm.chips_available(self.rules, used, 20))
        self.assertIn("freehit", fm.chips_available(self.rules, used, 21))

    def test_chip_deadlines(self):
        used = [{"chip": "wildcard", "gameweek": 12, "half": 1}]
        self.assertEqual(fm.chip_deadlines(self.rules, used, 15), {"freehit": 19, "bboost": 19, "3xc": 19})

    def test_first_half_chips_expire(self):
        rows = fm.chip_status(self.rules, [{"chip": "3xc", "gameweek": 12, "half": 1}], 20)
        first = {r["chip"]: r for r in rows if r["half"] == 1}
        second = {r["chip"]: r for r in rows if r["half"] == 2}
        self.assertEqual(len(rows), 8)
        self.assertFalse(any(r["available"] for r in first.values()))
        self.assertEqual(first["3xc"]["used_gameweek"], 12)
        self.assertTrue(all(r["available"] and r["active"] for r in second.values()))
        self.assertEqual(first["wildcard"]["window"], [2, 19])
        self.assertEqual(first["wildcard"]["label"], "Wildcard")

    def test_chip_misuse_is_caught(self):
        state = after_start()
        state = fm.apply_entry(state, lock(next_entry(state, 10, swaps={5: 10, 6: 11}, chip="wildcard")))
        again = next_entry(state, 11, swaps={7: 12}, chip="wildcard")
        self.assertIn("Wildcard can't be played in gameweek 11", fm.validate_entry(state, again))
        unknown = next_entry(state, 11, chip="turbo")
        self.assertIn("Unknown chip 'turbo'", fm.validate_entry(state, unknown))
        first = fm.build_entry(fm.new_state(SEASON), 9, deadline(9), STARTERS, BENCH, CAPTAIN, VICE, pool(),
                               chip="wildcard", now=before(9))
        self.assertIn("Wildcard can't be played with the first squad", fm.validate_entry(fm.new_state(SEASON), first))
        self.assertEqual(fm.apply_entry(state, lock(next_entry(state, 11)))["chips_used"],
                         [{"chip": "wildcard", "gameweek": 10, "half": 1}])


class TestFreeHit(unittest.TestCase):
    def test_squad_prices_and_bank_revert(self):
        state = after_start(prices={9: 55})               # bank 105
        before_fh = copy.deepcopy(state)
        fh = next_entry(state, 10, swaps={9: 10, 17: 18, 23: 24}, chip="freehit", prices={9: 70, 10: 50})
        self.assertEqual(fm.validate_entry(state, fh, prices={k: v["now_cost"] for k, v in
                                                              pool({9: 70, 10: 50}).items()}), [])
        self.assertEqual(fh["bank_after"], 105 + 62 + 60 + 60 - 50 - 60 - 60)      # 9 sold at 55 + (70-55)//2
        after_fh = fm.apply_entry(state, lock(fh))
        self.assertEqual(after_fh["squad"], before_fh["squad"])
        self.assertEqual(after_fh["bank"], before_fh["bank"])
        self.assertEqual(after_fh["free_transfers"], 2)       # the banked one kept, plus one
        self.assertEqual(after_fh["chips_used"], [{"chip": "freehit", "gameweek": 10, "half": 1}])
        # GW11 starts from the GW9 squad, still bought at the GW9 prices
        e11 = next_entry(after_fh, 11, prices={9: 70})
        self.assertEqual(fm.validate_entry(after_fh, e11), [])
        p9 = next(p for p in e11["squad"] if p["element"] == 9)
        self.assertEqual((p9["purchase_price"], p9["now_cost"], p9["selling_price"]), (55, 70, 62))
        self.assertEqual(sorted(p["element"] for p in e11["squad"]), sorted(STARTERS + BENCH))


def actual(points=2, minutes=90, **over):
    """Every player of the pool scores `points` in `minutes`; over: P<element>=(points, minutes)."""
    out = {}
    for k in range(1, 26):
        pts, mins = over.get(f"P{k}", (points, minutes))
        out[k] = {"points": pts, "minutes": mins}
    return out


FINISHED = {"finished": True, "data_checked": True, "average_entry_score": 45, "highest_score": 120}


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.entry = lock(start_entry())

    def score(self, entry=None, **over):
        return fm.score_entry(entry or self.entry, actual(**over), FINISHED)

    def player(self, result, element):
        return next(p for p in result["players"] if p["element"] == element)

    def test_captain_doubles(self):
        r = self.score(P21=(10, 90))
        self.assertEqual(r["gross"], 10 * 2 + 2 * 10)
        self.assertEqual((r["captain"], r["armband"], r["vice_captain_used"]), ("P21", "P21", False))
        self.assertEqual(self.player(r, 21)["multiplier"], 2)
        self.assertEqual((r["gw_average"], r["gw_highest"], r["bench_points"]), (45, 120, 8))

    def test_triple_captain_and_vice_fallback(self):
        tc = {**self.entry, "chip": "3xc"}
        self.assertEqual(self.score(tc, P21=(10, 90))["gross"], 10 * 3 + 2 * 10)
        r = self.score(tc, P21=(0, 0), P13=(6, 90))         # captain didn't play: the vice takes x3
        self.assertEqual((r["armband"], r["vice_captain_used"]), ("P13", True))
        self.assertEqual(self.player(r, 13)["multiplier"], 3)
        self.assertEqual(self.player(r, 21)["multiplier"], 0)
        # FWD 21 out: the first outfield sub who keeps a valid formation is DEF 9
        self.assertEqual(r["auto_subs"], [["Player 21", "Player 9"]])
        self.assertEqual(r["gross"], 6 * 3 + 2 * 9 + 2)
        r = self.score(P21=(0, 0), P13=(0, 0))               # neither played: no armband
        self.assertIsNone(r["armband"])
        self.assertTrue(all(p["multiplier"] <= 1 for p in r["players"]))

    def test_auto_subs(self):
        r = self.score(P5=(0, 0), P1=(0, 0))
        self.assertEqual(r["auto_subs"], [["Player 1", "Player 2"], ["Player 5", "Player 9"]])
        self.assertTrue(self.player(r, 9)["counted"] and self.player(r, 9)["subbed_in"])
        self.assertTrue(self.player(r, 5)["subbed_out"] and not self.player(r, 5)["counted"])
        self.assertEqual(r["gross"], 2 * 2 + 2 * 10)
        self.assertEqual(r["bench_points"], 2 + 2)           # 17 and 23 (5 and 1 scored 0)

    def test_bench_boost_counts_all_fifteen(self):
        bb = {**self.entry, "chip": "bboost"}
        r = self.score(bb, P5=(0, 0))
        self.assertEqual(r["auto_subs"], [])
        self.assertTrue(all(p["counted"] for p in r["players"] if p["element"] != 5))
        self.assertEqual(r["gross"], 2 * 2 + 2 * 13)         # captain x2, 13 others, P5 0
        self.assertEqual(r["bench_points"], 0)

    def test_undecided_players_while_live(self):
        live = {"finished": False}
        r = fm.score_entry(self.entry, {}, live, finished_fixtures=[100])   # only club ARS's match is over
        p1, p5 = self.player(r, 1), self.player(r, 5)
        self.assertEqual((p1["points"], p1["played"]), (0, False))
        self.assertEqual((p5["points"], p5["played"]), (None, None))
        self.assertEqual(r["auto_subs"], [])                 # the bench keeper (club AVL) is undecided
        self.assertFalse(r["finished"])

    def test_ready_to_score(self):
        self.assertTrue(fm.ready_to_score(FINISHED))
        self.assertFalse(fm.ready_to_score({"finished": True, "data_checked": False}))

    def test_score_from_the_live_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LIVE_DATA_DIR": tmp}):
            ss.clear_cache()
            with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"season": SEASON, "files": {"season": "FPL_2026_2027.csv"}, "finished_fixtures": [],
                           "events": [{"id": 9, **FINISHED}]}, f)
            with open(os.path.join(tmp, "FPL_2026_2027.csv"), "w", encoding="utf-8") as f:
                f.write("element,GW,minutes,total_points\n")
                for k in range(1, 26):
                    f.write(f"{k},9,90,2\n")
                f.write("21,9,90,5\n")                        # a double gameweek: summed
            r = fm.score_live(self.entry, SEASON, previous_total=10)
            ss.clear_cache()
        self.assertEqual((r["gross"], r["total"], r["gw_average"]), (14 + 2 * 10, 10 + 34, 45))


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.state = after_start()
        self.prices = {k: v["now_cost"] for k, v in pool().items()}

    def test_valid_entries(self):
        self.assertEqual(fm.validate_entry(fm.new_state(SEASON), start_entry(), prices=self.prices), [])
        self.assertEqual(fm.validate_entry(self.state, next_entry(self.state, 10, swaps={9: 10}),
                                           prices=self.prices), [])

    def test_budget(self):
        e = next_entry(self.state, 10, swaps={21: 24}, prices={24: 200})
        self.assertIn("Over budget by 4.0m", fm.validate_entry(self.state, e))

    def test_three_per_club(self):
        e = next_entry(self.state, 10, swaps={9: 11, 23: 24}, teams={24: "ARS"})   # ARS: 1, 21, 11, 24
        self.assertIn("4 players from ARS (at most 3)", fm.validate_entry(self.state, e))

    def test_a_club_over_the_limit_after_a_move_may_stay_but_not_grow(self):
        moved = {5: "ARS", 6: "ARS"}                         # ARS: 1 and 21, then 5 and 6 move there
        e = next_entry(self.state, 10, teams=moved)
        self.assertEqual(fm.validate_entry(self.state, e), [])
        e = next_entry(self.state, 10, swaps={9: 10}, teams=moved)     # other transfers are fine
        self.assertEqual(fm.validate_entry(self.state, e), [])
        e = next_entry(self.state, 10, swaps={9: 11}, teams=moved)     # 11 is another ARS defender
        self.assertIn("5 players from ARS (at most 4 after a player moved there)", fm.validate_entry(self.state, e))
        e = next_entry(self.state, 10, swaps={5: 10, 9: 11}, teams=moved)   # sell one, buy one: FPL blocks the buy
        self.assertIn("4 players from ARS (at most 3)", fm.validate_entry(self.state, e))

    def test_positions(self):
        e = next_entry(self.state, 10, swaps={9: 18})        # a defender out, a midfielder in
        errors = fm.validate_entry(self.state, e)
        self.assertIn("Squad has 4 DEF, not 5", errors)
        self.assertIn("Squad has 6 MID, not 5", errors)
        self.assertTrue(any("changes position" in m for m in errors))
        xi = start_entry()
        for p in xi["squad"]:                                 # both goalkeepers start, a midfielder on the bench
            if p["element"] == 16:
                p["role"], p["bench_order"] = "bench", 1
            if p["element"] == 2:
                p["role"], p["bench_order"] = "starter", None
        self.assertIn("XI has 2 GK", fm.validate_entry(fm.new_state(SEASON), xi))

    def test_duplicate_players(self):
        e = start_entry()
        e["squad"][14] = {**e["squad"][14], "element": e["squad"][13]["element"]}
        self.assertTrue(any(m.startswith("Duplicate players") for m in fm.validate_entry(fm.new_state(SEASON), e)))
        e = next_entry(self.state, 10, swaps={9: 10})
        e["transfers"].append(copy.deepcopy(e["transfers"][0]))
        self.assertIn("A player is transferred twice", fm.validate_entry(self.state, e))

    def test_lineup_rules(self):
        e = start_entry()
        for p in e["squad"]:
            p["captain"] = p["element"] == 23                 # a bench player
        self.assertIn("Need exactly one captain, in the XI", fm.validate_entry(fm.new_state(SEASON), e))
        e = start_entry()
        for p in e["squad"]:
            if p["role"] == "bench":
                p["bench_order"] = {2: 2, 9: 1, 17: 3, 23: 4}[p["element"]]
        self.assertIn("The substitute goalkeeper must be first on the bench",
                      fm.validate_entry(fm.new_state(SEASON), e))

    def test_bookkeeping(self):
        e = next_entry(self.state, 10, swaps={9: 10})
        e.update(free_transfers=2, hits=1, bank_after=0)
        errors = fm.validate_entry(self.state, e)
        self.assertIn("free_transfers 2 should be 1", errors)
        self.assertIn("hits/hit_cost 1/0 should be 0/0", errors)
        self.assertIn("bank_after 0 should be 100", errors)
        e = next_entry(self.state, 10, swaps={9: 10})
        e["squad"] = [p for p in e["squad"] if p["element"] != 10] + [
            {**next(p for p in e["squad"] if p["element"] == 10), "element": 11}]
        self.assertIn("The squad doesn't match the previous squad and the transfers", fm.validate_entry(self.state, e))

    def test_prices_must_be_todays(self):
        e = next_entry(self.state, 10, swaps={9: 10})
        errors = fm.validate_entry(self.state, e, prices={**self.prices, 10: 65, 9: 64})
        self.assertIn("Player 10: costs 65, not 60", errors)
        self.assertIn("Player 9: sells for 62, not 60", errors)


def scored(entry, points):
    r = fm.score_entry(entry, actual(points), FINISHED)
    return {**entry, "status": fm.SCORED, "result": r}


def season_entries():
    """GW9 scored, GW10 a transfer and a hit (scored), GW11 free hit (scored), GW12 planned."""
    e9 = scored(lock(start_entry()), 2)
    s = fm.apply_entry(fm.new_state(SEASON), e9)
    e10 = scored(lock(next_entry(s, 10, swaps={9: 10, 17: 18})), 3)
    s = fm.apply_entry(s, e10)
    e11 = scored(lock(next_entry(s, 11, swaps={5: 11, 13: 19}, chip="freehit")), 1)
    s = fm.apply_entry(s, e11)
    e12 = next_entry(s, 12, swaps={6: 12})
    e11["result"]["league"] = {"id": 284, "name": "Gameweek 9", "rank": 1000, "size": 300000, "percentile": 99.7}
    return [e9, e10, e11, e12]


def playing_entries():
    """GW9 and GW10 scored, GW11 locked and being played, GW12 already planned."""
    e9, e10 = season_entries()[:2]
    s = fm.apply_entry(fm.apply_entry(fm.new_state(SEASON), e9), e10)
    e11 = lock(next_entry(s, 11, swaps={5: 11}))
    e12 = next_entry(fm.apply_entry(s, e11), 12, swaps={6: 12})
    return [e9, e10, e11, e12]


class TestReplay(unittest.TestCase):
    def test_history_and_totals(self):
        state = fm.replay(season_entries())
        self.assertEqual((state["gameweek"], state["next_gameweek"]), (11, 12))
        self.assertEqual([h["gameweek"] for h in state["history"]], [9, 10, 11])
        self.assertEqual([h["points"] for h in state["history"]], [24, 36 - 4, 12])
        self.assertEqual([h["total"] for h in state["history"]], [24, 56, 68])
        self.assertEqual(state["totals"]["points"], 68)
        self.assertEqual(state["totals"]["vs_average"], 68 - 3 * 45)
        self.assertEqual(state["totals"]["league"]["rank"], 1000)
        self.assertEqual(state["problems"], [])
        self.assertEqual(state["free_transfers"], 2)          # GW10's hit left 1; the free hit kept it, +1
        self.assertIn(10, {p["element"] for p in state["squad"]})        # GW10's buy, not the free hit's
        self.assertEqual(fm.replay(season_entries(), include_planned=True)["gameweek"], 12)

    def test_idempotent(self):
        entries = season_entries()
        snapshot = copy.deepcopy(entries)
        first = fm.replay(entries)
        self.assertEqual(fm.replay(entries), first)
        self.assertEqual(fm.replay(list(reversed(entries))), first)
        self.assertEqual(entries, snapshot)                    # inputs untouched
        with tempfile.TemporaryDirectory() as tmp:
            for e in entries:
                fm.save_entry(tmp, e, now=before(9))
            path = os.path.join(fm.season_dir(tmp, SEASON), fm.STATE_FILE)
            fm.rebuild_state(tmp, SEASON)
            data = open(path, "rb").read()
            fm.rebuild_state(tmp, SEASON)
            self.assertEqual(open(path, "rb").read(), data)
            self.assertEqual(json.loads(data), json.loads(json.dumps(first)))
            self.assertEqual([e["gameweek"] for e in fm.load_entries(tmp, SEASON)], [9, 10, 11, 12])

    def test_problems_are_reported(self):
        entries = season_entries()
        entries[1]["hit_cost"] = 0
        state = fm.replay(entries)
        self.assertEqual(state["problems"], [{"gameweek": 10, "errors": ["hits/hit_cost 1/0 should be 1/4"]}])


class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_changes_after_the_deadline(self):
        e = start_entry()
        fm.save_entry(self.tmp.name, e, now=before(9))
        with self.assertRaises(fm.EntryLockedError):          # a plan saved too late
            fm.save_entry(self.tmp.name, e, now=after(9))
        locked = fm.lock_entry(e, now=after(9))
        self.assertEqual(locked["status"], fm.LOCKED)
        self.assertEqual(fm.lock_entry(e, now=before(9))["status"], fm.PLANNED)
        fm.save_entry(self.tmp.name, locked, now=after(9))
        changed = copy.deepcopy(locked)
        changed["squad"][0]["captain"] = True
        with self.assertRaises(fm.EntryLockedError):
            fm.save_entry(self.tmp.name, changed, now=after(9))
        with self.assertRaises(fm.EntryLockedError):
            fm.save_entry(self.tmp.name, e, now=before(9))     # can't go back to planned
        fm.save_entry(self.tmp.name, scored(locked, 2), now=after(9))
        self.assertEqual(fm.load_entry(self.tmp.name, SEASON, 9)["status"], fm.SCORED)
        self.assertFalse(any(n.endswith(".tmp") for n in os.listdir(fm.season_dir(self.tmp.name, SEASON))))

    def test_rollover_plays_the_last_team_again(self):
        state = after_start()
        e = fm.rollover_entry(state, 10, deadline(10), prices={9: 62}, now=after(10))
        self.assertEqual(e["status"], fm.LOCKED)
        self.assertEqual((e["transfers"], e["chip"], e["free_transfers"]), ([], None, 1))
        self.assertEqual(lineup(fm.apply_entry(state, e)), lineup(state))
        self.assertEqual(fm.validate_entry(state, e), [])
        self.assertEqual(next(p for p in e["squad"] if p["element"] == 9)["now_cost"], 62)


def pick_file(deadline_text=None):
    """A saved model pick (model_picks/<season>/gw09.json): starters, then the bench with the
    goalkeeper second in the file."""
    costs = {1: 4.5, 2: 4.0, 5: 7.9, 6: 5.5, 7: 4.6, 8: 5.1, 9: 4.3, 13: 10.1, 14: 8.3, 15: 6.6, 16: 5.9,
             17: 5.4, 21: 14.0, 22: 7.7, 23: 5.6}             # 99.5m
    p = pool()
    order = STARTERS + [9, 2, 17, 23]
    return {"season": SEASON, "gameweek": 9, "deadline": deadline_text or deadline(9),
            "archived_at": "2026-10-30T06:00:00+00:00", "source": "pipeline", "model": "v4",
            "players": [{"element": k, "name": p[k]["name"], "position": p[k]["position"], "team": p[k]["team"],
                         "cost": costs[k], "predicted_points": p[k]["predicted_points"],
                         "role": "starter" if k in STARTERS else "bench", "captain": k == CAPTAIN,
                         "vice_captain": k == VICE, "fixtures": p[k]["fixtures"],
                         "opponents": [{"team": "X", "home": True}]} for k in order]}


class TestStart(unittest.TestCase):
    def test_from_pick(self):
        pick = pick_file()
        e = fm.start_entry_from_pick(pick, now=before(9), web_names={"Player 21": "Haaland"})
        self.assertEqual(e["status"], fm.PLANNED)
        self.assertEqual(fm.start_entry_from_pick(pick, now=after(9))["status"], fm.LOCKED)
        self.assertEqual((e["gameweek"], e["deadline"], e["model"], e["planned_at"]),
                         (9, deadline(9), "v4", "2026-10-30T06:00:00+00:00"))
        self.assertEqual((e["transfers"], e["chip"], e["free_transfers"], e["hits"]), ([], None, 0, 0))
        by = {p["element"]: p for p in e["squad"]}
        self.assertEqual((by[1]["purchase_price"], by[5]["purchase_price"], by[13]["purchase_price"]), (45, 79, 101))
        self.assertEqual(by[21]["web_name"], "Haaland")
        self.assertTrue(by[21]["captain"] and by[13]["vice_captain"])
        self.assertEqual({k: by[k]["bench_order"] for k in BENCH}, {2: 1, 9: 2, 17: 3, 23: 4})
        spent = sum(round(c["cost"] * 10) for c in pick["players"])
        self.assertEqual((e["bank_before"], e["bank_after"]), (1000, 1000 - spent))
        self.assertEqual(by[5]["fixtures"], [104])
        self.assertEqual(fm.validate_entry(fm.new_state(SEASON), e), [])
        self.assertAlmostEqual(e["projected_points"],
                               sum(p["predicted_points"] for p in pick["players"] if p["role"] == "starter")
                               + by[21]["predicted_points"])

    def test_ensure_start_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(fm.ensure_start_entry(tmp, SEASON, 9, now=before(9)))
            picks = os.path.join(tmp, "model_picks", SEASON)
            os.makedirs(picks)
            with open(os.path.join(picks, "gw09.json"), "w", encoding="utf-8") as f:
                json.dump(pick_file(), f)
            self.assertEqual(fm.ensure_start_entry(tmp, SEASON, 9, now=before(9))["status"], fm.PLANNED)
            locked = fm.ensure_start_entry(tmp, SEASON, 9, now=after(9))
            self.assertEqual(locked["status"], fm.LOCKED)
            changed = pick_file()
            changed["players"][0]["cost"] = 5.0
            with open(os.path.join(picks, "gw09.json"), "w", encoding="utf-8") as f:
                json.dump(changed, f)
            self.assertEqual(fm.ensure_start_entry(tmp, SEASON, 9, now=after(9)), locked)
            self.assertEqual(fm.load_entry(tmp, SEASON, 9), locked)


class TestView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"LIVE_DATA_DIR": self.tmp.name})
        self.env.start()
        ss.clear_cache()

    def tearDown(self):
        self.env.stop()
        ss.clear_cache()
        self.tmp.cleanup()

    def test_not_found_before_the_first_squad(self):
        with self.assertRaises(ss.NotFoundError):
            fm.get_manager_view(SEASON)

    def test_payload(self):
        for e in season_entries():
            fm.save_entry(self.tmp.name, e, now=before(9))
        with open(os.path.join(self.tmp.name, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"season": SEASON, "next_deadline": deadline(12),
                       "files": {"status": "player_status_2026_27.csv"}}, f)
        with open(os.path.join(self.tmp.name, "player_status_2026_27.csv"), "w", encoding="utf-8") as f:
            f.write("element,code,name,web_name\n")
            for k in range(1, 26):
                f.write(f"{k},{1000 + k},Player {k},W{k}\n")
        r = fm.get_manager_view(SEASON, photo_for=lambda name, team, code: f"photo:{code}", now=before(12))
        self.assertEqual((r["season"], r["start_gameweek"], r["model"]), (SEASON, 9, "v4"))
        self.assertEqual(r["rules"], {"budget": 1000, "max_free_transfers": 5, "hit_cost": 4})
        self.assertEqual((r["current"]["gameweek"], r["current"]["status"]), (12, fm.PLANNED))
        p = r["current"]["squad"][0]
        self.assertEqual(p["photo"], f"photo:{1000 + p['element']}")
        self.assertNotIn("_code", p)
        self.assertTrue(r["current"]["transfers"][0]["in"]["photo"].startswith("photo:"))
        self.assertEqual(r["current"]["team_value"], 900)
        self.assertEqual([h["gameweek"] for h in r["history"]], [9, 10, 11])
        self.assertEqual(r["totals"]["points"], 68)
        self.assertEqual(r["totals"]["league"]["name"], "Gameweek 9")
        self.assertEqual(r["next_deadline"], deadline(12))
        self.assertEqual(r["state"]["free_transfers"], 2)
        chips = {(c["chip"], c["half"]): c for c in r["chips"]}
        self.assertEqual(chips[("freehit", 1)]["used_gameweek"], 11)
        self.assertFalse(chips[("freehit", 1)]["available"])
        self.assertTrue(chips[("wildcard", 1)]["active"])
        # Past GW12's deadline the plan counts even before the pipeline locks it
        late = fm.get_manager_view(SEASON, now=after(12))
        self.assertEqual(late["current"]["status"], fm.LOCKED)
        self.assertIsNone(late["current"].get("result"))

        # While GW12 is played, FPL's live points give a provisional result; the entry stays locked
        squad = late["current"]["squad"]
        feed = {"actual": {int(squad[0]["element"]): {"points": 7, "minutes": 90}}, "finished": [], "started": [],
                "fetched_at": "2026-11-07T15:00:00+00:00"}
        live = fm.get_manager_view(SEASON, now=after(12), live=lambda gw: feed if gw == 12 else None)
        result = live["current"]["result"]
        self.assertEqual(live["current"]["status"], fm.LOCKED)
        self.assertEqual(result["live"], {"fetched_at": "2026-11-07T15:00:00+00:00"})
        self.assertEqual(result["total"], 68 + result["points"])
        scored = {p["element"]: p for p in result["players"]}
        self.assertEqual(scored[int(squad[0]["element"])]["points"], 7)
        # Before the deadline nobody asks FPL
        fm.get_manager_view(SEASON, now=before(12), live=lambda gw: self.fail("asked before the deadline"))

    def write_meta(self, events):
        with open(os.path.join(self.tmp.name, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"season": SEASON, "next_deadline": deadline(12), "events": events}, f)

    def test_the_gameweek_being_played_comes_first(self):
        for e in playing_entries():
            fm.save_entry(self.tmp.name, e, now=before(9))
        self.write_meta([{"id": 11, "finished": False}])
        now = after(11)
        r = fm.get_manager_view(SEASON, now=now)
        # GW11 is played while GW12 is already planned: the page opens on GW11
        self.assertEqual((r["default_gameweek"], r["current"]["gameweek"], r["current"]["status"]), (11, 11, fm.LOCKED))
        self.assertIsNone(r["current"]["result"])        # no live feed and no CSV rows yet: shown as locked
        self.assertEqual([(g["gameweek"], g["status"], g["live"]) for g in r["gameweeks"]],
                         [(9, fm.SCORED, False), (10, fm.SCORED, False), (11, fm.LOCKED, True), (12, fm.PLANNED, False)])
        self.assertEqual([g["points"] for g in r["gameweeks"]], [h["points"] for h in r["history"]] + [None, None])

        # FPL's live points give GW11 a provisional result on top of the scored total
        squad = r["current"]["squad"]
        first = int(squad[0]["element"])
        feed = {"actual": {first: {"points": 9, "minutes": 90}}, "finished": [], "started": [],
                "fetched_at": "2026-11-14T15:00:00+00:00"}
        live = fm.get_manager_view(SEASON, now=now, live=lambda gw: feed if gw == 11 else None)
        result = live["current"]["result"]
        self.assertEqual(live["current"]["status"], fm.LOCKED)
        self.assertTrue(result["provisional"])
        self.assertEqual(result["live"], {"fetched_at": "2026-11-14T15:00:00+00:00"})
        self.assertEqual(result["total"], r["totals"]["points"] + result["points"])
        self.assertEqual({p["element"]: p["points"] for p in result["players"]}[first], 9)

        # Without the feed, the season CSV's finished matches count
        with open(os.path.join(self.tmp.name, "FPL_2026_2027.csv"), "w", encoding="utf-8") as f:
            f.write(f"element,GW,minutes,total_points\n{first},11,90,6\n{first},10,90,2\n")
        ss.clear_cache()
        csv = fm.get_manager_view(SEASON, now=now)["current"]["result"]
        self.assertIsNone(csv["live"])
        self.assertEqual({p["element"]: p["points"] for p in csv["players"]}[first], 6)

        # The next plan and past gameweeks on request; FPL isn't asked for them
        never = lambda gw: self.fail(f"asked FPL for GW{gw}")
        plan = fm.get_manager_view(SEASON, now=now, gameweek=12, live=never)
        self.assertEqual((plan["current"]["gameweek"], plan["current"]["status"], plan["default_gameweek"]),
                         (12, fm.PLANNED, 11))
        past = fm.get_manager_view(SEASON, now=now, gameweek=10, live=never)
        self.assertEqual(past["current"]["result"]["points"], r["history"][1]["points"])
        with self.assertRaises(fm.NoEntryError):
            fm.get_manager_view(SEASON, now=now, gameweek=8)
        self.assertTrue(issubclass(fm.NoEntryError, ss.NotFoundError))     # the route's 404

        # Past GW12's deadline the latest gameweek is the one being played
        self.assertEqual(fm.get_manager_view(SEASON, now=after(12))["default_gameweek"], 12)


if __name__ == "__main__":
    unittest.main()
