"""Answer cards (services/answer_cards.py) and the `cards`/`follow_ups` fields of POST /api/query.

Neo4j is faked by FakeConn, which answers each query by its text, so the card builders run
their real Cypher-shaped code paths without a database.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import answer_cards as ac  # noqa: E402
from services import season_service as ss  # noqa: E402

CURRENT = "2026-27"
PREVIOUS = "2025-26"


# ------------------------------------------------------------------ fake graph

def _ko(days):
    """Kickoff `days` after 20 Aug 2026, 14:00 UTC."""
    return datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc) + timedelta(days=days)


PLAYER_INFO = {
    "Erling Haaland": {"name": "Erling Haaland", "position": "FWD", "current_team": "Man City", "last_team": "Man City",
                       "season_team": "Man City", "season_position": "FWD", "fpl_code": 223094,
                       "status": "a", "chance": None, "news": ""},
    "Bukayo Saka": {"name": "Bukayo Saka", "position": "MID", "current_team": "Arsenal", "last_team": "Arsenal",
                    "season_team": "Arsenal", "season_position": "MID", "fpl_code": 223340,
                    "status": "d", "chance": 75, "news": "Knock - 75% chance of playing"},
    "Old Striker": {"name": "Old Striker", "position": "FWD", "current_team": None, "last_team": "Spurs",
                    "season_team": "Spurs", "season_position": "FWD", "fpl_code": None,
                    "status": None, "chance": None, "news": None},
}


def season_row(name, season, team, position, points, goals, assists, apps, games=None, minutes=None,
               clean_sheets=0, bonus=0, value=100):
    games = games if games is not None else apps
    return {"name": name, "season": season, "team": team, "position": position, "total_points": points,
            "goals": goals, "assists": assists, "clean_sheets": clean_sheets, "bonus": bonus,
            "minutes": minutes if minutes is not None else apps * 90, "saves": 0, "games": games,
            "appearances": apps, "ict_sum": apps * 8.0, "value_sum": games * value}


SEASONS = {
    "Erling Haaland": [
        season_row("Erling Haaland", PREVIOUS, "Man City", "FWD", 239, 27, 8, 34, games=38, bonus=30, value=145),
        season_row("Erling Haaland", CURRENT, "Man City", "FWD", 39, 5, 0, 5, bonus=6, value=150),
    ],
    "Bukayo Saka": [
        season_row("Bukayo Saka", PREVIOUS, "Arsenal", "MID", 180, 12, 10, 33, games=38, value=100),
        season_row("Bukayo Saka", CURRENT, "Arsenal", "MID", 32, 3, 0, 5, value=101),
    ],
}

HOME = {"Man City": [(1, "Bournemouth", True, 2, 1), (2, "Crystal Palace", False, 4, 1), (3, "Coventry City", True, 1, 0),
                     (4, "Man Utd", False, 1, 0), (5, "Sunderland", True, 5, 3)],
        "Arsenal": [(1, "Coventry City", True, 3, 0), (2, "Aston Villa", False, 1, 0), (3, "Chelsea", True, 2, 1),
                    (4, "Sunderland", False, 2, 0), (5, "Brighton", False, 0, 3)]}
POINTS = {"Erling Haaland": [(2, 0, 0), (13, 2, 0), (9, 1, 0), (9, 1, 0), (6, 1, 0)],   # points, goals, assists
          "Bukayo Saka": [(10, 1, 1), (6, 0, 1), (9, 1, 0), (5, 1, 0), (2, 0, 0)]}


def match_rows_for(name):
    team = PLAYER_INFO[name]["current_team"]
    rows = []
    for (gw, opponent, home, gf, ga), (pts, goals, assists) in zip(HOME[team], POINTS[name]):
        rows.append({"name": name, "season": CURRENT, "gameweek": gw, "kickoff_time": _ko(gw * 7),
                     "points": pts, "minutes": 90, "goals": goals, "assists": assists,
                     "clean_sheets": 1 if ga == 0 else 0, "bonus": 0, "team": team, "was_home": home,
                     "home_team": team if home else opponent, "away_team": opponent if home else team,
                     "home_score": gf if home else ga, "away_score": ga if home else gf,
                     # price rises 0.1 a week, ownership 100k; xG 0.5 a match, xA 0.25
                     "value": 150 + gw, "selected": 6_000_000 + gw * 100_000, "xg": 0.5, "xa": 0.25,
                     "xgi": 0.75, "xgc": 1.0, "goals_conceded": ga})
    # one big haul last season
    rows.append({"name": name, "season": PREVIOUS, "gameweek": 30, "kickoff_time": datetime(2026, 3, 1, tzinfo=timezone.utc),
                 "points": 21, "minutes": 90, "goals": 3, "assists": 1, "clean_sheets": 0, "bonus": 3, "team": team,
                 "was_home": True, "home_team": team, "away_team": "Leeds", "home_score": 4, "away_score": 1})
    return rows


POOL = {
    ("Erling Haaland", CURRENT): [{"name": "Erling Haaland", "total_points": 39}, {"name": "Alexander Isak", "total_points": 30},
                                  {"name": "Ollie Watkins", "total_points": 20}],
    ("Bukayo Saka", CURRENT): [{"name": "Pascal Groß", "total_points": 47}, {"name": "Bukayo Saka", "total_points": 32},
                               {"name": "Cole Palmer", "total_points": 25}],
}


def fixture_rows():
    rows = []
    for team, games in HOME.items():
        for gw, opponent, home, gf, ga in games:
            rows.append({"id": f"{team}-{gw}", "gameweek": gw, "kickoff_time": _ko(gw * 7), "status": "finished",
                         "home_team": team if home else opponent, "away_team": opponent if home else team,
                         "home_score": gf if home else ga, "away_score": ga if home else gf,
                         "home_difficulty": 3, "away_difficulty": 3})
    upcoming = [("Man City", "Ipswich", 7, 2, 4), ("Nott'm Forest", "Arsenal", 7, 3, 2),
                ("Arsenal", "Everton", 8, 2, 3), ("Leeds", "Arsenal", 9, 3, 2), ("Arsenal", "Fulham", 10, 2, 3)]
    for i, (home, away, gw, hd, ad) in enumerate(upcoming):
        rows.append({"id": f"up-{i}", "gameweek": gw, "kickoff_time": _ko(42 + i), "status": "scheduled",
                     "home_team": home, "away_team": away, "home_score": None, "away_score": None,
                     "home_difficulty": hd, "away_difficulty": ad})
    return rows


class FakeConn:
    """execute_query answers by the query text; unknown queries return []."""

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def execute_query(self, query, params=None):
        params = params or {}
        self.calls.append((query, params))
        if query in self.fail:
            raise RuntimeError("Neo4j unavailable")
        if query == ac.PLAYERS_QUERY:
            return [PLAYER_INFO[n] for n in params["names"] if n in PLAYER_INFO]
        if query == ac.PLAYER_SEASONS_QUERY:
            return [r for n in params["names"] for r in SEASONS.get(n, [])]
        if query == ac.PLAYER_MATCHES_QUERY:
            return [r for n in params["names"] if n in POINTS for r in match_rows_for(n)]
        if query == ac.POSITION_POOL_QUERY:
            return POOL.get((params["name"], params["season"]), [])
        if query == ac.TEAM_PLAYERS_QUERY:
            return [{"name": "Bukayo Saka", "position": "MID", "total_points": 32, "goals": 3, "assists": 0,
                     "clean_sheets": 1, "games": 5, "current_team": "Arsenal", "last_team": "Arsenal", "fpl_code": 223340},
                    {"name": "David Raya Martín", "position": "GK", "total_points": 30, "goals": 0, "assists": 0,
                     "clean_sheets": 3, "games": 5, "current_team": "Arsenal", "last_team": "Arsenal", "fpl_code": 154561}]
        if query == ss.FIXTURES_QUERY:
            return fixture_rows()
        if query == ss.INJURIES_QUERY:
            return [{"name": "William Saliba", "team": "Arsenal", "position": "DEF", "status": "i",
                     "chance_of_playing_next_round": 0, "news": "Back injury - Unknown return date",
                     "news_added": None, "fpl_code": 462424}]
        if "LIMIT $limit" in query and "points_per_game" in query and "st.position = $position" in query:  # leaders
            return [{"name": "Erling Haaland", "team": "Man City", "position": "FWD", "games": 34, "minutes": 2900,
                     "goals": 27, "assists": 8, "clean_sheets": 0, "bonus": 30, "total_points": 239,
                     "points_per_game": 7.0, "status": "a", "chance_of_playing_next_round": None, "news": None,
                     "fpl_code": 223094}]
        return []


def predictions_df():
    rows = [
        ("Erling Haaland", "FWD", "Man City", 8.58, 156, "Ipswich", True, 411),
        ("Bukayo Saka", "MID", "Arsenal", 4.62, 101, "Nott'm Forest", False, 12),
        ("Bruno Borges Fernandes", "MID", "Man Utd", 4.8, 119, "Leeds & Spurs", False, 300),
        ("Pascal Groß", "MID", "Brighton", 3.1, 56, "Fulham", True, 77),
        ("William Saliba", "DEF", "Arsenal", 0.0, 60, "Nott'm Forest", False, 5),
    ]
    df = pd.DataFrame(rows, columns=["name", "position", "team", "predicted_points", "value", "opponent",
                                     "is_home", "element"])
    df["gameweek"] = 7
    df["kickoff_time"] = "2026-10-17T14:00:00Z"
    df["prediction_method"] = "xgboost_v3"
    df["status"] = ["a", "d", "a", "a", "i"]
    df["chance_of_playing_next_round"] = [None, 75, None, None, 0]
    df["news"] = [None, "Knock - 75% chance of playing", None, None, "Back injury"]
    df["name_lower"] = df["name"].str.lower()
    return df


def photo_for(name, team, code):
    return f"https://photos.test/{code}.png" if code else None


def context(intent, method=None, params=None, entities=None, results=None, question="question", conn=None,
            predictions="default"):
    return ac.CardContext(
        question=question, intent=intent, method=method, params=params or {},
        entities={"players": [], "teams": [], "positions": [], **(entities or {})},
        results=results or [], conn=conn or FakeConn(), current_season=CURRENT, photo_for=photo_for,
        predictions=predictions_df() if predictions == "default" else predictions,
        photo_codes={411: 223094, 12: 223340}, web_names={"Bruno Borges Fernandes": "B.Fernandes"},
        meta={"next_gameweek": 7, "next_deadline": "2026-10-17T10:00:00Z"},
    )


def build(ctx):
    return asyncio.run(ac.build_cards(ctx))


@pytest.fixture(autouse=True)
def fresh_cache():
    ss.clear_cache()
    yield
    ss.clear_cache()


# ------------------------------------------------------------------ pure helpers

def test_competition_ranks_share_places():
    assert ac.competition_ranks([22, 22, 20, 19, 19]) == [1, 1, 3, 4, 4]


def test_short_name_prefers_web_name_and_keeps_particles():
    assert ac.short_name("Bruno Borges Fernandes", {"Bruno Borges Fernandes": "B.Fernandes"}) == "B.Fernandes"
    assert ac.short_name("Kevin De Bruyne") == "De Bruyne"
    assert ac.short_name("Virgil van Dijk") == "van Dijk"
    assert ac.short_name("Mohamed Salah") == "Salah"
    assert ac.short_name("Rodri") == "Rodri"


def test_match_rows_take_the_players_side():
    rows = ac.match_rows([
        {"season": CURRENT, "gameweek": 2, "kickoff_time": _ko(2), "points": 13, "minutes": 90, "goals": 2,
         "team": "Man City", "was_home": False, "home_team": "Crystal Palace", "away_team": "Man City",
         "home_score": 1, "away_score": 4},
        # no was_home: derived from the player's team
        {"season": CURRENT, "gameweek": 1, "kickoff_time": _ko(1), "points": 2, "minutes": 90,
         "team": "Man City", "was_home": None, "home_team": "Man City", "away_team": "Bournemouth",
         "home_score": 1, "away_score": 1},
    ])
    assert [r["gameweek"] for r in rows] == [1, 2]                        # oldest first
    assert rows[0]["home"] is True and rows[0]["opponent"] == "Bournemouth" and rows[0]["result"] == "D"
    assert rows[1]["home"] is False and rows[1]["opponent"] == "Crystal Palace"
    assert (rows[1]["team_score"], rows[1]["opponent_score"], rows[1]["result"]) == (4, 1, "W")
    assert ac.form_point(rows[1])["score"] == "4-1"


def test_price_trend_counts_a_double_gameweek_once():
    rows = [{"season": CURRENT, "gameweek": gw, "kickoff_time": _ko(i), "value": value, "selected": sel}
            for i, (gw, value, sel) in enumerate([(1, 150, 6_000_000), (2, 151, 6_200_000), (2, 151, 6_250_000),
                                                  (3, 153, 6_500_000)])]
    rows.append({"season": PREVIOUS, "gameweek": 38, "kickoff_time": _ko(-30), "value": 140, "selected": 1})
    trend = ac.price_trend(ac.match_rows(rows), CURRENT)
    assert [p["gameweek"] for p in trend["points"]] == [1, 2, 3]
    assert trend["points"][1] == {"gameweek": 2, "price": 15.1, "selected": 6_250_000}   # last match of the GW
    assert trend["price_change"] == 0.3 and trend["selected_change"] == 500_000
    assert ac.price_trend(ac.match_rows(rows[:1]), CURRENT) is None                       # one gameweek
    assert ac.price_trend(ac.match_rows(rows), None) is None


def test_underlying_stats_sums_expected_goals_per_90():
    matches = ac.match_rows(match_rows_for("Erling Haaland"))
    xs = ac.underlying_stats(matches, CURRENT)
    assert (xs["xg"], xs["xa"], xs["xgi"], xs["goals"], xs["assists"], xs["minutes"]) == (2.5, 1.25, 3.75, 5, 0, 450)
    assert xs["xg_per90"] == 0.5 and xs["xgi_per90"] == 0.75
    # the previous season's row has no expected stats
    assert ac.underlying_stats(matches, PREVIOUS) is None


def test_finishing_highlight_compares_goals_with_xg():
    matches = ac.match_rows(match_rows_for("Erling Haaland"))
    xs = dict(ac.underlying_stats(matches, CURRENT), goals=5, xg=2.5)
    out = ac.player_highlights(name="Erling Haaland", position="FWD", seasons=[], matches=[], scope=CURRENT,
                               rank=None, rank_season=None, totals=ac.season_totals([]), current=CURRENT,
                               underlying=xs)
    assert out[0]["label"] == "Finishing" and out[0]["value"] == "+2.5 v xG" and out[0]["tone"] == "accent"
    under = ac.player_highlights(name="X", position="FWD", seasons=[], matches=[], scope=CURRENT, rank=None,
                                 rank_season=None, totals=ac.season_totals([]), current=CURRENT,
                                 underlying=dict(xs, goals=1, xg=3.0))
    assert under[0]["value"] == "-2.0 v xG" and under[0]["tone"] == "warning"


def test_position_rank_and_benchmark_rival():
    pool = POOL[("Bukayo Saka", CURRENT)]
    assert ac.position_rank(pool, "Bukayo Saka") == (2, 3)
    assert ac.position_rank(pool, "Nobody") is None
    assert ac.rival_in_pool(pool, "Bukayo Saka") == "Pascal Groß"       # the leader
    assert ac.rival_in_pool(pool, "Pascal Groß") == "Bukayo Saka"       # the leader gets the runner-up


def test_player_highlights_streaks_rank_and_haul():
    matches = ac.match_rows(match_rows_for("Erling Haaland"))
    totals = ac.season_totals(SEASONS["Erling Haaland"][1:])
    out = ac.player_highlights(name="Erling Haaland", position="FWD", seasons=SEASONS["Erling Haaland"],
                               matches=matches, scope=CURRENT, rank=(1, 50), rank_season=CURRENT,
                               totals=totals, current=CURRENT)
    labels = [h["label"] for h in out]
    assert labels[0] == "Scoring streak" and out[0]["value"] == "4 games"
    assert "FWD rank" in labels and out[labels.index("FWD rank")]["tone"] == "accent"
    haul = out[labels.index("Biggest haul")]
    assert haul["value"] == "13 pts" and "Crystal Palace (A)" in haul["detail"]  # this season's scope only
    assert len(out) <= ac.MAX_HIGHLIGHTS


def test_player_highlights_blank_run_is_a_warning():
    matches = [{"season": CURRENT, "gameweek": gw, "kickoff": None, "points": pts, "minutes": 90, "goals": 0,
                "assists": 0, "clean_sheets": 0, "bonus": 0, "opponent": "X", "home": True, "team_score": 0,
                "opponent_score": 1, "result": "L"} for gw, pts in enumerate([8, 1, 2, 1], start=1)]
    out = ac.player_highlights(name="A", position="MID", seasons=[], matches=matches, scope=CURRENT, rank=None,
                               rank_season=None, totals=ac.season_totals([]), current=CURRENT)
    blank = next(h for h in out if h["label"] == "Blank run")
    assert blank["value"] == "3 games" and blank["tone"] == "warning"


def test_comparison_metrics_mark_the_better_side():
    totals = [ac.season_totals(SEASONS["Erling Haaland"][1:]), ac.season_totals(SEASONS["Bukayo Saka"][1:])]
    metrics = {m["key"]: m for m in ac.comparison_metrics(totals, ["FWD", "MID"])}
    assert metrics["total_points"]["values"] == [39, 32] and metrics["total_points"]["better"] == 0
    assert metrics["points_per_game"]["values"] == [7.8, 6.4]
    assert metrics["avg_price"]["better"] is None and metrics["avg_price"]["scored"] is False
    assert metrics["avg_price"]["values"] == [15.0, 10.1]
    # two forwards: clean sheets don't score for them, so the row is left out
    assert "clean_sheets" not in {m["key"] for m in ac.comparison_metrics(totals, ["FWD", "FWD"])}


def test_prediction_for_ranks_and_exact_opponents():
    fixtures = ss.clean_fixture_rows(fixture_rows())
    pred = ac.prediction_for(predictions_df(), "erling haaland".title(), fixtures)
    assert pred["points"] == 8.6 and pred["gameweek"] == 7
    assert pred["rank"] == 1 and pred["pool"] == 5 and pred["position_rank"] == 1 and pred["position_pool"] == 1
    assert pred["opponents"] == [{"team": "Ipswich", "home": True, "difficulty": 2,
                                  "kickoff_time": "2026-10-01T14:00:00+00:00", "gameweek": 7}]
    assert ac.prediction_for(predictions_df(), "Retired Player", fixtures) is None


def test_prediction_opponents_fall_back_to_the_file_text():
    # no fixtures in the graph: "Leeds & Spurs" from the file, both away like the first fixture
    pred = ac.prediction_for(predictions_df(), "Bruno Borges Fernandes", [])
    assert [(o["team"], o["home"], o["difficulty"]) for o in pred["opponents"]] == [("Leeds", False, None), ("Spurs", False, None)]


def test_leaderboard_spec_titles_and_columns():
    spec = ac.leaderboard_spec("get_top_points_by_position", {"sort_by": "goals", "position": "MID"})
    assert spec["title"] == "Top scorers" and spec["metric"]["key"] == "goals"
    spec = ac.leaderboard_spec("get_top_points_by_position", {"position": "DEF"})
    assert spec["title"] == "Top defenders" and spec["metric"]["key"] == "total_points"
    assert ac.leaderboard_spec("get_most_transferred_players", {"direction": "out"})["title"] == "Most transferred out"
    assert ac.leaderboard_spec("get_best_value_players", {})["metric"]["format"] == "dec1"
    assert ac.leaderboard_spec("get_team_top_performers", {"team_name": "Arsenal"})["title"] == "Arsenal top performers"
    assert ac.leaderboard_spec("compare_players", {}) is None
    assert ac.leaderboard_subtitle("get_top_points_by_position", {"sort_by": "goals", "position": "MID", "season": CURRENT},
                                   CURRENT) == "Midfielders · 2026-27 so far"


def _leader_rows(values, team="Arsenal"):
    return [{"player_name": f"Player {i}", "goals": v, "total_points": 100 - i, "assists": 1, "team": team,
             "position": "MID"} for i, v in enumerate(values)]


def test_leaderboard_joint_leaders_and_club_cluster():
    card = ac.leaderboard_card("get_top_points_by_position", {"sort_by": "goals", "season": "2022-23"},
                               _leader_rows([20, 20, 18, 15, 12, 11]), {}, photo=photo_for)
    assert [r["rank"] for r in card["rows"]] == [1, 1, 3, 4, 5, 6]
    labels = {h["label"]: h for h in card["highlights"]}
    assert labels["Joint leaders"]["detail"] == "0, 1 on 20 goals"
    assert labels["Club cluster"]["value"] == "6 of 6"
    # past season: no availability on the rows
    assert card["rows"][0]["player"]["status"] is None


def test_leaderboard_margin_singular_unit_and_nothing_to_rank():
    card = ac.leaderboard_card("get_clean_sheet_leaders", {"season": CURRENT},
                               [{"player_name": "A B", "total_clean_sheets": 4}, {"player_name": "C D", "total_clean_sheets": 3}],
                               {}, photo=photo_for)
    assert card["highlights"][0]["detail"] == "B leads D by 1 clean sheet"
    assert ac.leaderboard_card("get_most_transferred_players", {"season": CURRENT},
                               [{"player_name": "A", "transfers": 0}, {"player_name": "B", "transfers": 0}], {},
                               photo=photo_for) is None


def test_grouped_leaderboard_keeps_five_per_position():
    rows = [{"player_name": f"{pos} {i}", "position": pos, "total_points": 100 - i}
            for pos in ("GK", "DEF") for i in range(7)]
    card = ac.leaderboard_card("get_top_players_all_positions", {}, rows, {}, photo=photo_for)
    assert card["grouped"] is True and len(card["rows"]) == 10
    assert {r["group"] for r in card["rows"]} == {"GK", "DEF"} and card["rows"][0]["rank"] is None
    assert card["highlights"] == []


def test_team_highlights_streak_and_fixture_run():
    results = [{"result": r, "goals_against": ga} for r, ga in (("L", 2), ("W", 0), ("W", 0), ("W", 1))]
    upcoming = [{"difficulty": d} for d in (2, 2, 3)]
    out = ac.team_highlights("Arsenal", results, None, [], upcoming, [], ac.short_name)
    assert out[0]["value"] == "Won 3 in a row"
    assert any(h["label"] == "Clean sheets" and h["value"] == "2 in 4" for h in out)
    assert any(h["label"] == "Next 3 fixtures" and h["value"] == "FDR 2.3" for h in out)


def test_head_to_head_card_record_and_unbeaten_run():
    raw = [{"season": s, "gameweek": gw, "home_team": h, "away_team": a, "home_score": hs, "away_score": as_}
           for s, gw, h, a, hs, as_ in [
               ("2021-22", 2, "Arsenal", "Chelsea", 0, 2), ("2022-23", 10, "Chelsea", "Arsenal", 0, 1),
               ("2023-24", 34, "Arsenal", "Chelsea", 5, 0), ("2024-25", 11, "Chelsea", "Arsenal", 1, 1),
               ("2025-26", 3, "Arsenal", "Chelsea", 2, 1)]]
    card = ac.fixtures_card(["Arsenal", "Chelsea"], raw, None, CURRENT)
    assert card["record"] == {"team": "Arsenal", "played": 5, "won": 3, "drawn": 1, "lost": 1, "goals_for": 9, "goals_against": 4}
    assert card["matches"][0]["season"] == "2025-26"                  # newest first
    labels = {h["label"]: h for h in card["highlights"]}
    assert labels["Biggest win"]["value"] == "Arsenal 5-0 Chelsea"
    assert labels["Best Chelsea win"]["value"] == "Arsenal 0-2 Chelsea"
    assert labels["Last meeting"]["detail"] == "GW 3, 2025-26"
    assert labels["Unbeaten run"]["value"] == "4 meetings"
    assert card["subtitle"] == "Head to head · 2021-22 to 2025-26"


@pytest.mark.parametrize("question,intent", [
    ("Erling Haaland stats this season", "player_stats"),
    ("Erling Haaland stats in 2025-26", "player_stats"),
    # what tapping a player on a card sends (src/components/assistant/cards/shared.tsx)
    ("Erling Haaland stats in 2026-27", "player_stats"),
    ("Erling Haaland stats", "player_stats"),
    ("Predicted points for Erling Haaland next gameweek", "prediction"),
    ("Compare Erling Haaland and Bukayo Saka this season", "player_comparison"),
    ("Top 5 midfielders by points this season", "top_points"),
    ("Top goal scorers in 2025-26", "top_scorers"),
    ("Predicted points for forwards next gameweek", "prediction"),
    ("Who should I captain this gameweek?", "prediction"),
    ("Show Arsenal match results this season", "fixture_results"),
    ("Best players at Arsenal this season", "team_analysis"),
    ("How is Arsenal doing this season?", "team_analysis"),
    ("Head to head Arsenal vs Chelsea", "head_to_head"),
])
def test_follow_up_templates_route_to_their_intent(question, intent):
    assert ac.follow_up_questions("something else", [(question, intent)]) == [question]


def test_follow_ups_drop_misrouted_repeated_and_asked_questions():
    out = ac.follow_up_questions("Top goal scorers this season", [
        ("Top goal scorers this season", "top_scorers"),        # the question just asked
        ("Erling Haaland stats", "prediction"),                 # routes to player_stats, not prediction
        ("Erling Haaland stats", "player_stats"),
        ("erling haaland stats", "player_stats"),               # duplicate
    ])
    assert out == ["Erling Haaland stats"]


def test_only_teams_the_extractor_knows_get_follow_ups(monkeypatch):
    from preprocessing.entity_extractor import EntityExtractor
    monkeypatch.delattr(EntityExtractor, "find_teams", raising=False)  # the substring scan
    monkeypatch.setattr(EntityExtractor, "TEAMS", {
        "arsenal": "Arsenal", "man city": "Man City", "city": "Man City",
        "nott'm forest": "Nott'm Forest", "forest": "Nott'm Forest",
    })
    assert ac.askable_team("Arsenal") and ac.askable_team("Man City") and ac.askable_team("Nott'm Forest")
    assert not ac.askable_team("Ipswich")          # not in the extractor's list
    assert not ac.askable_team("Coventry City")    # "city" would be read as Man City


def test_askable_team_uses_whole_word_matching_when_the_extractor_has_it(monkeypatch):
    from preprocessing.entity_extractor import EntityExtractor
    found = {"Coventry City": ["Coventry City"], "Leeds": ["Leeds"], "Ipswich": []}
    monkeypatch.setattr(EntityExtractor, "find_teams", classmethod(lambda cls, text: found[text]), raising=False)
    assert ac.askable_team("Coventry City") and ac.askable_team("Leeds")
    assert not ac.askable_team("Ipswich")


# ------------------------------------------------------------------ build_cards (fake Neo4j)

def test_player_stats_card():
    ctx = context("player_stats", "get_player_season_stats", {"player_name": "Erling Haaland", "season": CURRENT},
                  {"players": ["Erling Haaland"]}, question="Erling Haaland stats this season")
    cards, follow_ups = build(ctx)
    assert len(cards) == 1
    card = cards[0]
    assert card["type"] == "player" and card["season"] == CURRENT and card["season_label"] == "2026-27 so far"
    assert card["player"] == {"name": "Erling Haaland", "team": "Man City", "position": "FWD",
                              "photo": "https://photos.test/223094.png", "status": "a", "chance": None, "news": None}
    stats = {s["key"]: s["value"] for s in card["stats"]}
    assert stats["total_points"] == 39 and stats["goals"] == 5 and stats["points_per_game"] == 7.8
    assert [f["points"] for f in card["form"]] == [2, 13, 9, 9, 6]       # this season only, oldest first
    assert card["prediction"]["points"] == 8.6 and card["prediction"]["opponents"][0]["difficulty"] == 2
    assert [(f["team"], f["home"], f["difficulty"]) for f in card["next_fixtures"]] == [("Ipswich", True, 2)]
    assert card["trend"]["price_change"] == 0.4 and len(card["trend"]["points"]) == 5
    assert card["underlying"]["xg"] == 2.5
    assert card["highlights"][0]["label"] == "Scoring streak"
    assert "_follow_ups" not in card
    assert "Compare Erling Haaland and Alexander Isak this season" in follow_ups
    assert "Predicted points for Erling Haaland next gameweek" in follow_ups
    json.dumps(cards)  # JSON-safe: no Neo4j/numpy/NaN values


def test_all_seasons_player_card_has_season_bars():
    ctx = context("player_stats", "get_player_all_seasons_stats", {"player_name": "Bukayo Saka"},
                  {"players": ["Bukayo Saka"]})
    card = build(ctx)[0][0]
    assert card["season"] is None and card["season_label"] == "All seasons"
    assert [s["season"] for s in card["seasons"]] == [PREVIOUS, CURRENT]
    assert card["player"]["status"] == "d" and card["player"]["chance"] == 75
    assert {s["key"]: s["value"] for s in card["stats"]}["total_points"] == 212


def test_gameweek_question_includes_that_match():
    ctx = context("player_stats", "get_player_gameweek_performance",
                  {"player_name": "Erling Haaland", "season": CURRENT, "gameweek": 2}, {"players": ["Erling Haaland"]})
    card = build(ctx)[0][0]
    assert card["match"]["gameweek"] == 2 and card["match"]["points"] == 13 and card["match"]["score"] == "4-1"


def test_comparison_card():
    ctx = context("player_comparison", "compare_players", {"player1": "Erling Haaland", "player2": "Bukayo Saka",
                                                           "season": CURRENT},
                  {"players": ["Erling Haaland", "Bukayo Saka"]})
    cards, follow_ups = build(ctx)
    card = cards[0]
    assert card["type"] == "comparison" and [p["name"] for p in card["players"]] == ["Erling Haaland", "Bukayo Saka"]
    assert card["leads"][0] > card["leads"][1]
    assert [p["points"] for p in card["predictions"]] == [8.6, 4.6]
    assert [len(f) for f in card["form"]] == [5, 5]
    keys = [m["key"] for m in card["metrics"]]
    assert keys.index("xg") == keys.index("assists") + 1 and "xa" in keys      # one season: expected stats
    assert [[f["team"] for f in side] for side in card["next_fixtures"]] == [["Ipswich"], ["Nott'm Forest", "Everton", "Leeds"]]
    assert any(h["label"] == "Next GW" for h in card["highlights"])
    assert "Compare Erling Haaland and Bukayo Saka in 2025-26" in follow_ups


def test_comparison_with_one_unknown_player_falls_back_to_a_player_card():
    ctx = context("player_comparison", "compare_players", {}, {"players": ["Erling Haaland", "Nobody Known"]})
    assert build(ctx)[0][0]["type"] == "player"


def test_leaderboard_card_with_photos_and_availability():
    results = [{"player_name": "Erling Haaland", "position": "FWD", "team": "Man City", "goals": 5, "total_points": 39,
                "assists": 0},
               {"player_name": "Bukayo Saka", "position": "MID", "team": "Arsenal", "goals": 3, "total_points": 32,
                "assists": 0}]
    ctx = context("top_scorers", "get_top_points_by_position", {"sort_by": "goals", "season": CURRENT}, results=results)
    card = build(ctx)[0][0]
    assert card["type"] == "leaderboard" and card["title"] == "Top scorers"
    assert card["rows"][0]["player"]["photo"] == "https://photos.test/223094.png"
    assert card["rows"][1]["player"]["status"] == "d"                  # current season: availability shown
    assert card["rows"][0]["values"] == {"total_points": 39, "assists": 0}


def test_prediction_card_for_captain_question():
    ctx = context("prediction", None, {}, question="Who should I captain this gameweek?")
    cards, follow_ups = build(ctx)
    card = cards[0]
    assert card["type"] == "prediction" and card["title"] == "Captain picks" and card["captain"] is True
    assert card["gameweek"] == 7 and card["deadline"] == "2026-10-17T10:00:00Z" and card["model"] == "XGBoost v3"
    assert [r["player"]["name"] for r in card["rows"]] == ["Erling Haaland", "Bruno Borges Fernandes", "Bukayo Saka",
                                                          "Pascal Groß", "William Saliba"]
    top = card["rows"][0]
    assert top["price"] == 15.6 and top["player"]["photo"] == "https://photos.test/223094.png"
    assert top["opponents"][0]["team"] == "Ipswich" and top["opponents"][0]["difficulty"] == 2
    labels = {h["label"]: h for h in card["highlights"]}
    assert labels["Top pick"]["detail"] == "Haaland ahead of B.Fernandes"
    assert labels["Double gameweek"]["value"] == "B.Fernandes"
    assert "Who should I captain this gameweek?" not in follow_ups


def test_position_prediction_card_filters_rows():
    ctx = context("prediction", None, {}, {"positions": ["MID"]}, question="Best midfielders for next gameweek")
    card = build(ctx)[0][0]
    assert card["position"] == "MID" and card["title"] == "Predicted midfielders"
    assert {r["player"]["position"] for r in card["rows"]} == {"MID"}


def test_prediction_for_one_and_two_players():
    one = build(context("prediction", None, {}, {"players": ["Erling Haaland"]}))[0][0]
    assert one["type"] == "player" and one["emphasis"] == "prediction" and one["season"] == CURRENT
    two = build(context("prediction", None, {}, {"players": ["Erling Haaland", "Bukayo Saka"]}))[0][0]
    assert two["type"] == "comparison" and two["emphasis"] == "prediction" and two["season"] == CURRENT


def test_no_predictions_file_means_no_prediction_card():
    assert build(context("prediction", None, {}, predictions=None)) == ([], [])


def test_team_analysis_gives_team_and_leaderboard_cards():
    results = [{"player_name": "Bukayo Saka", "total_points": 32, "goals": 3, "assists": 0}]
    ctx = context("team_analysis", "get_team_top_performers", {"team_name": "Arsenal", "season": CURRENT},
                  {"teams": ["Arsenal"]}, results, question="How is Arsenal doing this season?")
    cards, follow_ups = build(ctx)
    assert [c["type"] for c in cards] == ["team", "leaderboard"]
    team = cards[0]
    assert team["standing"]["position"] == 2 and team["standing"]["points"] == 12   # Man City won all 5
    assert [f["result"] for f in team["form"]] == ["W", "W", "W", "W", "L"]
    assert [f["team"] for f in team["next_fixtures"]] == ["Nott'm Forest", "Everton", "Leeds", "Fulham"]
    assert team["next_fixtures"][0] == {"team": "Nott'm Forest", "home": False, "difficulty": 2,
                                        "kickoff_time": "2026-10-02T14:00:00+00:00", "gameweek": 7}
    assert team["flagged"][0]["name"] == "William Saliba" and team["flagged_total"] == 1
    assert team["top_players"][0]["player"]["photo"] == "https://photos.test/223340.png"
    assert "Show Arsenal match results this season" in follow_ups
    # one club's list: no "club cluster" highlight
    assert all(h["label"] != "Club cluster" for h in cards[1]["highlights"])


def test_team_not_in_this_season_gets_no_team_card():
    ctx = context("team_analysis", "get_team_top_performers", {"team_name": "Burnley", "season": CURRENT},
                  {"teams": ["Burnley"]}, [])
    assert build(ctx) == ([], [])


def test_fixture_results_card_then_team_card():
    raw = [{"season": CURRENT, "gameweek": 1, "home_team": "Arsenal", "away_team": "Coventry City",
            "home_score": 3, "away_score": 0, "kickoff_time": "2026-08-22T14:00:00Z"}]
    ctx = context("fixture_results", "get_fixture_results", {"team_name": "Arsenal", "season": CURRENT},
                  {"teams": ["Arsenal"]}, raw)
    cards, _ = build(ctx)
    assert [c["type"] for c in cards] == ["fixtures", "team"]
    assert cards[0]["record"]["won"] == 1 and cards[0]["matches"][0]["result"] == "W"


def test_season_summary_card():
    row = {"season": PREVIOUS, "total_gameweeks": 38, "total_fixtures": 380, "total_goals": 1060,
           "total_fpl_points": 50000, "total_players": 700, "goals_per_game": 2.79}
    cards, follow_ups = build(context("season_summary", "get_season_summary", {"season": PREVIOUS}, results=[row]))
    card = cards[0]
    assert card["type"] == "season" and card["in_progress"] is False
    assert {s["key"]: s["value"] for s in card["stats"]}["goals_per_game"] == 2.79
    assert card["leaders"][0]["player"]["name"] == "Erling Haaland"
    assert card["highlights"][0]["label"] == "Top scorer"
    assert follow_ups[0] == "Top goal scorers in 2025-26"


def test_general_question_only_shows_players_named_in_it():
    named = context("general_question", None, {}, {"players": ["Erling Haaland"]}, question="Is Haaland worth it?")
    assert build(named)[0][0]["type"] == "player"
    fuzzy = context("general_question", None, {}, {"players": ["Erling Haaland"]}, question="Who wins the league?")
    assert build(fuzzy) == ([], [])


def test_a_failing_lookup_skips_that_card_only():
    conn = FakeConn(fail={ac.TEAM_PLAYERS_QUERY})
    results = [{"player_name": "Bukayo Saka", "total_points": 32, "goals": 3, "assists": 0}]
    ctx = context("team_analysis", "get_team_top_performers", {"team_name": "Arsenal", "season": CURRENT},
                  {"teams": ["Arsenal"]}, results, conn=conn)
    cards, _ = build(ctx)
    assert [c["type"] for c in cards] == ["leaderboard"]


def test_unknown_intent_without_entities_has_no_cards():
    assert build(context("unknown", None)) == ([], [])


# ------------------------------------------------------------------ best XI (team of the week)

def sample_team(mode="predicted"):
    predicted = mode == "predicted"

    def p(name, pos, team, pts, cost, captain=False, vice=False, status="a", news=None):
        return {"name": name, "team": team, "position": pos, "points": pts, "cost": cost if predicted else None,
                "captain": captain, "vice_captain": vice, "opponents": [{"team": "Ipswich", "home": True}],
                "status": status, "chance_of_playing_next_round": 75 if status == "d" else None, "news": news,
                "photo": None}

    starters = [p("Keeper A", "GK", "Arsenal", 4.1, 5.5), p("Def B", "DEF", "Arsenal", 4.5, 6.0),
                p("Def C", "DEF", "Arsenal", 4.0, 5.0), p("Def D", "DEF", "Chelsea", 3.9, 4.5),
                p("Mid E", "MID", "Liverpool", 6.0, 12.5, vice=True), p("Mid F", "MID", "Man Utd", 5.5, 9.0),
                p("Mid G", "MID", "Brighton", 5.0, 6.5, status="d", news="Knock - 75% chance of playing"),
                p("Mid H", "MID", "Spurs", 4.8, 7.0), p("Erling Haaland", "FWD", "Man City", 8.6, 15.5, captain=True),
                p("Fwd J", "FWD", "Newcastle", 5.2, 8.0), p("Fwd K", "FWD", "Leeds", 4.9, 6.0)]
    bench = [p("Keeper L", "GK", "Everton", 3.0, 4.0), p("Def M", "DEF", "Fulham", 2.5, 4.0),
             p("Mid N", "MID", "Wolves", 2.0, 4.5), p("Fwd O", "FWD", "Burnley", 1.9, 4.5)]
    return {"mode": mode, "gameweek": 7 if predicted else 5, "formation": "3-4-3", "total_points": 65.1,
            "total_cost": 99.5 if predicted else None, "budget": 100.0 if predicted else None,
            "starters": starters, "bench": bench if predicted else []}


@pytest.mark.parametrize("question", [
    "Team of the week", "Best XI for next gameweek", "Best team for next gameweek", "Pick me a team for gameweek 7",
    "Build me the best squad", "Dream team for gameweek 5", "What was the dream team last gameweek?",
])
def test_best_xi_questions_get_their_own_intent(question):
    assert ac._intent_of(question) == "team_of_the_week"


@pytest.mark.parametrize("question", [
    "Who should I captain this gameweek?", "Best midfielders for next gameweek", "Which is the best team in the league?",
    "Who should I pick for my team?", "How is Arsenal doing this season?",
])
def test_other_questions_keep_their_intent(question):
    assert ac._intent_of(question) != "team_of_the_week"


@pytest.mark.parametrize("question,gameweek,expected", [
    ("Best team for next gameweek", None, ("predicted", None)),
    ("Team of the week for gameweek 5", 5, ("actual", 5)),        # a finished gameweek
    ("Team of the week for gameweek 9", 9, ("predicted", None)),   # not played yet
    ("Dream team", None, ("actual", 6)),                           # the latest finished one
    ("What was the team of the week last gameweek?", None, ("actual", 6)),
])
def test_team_of_week_target(question, gameweek, expected):
    assert ss.team_of_week_target(question, gameweek, [1, 2, 3, 4, 5, 6]) == expected


def test_no_finished_gameweek_means_the_predicted_team():
    assert ss.team_of_week_target("Dream team", None, []) == ("predicted", None)


def test_team_of_week_context_for_the_prompt():
    text = ss.team_of_week_context(sample_team())
    assert text.startswith("Predicted best FPL squad for gameweek 7")
    assert "- Erling Haaland (FWD, Man City, £15.5m) (captain): 8.6 predicted pts vs Ipswich (H)" in text
    assert "[FPL availability: d: Knock - 75% chance of playing]" in text
    assert "Bench:" in text and text.endswith("Total predicted points: 65.1 (captain counted twice); squad cost £99.5m.")
    dream = ss.team_of_week_context(sample_team("actual"))
    assert dream.startswith("Dream team for gameweek 5") and "(captain)" in dream and "Bench:" not in dream


def test_pitch_card_from_a_predicted_squad():
    card = ac.pitch_card(sample_team(), finished_gameweeks=[1, 2, 3, 4, 5, 6])
    assert card["type"] == "pitch" and card["mode"] == "predicted" and card["formation"] == "3-4-3"
    assert len(card["starters"]) == 11 and len(card["bench"]) == 4
    captain = next(s for s in card["starters"] if s["captain"])
    assert captain["player"]["name"] == "Erling Haaland" and captain["cost"] == 15.5
    labels = [h["label"] for h in card["highlights"]]
    assert labels == ["Captain", "Fitness doubt", "Most picked club", "In the bank"]
    assert card["highlights"][2]["value"] == "Arsenal" and card["highlights"][3]["value"] == "£0.5m"
    assert ("Dream team for gameweek 6", "team_of_the_week") in card["_follow_ups"]


def test_pitch_card_for_a_dream_team_has_no_armbands_or_availability():
    card = ac.pitch_card(sample_team("actual"), finished_gameweeks=[1, 2, 3, 4, 5, 6])
    assert not any(s["captain"] or s["vice_captain"] for s in card["starters"])
    assert all(s["player"]["status"] is None for s in card["starters"]) and card["bench"] == []
    assert card["highlights"][0]["label"] == "Star of the week" and card["highlights"][0]["value"] == "Haaland"
    assert ("Dream team for gameweek 4", "team_of_the_week") in card["_follow_ups"]


def test_build_pitch_card_reads_the_team_of_the_week():
    with patch.object(ss, "get_team_of_the_week", return_value=sample_team()) as totw:
        cards, follow_ups = build(context("team_of_the_week", None, {"team_mode": "predicted", "gameweek": 7}))
    assert totw.call_args.args[1:3] == ("predicted", None)
    assert [c["type"] for c in cards] == ["pitch"]
    assert "Who should I captain this gameweek?" in follow_ups


# ------------------------------------------------------------------ POST /api/query

def _app_state(conn):
    import api_main
    from preprocessing.intent_classifier import IntentClassifier
    from preprocessing.entity_extractor import EntityExtractor
    from services.player_index import PlayerIndex

    index = PlayerIndex([{"player_name": n, "position": i["position"], "element_id": 1, "team_name": i["current_team"],
                          "is_current": True, "career_points": 100} for n, i in PLAYER_INFO.items() if i["current_team"]])
    extractor = EntityExtractor()
    extractor.player_index = index
    return dict(api_main.app_state, neo4j_conn=conn, llm_manager=None, intent_classifier=IntentClassifier(),
                entity_extractor=extractor, player_index=index, latest_season=CURRENT, avatar_service=None,
                ml_integration=None)


CARD_INPUTS = {"predictions": None, "photo_codes": {}, "web_names": {}, "meta": {}}


def test_query_endpoint_returns_cards_and_follow_ups():
    import api_main

    conn = FakeConn()
    inputs = dict(CARD_INPUTS, predictions=predictions_df())
    request = api_main.QueryRequest(question="Erling Haaland stats this season", retrieval_method="Baseline")
    with patch.dict(api_main.app_state, _app_state(conn), clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=inputs):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    body = json.loads(response.model_dump_json())
    assert body["intent"] == "player_stats"
    assert [c["type"] for c in body["cards"]] == ["player"]
    assert body["cards"][0]["player"]["photo"] == "https://resources.premierleague.com/premierleague25/photos/players/110x140/223094.png"
    assert body["follow_ups"] and all(isinstance(q, str) for q in body["follow_ups"])


def test_best_xi_question_answers_from_the_team_of_the_week():
    import api_main

    conn = FakeConn()
    request = api_main.QueryRequest(question="Best team for next gameweek", retrieval_method="Baseline")
    with patch.dict(api_main.app_state, _app_state(conn), clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=CARD_INPUTS), \
            patch.object(ss, "get_team_of_the_week", return_value=sample_team()):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    assert response.intent == "team_of_the_week"
    assert response.ml_context.startswith("Predicted best FPL squad for gameweek 7")
    assert [r["player_name"] for r in response.results][:2] == ["Keeper A", "Def B"] and len(response.results) == 15
    assert [c["type"] for c in response.cards] == ["pitch"]


# ------------------------------------------------------------------ club squads (get_team_squad)

SQUAD_ROWS = [
    {"player_name": "David Raya Martín", "position": "GK", "team": "Arsenal", "total_points": 30, "minutes": 450,
     "games": 5, "goals": 0, "assists": 0, "status": "a", "news": None},
    {"player_name": "Backup Keeper", "position": "GK", "team": "Arsenal", "total_points": 0, "minutes": 0,
     "games": 0, "goals": 0, "assists": 0, "status": "a", "news": None},
]


class SquadConn(FakeConn):
    def execute_query(self, query, params=None):
        if "PLAYS_FOR]->(:Team {name: $team_name})" in query:
            self.calls.append((query, params or {}))
            rows = [r for r in SQUAD_ROWS if r["position"] == (params or {}).get("position", r["position"])]
            return rows[:(params or {}).get("limit", 40)]
        return super().execute_query(query, params)


def test_squad_leaderboard_spec():
    spec = ac.leaderboard_spec("get_team_squad", {"team_name": "Coventry City", "position": "GK"})
    assert spec["title"] == "Coventry City goalkeepers" and not spec["grouped"]
    assert [c["key"] for c in spec["columns"]] == ["games", "minutes"]
    whole = ac.leaderboard_spec("get_team_squad", {"team_name": "Aston Villa"})
    assert whole["title"] == "Aston Villa squad" and whole["grouped"]
    # the title names the position, so the subtitle doesn't repeat it
    assert ac.leaderboard_subtitle("get_team_squad", {"position": "GK", "season": CURRENT}) == ac.season_label(CURRENT, CURRENT)


def test_squad_question_gives_squad_then_team_cards():
    ctx = context("team_analysis", "get_team_squad", {"team_name": "Arsenal", "position": "GK", "season": CURRENT},
                  {"teams": ["Arsenal"], "positions": ["GK"]}, SQUAD_ROWS, question="Who is the Arsenal goalkeeper?")
    cards, _ = build(ctx)
    assert [c["type"] for c in cards] == ["leaderboard", "team"]
    board = cards[0]
    assert board["title"] == "Arsenal goalkeepers"
    assert [r["player"]["name"] for r in board["rows"]] == ["David Raya Martín", "Backup Keeper"]
    assert all(h["label"] != "Club cluster" for h in board["highlights"])


def test_squad_question_through_the_query_endpoint():
    import api_main

    conn = SquadConn()
    request = api_main.QueryRequest(question="Who is the Arsenal goalkeeper?", retrieval_method="Baseline")
    with patch.dict(api_main.app_state, _app_state(conn), clear=True),             patch.object(api_main, "_answer_card_inputs", return_value=CARD_INPUTS):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    assert response.intent == "team_analysis"
    assert "PLAYS_FOR]->(:Team {name: $team_name})" in response.cypher_query
    squad_params = next(p for q, p in conn.calls if "PLAYS_FOR]->(:Team {name: $team_name})" in q)
    assert squad_params == {"team_name": "Arsenal", "season": CURRENT, "limit": 40, "position": "GK"}
    assert [r["player_name"] for r in response.results] == ["David Raya Martín", "Backup Keeper"]
    assert [c["type"] for c in response.cards][:1] == ["leaderboard"]


# ------------------------------------------------------------------ streamed answers

class FakeChunk:
    def __init__(self, text):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=text))]


class FakeStream:
    def __init__(self, texts, fail_at=None):
        self.texts, self.fail_at, self.closed = texts, fail_at, False

    def __iter__(self):
        for i, text in enumerate(self.texts):
            if i == self.fail_at:
                raise RuntimeError("connection reset")
            yield FakeChunk(text)

    def close(self):
        self.closed = True


def fake_llm_manager(behaviours):
    """An LLMManager whose Groq client answers each model name with a FakeStream or raises."""
    from llm.llm_manager import LLMManager

    calls = []

    def create(model, **kwargs):
        calls.append((model, kwargs.get("stream")))
        outcome = behaviours[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    llm = LLMManager.__new__(LLMManager)
    llm.client, llm.default_model, llm.response_history = None, "gpt-oss-120b", []
    llm.groq_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, calls


def test_generate_stream_yields_the_pieces():
    llm, calls = fake_llm_manager({"openai/gpt-oss-120b": FakeStream(["Haaland ", None, "scored."])})
    assert list(llm.generate_stream("prompt")) == ["Haaland ", "scored."]   # empty deltas skipped
    assert calls == [("openai/gpt-oss-120b", True)]


def test_generate_stream_falls_back_on_quota_errors_before_the_first_piece():
    llm, calls = fake_llm_manager({
        "openai/gpt-oss-120b": RuntimeError("Error code: 429 - rate limit reached"),
        "openai/gpt-oss-20b": FakeStream(["From the smaller model."]),
    })
    assert list(llm.generate_stream("prompt")) == ["From the smaller model."]
    assert [model for model, _ in calls] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_generate_stream_errors():
    from llm.llm_manager import LLMStreamError

    llm, calls = fake_llm_manager({"openai/gpt-oss-120b": RuntimeError("invalid request")})
    with pytest.raises(LLMStreamError, match="invalid request"):
        list(llm.generate_stream("prompt"))
    assert len(calls) == 1                                    # not a quota error: no fallback
    llm, _ = fake_llm_manager({"openai/gpt-oss-120b": FakeStream(["Half an ", "answer"], fail_at=1)})
    pieces = llm.generate_stream("prompt")
    assert next(pieces) == "Half an "
    with pytest.raises(RuntimeError, match="connection reset"):   # text already sent: no fallback
        next(pieces)


def test_generate_stream_stops_early_and_closes_the_stream():
    stream = FakeStream(["one ", "two ", "three"])
    llm, _ = fake_llm_manager({"openai/gpt-oss-120b": stream})
    seen = []
    for piece in llm.generate_stream("prompt", should_stop=lambda: len(seen) >= 2):
        seen.append(piece)
    assert seen == ["one ", "two "] and stream.closed


class FakeLLM:
    """The LLM manager seen by the handler: streams fixed pieces, or fails."""
    available = True

    def __init__(self, pieces=("Haaland has ", "5 goals."), error=None):
        self.pieces, self.error = pieces, error

    def generate_stream(self, prompt, model_key=None, should_stop=None):
        yield from self.pieces
        if self.error:
            raise RuntimeError(self.error)


def stream_events(question, llm, **request):
    """Run _answer_events for a question and return [(event, data), ...]."""
    import api_main

    conn = FakeConn()
    body = api_main.QueryRequest(question=question, retrieval_method="Baseline", **request)

    async def collect():
        return [chunk async for chunk in api_main._answer_events(body, conn)]

    with patch.dict(api_main.app_state, dict(_app_state(conn), llm_manager=llm), clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=dict(CARD_INPUTS, predictions=predictions_df())):
        chunks = asyncio.run(collect())
    events = []
    for chunk in chunks:
        assert chunk.endswith("\n\n")
        name, data = chunk.strip().split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def test_stream_sends_cards_pieces_answer_and_done():
    events = stream_events("Erling Haaland stats this season", FakeLLM())
    names = [name for name, _ in events]
    assert names[-2:] == ["answer", "done"] and names.count("cards") == 1
    assert [data["text"] for name, data in events if name == "delta"] == ["Haaland has ", "5 goals."]
    cards = next(data for name, data in events if name == "cards")
    assert [c["type"] for c in cards["cards"]] == ["player"] and cards["follow_ups"]
    answer, done = events[-2][1], events[-1][1]
    assert answer["text"] == "Haaland has 5 goals." == done["answer"]
    assert done["intent"] == "player_stats" and done["cards"] == cards["cards"]


def test_stream_reports_a_failed_model():
    events = stream_events("Erling Haaland stats this season", FakeLLM(pieces=(), error="quota exhausted"))
    answer = dict(events)["answer"]["text"]
    # No error text: with nothing to show, a plain "try again"; the cards still arrive
    assert answer == ("I can't write an answer right now: the language model isn't responding. "
                      "Please try again in a minute.")
    assert [c["type"] for c in dict(events)["cards"]["cards"]] == ["player"]
    cut = stream_events("Erling Haaland stats this season", FakeLLM(pieces=("Haaland has",), error="reset"))
    assert dict(cut)["answer"]["text"] == "Haaland has\n\n_(The answer was cut short.)_"


def test_stream_greeting():
    events = stream_events("hello", FakeLLM(), is_first_message=True)
    assert [name for name, _ in events] == ["cards", "answer", "done"]
    assert events[0][1]["follow_ups"] == list(ac.STARTER_QUESTIONS)


def test_stream_endpoint_speaks_server_sent_events():
    import api_main
    from fastapi.testclient import TestClient

    conn = FakeConn()
    api_main.app.dependency_overrides[api_main.get_neo4j_conn] = lambda: conn
    try:
        with patch.dict(api_main.app_state, dict(_app_state(conn), llm_manager=FakeLLM()), clear=True), \
                patch.object(api_main, "_answer_card_inputs", return_value=CARD_INPUTS):
            response = TestClient(api_main.app).post("/api/query/stream", json={
                "question": "Erling Haaland stats this season", "retrieval_method": "Baseline"})
    finally:
        api_main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.count("event: done\n") == 1 and "event: delta\n" in response.text


def test_query_endpoint_survives_card_failures():
    import api_main

    with patch.object(api_main, "_answer_card_inputs", side_effect=RuntimeError("disk")):
        cards, follow_ups = asyncio.run(api_main.build_answer_cards(
            question="q", intent="player_stats", method=None, params={}, entities={}, results=[], conn=FakeConn()))
    assert (cards, follow_ups) == ([], [])


def test_greeting_suggests_starter_questions():
    import api_main

    request = api_main.QueryRequest(question="hello", is_first_message=True)
    response = asyncio.run(api_main.query_fpl(request, conn=FakeConn()))
    assert response.cards == [] and response.follow_ups == list(ac.STARTER_QUESTIONS)


def test_per_match_prediction_question_gets_the_fixture_card():
    ctx = context("prediction", None, {}, question="Players expected to get the most points for each match next GW")
    cards, _ = build(ctx)
    card = cards[0]
    assert card["type"] == "fixture_picks" and card["gameweek"] == 7 and card["deadline"] == "2026-10-17T10:00:00Z"
    city = next(f for f in card["fixtures"] if "Man City" in (f["home"], f["away"]))
    assert city["players"][0]["player"]["name"] == "Erling Haaland"
    assert city["players"][0]["player"]["photo"] == "https://photos.test/223094.png"
    united = next(f for f in card["fixtures"] if "Man Utd" in (f["home"], f["away"]))
    assert united["players"][0]["double"] is True


def test_per_match_question_answers_from_predictions_by_fixture():
    import api_main

    conn = FakeConn()
    inputs = dict(CARD_INPUTS, predictions=predictions_df(), meta={"next_gameweek": 7})
    request = api_main.QueryRequest(
        question="get the players expected to get the most points for each match in the next GW",
        retrieval_method="Baseline")
    with patch.dict(api_main.app_state, _app_state(conn), clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=inputs), \
            patch.object(api_main, "get_predictions_df", return_value=predictions_df()):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    assert response.intent == "prediction"          # the wording alone scores TOP_POINTS
    assert "players with the most predicted points in each" in response.ml_context
    assert "Erling Haaland (FWD, Man City): 8.6 pts" in response.ml_context
    assert [c["type"] for c in response.cards] == ["fixture_picks"]
