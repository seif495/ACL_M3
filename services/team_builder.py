"""
Team of the Week builder: the predicted squad again, under the visitor's own constraints.

POST /api/team-of-the-week/build takes a budget (80.0-110.0m), players to lock (must be in
the squad) and exclude (never picked), an optional fixed formation and a club limit (1-3), and
re-runs the optimiser behind the predicted Team of the Week (`team_optimizer.best_squad`) on
the same candidates (`season_service.predicted_pool`). The response has the predicted
payload's shape (starters, bench, captain, formation, totals, photos, web_name), plus:

  budget_used   the squad's cost in millions (same as total_cost)
  optimal       false when the solve hit its time limit: the best squad found, not proven best
  ownership     per player, FPL's selected-by percentage when the status file has it
  template      the "most-owned" squad for comparison: the most-owned available players at each
                position (2 GK, 5 DEF, 5 MID, 3 FWD, at most 3 a club), arranged by the same
                optimiser, with its predicted points and cost

Everything is pure except `register` and the cached file/graph reads. A solve takes a CPU
core for a second or two, so at most MAX_CONCURRENT_BUILDS run at once (a request waits up to
QUEUE_SECONDS for a slot, then 503), each is capped at SOLVE_SECONDS, and each client is
limited by TEAM_BUILDER_RATE_LIMITS (default "10/60,100/3600").
"""
import logging
import os
import threading
from typing import Annotated, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, StringConstraints, field_validator

from config.settings import CURRENT_SEASON
from services import season_service as ss
from services import team_optimizer as opt
from utils.text import normalize_name

logger = logging.getLogger(__name__)

MIN_BUDGET, MAX_BUDGET, DEFAULT_BUDGET = 80.0, 110.0, 100.0   # millions
MAX_LOCKED, MAX_EXCLUDED = 15, 200
SOLVE_SECONDS = 5.0                 # per solve
MAX_CONCURRENT_BUILDS = 2           # solves at once
QUEUE_SECONDS = 10.0                # how long a request waits for a free slot before a 503
TEMPLATE_SOLVE_SECONDS = 2.0

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_BUILDS)

PLURAL = {"GK": "goalkeepers", "DEF": "defenders", "MID": "midfielders", "FWD": "forwards"}
SINGULAR = {"GK": "goalkeeper", "DEF": "defender", "MID": "midfielder", "FWD": "forward"}

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class BuildError(ValueError):
    """The constraints can't be met (HTTP 422); the message says why."""


class BuildBusy(RuntimeError):
    """Too many squads are being built right now (HTTP 503)."""


class BuildRequest(BaseModel):
    budget: float = Field(DEFAULT_BUDGET, ge=MIN_BUDGET, le=MAX_BUDGET)
    locked: List[Name] = Field(default_factory=list, max_length=MAX_LOCKED)
    excluded: List[Name] = Field(default_factory=list, max_length=MAX_EXCLUDED)
    formation: Optional[str] = None
    max_per_club: int = Field(opt.DEFAULT_MAX_PER_CLUB, ge=1, le=3)

    @field_validator("formation")
    @classmethod
    def _formation(cls, value: Optional[str]) -> Optional[str]:
        """None, "" and "best" leave the formation to the optimiser."""
        value = (value or "").strip().lower()
        if value in ("", "best"):
            return None
        if value not in opt.FORMATIONS:
            raise ValueError(f"formation must be 'best' or one of {', '.join(opt.FORMATIONS)}")
        return value


def money(tenths: float) -> str:
    return f"£{tenths / 10.0:.1f}m"


# ------------------------------------------------------------------ names

