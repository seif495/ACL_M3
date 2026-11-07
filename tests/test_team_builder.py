"""Tests for the Team of the Week builder: best_squad's locked/excluded/formation arguments,
services.team_builder's checks and payload, and POST /api/team-of-the-week/build with patched
inputs. No Neo4j, no files."""
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import season_service as ss
from services import team_builder as tb
from services import team_optimizer as opt
from services.team_optimizer import Candidate


def make_pool(clubs: int = 10):
    """Six players a club (GK, 2 DEF, 2 MID, FWD) with spread points and prices; all unique."""
    positions = ["GK", "DEF", "DEF", "MID", "MID", "FWD"]
    pool = []
    for c in range(clubs):
        for k, pos in enumerate(positions):
            n = c * 6 + k
            pool.append(Candidate(id=f"{pos}{c}-{k}", position=pos, team=f"Club{c}",
                                  points=round(1.0 + (n * 37 % 53) / 8.0, 2), cost=40 + (n * 11) % 70))
    return pool


def squad_value(sel: opt.Selection) -> float:
    """The solver's objective, from a selection: XI + captain + weighted bench."""
    w1, w2, w3 = opt.BENCH_WEIGHTS
    value = sum(c.points for c in sel.starters) + sel.captain.points
    outfield = [c for c in sel.bench if c.position != "GK"]
    for c, w in zip(outfield, (w1, w2, w3)):
        value += w * c.points
    value += sum(opt.BENCH_GK_WEIGHT * c.points for c in sel.bench if c.position == "GK")
    return value


def ids(sel: opt.Selection):
    return {c.id for c in sel.starters + sel.bench}


# ------------------------------------------------------------------ best_squad arguments

def test_defaults_unchanged():
    pool = make_pool()
    plain = opt.best_squad(pool)
    explicit = opt.best_squad(pool, locked=(), excluded=(), formation=None)
    assert ids(plain) == ids(explicit)
    assert [c.id for c in plain.starters] == [c.id for c in explicit.starters]


def test_locked_player_is_in_the_squad_even_if_poor():
    pool = make_pool()
    worst = min((c for c in pool if c.position == "FWD"), key=lambda c: c.points)
    assert worst.id not in ids(opt.best_squad(pool))
    sel = opt.best_squad(pool, locked=[worst.id])
    assert worst.id in ids(sel)
    assert opt.validate_squad(sel) == []


def test_excluded_player_is_never_picked():
    pool = make_pool()
    best = opt.best_squad(pool)
    gone = [c.id for c in best.starters[:3]]
    sel = opt.best_squad(pool, excluded=gone)
    assert not ids(sel) & set(gone)
    assert squad_value(sel) <= squad_value(best) + 1e-6


@pytest.mark.parametrize("formation", opt.FORMATIONS)
def test_fixed_formation(formation):
    pool = make_pool()
    sel = opt.best_squad(pool, formation=formation)
    assert sel.formation == formation
    assert opt.validate_squad(sel) == []
    assert squad_value(sel) <= squad_value(opt.best_squad(pool)) + 1e-6


def test_best_formation_is_the_best_of_the_fixed_ones():
    pool = make_pool()
    best = squad_value(opt.best_squad(pool))
    fixed = max(squad_value(opt.best_squad(pool, formation=f)) for f in opt.FORMATIONS)
    assert best == pytest.approx(fixed, abs=1e-6) or best > fixed


def test_unknown_formation():
    for bad in ("2-4-4", "4-4-1", "abc", "6-3-1"):
        with pytest.raises(ValueError):
            opt.best_squad(make_pool(), formation=bad)


def test_locked_and_excluded_conflict():
    pool = make_pool()
    with pytest.raises(opt.InfeasibleError, match="both locked and excluded"):
        opt.best_squad(pool, locked=[pool[0].id], excluded=[pool[0].id])


def test_locked_outside_the_pool():
    with pytest.raises(opt.InfeasibleError, match="not in the pool"):
        opt.best_squad(make_pool(), locked=["Nobody"])
    pool = make_pool()
    with pytest.raises(opt.InfeasibleError, match="not in the pool"):
        opt.best_squad([c for c in pool if c.id != pool[0].id], locked=[pool[0].id])


