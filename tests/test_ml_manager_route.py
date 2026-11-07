"""GET /api/ml-manager: the route only passes services.fpl_manager.get_manager_view's payload
through and maps "no manager yet" to 404. The engine is replaced by a stand-in module, so these
tests don't depend on its files or its state."""
import sys
import types

import pytest
from fastapi.testclient import TestClient

import api_main
from services import season_service as ss

VIEW = {
    "season": "2026-27",
    "start_gameweek": 9,
    "model": "v4",
    "rules": {"budget": 1000, "max_free_transfers": 5, "hit_cost": 4},
    "current": {
        "season": "2026-27", "gameweek": 10, "deadline": "2026-11-06T18:30:00Z", "status": "planned",
        "planned_at": "2026-11-05T06:00:00Z", "model": "v4", "chip": None, "transfers": [],
        "free_transfers": 1, "transfers_made": 0, "hits": 0, "hit_cost": 0,
        "bank_before": 5, "bank_after": 5, "squad": [], "projected_points": 55.2,
        "horizon": [{"gameweek": 10, "projected": 55.2}], "reason": "No transfer: the free one is saved",
        "result": None,
    },
    "chips": [{"chip": "wildcard", "label": "Wildcard", "window": [2, 19], "used_gameweek": None, "available": True}],
    "history": [],
    "totals": {"points": 0, "gameweeks": 0, "vs_average": None, "league": None},
    "next_deadline": "2026-11-06T18:30:00Z",
}


class EngineNotFound(LookupError):
    """The engine's own "no manager yet" error, if it defines one."""


def fake_engine(view=None, error=None, own_not_found=True):
    module = types.ModuleType("services.fpl_manager")
    calls = []

    def get_manager_view(season=None, photo_for=None, live=None, **kwargs):
        calls.append({"season": season, "photo_for": photo_for, **kwargs})
        if error is not None:
            raise error
        return view

    module.get_manager_view = get_manager_view
    if own_not_found:
        module.NotFoundError = EngineNotFound
    module.calls = calls
    return module


@pytest.fixture
def client():
    return TestClient(api_main.app)


def use_engine(monkeypatch, module):
    # The route imports the engine per call, so the stand-in in sys.modules is what it gets.
    monkeypatch.setitem(sys.modules, "services.fpl_manager", module)


def test_returns_the_view(client, monkeypatch):
    engine = fake_engine(VIEW)
    use_engine(monkeypatch, engine)
    resp = client.get("/api/ml-manager")
    assert resp.status_code == 200
    assert resp.json() == VIEW
    # Photos come from the app's no-network helper.
    assert engine.calls[0]["photo_for"] is api_main._photo_for


def test_passes_the_gameweek(client, monkeypatch):
    engine = fake_engine(VIEW)
    use_engine(monkeypatch, engine)
    assert client.get("/api/ml-manager?gameweek=11").status_code == 200
    assert engine.calls[0]["gameweek"] == 11
    # without one, the engine picks (the gameweek being played, else the latest)
    client.get("/api/ml-manager")
    assert "gameweek" not in engine.calls[1]
    assert client.get("/api/ml-manager?gameweek=0").status_code == 422


def test_engine_not_found_is_404(client, monkeypatch):
    use_engine(monkeypatch, fake_engine(error=EngineNotFound("The ML manager starts in GW9")))
    resp = client.get("/api/ml-manager")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "The ML manager starts in GW9"


def test_season_service_not_found_is_404(client, monkeypatch):
    use_engine(monkeypatch, fake_engine(error=ss.NotFoundError(""), own_not_found=False))
    resp = client.get("/api/ml-manager")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "The ML manager hasn't started yet"


def test_other_errors_are_not_404(monkeypatch):
    use_engine(monkeypatch, fake_engine(error=KeyError("squad")))
    resp = TestClient(api_main.app, raise_server_exceptions=False).get("/api/ml-manager")
    assert resp.status_code == 500


def test_real_engine_without_entries_is_404(client, monkeypatch, tmp_path):
    pytest.importorskip("services.fpl_manager")
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    assert client.get("/api/ml-manager").status_code == 404


def test_missing_engine_is_503(client, monkeypatch):
    # None in sys.modules makes the import fail, as if the module weren't there.
    monkeypatch.setitem(sys.modules, "services.fpl_manager", None)
    resp = client.get("/api/ml-manager")
    assert resp.status_code == 503