def resolve_names(names: Iterable[str], known: Iterable[str]) -> Tuple[List[str], List[str]]:
    """(resolved, unknown): each name as the candidates spell it. An exact match wins, else one
    that matches ignoring case, accents and punctuation ("Odegaard"); an ambiguous or
    missing name is unknown. Duplicates collapse, order is kept."""
    known = list(known)
    exact = set(known)
    folded: Dict[str, List[str]] = {}
    for k in known:
        folded.setdefault(normalize_name(k), []).append(k)
    resolved: List[str] = []
    unknown: List[str] = []
    for name in names:
        if name in exact:
            hit: Optional[str] = name
        else:
            options = folded.get(normalize_name(name), [])
            hit = options[0] if len(options) == 1 else None
        if hit is None:
            unknown.append(name)
        elif hit not in resolved:
            resolved.append(hit)
    return resolved, unknown


# ------------------------------------------------------------------ constraint checks

def check_constraints(pool: Sequence[opt.Candidate], budget: int, locked: Sequence[str],
                      excluded: Sequence[str], max_per_club: int) -> None:
    """The obvious reasons no squad fits, as readable BuildErrors, before the solver runs.
    `pool` is the full candidate list; `locked` and `excluded` are resolved ids."""
    by_id = {c.id: c for c in pool}
    both = [n for n in locked if n in set(excluded)]
    if both:
        raise BuildError(f"{', '.join(both)} can't be both locked and excluded")

    locked_players = [by_id[n] for n in locked]
    locked_cost = sum(c.cost for c in locked_players)
    if locked_cost > budget:
        raise BuildError(f"Locked players cost {money(locked_cost)}, over the {money(budget)} budget")

    for pos in opt.POSITIONS:
        count = sum(c.position == pos for c in locked_players)
        if count > opt.SQUAD_SIZE[pos]:
            raise BuildError(f"{count} locked {PLURAL[pos]} but a squad has {opt.SQUAD_SIZE[pos]}")
    clubs: Dict[str, int] = {}
    for c in locked_players:
        clubs[c.team] = clubs.get(c.team, 0) + 1
    for club, count in sorted(clubs.items()):
        if count > max_per_club:
            raise BuildError(f"{count} locked players from {club} but the limit is {max_per_club}")

    skip = set(excluded)
    open_players = [c for c in pool if c.id not in skip]
    for pos in opt.POSITIONS:
        have = sum(c.position == pos for c in open_players)
        if have < opt.SQUAD_SIZE[pos]:
            noun = SINGULAR[pos] if have == 1 else PLURAL[pos]
            raise BuildError(f"Only {have} {noun} left after the exclusions; a squad needs {opt.SQUAD_SIZE[pos]}")

    # Cheapest way to fill the rest of the squad (a lower bound: it ignores the club limit)
    cheapest = locked_cost
    for pos in opt.POSITIONS:
        need = opt.SQUAD_SIZE[pos] - sum(c.position == pos for c in locked_players)
        costs = sorted(c.cost for c in open_players if c.position == pos and c.id not in set(locked))
        cheapest += sum(costs[:max(need, 0)])
    if cheapest > budget:
        what = "The cheapest squad with these locked players" if locked else "The cheapest valid squad"
        raise BuildError(f"{what} costs {money(cheapest)}, over the {money(budget)} budget")


# ------------------------------------------------------------------ the template

def template_squad(pool: Sequence[opt.Candidate], ownership: Dict[str, float],
                   max_per_club: int = opt.DEFAULT_MAX_PER_CLUB) -> List[opt.Candidate]:
    """The most-owned squad: per position the most-owned candidates (ties: more predicted
    points, then name), skipping a player whose club already has `max_per_club`. Empty when
    ownership isn't known for enough players."""
    chosen: List[opt.Candidate] = []
    per_club: Dict[str, int] = {}
    for pos in opt.POSITIONS:
        ranked = sorted((c for c in pool if c.position == pos and c.id in ownership),
                        key=lambda c: (-ownership[c.id], -c.points, c.id))
        taken = 0
        for c in ranked:
            if taken == opt.SQUAD_SIZE[pos]:
                break
            if per_club.get(c.team, 0) >= max_per_club:
                continue
            chosen.append(c)
            per_club[c.team] = per_club.get(c.team, 0) + 1
            taken += 1
        if taken < opt.SQUAD_SIZE[pos]:
            return []
    return chosen