def test_club_limit_with_locks():
    pool = make_pool()
    club = [c for c in pool if c.team == "Club0"]
    sel = opt.best_squad(pool, locked=[c.id for c in club[:2]], max_per_club=2)
    assert sum(c.team == "Club0" for c in sel.starters + sel.bench) == 2
    assert opt.validate_squad(sel, max_per_club=2) == []


def test_infeasible_lock_is_reported():
    pool = make_pool()
    gks = [c.id for c in pool if c.position == "GK"][:3]
    with pytest.raises(opt.InfeasibleError):
        opt.best_squad(pool, locked=gks)


def test_time_limit_error_is_an_infeasible_error():
    assert issubclass(opt.SolveTimeLimit, opt.InfeasibleError)


# ------------------------------------------------------------------ names

def test_resolve_names():
    known = ["Martin Ødegaard", "Mo Salah", "Joao Pedro", "João Pedro"]
    resolved, unknown = tb.resolve_names(["martin odegaard", "Mo Salah", "Mo Salah", "Nobody", "joao pedro"], known)
    assert resolved == ["Martin Ødegaard", "Mo Salah"]
    assert unknown == ["Nobody", "joao pedro"]            # the folded name is ambiguous


# ------------------------------------------------------------------ constraint checks

def check(pool, budget=1000, locked=(), excluded=(), max_per_club=3):
    tb.check_constraints(pool, budget, list(locked), list(excluded), max_per_club)


def test_locked_over_budget_message():
    pool = [Candidate("A", "FWD", "X", 5.0, 600), Candidate("B", "FWD", "Y", 5.0, 445)] + make_pool()
    with pytest.raises(tb.BuildError) as exc:
        check(pool, locked=["A", "B"])
    assert str(exc.value) == "Locked players cost £104.5m, over the £100.0m budget"


def test_locked_club_limit_message():
    pool = [Candidate(f"Gunner{i}", "MID", "Arsenal", 4.0, 50) for i in range(4)] + make_pool()
    with pytest.raises(tb.BuildError) as exc:
        check(pool, locked=[f"Gunner{i}" for i in range(4)])
    assert str(exc.value) == "4 locked players from Arsenal but the limit is 3"


def test_locked_position_message():
    pool = make_pool()
    gks = [c.id for c in pool if c.position == "GK"][:3]
    with pytest.raises(tb.BuildError, match="3 locked goalkeepers but a squad has 2"):
        check(pool, locked=gks)


def test_exclusions_leave_too_few():
    pool = make_pool()
    gks = [c.id for c in pool if c.position == "GK"]
    with pytest.raises(tb.BuildError, match="Only 1 goalkeeper left after the exclusions"):
        check(pool, excluded=gks[:-1])


def test_cheapest_squad_over_budget():
    pool = make_pool()
    with pytest.raises(tb.BuildError, match="cheapest valid squad costs"):
        check(pool, budget=500)
    star = max(pool, key=lambda c: c.cost)
    with pytest.raises(tb.BuildError, match="cheapest squad with these locked players"):
        check(pool, budget=700, locked=[star.id])


def test_valid_constraints_pass():
    pool = make_pool()
    check(pool, locked=[pool[3].id], excluded=[pool[4].id])


# ------------------------------------------------------------------ template

def details_for(pool):
    return {c.id: {"team": c.team, "cost": round(c.cost / 10.0, 1), "opponents": [], "status": "a",
                   "chance_of_playing_next_round": None, "news": None, "element": i + 1, "_code": None}
            for i, c in enumerate(pool)}


def test_template_is_the_most_owned_valid_squad():
    pool = make_pool()
    owned = {c.id: float((i * 7) % 90) for i, c in enumerate(pool)}
    squad = tb.template_squad(pool, owned)
    assert len(squad) == 15
    for pos, n in opt.SQUAD_SIZE.items():
        mine = [c for c in squad if c.position == pos]
        assert len(mine) == n
        # the most-owned player of each position is in (his club can't be full yet)
        top = max((c for c in pool if c.position == pos), key=lambda c: (owned[c.id], -ord(c.id[-1])))
        assert owned[top.id] == max(owned[c.id] for c in mine)
    assert max(sum(c.team == t for c in squad) for t in {c.team for c in squad}) <= 3
    assert tb.template_squad(pool, {}) == []


