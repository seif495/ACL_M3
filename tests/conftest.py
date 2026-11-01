"""Shared test setup: no test reaches FPL's live feed, API-Football or the Guardian."""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture(autouse=True)
def _no_live_fpl(monkeypatch):
    from services import live_points

    def offline(path):
        raise RuntimeError(f"tests don't call FPL ({path})")

    monkeypatch.setattr(live_points, "_http_get", offline)
    # No test reaches API-Football or the Guardian with a developer's real keys
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    live_points.clear_cache()
    yield
    live_points.clear_cache()
