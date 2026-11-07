"""
Team strength: a Poisson attack/defence model with a home advantage (Maher / Dixon-Coles
style, without the low-score correction), fitted to a season's finished matches.

Each side's goals are Poisson with a log-mean of

    home:  mu + home_advantage + attack[home] - defence[away]
    away:  mu                  + attack[away] - defence[home]

(`attack` higher = scores more, `defence` higher = concedes less.) The fit is a penalised
maximum likelihood, deterministic (convex, L-BFGS-B from a fixed start):

  * recent matches count more: weight 0.5 ** (age / half_life), age in gameweeks (about 20);
  * every rating is pulled toward a prior with a ridge penalty. The prior is the previous
    season's fitted ratings, carried over at `PRIOR_CARRY` (ratings regress to the mean). A
    club with no previous Premier League season (promoted) gets the average of the clubs
    that went down, so it starts weaker than average; mu and the home advantage are pulled
    toward last season's values. The prior is what a club is judged by after a few matches,
    and fades as results come in.

`simulate_season` plays the unplayed fixtures from the model many times, vectorised with
numpy, on top of the current table (same tie-break order as `season_service.compute_table`),
and gives each club's expected points and position, its chances of the title, a Champions
League place, Europe and relegation, and its distribution over the 20 positions.

Pure: plain lists and dicts in and out, no Neo4j and no network. Nothing here uses
bookmakers' odds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize

from services import season_service as ss

HALF_LIFE = 20.0            # matches (gameweeks) after which a result counts half as much
PRIOR_SD = 0.30             # how far a club's attack/defence may stray from its prior (log scale)
FIRST_SEASON_PRIOR_SD = 0.35  # the same when fitting last season itself (prior: average)
PRIOR_CARRY = 0.7           # share of last season's rating carried into this one
MU_SD = 0.30                # prior sd of the baseline log goal rate and of the home advantage
HOME_SD = 0.20
DEFAULT_MU = math.log(1.25)        # typical Premier League away goals a match
DEFAULT_HOME = math.log(1.55 / 1.25)  # and the home side's edge over it
RELEGATED = 3               # clubs that go down; their average is a promoted club's prior

DEFAULT_SIMULATIONS = 10_000
DEFAULT_SEED = 2026
MAX_GOALS = 12              # score grid for match probabilities: 0..11 goals a side
TOP_SCORES = 3

# Season-end zones, as drawn on the league table (LeagueTable.tsx): places 1-4 Champions League,
# 5-6 Europa / Conference League, the bottom three relegated.
CHAMPIONS_LEAGUE_PLACES = 4
EUROPE_PLACES = 6
RELEGATION_PLACES = 3


# ------------------------------------------------------------------ fitting

@dataclass
class Fit:
    mu: float
    home: float
    attack: np.ndarray
    defence: np.ndarray


def _weights(ages: np.ndarray, half_life: Optional[float]) -> np.ndarray:
    if not half_life:
        return np.ones_like(ages, dtype=float)
    return np.power(0.5, ages / float(half_life))


def fit_poisson(home_idx: np.ndarray, away_idx: np.ndarray, home_goals: np.ndarray,
                away_goals: np.ndarray, weights: np.ndarray, n_teams: int,
                prior_attack: Optional[np.ndarray] = None, prior_defence: Optional[np.ndarray] = None,
                prior_mu: float = DEFAULT_MU, prior_home: float = DEFAULT_HOME,
                prior_sd: float = PRIOR_SD) -> Fit:
    """Penalised maximum-likelihood fit of the attack/defence model (see the module docstring).

    The objective is convex, so the optimum is unique and L-BFGS-B finds it from the same start
    every time. With no matches the prior itself comes back."""
    a0 = np.zeros(n_teams) if prior_attack is None else np.asarray(prior_attack, dtype=float)
    d0 = np.zeros(n_teams) if prior_defence is None else np.asarray(prior_defence, dtype=float)
    hi, ai = np.asarray(home_idx, dtype=int), np.asarray(away_idx, dtype=int)
    hg, ag = np.asarray(home_goals, dtype=float), np.asarray(away_goals, dtype=float)
    w = np.asarray(weights, dtype=float)
    if len(hi) == 0:
        return Fit(prior_mu, prior_home, a0.copy(), d0.copy())

    ridge = 1.0 / (prior_sd ** 2)
    mu_ridge, home_ridge = 1.0 / (MU_SD ** 2), 1.0 / (HOME_SD ** 2)

    def unpack(x: np.ndarray):
        return x[0], x[1], x[2:2 + n_teams], x[2 + n_teams:]

    def objective(x: np.ndarray):
        mu, h, a, d = unpack(x)
        eta_h = mu + h + a[hi] - d[ai]
        eta_a = mu + a[ai] - d[hi]
        lam_h, lam_a = np.exp(eta_h), np.exp(eta_a)
        loglik = np.sum(w * (hg * eta_h - lam_h)) + np.sum(w * (ag * eta_a - lam_a))
        penalty = 0.5 * (ridge * (np.sum((a - a0) ** 2) + np.sum((d - d0) ** 2))
                         + mu_ridge * (mu - prior_mu) ** 2 + home_ridge * (h - prior_home) ** 2)
        r_h, r_a = w * (hg - lam_h), w * (ag - lam_a)
        g_mu = r_h.sum() + r_a.sum() - mu_ridge * (mu - prior_mu)
        g_h = r_h.sum() - home_ridge * (h - prior_home)
        g_a = (np.bincount(hi, r_h, n_teams) + np.bincount(ai, r_a, n_teams)) - ridge * (a - a0)
        g_d = -(np.bincount(ai, r_h, n_teams) + np.bincount(hi, r_a, n_teams)) - ridge * (d - d0)
        return -(loglik - penalty), -np.concatenate(([g_mu, g_h], g_a, g_d))

    x0 = np.concatenate(([prior_mu, prior_home], a0, d0))
    result = minimize(objective, x0, jac=True, method="L-BFGS-B",
                      options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8})
    mu, h, a, d = unpack(result.x)
    return Fit(float(mu), float(h), a.copy(), d.copy())


def _finished(f: Dict[str, Any]) -> bool:
    return f.get("status") == "finished" and f.get("home_score") is not None and f.get("away_score") is not None


def _arrays(fixtures: Iterable[Dict[str, Any]], index: Dict[str, int], half_life: Optional[float]):
    """Index, goal and recency-weight arrays of the finished fixtures whose clubs are in `index`."""
    played = [f for f in fixtures if _finished(f) and f["home_team"] in index and f["away_team"] in index]
    gws = [f["gameweek"] for f in played if f.get("gameweek") is not None]
    latest = max(gws) if gws else 0
    ages = np.array([max(0, latest - f["gameweek"]) if f.get("gameweek") is not None else 0 for f in played],
                    dtype=float)
    return (np.array([index[f["home_team"]] for f in played], dtype=int),
            np.array([index[f["away_team"]] for f in played], dtype=int),
            np.array([f["home_score"] for f in played], dtype=float),
            np.array([f["away_score"] for f in played], dtype=float),
            _weights(ages, half_life), len(played))


def last_season_prior(previous: Sequence[Dict[str, Any]], prior_sd: float = FIRST_SEASON_PRIOR_SD) -> Optional[Dict[str, Any]]:
    """Ratings from a whole earlier season (every match counts the same, pulled toward the average),
    plus the average of the clubs that went down. None when that season has no finished match."""
    previous = list(previous)
    teams = sorted({f["home_team"] for f in previous} | {f["away_team"] for f in previous})
    index = {t: i for i, t in enumerate(teams)}
    hi, ai, hg, ag, w, n = _arrays(previous, index, None)
    if not n:
        return None
    fit = fit_poisson(hi, ai, hg, ag, w, len(teams), prior_sd=prior_sd)
    table = ss.compute_table([f for f in previous if _finished(f)])
    down = [r["team"] for r in table[-RELEGATED:]] if len(table) > RELEGATED else []
    down_idx = [index[t] for t in down if t in index]
    return {
        "teams": {t: (float(fit.attack[i]), float(fit.defence[i])) for t, i in index.items()},
        "mu": fit.mu, "home": fit.home,
        "relegated": ((float(np.mean(fit.attack[down_idx])), float(np.mean(fit.defence[down_idx])))
                      if down_idx else (0.0, 0.0)),
        "relegated_clubs": down,
    }


# ------------------------------------------------------------------ the model

class TeamStrength:
    """Fitted ratings for one season's clubs. Build it with `TeamStrength.fit(fixtures, previous)`."""

    def __init__(self, teams: Sequence[str], fit: Fit, fixtures: Sequence[Dict[str, Any]] = (),
                 matches: int = 0, promoted: Iterable[str] = (), prior_season: bool = False):
        self.teams: List[str] = list(teams)
        self.index = {t: i for i, t in enumerate(self.teams)}
        self.mu, self.home_advantage = fit.mu, fit.home
        self.attack, self.defence = fit.attack, fit.defence
        self._fixtures = list(fixtures)
        self.matches = matches                    # finished matches the fit used
        self.promoted = set(promoted)             # clubs rated from the relegated clubs' average
        self.has_prior_season = prior_season

    # -- construction

    @classmethod
    def fit(cls, fixtures: Sequence[Dict[str, Any]], previous: Optional[Sequence[Dict[str, Any]]] = None,
            half_life: Optional[float] = HALF_LIFE, prior_sd: float = PRIOR_SD,
            prior_carry: float = PRIOR_CARRY) -> "TeamStrength":
        """Fit to this season's `fixtures` (rows as season_service gives them: finished ones have
        scores). `previous` is last season's fixtures, the prior; without it the prior is average."""
        fixtures = list(fixtures)
        teams = sorted({f["home_team"] for f in fixtures} | {f["away_team"] for f in fixtures})
        index = {t: i for i, t in enumerate(teams)}
        prior = last_season_prior(previous) if previous else None

        a0, d0, promoted = np.zeros(len(teams)), np.zeros(len(teams)), []
        mu0, home0 = DEFAULT_MU, DEFAULT_HOME
        if prior:
            mu0, home0 = prior["mu"], prior["home"]
            down_a, down_d = prior["relegated"]
            for t, i in index.items():
                known = prior["teams"].get(t)
                if known is None:
                    promoted.append(t)
                    known = (down_a, down_d)
                a0[i], d0[i] = prior_carry * known[0], prior_carry * known[1]
        hi, ai, hg, ag, w, n = _arrays(fixtures, index, half_life)
        fit = fit_poisson(hi, ai, hg, ag, w, len(teams), a0, d0, mu0, home0, prior_sd)
        return cls(teams, fit, fixtures, n, promoted, prior is not None)

    # -- ratings

    def expected_goals(self, home: str, away: str) -> Tuple[float, float]:
        """Expected goals of `home` and `away` in a match at `home`'s ground."""
        try:
            h, a = self.index[home], self.index[away]
        except KeyError as exc:
            raise ValueError(f"No rating for {exc.args[0]}") from None
        return (math.exp(self.mu + self.home_advantage + self.attack[h] - self.defence[a]),
                math.exp(self.mu + self.attack[a] - self.defence[h]))

    def ratings(self) -> List[Dict[str, Any]]:
        """Every club's attack, defence and a single strength, strongest first.

        `attack` and `defence` are log-scale (0 = the league average of the fit; higher is
        better for both). `strength` = attack + defence, the log-ratio of its goals scored to
        conceded against an average side. `goal_difference` is the expected goal difference a
        match against an average side on neutral ground."""
        att_mean, def_mean = float(np.mean(self.attack)), float(np.mean(self.defence))
        neutral = self.mu + self.home_advantage / 2.0 + (att_mean - def_mean) / 2.0
        out = []
        for team, i in self.index.items():
            a, d = float(self.attack[i] - att_mean), float(self.defence[i] - def_mean)
            out.append({
                "team": team,
                "attack": round(a, 4), "defence": round(d, 4), "strength": round(a + d, 4),
                "goal_difference": round(math.exp(neutral + a) - math.exp(neutral - d), 3),
                "promoted": team in self.promoted,
            })
        out.sort(key=lambda r: (-r["strength"], r["team"]))
        return out

    def summary(self) -> Dict[str, Any]:
        """Model-level numbers: league goal rates and the home advantage."""
        mean_att, mean_def = float(np.mean(self.attack)), float(np.mean(self.defence))
        away = math.exp(self.mu + mean_att - mean_def)
        home = math.exp(self.mu + self.home_advantage + mean_att - mean_def)
        return {"home_advantage": round(self.home_advantage, 4), "home_goals": round(home, 3),
                "away_goals": round(away, 3), "matches": self.matches, "half_life": HALF_LIFE,
                "prior_season": self.has_prior_season}

    # -- one match

    def match_probabilities(self, home: str, away: str, top_scores: int = TOP_SCORES) -> Dict[str, Any]:
        """Win/draw/win chances, expected goals, the likeliest scores and clean-sheet chances of one
        match at `home`'s ground. Goals are independent Poisson; the score grid is renormalised so
        the three outcomes sum to 1."""
        lam_h, lam_a = self.expected_goals(home, away)
        ph, pa = _poisson_pmf(lam_h), _poisson_pmf(lam_a)
        grid = np.outer(ph, pa)
        grid /= grid.sum()
        p_home = float(np.tril(grid, -1).sum())      # home goals > away goals
        p_away = float(np.triu(grid, 1).sum())
        p_draw = float(np.trace(grid))
        flat = np.argsort(-grid, axis=None, kind="stable")[:top_scores]
        scores = [{"home": int(i // grid.shape[1]), "away": int(i % grid.shape[1]),
                   "probability": float(grid.flat[i])} for i in flat]
        return {
            "home_team": home, "away_team": away,
            "home_win": p_home, "draw": p_draw, "away_win": p_away,
            "expected_goals_home": lam_h, "expected_goals_away": lam_a,
            "most_likely_scores": scores,
            "clean_sheet_home": math.exp(-lam_a),     # the home side keeps a clean sheet: away scores 0
            "clean_sheet_away": math.exp(-lam_h),
        }

    # -- the season

    def simulate_season(self, n: int = DEFAULT_SIMULATIONS, seed: int = DEFAULT_SEED,
                        fixtures: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Play the unplayed fixtures `n` times from the model on top of the current table.

        Finished matches count as they ended; anything else (scheduled or still in play) is
        simulated in full. Ties break like the table: points, goal difference, goals scored,
        then club name. Seeded, so the same inputs give the same answer. One row a club, best
        expected points first:

            team, current_position, played, points, goal_difference, remaining,
            expected_points, expected_position, predicted_position,
            p_title, p_top4 (places 1-4), p_europe (5-6, the Europa / Conference places),
            p_top6 (any European place), p_relegation (bottom three),
            position_probabilities (chance of finishing 1st..Nth).
        """
        fixtures = list(self._fixtures if fixtures is None else fixtures)
        teams = self.teams
        t_count = len(teams)
        idx = self.index
        if n < 1:
            raise ValueError("n must be at least 1")

        done = [f for f in fixtures if _finished(f)]
        left = [f for f in fixtures if not _finished(f) and f["home_team"] in idx and f["away_team"] in idx]
        # the table as it stands, with a row for every club (compute_table keeps clubs with fixtures only)
        table = {r["team"]: r for r in ss.compute_table(
            done + [dict(f, status="scheduled", home_score=None, away_score=None) for f in left])}
        pts0 = np.array([table[t]["points"] for t in teams], dtype=np.int64)
        gd0 = np.array([table[t]["goal_difference"] for t in teams], dtype=np.int64)
        gf0 = np.array([table[t]["goals_for"] for t in teams], dtype=np.int64)
        current_pos = {r["team"]: r["position"] for r in table.values()}

        rng = np.random.default_rng(seed)
        r_count = len(left)
        if r_count:
            hi = np.array([idx[f["home_team"]] for f in left], dtype=int)
            ai = np.array([idx[f["away_team"]] for f in left], dtype=int)
            lam_h = np.exp(self.mu + self.home_advantage + self.attack[hi] - self.defence[ai])
            lam_a = np.exp(self.mu + self.attack[ai] - self.defence[hi])
            hg = rng.poisson(lam_h, size=(n, r_count)).astype(np.int32)
            ag = rng.poisson(lam_a, size=(n, r_count)).astype(np.int32)
            home_m = np.zeros((r_count, t_count), dtype=np.float32)
            away_m = np.zeros((r_count, t_count), dtype=np.float32)
            home_m[np.arange(r_count), hi] = 1.0
            away_m[np.arange(r_count), ai] = 1.0
            draw = hg == ag
            pts_h = (3 * (hg > ag) + draw).astype(np.float32)
            pts_a = (3 * (ag > hg) + draw).astype(np.float32)
            diff = (hg - ag).astype(np.float32)
            pts = pts0 + np.rint(pts_h @ home_m + pts_a @ away_m).astype(np.int64)
            gd = gd0 + np.rint(diff @ home_m - diff @ away_m).astype(np.int64)
            gf = gf0 + np.rint(hg.astype(np.float32) @ home_m + ag.astype(np.float32) @ away_m).astype(np.int64)
        else:
            pts = np.tile(pts0, (n, 1))
            gd = np.tile(gd0, (n, 1))
            gf = np.tile(gf0, (n, 1))

        # one sortable integer: points, then goal difference, then goals scored, then the club's
        # place in alphabetical order (`teams` is sorted, compute_table breaks the last tie by name)
        key = ((pts * 10_000 + (gd + 5_000)) * 10_000 + gf) * 64 + (63 - np.arange(t_count))
        order = np.argsort(-key, axis=1, kind="stable")          # club index at each position
        position = np.empty_like(order)
        np.put_along_axis(position, order, np.broadcast_to(np.arange(t_count), order.shape), axis=1)

        rows = []
        for t, team in enumerate(teams):
            dist = np.bincount(position[:, t], minlength=t_count) / float(n)
            rows.append({
                "team": team,
                "current_position": current_pos.get(team),
                "played": table[team]["played"],
                "points": int(pts0[t]),
                "goal_difference": int(gd0[t]),
                "remaining": sum(1 for f in left if team in (f["home_team"], f["away_team"])),
                "expected_points": float(pts[:, t].mean()),
                "expected_position": float(position[:, t].mean() + 1.0),
                "p_title": float(dist[0]),
                "p_top4": float(dist[:CHAMPIONS_LEAGUE_PLACES].sum()),
                "p_europe": float(dist[CHAMPIONS_LEAGUE_PLACES:EUROPE_PLACES].sum()),
                "p_top6": float(dist[:EUROPE_PLACES].sum()),
                "p_relegation": float(dist[t_count - RELEGATION_PLACES:].sum()),
                "position_probabilities": [float(p) for p in dist],
            })
        rows.sort(key=lambda r: (-r["expected_points"], r["expected_position"], r["team"]))
        for i, r in enumerate(rows):
            r["predicted_position"] = i + 1
        return rows


def _poisson_pmf(lam: float, size: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(size)
    log_fact = np.array([math.lgamma(i + 1) for i in k])
    return np.exp(k * math.log(lam) - lam - log_fact)