def test_template_payload():
    pool = make_pool()
    owned = {c.id: float(i % 40) for i, c in enumerate(pool)}
    res = tb.template_payload(pool, details_for(pool), 9, owned)
    assert len(res["players"]) == 15 and sum(p["starts"] for p in res["players"]) == 11
    assert res["total_points"] > 0 and res["total_cost"] > 0
    assert tb.template_payload(pool, details_for(pool), 9, {}) is None


# ------------------------------------------------------------------ payload

def request(**kw):
    return tb.BuildRequest(**kw)


def test_payload_shape_and_ownership():
    pool = make_pool()
    owned = {c.id: float(i % 30) for i, c in enumerate(pool)}
    res = tb.build_payload(pool, details_for(pool), 9, owned, request(budget=95.5, formation="4-4-2"))
    assert res["mode"] == "predicted" and res["gameweek"] == 9
    assert res["formation"] == "4-4-2" and res["budget"] == 95.5
    assert res["budget_used"] == res["total_cost"] <= 95.5
    assert res["optimal"] is True
    assert len(res["starters"]) == 11 and len(res["bench"]) == 4
    assert sum(p["captain"] for p in res["starters"]) == 1
    assert all(p["ownership"] == owned[p["name"]] for p in res["starters"] + res["bench"])
    assert res["template"]["total_points"] > 0


def test_payload_locked_and_excluded_by_loose_name():
    pool = make_pool() + [Candidate("Martin Ødegaard", "MID", "ClubZ", 0.6, 45)]
    res = tb.build_payload(pool, details_for(pool), 9, {}, request(locked=["martin odegaard"]))
    names = [p["name"] for p in res["starters"] + res["bench"]]
    assert "Martin Ødegaard" in names and res["locked"] == ["Martin Ødegaard"]
    assert res["template"] is None and all(p["ownership"] is None for p in res["starters"])


def test_unknown_locked_player_is_a_readable_error():
    pool = make_pool()
    with pytest.raises(tb.BuildError, match="Nobody can't be locked"):
        tb.build_payload(pool, details_for(pool), 9, {}, request(locked=["Nobody"]))
    # an unknown excluded player is ignored
    tb.build_payload(pool, details_for(pool), 9, {}, request(excluded=["Nobody"]))


def test_request_validation():
    assert request().budget == 100.0 and request().formation is None and request().max_per_club == 3
    assert request(formation="BEST").formation is None and request(formation="").formation is None
    assert request(formation="3-5-2").formation == "3-5-2"
    for bad in ({"budget": 79.9}, {"budget": 110.1}, {"max_per_club": 0}, {"max_per_club": 4},
                {"formation": "2-4-4"}, {"locked": ["x"] * 16}, {"excluded": ["x"] * 201},
                {"locked": ["x" * 81]}, {"locked": [""]}):
        with pytest.raises(ValueError):
            request(**bad)
    assert len(request(locked=["x"] * 15, excluded=["y"] * 200).excluded) == 200


# ------------------------------------------------------------------ route

@pytest.fixture
def client(monkeypatch):
    pool = make_pool()
    owned = {c.id: float(i % 30) for i, c in enumerate(pool)}
    monkeypatch.setattr(tb, "load_inputs", lambda conn, season=ss.CURRENT_SEASON: (pool, details_for(pool), 9, owned))
    monkeypatch.setattr(ss, "status_lookup", lambda season=ss.CURRENT_SEASON: {"web_name": {pool[0].id: "Short"}, "code": {}})
    app = FastAPI()
    tb.register(app, lambda: None, lambda name, team, code: f"/photo/{name}")
    app.pool = pool
    return TestClient(app)


def test_route_returns_the_predicted_payload(client):
    r = client.post("/api/team-of-the-week/build", json={"budget": 98.0, "formation": "3-5-2"})
    assert r.status_code == 200
    body = r.json()
    assert body["formation"] == "3-5-2" and body["budget"] == 98.0 and body["budget_used"] <= 98.0
    assert len(body["starters"]) == 11 and len(body["bench"]) == 4
    first = body["starters"][0]
    assert first["photo"] == f"/photo/{first['name']}" and "_code" not in first
    assert "ownership" in first and "web_name" in first
    assert body["template"]["players"]


