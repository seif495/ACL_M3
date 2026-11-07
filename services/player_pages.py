"""
Player page URLs: /players/<slug> (e.g. /players/erling-haaland).

A player's graph identity is the name string, so a slug maps to one name. The slug is the
normalized full name (utils.text.normalize_name: lowercase, accents and letters such as ø and ł
folded, punctuation removed) joined with "-". The frontend mirrors it in src/utils/playerPath.ts
(tests/test_player_pages.py holds the shared expected strings).

GET /api/players/by-slug/{slug} -> {name, slug}, or 404. The profile itself comes from the
existing GET /api/players/profile?name=&season=.
"""
import logging
import re
import threading
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException

from utils.text import normalize_name

logger = logging.getLogger(__name__)

SLUG_PATTERN = re.compile(r"[a-z0-9-]{2,80}")


def slugify_player(name: str) -> str:
    """"Martin Ødegaard" -> "martin-odegaard", "N'Golo Kanté" -> "n-golo-kante"."""
    return normalize_name(name or "").replace(" ", "-")


def build_slug_map(index: Any) -> Dict[str, str]:
    """slug -> graph name for every player of the index. When two names share a slug the more
    popular player (current, more career points) wins."""
    best: Dict[str, Any] = {}
    for entry in getattr(index, "entries", []):
        slug = slugify_player(entry.player_name)
        if not slug:
            continue
        held = best.get(slug)
        if held is None or entry.popularity > held.popularity:
            best[slug] = entry
    return {slug: entry.player_name for slug, entry in best.items()}


class SlugCache:
    """The slug map of the current index, rebuilt when the app swaps in a new index
    (after a reload or a connection change)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: Any = None
        self._map: Dict[str, str] = {}

    def lookup(self, index: Any, slug: str) -> Optional[str]:
        with self._lock:
            if index is not self._index:
                self._map = build_slug_map(index)
                self._index = index
            return self._map.get(slug)


def register(app: FastAPI, get_conn: Callable[..., Any], get_index: Callable[..., Any]) -> None:
    """Add GET /api/players/by-slug/{slug}. `get_index` is api_main's get_player_index (it
    builds the index when missing and raises a 503 without Neo4j); `get_conn` is kept for
    symmetry with the other registrars. A plain `def` route: the index build is blocking."""
    cache = SlugCache()

    @app.get("/api/players/by-slug/{slug}")
    def player_by_slug(slug: str) -> Dict[str, str]:
        if not SLUG_PATTERN.fullmatch(slug):
            raise HTTPException(status_code=404, detail="Player not found")
        name = cache.lookup(get_index(), slug)
        if name is None:
            raise HTTPException(status_code=404, detail="Player not found")
        return {"name": name, "slug": slug}