# ------------------------------------------------------------------ the build (pure)

def build_payload(pool: Sequence[opt.Candidate], details: Dict[str, Dict[str, Any]], gameweek: int,
                  ownership_by_name: Dict[str, float], req: BuildRequest,
                  time_limit: float = SOLVE_SECONDS) -> Dict[str, Any]:
    """Solve the request and return the predicted-team payload (no photos). BuildError when no
    squad fits; opt.SolveTimeLimit when the solver ran out of time."""
    budget = int(round(req.budget * 10))
    known = [c.id for c in pool]
    locked, unknown = resolve_names(req.locked, known)
    if unknown:
        raise BuildError(f"{', '.join(unknown)} can't be locked: no prediction for the next gameweek, "
                         "or injured, suspended or unavailable")
    excluded, _ = resolve_names(req.excluded, known)      # an unknown excluded player changes nothing
    check_constraints(pool, budget, locked, excluded, req.max_per_club)
    try:
        selection = opt.best_squad(pool, budget=budget, max_per_club=req.max_per_club, time_limit=time_limit,
                                   locked=locked, excluded=excluded, formation=req.formation)
    except opt.SolveTimeLimit:
        raise
    except opt.InfeasibleError as exc:
        raise BuildError(f"No squad fits these limits ({exc})") from exc

    payload = ss.build_team_of_the_week(selection, details, "predicted", gameweek, budget / 10.0)
    payload["budget_used"] = payload["total_cost"]
    payload["optimal"] = selection.optimal          # False: the time limit stopped the search early
    payload["max_per_club"] = req.max_per_club
    payload["locked"] = locked
    payload["excluded"] = excluded
    for p in payload["starters"] + payload["bench"]:
        p["ownership"] = ownership_by_name.get(p["name"])
    payload["template"] = template_payload(pool, details, gameweek, ownership_by_name)
    return payload


