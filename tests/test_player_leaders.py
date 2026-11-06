"""Season leaders table: GET /api/players/leaders and season_service.get_player_leaders."""
import os
import sys
import typing
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import api_main  # noqa: E402
from config.settings import CURRENT_SEASON, SEASONS  # noqa: E402
from services import season_service as ss  # noqa: E402

PAST_SEASON = SEASONS[-2]


def raw_row(name="Mohamed Salah", **overrides):
    """A row as LEADERS_QUERY returns it."""
    row = {
        "name": name, "team": "Liverpool", "position": "MID", "games": 10, "minutes": 870,
        "goals": 7, "assists": 4, "clean_sheets": 3, "bonus": 9, "total_points": 83,
        "points_per_game": 8.3, "status": "d", "chance_of_playing_next_round": 75,
        "news": "Knock - 75% chance of playing", "fpl_code": 118748,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def fresh_cache():
    ss.clear_cache()
    yield
    ss.clear_cache()


def mock_conn(rows=None):
    conn = MagicMock()
    conn.execute_query.return_value = rows if rows is not None else [raw_row()]
    return conn


# --- query building ------------------------------------------------------------------------------

@pytest.mark.parametrize("sort", list(ss.LEADER_SORTS))
def test_sort_key_maps_to_fixed_order_by_column(sort):
    query, _ = ss.leaders_query(sort)
    assert f"ORDER BY {ss.LEADER_SORTS[sort]} DESC, total_points DESC, name" in query
    assert "{order_by}" not in query


def test_unknown_sort_or_position_rejected():
    with pytest.raises(ValueError):
        ss.leaders_query("total_points DESC; MATCH (n) DETACH DELETE n //")
    with pytest.raises(ValueError):
        ss.leaders_query("total_points", position="GKP")


def test_points_per_game_sort_needs_minutes():
    # A one-off cameo must not top the rate table; other sorts include everyone
    _, params = ss.leaders_query("points_per_game")
    assert params["min_minutes"] == ss.PPG_MIN_MINUTES == 270
    _, params = ss.leaders_query("total_points")
    assert params["min_minutes"] == 0


def test_games_counts_appearances_not_gameweek_rows():
    query, _ = ss.leaders_query()
    assert "coalesce(st.appearances, st.games, 0) AS games" in query
    assert "coalesce(st.minutes, 0) >= $min_minutes" in query


def test_query_is_parameterised():
    query, params = ss.leaders_query("goals", "MID", "2023-24", 25)
    assert params == {"season": "2023-24", "position": "MID", "limit": 25, "min_minutes": 0,
                      "team": None, "max_price": None}
    assert "(:Season {id: $season})" in query
    assert "st.position = $position" in query      # the position that season
    assert "LIMIT $limit" in query
    assert "2023-24" not in query and "'MID'" not in query


def test_club_and_price_filters_are_parameters():
    query, params = ss.leaders_query("total_points", team="  Man City ", max_price=7.5)
    assert params["team"] == "man city" and params["max_price"] == 7.5
    assert "toLower(st.team) = $team" in query and "price <= $max_price" in query
    assert "Man City" not in query


def test_price_is_the_average_in_millions():
    row = ss.leader_rows([raw_row(price=7.4999)], include_availability=True)[0]
    assert row["price"] == 7.5
    assert ss.leader_rows([raw_row()], include_availability=True)[0]["price"] is None


def test_no_position_passes_null():
    _, params = ss.leaders_query()
    assert params["position"] is None
    assert params["season"] == CURRENT_SEASON and params["limit"] == 50


def test_api_sort_literal_matches_service():
    assert set(typing.get_args(api_main.LeaderSort)) == set(ss.LEADER_SORTS)
    assert set(typing.get_args(api_main.LeaderPosition)) == set(ss.LEADER_POSITIONS)


# --- row shaping ---------------------------------------------------------------------------------

def test_points_per_game_two_dp_and_zero_games_guard():
    rows = ss.leader_rows([raw_row(total_points=20, games=3), raw_row("B", total_points=0, games=0)],
                          include_availability=True)
    assert rows[0]["points_per_game"] == 6.67
    assert rows[1]["points_per_game"] == 0.0


def test_rows_keep_query_order_and_fill_missing_numbers():
    rows = ss.leader_rows([raw_row("A"), raw_row("B", minutes=None, bonus=None, position="GKP")],
                          include_availability=True)
    assert [r["name"] for r in rows] == ["A", "B"]
    assert rows[1]["minutes"] == 0 and rows[1]["bonus"] == 0
    assert rows[1]["position"] == "GK"


def test_current_season_keeps_availability():
    row = ss.leader_rows([raw_row()], include_availability=True)[0]
    assert row["status"] == "d"
    assert row["chance_of_playing_next_round"] == 75
    assert row["news"].startswith("Knock")


def test_past_season_has_null_availability():
    row = ss.leader_rows([raw_row()], include_availability=False)[0]
    assert row["status"] is None and row["chance_of_playing_next_round"] is None and row["news"] is None


# --- get_player_leaders ---------------------------------------------------------------------------

def test_get_player_leaders_payload_and_photo():
    conn = mock_conn()
    photo_for = MagicMock(return_value="https://img/salah.png")
    res = ss.get_player_leaders(conn, CURRENT_SEASON, "MID", "goals", 10, photo_for=photo_for)

    assert res["season"] == CURRENT_SEASON and res["sort"] == "goals" and res["position"] == "MID"
    query, params = conn.execute_query.call_args.args
    assert "ORDER BY goals DESC" in query
    assert params == {"season": CURRENT_SEASON, "position": "MID", "limit": 10, "min_minutes": 0,
                      "team": None, "max_price": None}
    player = res["players"][0]
    assert player["photo"] == "https://img/salah.png" and "_code" not in player
    photo_for.assert_called_once_with("Mohamed Salah", "Liverpool", 118748)
    assert player["status"] == "d"


def test_get_player_leaders_past_season_nulls_availability():
    res = ss.get_player_leaders(mock_conn(), PAST_SEASON)
    assert res["players"][0]["status"] is None
    assert res["players"][0]["news"] is None


def test_get_player_leaders_cached_per_params():
    conn = mock_conn()
    ss.get_player_leaders(conn, CURRENT_SEASON)
    ss.get_player_leaders(conn, CURRENT_SEASON)
    assert conn.execute_query.call_count == 1
    ss.get_player_leaders(conn, CURRENT_SEASON, sort="assists")
    assert conn.execute_query.call_count == 2
    ss.clear_cache()
    ss.get_player_leaders(conn, CURRENT_SEASON)
    assert conn.execute_query.call_count == 3


def test_get_player_leaders_adds_fpl_short_name():
    """FPL's short name comes along for display; `name` stays the full name lookups key on."""
    conn = mock_conn([raw_row(), raw_row(name="Francisco Evanilson de Lima Barbosa")])
    lookup = {"web_name": {"Francisco Evanilson de Lima Barbosa": "Evanilson"}, "code": {}}
    with patch.object(ss, "status_lookup", return_value=lookup):
        players = ss.get_player_leaders(conn, CURRENT_SEASON)["players"]
    assert [(p["name"], p["web_name"]) for p in players] == [
        ("Mohamed Salah", None), ("Francisco Evanilson de Lima Barbosa", "Evanilson")]
    # the cached rows themselves are not changed
    with patch.object(ss, "status_lookup", return_value={"web_name": {}, "code": {}}):
        again = ss.get_player_leaders(conn, CURRENT_SEASON)["players"]
    assert [p["web_name"] for p in again] == [None, None] and conn.execute_query.call_count == 1


def test_get_player_leaders_unknown_season():
    with pytest.raises(ss.NotFoundError):
        ss.get_player_leaders(mock_conn(), "1999-00")


# --- HTTP route (no lifespan: no Neo4j/LLM startup) -------------------------------------------

@pytest.fixture
def client():
    return TestClient(api_main.app)


@pytest.fixture
def conn():
    c = mock_conn()
    state = dict(api_main.app_state, neo4j_conn=c, avatar_service=None)
    with patch.dict(api_main.app_state, state):
        yield c


def test_route_defaults(client, conn):
    resp = client.get("/api/players/leaders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["season"] == CURRENT_SEASON and body["sort"] == "total_points" and body["position"] is None
    _, params = conn.execute_query.call_args.args
    assert params == {"season": CURRENT_SEASON, "position": None, "limit": 50, "min_minutes": 0,
                      "team": None, "max_price": None}
    player = body["players"][0]
    assert player["points_per_game"] == 8.3
    assert player["photo"] == api_main.FPL_PHOTO_URL.format(code=118748)   # fpl_code fallback
    assert set(player) == {"name", "web_name", "team", "position", "games", "minutes", "goals", "assists",
                           "clean_sheets", "bonus", "total_points", "points_per_game", "price", "status",
                           "chance_of_playing_next_round", "news", "photo"}


def test_route_passes_filters(client, conn):
    resp = client.get(f"/api/players/leaders?season={PAST_SEASON}&position=DEF&sort=clean_sheets&limit=5"
                      "&team=Arsenal&max_price=5.5")
    assert resp.status_code == 200
    query, params = conn.execute_query.call_args.args
    assert params == {"season": PAST_SEASON, "position": "DEF", "limit": 5, "min_minutes": 0,
                      "team": "arsenal", "max_price": 5.5}
    assert "ORDER BY clean_sheets DESC" in query
    assert resp.json()["players"][0]["status"] is None


@pytest.mark.parametrize("qs", ["sort=name", "sort=total_points%20ASC", "position=GKP", "position=mid",
                                "limit=0", "limit=101", "limit=abc", "max_price=1", "max_price=x"])
def test_route_rejects_bad_params(client, conn, qs):
    assert client.get(f"/api/players/leaders?{qs}").status_code == 422
    conn.execute_query.assert_not_called()


def test_route_unknown_season_is_404(client, conn):
    assert client.get("/api/players/leaders?season=2019-20").status_code == 404
    conn.execute_query.assert_not_called()


def test_route_neo4j_errors_are_503(client, conn):
    from neo4j.exceptions import ServiceUnavailable
    conn.execute_query.side_effect = ServiceUnavailable("down")
    assert client.get("/api/players/leaders").status_code == 503


def test_route_without_connection_is_503(client):
    with patch.dict(api_main.app_state, dict(api_main.app_state, neo4j_conn=None)):
        assert client.get("/api/players/leaders").status_code == 503


def test_clubs_for_a_season(client, conn):
    conn.execute_query.return_value = [{"team": "Arsenal"}, {"team": None}, {"team": "Man City"}]
    resp = client.get(f"/api/clubs?season={PAST_SEASON}")
    assert resp.status_code == 200 and resp.json() == {"season": PAST_SEASON, "clubs": ["Arsenal", "Man City"]}
    query, params = conn.execute_query.call_args.args
    assert params == {"season": PAST_SEASON} and "DISTINCT st.team" in query
    assert client.get("/api/clubs?season=1999-00").status_code == 404