def test_route_locks_and_excludes(client):
    pool = client.app.pool
    locked = min((c for c in pool if c.position == "DEF"), key=lambda c: c.points).id
    best = opt.best_squad(pool)
    excluded = best.captain.id
    r = client.post("/api/team-of-the-week/build", json={"locked": [locked], "excluded": [excluded]})
    assert r.status_code == 200
    names = {p["name"] for p in r.json()["starters"] + r.json()["bench"]}
    assert locked in names and excluded not in names


def test_route_422_explains(client):
    pool = client.app.pool
    dear = sorted(pool, key=lambda c: -c.cost)[:3]
    cost = sum(c.cost for c in dear) / 10.0
    r = client.post("/api/team-of-the-week/build", json={"budget": 80.0, "locked": [c.id for c in dear]})
    if cost > 80.0:
        assert r.status_code == 422
        assert r.json()["detail"].startswith("Locked players cost")
    r = client.post("/api/team-of-the-week/build", json={"locked": ["Nobody"]})
    assert r.status_code == 422 and "Nobody" in r.json()["detail"]
    assert client.post("/api/team-of-the-week/build", json={"budget": 120}).status_code == 422


def test_route_404_without_predictions(client, monkeypatch):
    def none(conn, season=ss.CURRENT_SEASON):
        raise ss.NotFoundError("No predictions available")
    monkeypatch.setattr(tb, "load_inputs", none)
    r = client.post("/api/team-of-the-week/build", json={})
    assert r.status_code == 404 and r.json()["detail"] == "No predictions available"


def test_route_503_when_busy(client, monkeypatch):
    monkeypatch.setattr(tb, "QUEUE_SECONDS", 0.05)
    held = [tb._slots.acquire() for _ in range(tb.MAX_CONCURRENT_BUILDS)]
    try:
        r = client.post("/api/team-of-the-week/build", json={})
        assert r.status_code == 503 and r.headers["retry-after"] == "10"
    finally:
        for _ in held:
            tb._slots.release()
    assert client.post("/api/team-of-the-week/build", json={}).status_code == 200


def test_route_503_when_the_solver_times_out(client, monkeypatch):
    def slow(*a, **k):
        raise opt.SolveTimeLimit("out of time")
    monkeypatch.setattr(opt, "best_squad", slow)
    r = client.post("/api/team-of-the-week/build", json={})
    assert r.status_code == 503
    # the slot is released
    assert tb._slots.acquire(blocking=False) and tb._slots.release() is None


def test_route_rate_limit(monkeypatch):
    monkeypatch.setenv("TEAM_BUILDER_RATE_LIMITS", "2/60")
    monkeypatch.setattr(tb, "load_inputs", lambda *a, **k: (_ for _ in ()).throw(ss.NotFoundError("none")))
    app = FastAPI()
    tb.register(app, lambda: None)
    c = TestClient(app)
    codes = [c.post("/api/team-of-the-week/build", json={}).status_code for _ in range(3)]
    assert codes == [404, 404, 429]


def test_concurrent_builds_are_capped(monkeypatch):
    """No more than MAX_CONCURRENT_BUILDS solves run at once, and every request still finishes."""
    import time
    pool = make_pool()
    monkeypatch.setattr(tb, "load_inputs", lambda conn, season=ss.CURRENT_SEASON: (pool, details_for(pool), 9, {}))
    monkeypatch.setattr(ss, "status_lookup", lambda season=ss.CURRENT_SEASON: {"web_name": {}, "code": {}})
    running, peak, lock = [0], [0], threading.Lock()
    real = opt.best_squad

    def counting(*a, **k):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        try:
            return real(*a, **k)
        finally:
            with lock:
                running[0] -= 1

    monkeypatch.setattr(opt, "best_squad", counting)
    done = []
    threads = [threading.Thread(target=lambda: done.append(tb.build(None, request()))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(done) == 6 and 1 <= peak[0] <= tb.MAX_CONCURRENT_BUILDS