def template_payload(pool: Sequence[opt.Candidate], details: Dict[str, Dict[str, Any]], gameweek: int,
                     ownership_by_name: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """The most-owned squad's predicted points, cost and players; None when it can't be built."""
    squad = template_squad(pool, ownership_by_name)
    if not squad:
        return None
    try:
        selection = opt.best_squad(squad, budget=10 ** 6, max_per_club=15, time_limit=TEMPLATE_SOLVE_SECONDS)
    except opt.InfeasibleError:
        return None
    team = ss.build_team_of_the_week(selection, details, "predicted", gameweek, None)
    players = []
    for p in team["starters"] + team["bench"]:
        players.append({"name": p["name"], "team": p["team"], "position": p["position"],
                        "points": p["points"], "cost": p["cost"], "ownership": ownership_by_name.get(p["name"]),
                        "starts": p in team["starters"]})
    costs = [p["cost"] for p in players]
    return {
        "total_points": team["total_points"],
        "total_cost": round(sum(costs), 1) if all(c is not None for c in costs) else None,
        "formation": team["formation"],
        "players": players,
    }


# ------------------------------------------------------------------ inputs (cached)

def ownership_by_element(season: str = CURRENT_SEASON) -> Dict[int, float]:
    """FPL's selected-by percentage per element from the status file; {} when it's missing or
    has no such column."""
    path = ss.status_path(season)

    def load() -> Dict[int, float]:
        if not os.path.exists(path):
            return {}
        import pandas as pd
        try:
            df = pd.read_csv(path, usecols=lambda c: c in ("element", "selected_by_percent"))
        except (OSError, ValueError):
            return {}
        if "element" not in df or "selected_by_percent" not in df:
            return {}
        out: Dict[int, float] = {}
        for r in df.to_dict("records"):
            element = ss._int_or_none(r.get("element"))
            try:
                pct = float(r.get("selected_by_percent"))
            except (TypeError, ValueError):
                continue
            if element is not None and pct == pct:
                out[element] = round(pct, 1)
        return out

    return ss._cached(("ownership", path, ss._mtime(path)), load)


def load_inputs(conn: Any, season: str = CURRENT_SEASON):
    """(pool, details, gameweek, ownership by name) for the predictions file, cached with the
    file. NotFoundError when there are no predictions."""
    from services.predictions import get_predictions_df, predictions_path

    path = predictions_path()
    df = get_predictions_df(path)
    if df is None or df.empty:
        raise ss.NotFoundError("No predictions available")
    gameweek = ss._int_or_none(df["gameweek"].iloc[0])
    if gameweek is None:
        raise ss.NotFoundError("Predictions file has no gameweek")

    def build():
        opponents: Dict[str, List[Dict[str, Any]]] = {}
        if conn is not None:
            try:
                opponents = ss.team_opponents(ss.load_fixtures(conn, season), gameweek)
            except Exception as exc:     # the predictions file alone is enough
                logger.warning("Fixtures unavailable for the team builder's opponents: %s", exc)
        pool, details = ss.predicted_pool(df.to_dict("records"), opponents, ss.status_lookup(season)["code"])
        by_element = ownership_by_element(season)
        owned = {name: by_element[d["element"]] for name, d in details.items() if d.get("element") in by_element}
        return pool, details, owned

    pool, details, owned = ss._cached(("builder-inputs", path, ss._mtime(path), ss._mtime(ss.status_path(season)),
                                       conn is not None), build)
    return pool, details, gameweek, owned


def build(conn: Any, req: BuildRequest, photo_for: Optional[ss.PhotoFn] = None,
          season: str = CURRENT_SEASON, time_limit: float = SOLVE_SECONDS) -> Dict[str, Any]:
    """The whole request: inputs, a slot, the solve, photos and FPL's short names.
    Raises ss.NotFoundError, BuildError, opt.SolveTimeLimit or BuildBusy."""
    pool, details, gameweek, owned = load_inputs(conn, season)
    if not _slots.acquire(timeout=QUEUE_SECONDS):
        raise BuildBusy("Lots of squads are being built right now; try again in a moment")
    try:
        payload = build_payload(pool, details, gameweek, owned, req, time_limit)
    finally:
        _slots.release()
    out = ss.with_photos(payload, photo_for)
    web_names = ss.status_lookup(season)["web_name"]
    for p in out["starters"] + out["bench"]:
        p["web_name"] = web_names.get(p["name"])
    return out


# ------------------------------------------------------------------ route

def register(app: Any, get_conn: Callable[[], Any], photo_for: Optional[ss.PhotoFn] = None) -> None:
    """Add POST /api/team-of-the-week/build. `get_conn` is api_main's get_neo4j_conn, called
    directly so a missing connection only costs the opponents. A plain `def` route: the
    solver blocks, so FastAPI runs it in its threadpool."""
    from fastapi import Depends, HTTPException

    from services.security import RateLimiter, limits_from_env

    limiter = RateLimiter(limits_from_env("TEAM_BUILDER_RATE_LIMITS", "10/60,100/3600"))
    app.state.team_builder_limiter = limiter

    def optional_conn() -> Any:
        try:
            return get_conn()
        except Exception:
            return None

    @app.post("/api/team-of-the-week/build", dependencies=[Depends(limiter.dependency)])
    def build_team(body: BuildRequest) -> Dict[str, Any]:
        """The predicted squad under the visitor's constraints (see the module docstring).
        404 without predictions, 422 when no squad fits (the detail says why), 503 when busy."""
        try:
            return build(optional_conn(), body, photo_for)
        except ss.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except opt.SolveTimeLimit:
            raise HTTPException(status_code=503, detail="That took too long to solve. Try fewer constraints.",
                                headers={"Retry-After": "10"})
        except BuildBusy as exc:
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "10"})
