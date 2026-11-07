"""Player page URLs (services/player_pages.py): slugs and GET /api/players/by-slug/{slug}."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services import player_pages
from services.player_index import PlayerIndex

# Shared with src/utils/playerPath.ts: the frontend must produce the same strings.
# (Keep this table and the comment block in playerPath.ts identical.)
SLUG_CASES = {
    "Erling Haaland": "erling-haaland",
    "Martin Ødegaard": "martin-odegaard",
    "N'Golo Kanté": "n-golo-kante",
    "Kevin De Bruyne": "kevin-de-bruyne",
    "Jakub Kiwior": "jakub-kiwior",
    "Łukasz Fabiański": "lukasz-fabianski",
    "Weiß Straße": "weiss-strasse",
    "Æthelwulf Þór": "aethelwulf-thor",
    "Trent Alexander-Arnold": "trent-alexander-arnold",
    "João Pedro Junqueira de Jesus": "joao-pedro-junqueira-de-jesus",
    "  Mo   Salah ": "mo-salah",
    "Dominik Szoboszlai": "dominik-szoboszlai",
    "": "",
}


@pytest.mark.parametrize("name,slug", SLUG_CASES.items())
def test_slugify_player(name, slug):
    assert player_pages.slugify_player(name) == slug


def make_index():
    return PlayerIndex([
        {"player_name": "Erling Haaland", "position": "FWD", "element_id": 1, "team_name": "Man City",
         "is_current": True, "career_points": 500},
        {"player_name": "Martin Ødegaard", "position": "MID", "element_id": 2, "team_name": "Arsenal",
         "is_current": True, "career_points": 400},
        # same slug, a past player with fewer points: the current one wins
        {"player_name": "Martin Odegaard", "position": "MID", "element_id": None, "team_name": "Arsenal",
         "is_current": False, "career_points": 10},
    ])


def test_slug_map_prefers_the_popular_player():
    mapping = player_pages.build_slug_map(make_index())
    assert mapping == {"erling-haaland": "Erling Haaland", "martin-odegaard": "Martin Ødegaard"}


def client_for(get_index):
    app = FastAPI()
    player_pages.register(app, lambda: None, get_index)
    return TestClient(app)


def test_by_slug_found_and_not_found():
    index = make_index()
    client = client_for(lambda: index)
    r = client.get("/api/players/by-slug/martin-odegaard")
    assert r.status_code == 200 and r.json() == {"name": "Martin Ødegaard", "slug": "martin-odegaard"}
    assert client.get("/api/players/by-slug/nobody-here").status_code == 404
    assert client.get("/api/players/by-slug/Erling_Haaland").status_code == 404     # not a slug
    assert client.get("/api/players/by-slug/x").status_code == 404


def test_slug_map_follows_a_new_index():
    holder = {"index": make_index()}
    client = client_for(lambda: holder["index"])
    assert client.get("/api/players/by-slug/bukayo-saka").status_code == 404
    holder["index"] = PlayerIndex([{"player_name": "Bukayo Saka", "position": "MID", "element_id": 3,
                                    "team_name": "Arsenal", "is_current": True, "career_points": 300}])
    assert client.get("/api/players/by-slug/bukayo-saka").json()["name"] == "Bukayo Saka"
    assert client.get("/api/players/by-slug/erling-haaland").status_code == 404


def test_without_neo4j_the_index_error_passes_through():
    def down():
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    assert client_for(down).get("/api/players/by-slug/erling-haaland").status_code == 503
