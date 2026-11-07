"""The Poisson team-strength model and the season simulation (services/team_strength.py). Offline."""
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from services import season_service as ss
from services import team_strength as ts

TEAMS = [f"Club {c}" for c in "ABCDEFGHIJKLMNOPQRST"]          # 20 clubs, alphabetical
KICKOFF = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)


def fixture(i, gw, home, away, hs=None, as_=None, status=None):
    return {"id": f"{i}-2026-27", "gameweek": gw, "kickoff_time": KICKOFF + timedelta(days=7 * (gw or 0), minutes=i),
            "status": status or ("finished" if hs is not None else "scheduled"),
            "home_team": home, "away_team": away, "home_score": hs, "away_score": as_,
            "home_difficulty": 3, "away_difficulty": 3}


def synthetic(teams, attack, defence, mu=0.1, home=0.25, seasons=4, seed=4):
    """Matches drawn from known ratings; every club plays every other home and away `seasons` times."""
    rng = np.random.default_rng(seed)
    out, n = [], 0
    for s in range(seasons):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if i == j:
                    continue
                lam_h = np.exp(mu + home + attack[i] - defence[j])
                lam_a = np.exp(mu + attack[j] - defence[i])
                n += 1
                out.append(fixture(n, 1 + n // 10, h, a, int(rng.poisson(lam_h)), int(rng.poisson(lam_a))))
    return out


def known_ratings(n, seed=3):
    rng = np.random.default_rng(seed)
    att, dfn = rng.normal(0, 0.25, n), rng.normal(0, 0.25, n)
    return att - att.mean(), dfn - dfn.mean()


# ------------------------------------------------------------------ fit

def test_fit_recovers_known_ratings():
    att, dfn = known_ratings(len(TEAMS))
    fixtures = synthetic(TEAMS, att, dfn, mu=0.1, home=0.25, seasons=4)
    model = ts.TeamStrength.fit(fixtures, half_life=None)
    got_a = np.array([model.attack[model.index[t]] for t in TEAMS])
    got_d = np.array([model.defence[model.index[t]] for t in TEAMS])
    got_a, got_d = got_a - got_a.mean(), got_d - got_d.mean()
    assert np.corrcoef(got_a, att)[0, 1] > 0.9
    assert np.corrcoef(got_d, dfn)[0, 1] > 0.9
    assert np.mean(np.abs(got_a - att)) < 0.08
    assert np.mean(np.abs(got_d - dfn)) < 0.08
    assert model.home_advantage == pytest.approx(0.25, abs=0.06)


def test_fit_is_deterministic():
    att, dfn = known_ratings(len(TEAMS))
    fixtures = synthetic(TEAMS, att, dfn, seasons=1)
    a, b = ts.TeamStrength.fit(fixtures), ts.TeamStrength.fit(fixtures)
    assert a.ratings() == b.ratings()
    assert a.mu == b.mu and a.home_advantage == b.home_advantage


def test_home_advantage_is_positive():
    att, dfn = known_ratings(len(TEAMS))
    model = ts.TeamStrength.fit(synthetic(TEAMS, att, dfn, home=0.25, seasons=1))
    assert model.home_advantage > 0
    # the same two clubs: the home side scores more at home than away
    home_xg, away_xg = model.expected_goals(TEAMS[0], TEAMS[1])
    swapped_home, swapped_away = model.expected_goals(TEAMS[1], TEAMS[0])
    assert home_xg > swapped_away and swapped_home > away_xg


def test_strong_club_is_rated_above_weak_club():
    att, dfn = known_ratings(len(TEAMS))
    att[0], dfn[0] = 1.0, 1.0        # a clearly strong club
    att[1], dfn[1] = -1.0, -1.0      # and a clearly weak one
    model = ts.TeamStrength.fit(synthetic(TEAMS, att, dfn, seasons=2))
    ratings = {r["team"]: r for r in model.ratings()}
    assert ratings[TEAMS[0]]["strength"] > ratings[TEAMS[1]]["strength"]
    assert model.ratings()[0]["team"] == TEAMS[0] and model.ratings()[-1]["team"] == TEAMS[1]
    p = model.match_probabilities(TEAMS[0], TEAMS[1])
    assert p["home_win"] > 0.7 > p["away_win"]
    assert ratings[TEAMS[0]]["goal_difference"] > 0 > ratings[TEAMS[1]]["goal_difference"]


def test_recent_matches_weigh_more():
    """A club that starts well then collapses is rated lower with a half-life than without."""
    teams = TEAMS[:6]
    fixtures, n = [], 0
    for gw in range(1, 31):
        for k in range(0, 6, 2):
            n += 1
            home, away = teams[k], teams[k + 1]
            # Club A: 3-0 winner for 15 weeks, then loses 0-3
            hs, as_ = (3, 0) if gw <= 15 else (0, 3)
            if k == 0:
                fixtures.append(fixture(n, gw, home, away, hs, as_))
            else:
                fixtures.append(fixture(n, gw, home, away, 1, 1))
    flat = ts.TeamStrength.fit(fixtures, half_life=None)
    recent = ts.TeamStrength.fit(fixtures, half_life=5)
    strength = lambda m: {r["team"]: r["strength"] for r in m.ratings()}[teams[0]]
    assert strength(recent) < strength(flat)


def test_prior_pulls_ratings_and_promoted_clubs_start_weaker():
    # last season: Club A great, Club T, S and R the worst three (they go down); this season has a new club
    prev_teams = TEAMS
    att, dfn = known_ratings(len(prev_teams))
    att[0], dfn[0] = 0.5, 0.5
    for i in (17, 18, 19):
        att[i], dfn[i] = -0.5, -0.5
    previous = synthetic(prev_teams, att, dfn, seasons=1, seed=11)
    now_teams = TEAMS[:17] + ["New Club"] + TEAMS[18:19]
    current = [fixture(1, 1, now_teams[0], now_teams[1], 1, 1)] + [
        fixture(i + 2, 2, h, a) for i, (h, a) in enumerate(zip(now_teams[2:10], now_teams[10:18]))]
    # no real data to speak of: the prior decides
    model = ts.TeamStrength.fit(current, previous)
    by = {r["team"]: r for r in model.ratings()}
    assert "New Club" in model.promoted
    assert by["New Club"]["promoted"] and by["New Club"]["strength"] < 0
    assert by[TEAMS[0]]["strength"] > 0
    # without a previous season everyone starts level
    assert all(abs(r["strength"]) < 0.2 for r in ts.TeamStrength.fit(current).ratings())


def test_no_matches_gives_prior_only():
    fixtures = [fixture(i + 1, 1, h, a) for i, (h, a) in enumerate(zip(TEAMS[:10], TEAMS[10:]))]
    model = ts.TeamStrength.fit(fixtures)
    assert model.matches == 0
    assert all(r["strength"] == 0 for r in model.ratings())
    assert model.home_advantage > 0


# ------------------------------------------------------------------ one match

def test_match_probabilities_sum_to_one():
    att, dfn = known_ratings(len(TEAMS))
    model = ts.TeamStrength.fit(synthetic(TEAMS, att, dfn, seasons=1))
    for home, away in [(TEAMS[0], TEAMS[1]), (TEAMS[5], TEAMS[3]), (TEAMS[19], TEAMS[0])]:
        p = model.match_probabilities(home, away)
        assert p["home_win"] + p["draw"] + p["away_win"] == pytest.approx(1.0, abs=1e-9)
        assert all(0 < p[k] < 1 for k in ("home_win", "draw", "away_win", "clean_sheet_home", "clean_sheet_away"))
        assert len(p["most_likely_scores"]) == ts.TOP_SCORES
        probs = [s["probability"] for s in p["most_likely_scores"]]
        assert probs == sorted(probs, reverse=True) and sum(probs) < 1
        assert p["expected_goals_home"] > 0 and p["expected_goals_away"] > 0


def test_clean_sheet_chance_follows_the_other_sides_goals():
    model = ts.TeamStrength.fit([fixture(1, 1, TEAMS[0], TEAMS[1])])
    p = model.match_probabilities(TEAMS[0], TEAMS[1])
    assert p["clean_sheet_home"] == pytest.approx(np.exp(-p["expected_goals_away"]))
    assert p["clean_sheet_away"] == pytest.approx(np.exp(-p["expected_goals_home"]))


def test_unknown_club_is_a_value_error():
    model = ts.TeamStrength.fit([fixture(1, 1, TEAMS[0], TEAMS[1])])
    with pytest.raises(ValueError):
        model.match_probabilities(TEAMS[0], "Nobody FC")


# ------------------------------------------------------------------ season simulation

def full_season(teams, seed=5):
    """A finished double round-robin with random scores."""
    rng = np.random.default_rng(seed)
    out, n = [], 0
    for i, h in enumerate(teams):
        for j, a in enumerate(teams):
            if i != j:
                n += 1
                out.append(fixture(n, 1 + (n - 1) // (len(teams) // 2), h, a, int(rng.poisson(1.5)), int(rng.poisson(1.2))))
    return out


def test_finished_season_simulates_to_the_actual_table():
    fixtures = full_season(TEAMS)
    model = ts.TeamStrength.fit(fixtures)
    rows = model.simulate_season(200, seed=1)
    actual = {r["team"]: r for r in ss.compute_table(fixtures)}
    assert len(rows) == 20
    for r in rows:
        want = actual[r["team"]]
        assert r["expected_points"] == want["points"]
        assert r["expected_position"] == want["position"]
        assert r["current_position"] == want["position"]
        assert r["remaining"] == 0
        assert r["position_probabilities"][want["position"] - 1] == 1.0
        assert r["p_title"] == (1.0 if want["position"] == 1 else 0.0)
        assert r["p_top4"] == (1.0 if want["position"] <= 4 else 0.0)
        assert r["p_relegation"] == (1.0 if want["position"] > 17 else 0.0)
    assert [r["team"] for r in rows] == [r["team"] for r in ss.compute_table(fixtures)]


def test_half_played_season_rows_are_consistent():
    att, dfn = known_ratings(len(TEAMS))
    fixtures = synthetic(TEAMS, att, dfn, seasons=1)
    half = [dict(f) if i < len(fixtures) // 2 else dict(f, status="scheduled", home_score=None, away_score=None)
            for i, f in enumerate(fixtures)]
    model = ts.TeamStrength.fit(half)
    rows = model.simulate_season(500, seed=2)
    assert sum(r["p_title"] for r in rows) == pytest.approx(1.0)
    assert sum(r["p_top4"] for r in rows) == pytest.approx(4.0)
    assert sum(r["p_europe"] for r in rows) == pytest.approx(2.0)
    assert sum(r["p_top6"] for r in rows) == pytest.approx(6.0)
    assert sum(r["p_relegation"] for r in rows) == pytest.approx(3.0)
    assert sum(r["expected_position"] for r in rows) == pytest.approx(sum(range(1, 21)))
    for r in rows:
        assert len(r["position_probabilities"]) == 20
        assert sum(r["position_probabilities"]) == pytest.approx(1.0)
        assert r["expected_points"] >= r["points"]
        assert r["remaining"] > 0
    assert [r["predicted_position"] for r in rows] == list(range(1, 21))
    assert [r["expected_points"] for r in rows] == sorted((r["expected_points"] for r in rows), reverse=True)


def test_simulation_is_deterministic_with_a_seed_and_varies_without_one():
    att, dfn = known_ratings(len(TEAMS))
    fixtures = synthetic(TEAMS, att, dfn, seasons=1)
    fixtures = [f if i < 100 else dict(f, status="scheduled", home_score=None, away_score=None)
                for i, f in enumerate(fixtures)]
    model = ts.TeamStrength.fit(fixtures)
    a, b = model.simulate_season(300, seed=9), model.simulate_season(300, seed=9)
    assert a == b
    assert model.simulate_season(300, seed=10) != a


def test_strong_club_is_favourite_for_the_title():
    att, dfn = known_ratings(len(TEAMS))
    att[4], dfn[4] = 0.9, 0.9
    fixtures = synthetic(TEAMS, att, dfn, seasons=1)
    fixtures = [f if i < 150 else dict(f, status="scheduled", home_score=None, away_score=None)
                for i, f in enumerate(fixtures)]
    rows = ts.TeamStrength.fit(fixtures).simulate_season(1000, seed=4)
    assert rows[0]["team"] == TEAMS[4] and rows[0]["p_title"] > 0.5


def test_live_match_is_simulated_in_full():
    fixtures = full_season(TEAMS[:4])
    fixtures[0] = dict(fixtures[0], status="in_progress", home_score=1, away_score=0)
    model = ts.TeamStrength.fit(fixtures)
    rows = model.simulate_season(100, seed=1)
    assert sum(r["remaining"] for r in rows) == 2       # both sides of that one match


def test_ten_thousand_simulations_are_fast():
    att, dfn = known_ratings(len(TEAMS))
    fixtures = synthetic(TEAMS, att, dfn, seasons=1)
    fixtures = [f if i < 40 else dict(f, status="scheduled", home_score=None, away_score=None)
                for i, f in enumerate(fixtures)]
    model = ts.TeamStrength.fit(fixtures)
    start = time.perf_counter()
    model.simulate_season(10_000, seed=1)
    assert time.perf_counter() - start < 15      # a few seconds in practice; slack for slow CI
