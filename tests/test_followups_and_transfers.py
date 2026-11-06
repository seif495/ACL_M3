"""Follow-up questions that refer back to players, and transfer questions.

Bug 2: "How is Isak doing this season?" then "Is he a better captain than Haaland this week?"
answered about Haaland alone and said Isak had no forecast. A pronoun before a named player is
now a player from the history (put first), and the LLM gets every named player's forecast.

Bug 3: "Should I sell Haaland for Isak and Palmer for Rice next gameweek? I have 1 free
transfer." got a vague answer from a career-best season. Transfer questions are now paired
(sold, bought) in order and answered from a numbers-first Transfer Analysis, with one
comparison card per move.

Neo4j is faked (FakeConn answers by query text) and no test reads the live data directory.
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

from config.settings import CURRENT_SEASON  # noqa: E402
from preprocessing.entity_extractor import EntityExtractor, history_exchanges  # noqa: E402
from preprocessing.intent_classifier import Intent, IntentClassifier  # noqa: E402
from services import answer_cards as ac  # noqa: E402
from services import player_outlook as po  # noqa: E402
from services import season_service as ss  # noqa: E402
from services import transfer_advice as ta  # noqa: E402
from services.player_index import PlayerIndex  # noqa: E402

NEXT_GW = 10

# name, position, club, career points (popularity), FPL element
PLAYERS = [
    ("Erling Haaland", "FWD", "Man City", 1500, 411),
    ("Alexander Isak", "FWD", "Liverpool", 900, 412),
    ("Cole Palmer", "MID", "Chelsea", 1000, 413),
    ("Declan Rice", "MID", "Arsenal", 700, 414),
    ("Mohamed Salah", "MID", "Liverpool", 2000, 415),
    ("Bukayo Saka", "MID", "Arsenal", 1200, 416),
    ("Ollie Watkins", "FWD", "Aston Villa", 800, 417),
]
CLUB = {name: club for name, _pos, club, _pts, _el in PLAYERS}
ELEMENT = {name: el for name, _pos, _club, _pts, el in PLAYERS}


def player_index():
    return PlayerIndex([{"player_name": name, "position": pos, "element_id": el, "team_name": club,
                         "is_current": True, "career_points": pts} for name, pos, club, pts, el in PLAYERS])


@pytest.fixture(scope="module")
def extractor():
    ex = EntityExtractor()
    ex.player_index = player_index()
    return ex


@pytest.fixture(autouse=True)
def fresh_cache(tmp_path, monkeypatch):
    # No test reads data/live: projections (if any) are written to a temporary directory
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    ss.clear_cache()
    yield
    ss.clear_cache()


def ask(extractor, question, *history):
    """Players the extractor finds in `question` after the (user, assistant) turns given."""
    chat = []
    for i, text in enumerate(history):
        chat.append({"role": "user" if i % 2 == 0 else "assistant", "content": text})
    return extractor.extract(question, chat).players


# ------------------------------------------------------------------ pronouns

ISAK = ("How is Isak doing this season?",
        "Alexander Isak has 38 points so far this season, well behind Erling Haaland's 61.")


def test_a_pronoun_before_a_named_player_is_the_earlier_player(extractor):
    # The bug: "he" was dropped because the question names Haaland
    assert ask(extractor, "Is he a better captain than Haaland this week?", *ISAK) == \
        ["Alexander Isak", "Erling Haaland"]


def test_a_pronoun_after_a_named_player_is_that_player(extractor):
    assert ask(extractor, "Haaland is in form, is he worth captaining?", *ISAK) == ["Erling Haaland"]
    assert ask(extractor, "Is Haaland fit, and will he start?", *ISAK) == ["Erling Haaland"]


def test_no_named_player_takes_the_earlier_one(extractor):
    assert ask(extractor, "How many points will he get next gameweek?", *ISAK) == ["Alexander Isak"]


def test_the_users_own_question_beats_names_in_the_answer(extractor):
    # The answer names Haaland first-ish as a rival; the subject is the player the user asked about
    history = ("How is Isak doing this season?",
               "Erling Haaland leads the forwards, and Alexander Isak is fourth with 38 points.")
    assert ask(extractor, "Is he worth buying?", *history) == ["Alexander Isak"]


def test_the_answer_is_used_when_the_question_named_no_player(extractor):
    history = ("Who is the top scorer this season?", "Erling Haaland, with 12 goals.")
    assert ask(extractor, "How many assists does he have?", *history) == ["Erling Haaland"]


def test_the_newest_exchange_wins(extractor):
    history = ISAK + ("Tell me about Mohamed Salah", "Salah has 70 points so far this season.")
    assert ask(extractor, "Is he fit?", *history) == ["Mohamed Salah"]
    # ... even when the newest exchange only names him in the answer
    history = ISAK + ("Who leads the scoring charts?", "Erling Haaland leads with 12 goals.")
    assert ask(extractor, "Is he a good captain?", *history) == ["Erling Haaland"]


def test_plural_pronouns_take_both_players(extractor):
    history = ("Compare Salah and Saka", "Salah has 70 points and Saka 52 so far this season.")
    assert ask(extractor, "Who has the better fixtures out of them?", *history) == \
        ["Mohamed Salah", "Bukayo Saka"]
    assert ask(extractor, "Which of the two should I captain?", *history) == ["Mohamed Salah", "Bukayo Saka"]


def test_plural_after_a_club_question_is_the_club(extractor):
    history = ("How are Arsenal doing?", "Arsenal are second; Bukayo Saka has 5 goals.")
    assert ask(extractor, "Who do they play next?", *history) == []
    # but picking among the players the answer named is about those players
    history = ("Who are Arsenal's best players?", "Bukayo Saka and Declan Rice lead the squad.")
    assert ask(extractor, "Which of them is the better buy?", *history) == ["Bukayo Saka", "Declan Rice"]


def test_a_club_named_before_they_keeps_the_history_out(extractor):
    history = ("Compare Salah and Saka", "Salah has 70 points and Saka 52.")
    assert ask(extractor, "Arsenal are flying, are they worth a triple-up?", *history) == []


def test_pronouns_in_earlier_questions_are_resolved_too(extractor):
    # The bug's conversation, one question further on
    history = ISAK + ("Is he a better captain than Haaland this week?",
                      "Haaland is forecast 5.4 points and Isak 4.3.")
    assert ask(extractor, "Who has the better fixtures out of them?", *history) == \
        ["Alexander Isak", "Erling Haaland"]


def test_history_exchanges_pair_questions_with_answers():
    chat = [{"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"}]
    assert history_exchanges(chat) == [("Q2", ""), ("Q1", "A1"), ("", "Hi!")]
    assert history_exchanges(chat, limit=1) == [("Q2", "")]


def test_without_the_index_a_pronoun_before_a_named_player_still_resolves():
    ex = EntityExtractor(known_players={name for name, *_ in PLAYERS})
    chat = [{"role": "user", "content": "How is Alexander Isak doing?"},
            {"role": "assistant", "content": "Alexander Isak has 38 points."}]
    players = ex.extract("Is he a better captain than Erling Haaland?", chat).players
    assert players[0] == "Alexander Isak" and "Erling Haaland" in players


def test_classifier_history_prefers_the_users_question():
    classifier = IntentClassifier()
    chat = [{"role": "user", "content": "How is Isak doing this season?"},
            {"role": "assistant", "content": "Erling Haaland leads, Alexander Isak is fourth."}]
    assert classifier._extract_players_from_history(chat) == ["Isak"]
    chat += [{"role": "user", "content": "Who leads the scoring charts?"},
             {"role": "assistant", "content": "Haaland leads with 12 goals."}]
    assert classifier._extract_players_from_history(chat) == ["Haaland"]
    # a club isn't a player
    chat = [{"role": "user", "content": "How are Arsenal doing?"},
            {"role": "assistant", "content": "Arsenal are second."}]
    assert classifier._extract_players_from_history(chat) == []


# ------------------------------------------------------------------ transfer parsing

def parse(extractor, question, pronoun_players=()):
    return ta.parse(question, extractor.player_mention_spans(question), pronoun_players)


BUG_QUESTION = "Should I sell Haaland for Isak and Palmer for Rice next gameweek? I have 1 free transfer."


def test_the_reported_question_pairs_both_moves_in_order(extractor):
    t = parse(extractor, BUG_QUESTION)
    assert t.pairs == [("Erling Haaland", "Alexander Isak"), ("Cole Palmer", "Declan Rice")]
    assert (t.free_transfers, t.free_transfers_stated, t.hit_mentioned) == (1, True, False)
    assert t.players == ["Erling Haaland", "Alexander Isak", "Cole Palmer", "Declan Rice"]
    assert t.unresolved == [] and t.others == []


@pytest.mark.parametrize("question, pairs", [
    ("Haaland out, Isak in?", [("Erling Haaland", "Alexander Isak")]),
    ("Haaland out Isak in, Palmer out Rice in", [("Erling Haaland", "Alexander Isak"), ("Cole Palmer", "Declan Rice")]),
    ("Should I swap Salah with Saka?", [("Mohamed Salah", "Bukayo Saka")]),
    ("Replace Palmer with Rice", [("Cole Palmer", "Declan Rice")]),
    ("swap out Haaland for Watkins", [("Erling Haaland", "Ollie Watkins")]),
    ("Should I get Isak instead of Haaland?", [("Erling Haaland", "Alexander Isak")]),
    ("Isak in for Haaland, worth a hit?", [("Erling Haaland", "Alexander Isak")]),
    ("Haaland -> Isak?", [("Erling Haaland", "Alexander Isak")]),
    ("Haaland for Isak? I have 2 free transfers", [("Erling Haaland", "Alexander Isak")]),
    ("Sell Haaland and Palmer for Isak and Rice", [("Erling Haaland", "Alexander Isak"), ("Cole Palmer", "Declan Rice")]),
    ("Transfer out Salah for Palmer, then Haaland to Isak, then Saka to Rice?",
     [("Mohamed Salah", "Cole Palmer"), ("Erling Haaland", "Alexander Isak"), ("Bukayo Saka", "Declan Rice")]),
])
def test_transfer_wordings_pair_sold_then_bought(extractor, question, pairs):
    assert parse(extractor, question).pairs == pairs


@pytest.mark.parametrize("question", [
    "Should I captain Isak instead of Haaland?",   # captaincy, not a transfer
    "Compare Haaland to Isak",
    "Most transferred in players this week",
    "What transfer should I make?",                # names nobody: the usual pipeline answers
    "Is Saka out injured in GW5?",
    "How is Isak doing this season?",
])
def test_other_questions_are_not_transfers(extractor, question):
    assert parse(extractor, question) is None


def test_free_transfers_and_hits():
    assert ta.free_transfers("I have 2 free transfers") == (2, True)
    assert ta.free_transfers("no free transfers left") == (0, True)
    assert ta.free_transfers("got 3FT") == (3, True)
    assert ta.free_transfers("one free transfer") == (1, True)
    assert ta.free_transfers("Should I sell Haaland?") == (1, False)
    assert ta.free_transfers("9 free transfers") == (5, True)   # FPL banks at most 5


def test_half_moves_pronouns_and_unknown_names(extractor):
    t = parse(extractor, "Should I sell Haaland for a cheaper striker next week?")
    assert [(m.sell, m.buy) for m in t.moves] == [("Erling Haaland", None)] and t.unresolved == []
    t = parse(extractor, "Should I sell Bloggs for Isak?")
    assert t.pairs == [] and t.unresolved == ["Bloggs"]
    assert [(m.sell, m.buy, m.missing) for m in t.moves] == [(None, "Alexander Isak", "Bloggs")]
    t = parse(extractor, "Should I sell him for Isak?", pronoun_players=["Mohamed Salah"])
    assert t.pairs == [("Mohamed Salah", "Alexander Isak")]
    t = parse(extractor, "sell Haaland for Isak or Watkins, I have no free transfers")
    assert t.pairs == [("Erling Haaland", "Alexander Isak")] and t.others == ["Ollie Watkins"]
    assert t.free_transfers == 0
    t = parse(extractor, "Is Isak worth buying?")
    assert t.moves == [] and t.others == ["Alexander Isak"]


# ------------------------------------------------------------------ data for the outlooks

def kickoff(days):
    return datetime.now(timezone.utc) + timedelta(days=days)


# Each club's next four opponents (home?, FPL difficulty for that club)
SCHEDULE = {
    "Man City": [("Nott'm Forest", False, 3), ("Fulham", True, 2), ("Spurs", False, 3), ("Everton", True, 2)],
    "Liverpool": [("Crystal Palace", False, 3), ("Brentford", True, 2), ("Chelsea", False, 4), ("Wolves", True, 2)],
    "Chelsea": [("Sunderland", False, 2), ("Arsenal", True, 4), ("Liverpool", True, 4), ("Leeds", False, 2)],
    "Arsenal": [("Hull City", True, 2), ("Chelsea", False, 4), ("Man Utd", True, 3), ("Bournemouth", False, 3)],
    "Aston Villa": [("Everton", True, 2), ("Man Utd", False, 3), ("Fulham", True, 2), ("Brighton", False, 3)],
}


def fixture_rows():
    rows, seen = [], set()
    for team, games in SCHEDULE.items():
        for step, (opponent, home, difficulty) in enumerate(games):
            if (step, frozenset((team, opponent))) in seen:
                continue  # Chelsea v Arsenal is in both clubs' lists
            seen.add((step, frozenset((team, opponent))))
            rows.append({"id": f"{team}-{step}", "gameweek": NEXT_GW + step, "kickoff_time": kickoff(2 + 7 * step),
                         "status": "scheduled", "home_team": team if home else opponent,
                         "away_team": opponent if home else team, "home_score": None, "away_score": None,
                         "home_difficulty": difficulty if home else 3, "away_difficulty": 3 if home else difficulty})
    return rows


# name -> (predicted points next GW, price in tenths, status, chance, news)
PREDICTIONS = {
    "Erling Haaland": (5.4, 157, "a", None, ""),
    "Alexander Isak": (4.3, 105, "a", None, ""),
    "Cole Palmer": (4.4, 105, "d", 75, "Groin injury - 75% chance of playing"),
    "Declan Rice": (3.2, 65, "a", None, ""),
    "Mohamed Salah": (6.0, 145, "a", None, ""),
    "Bukayo Saka": (3.9, 101, "a", None, ""),
}
# Projected points for the three gameweeks after the next one
LATER = {"Erling Haaland": (6.1, 5.0, 6.3), "Alexander Isak": (5.2, 3.9, 5.0),
         "Cole Palmer": (3.8, 3.5, 4.6), "Declan Rice": (3.0, 3.4, 3.1),
         "Mohamed Salah": (6.5, 4.4, 6.2), "Bukayo Saka": (4.2, 3.8, 4.0)}


def predictions_df():
    rows = []
    for name, (points, value, status, chance, news) in PREDICTIONS.items():
        opponent, home, _ = SCHEDULE[CLUB[name]][0]
        rows.append({"name": name, "position": next(p for n, p, *_ in PLAYERS if n == name), "team": CLUB[name],
                     "predicted_points": points, "value": value, "opponent": opponent, "is_home": home,
                     "gameweek": NEXT_GW, "kickoff_time": kickoff(2).isoformat(), "prediction_method": "xgboost_v4",
                     "status": status, "chance_of_playing_next_round": chance, "news": news,
                     "element": ELEMENT[name]})
    df = pd.DataFrame(rows)
    df["name_lower"] = df["name"].str.lower()
    return df


def write_projections(directory):
    rows = []
    for name, (points, value, status, *_rest) in PREDICTIONS.items():
        for step, pts in enumerate((points,) + LATER[name]):
            opponent, home, _ = SCHEDULE[CLUB[name]][step]
            rows.append({"element": ELEMENT[name], "name": name, "web_name": name.split()[-1],
                         "position": next(p for n, p, *_ in PLAYERS if n == name), "team": CLUB[name],
                         "now_cost": value, "gameweek": NEXT_GW + step, "fixtures": 1,
                         "opponents": f"{opponent} ({'H' if home else 'A'})", "predicted_points": pts,
                         "availability": 1.0, "status": status})
    pd.DataFrame(rows).to_csv(os.path.join(directory, po.PROJECTIONS_NAME), index=False)


# This season so far: points, goals, assists, appearances
SEASON = {"Erling Haaland": (61, 9, 1, 9), "Alexander Isak": (38, 4, 2, 9), "Cole Palmer": (45, 3, 4, 8),
          "Declan Rice": (40, 1, 3, 9), "Mohamed Salah": (70, 6, 5, 9), "Bukayo Saka": (52, 4, 3, 9)}


def season_row(name):
    points, goals, assists, apps = SEASON[name]
    position = next(p for n, p, *_ in PLAYERS if n == name)
    return {"name": name, "season": CURRENT_SEASON, "team": CLUB[name], "position": position,
            "total_points": points, "goals": goals, "assists": assists, "clean_sheets": 2, "bonus": 5,
            "minutes": apps * 85, "saves": 0, "games": apps, "appearances": apps, "ict_sum": apps * 8.0,
            "value_sum": apps * PREDICTIONS[name][1]}


class FakeConn:
    """execute_query answers by the query text; unknown queries return []."""

    def __init__(self):
        self.calls = []

    def execute_query(self, query, params=None):
        params = params or {}
        self.calls.append((query, params))
        names = [n for n in params.get("names", []) if n in SEASON]
        if query == po.SEASON_TOTALS_QUERY:
            return [season_row(n) for n in names]
        if query == ac.PLAYERS_QUERY:
            return [{"name": n, "position": season_row(n)["position"], "current_team": CLUB[n], "last_team": CLUB[n],
                     "season_team": CLUB[n], "season_position": season_row(n)["position"], "fpl_code": None,
                     "status": PREDICTIONS[n][2], "chance": PREDICTIONS[n][3], "news": PREDICTIONS[n][4]}
                    for n in names]
        if query == ac.PLAYER_SEASONS_QUERY:
            return [season_row(n) for n in names]
        if query == ss.FIXTURES_QUERY:
            return fixture_rows()
        return []


# ------------------------------------------------------------------ outlooks and contexts

def outlooks(names, projections=True, tmp=None):
    if projections:
        write_projections(os.environ["LIVE_DATA_DIR"])
    return po.load_outlooks(FakeConn(), names, predictions_df())


def test_forecast_context_lists_every_named_player():
    found = outlooks(["Alexander Isak", "Erling Haaland", "Old Striker"])
    text = po.forecast_context([found[n] for n in ["Alexander Isak", "Erling Haaland", "Old Striker"]])
    assert "Alexander Isak (FWD, Liverpool, £10.5m), available" in text
    assert f"- GW{NEXT_GW} predicted points: 4.3 vs Crystal Palace (A)" in text
    assert f"- GW{NEXT_GW} predicted points: 5.4 vs Nott'm Forest (A)" in text
    # Only a player missing from the file is said to have no prediction
    assert text.count("not in the current FPL game") == 2   # Old Striker's line, and the closing note
    assert "Old Striker: not in the current FPL game" in text
    assert f"{CURRENT_SEASON} so far: 38 points in 9 appearances (4.2 a game), 4 goals, 2 assists" in text
    assert f"{CURRENT_SEASON} so far: 61 points in 9 appearances (6.8 a game), 9 goals, 1 assist," in text


def test_outlook_projections_fixtures_and_availability():
    palmer = outlooks(["Cole Palmer"])["Cole Palmer"]
    assert palmer.price == 10.5 and palmer.points == 4.4 and palmer.gameweek == NEXT_GW
    assert [gw for gw, _, _ in palmer.projections] == [NEXT_GW, NEXT_GW + 1, NEXT_GW + 2, NEXT_GW + 3]
    assert palmer.projected(range(NEXT_GW, NEXT_GW + 4)) == round(4.4 + 3.8 + 3.5 + 4.6, 1)
    assert [f["team"] for f in palmer.fixtures] == ["Sunderland", "Arsenal", "Liverpool", "Leeds"]
    text = po.outlook_text(palmer)
    # FPL's news already gives the chance, so it isn't repeated; without it, the chance is added
    assert "doubtful: Groin injury - 75% chance of playing" in text
    palmer.news = "Groin injury"
    assert "doubtful, 75% chance of playing: Groin injury" in po.outlook_text(palmer)
    assert f"Projected points GW{NEXT_GW}-GW{NEXT_GW + 3}: 4.4 + 3.8 + 3.5 + 4.6 = 16.3" in text
    assert f"GW{NEXT_GW} Sunderland (A, FDR 2)" in text


def test_stale_or_missing_projections_are_left_out():
    write_projections(os.environ["LIVE_DATA_DIR"])
    stale = predictions_df().assign(gameweek=NEXT_GW + 1)   # the file starts a gameweek earlier
    found = po.load_outlooks(FakeConn(), ["Alexander Isak"], stale)
    assert found["Alexander Isak"].projections == []
    os.remove(os.path.join(os.environ["LIVE_DATA_DIR"], po.PROJECTIONS_NAME))
    assert po.load_projections() is None


def test_find_row_ignores_case_and_accents():
    df = predictions_df()
    df.loc[0, "name"] = "Erling Håland"
    df["name_lower"] = df["name"].str.lower()
    assert po.find_row(df, "erling håland")["name"] == "Erling Håland"
    assert po.find_row(df, "Erling Haland")["name"] == "Erling Håland"
    assert po.find_row(df, "Nobody Atall") is None


def test_next_gameweek_wording():
    for question in ("Is he a better captain than Haaland?", "Isak or Haaland this week?",
                     "Who's the better pick, Salah or Saka?", "Salah vs Saka next gameweek"):
        assert po.is_next_gameweek_question(question), question
    for question in ("Compare Salah and Saka in 2023-24", "Who scored more goals, Salah or Saka?"):
        assert not po.is_next_gameweek_question(question), question


def transfer_context(extractor, question):
    t = parse(extractor, question)
    return ta.context(t, outlooks(t.players))


def test_transfer_context_for_the_reported_question(extractor):
    text = transfer_context(extractor, BUG_QUESTION)
    lines = text.splitlines()
    assert lines[0] == ("Transfer question: 2 moves (sell -> buy), in the order asked. Free transfers: 1 "
                        "(as the user says); each move beyond 1 costs 4 points.")
    # Move 1 is the free one; move 2 is beyond the free transfer
    assert "Move 1 (free transfer): sell Erling Haaland -> buy Alexander Isak" in lines
    assert f"- GW{NEXT_GW} predicted points: Haaland 5.4, Isak 4.3: gain -1.1" in lines
    assert f"- Projected GW{NEXT_GW}-GW{NEXT_GW + 3}: Haaland 22.8, Isak 18.4: gain -4.4" in lines
    assert "- Price: Haaland £15.7m, Isak £10.5m: frees £5.2m" in lines
    assert "Move 2 (4-point hit): sell Cole Palmer -> buy Declan Rice" in lines
    assert f"- Projected GW{NEXT_GW}-GW{NEXT_GW + 3}: Palmer 16.3, Rice 12.7: gain -3.6; after the 4-point hit -7.6" in lines
    assert "- Price: Palmer £10.5m, Rice £6.5m: frees £4.0m" in lines
    assert "Whole plan: next-gameweek gain -2.3, projected gain over the horizon -8.0, hits 4 points, net -12.0, budget frees £9.2m." in lines
    # Every player's outlook, with this season's numbers and no career totals
    for name in ("Erling Haaland", "Alexander Isak", "Cole Palmer", "Declan Rice"):
        assert any(line.startswith(f"{name} (") for line in lines), name
    assert f"{CURRENT_SEASON} so far: 61 points in 9 appearances" in text
    assert "doubtful: Groin injury - 75% chance of playing" in text


def test_transfer_context_names_unknown_players(extractor):
    text = transfer_context(extractor, "Should I sell Bloggs for Isak?")
    assert 'Sell "Bloggs" (no such player in the data) -> buy Alexander Isak' in text
    assert "Not found in the data (say so plainly; don't guess who was meant): \"Bloggs\"" in text


def test_transfer_context_without_projections_weighs_the_next_gameweek(extractor):
    t = parse(extractor, BUG_QUESTION)
    text = ta.context(t, po.load_outlooks(FakeConn(), t.players, predictions_df()))   # no projections.csv
    assert f"- GW{NEXT_GW} predicted points: Palmer 4.4, Rice 3.2: gain -1.2; after the 4-point hit -5.2" in text
    assert "No projection beyond the next gameweek" in text
    assert "Whole plan: next-gameweek gain -2.3, hits 4 points, budget frees £9.2m." in text


def test_a_buy_question_without_a_move_lists_the_players(extractor):
    text = transfer_context(extractor, "Is Isak a better buy than Haaland this week?")
    assert text.startswith("Transfer question: no complete move (sell X -> buy Y) named")
    assert "Alexander Isak (FWD, Liverpool, £10.5m)" in text and "Erling Haaland (FWD, Man City, £15.7m)" in text


def test_transfer_context_with_free_transfers_to_spare(extractor):
    text = transfer_context(extractor, "Haaland out, Isak in and Palmer out, Rice in? I have 2 free transfers")
    assert "Move 2 (free transfer): sell Cole Palmer -> buy Declan Rice" in text
    assert "hits 0 points" in text


def test_transfer_cards_one_comparison_per_move():
    ctx = ac.CardContext(
        question=BUG_QUESTION, intent="transfer_advice", method=None,
        params={"season": CURRENT_SEASON, "transfer_moves": [["Erling Haaland", "Alexander Isak"],
                                                             ["Cole Palmer", "Declan Rice"], [None, "Mohamed Salah"]]},
        entities={"players": ["Erling Haaland", "Alexander Isak", "Cole Palmer", "Declan Rice"]},
        results=[], conn=FakeConn(), current_season=CURRENT_SEASON, predictions=predictions_df())
    cards, _follow_ups = asyncio.run(ac.build_cards(ctx))
    assert [c["type"] for c in cards] == ["comparison", "comparison"]
    assert [[p["name"] for p in c["players"]] for c in cards] == [["Erling Haaland", "Alexander Isak"],
                                                                 ["Cole Palmer", "Declan Rice"]]
    assert all(c["season"] == CURRENT_SEASON and c["emphasis"] == "prediction" for c in cards)
    assert [p["points"] for p in cards[0]["predictions"]] == [5.4, 4.3]


def test_prompt_has_the_transfer_rules_only_with_an_analysis():
    from llm.prompts import PromptTemplates
    plain = PromptTemplates.qa_template(question="q", kg_context="", ml_context="x")
    assert "TRANSFER ADVICE RULES" not in plain
    assert "Never say a player has no forecast when he has a line above" in plain
    prompt = PromptTemplates.qa_template(question="q", kg_context="", transfer_context="Move 1 ...")
    assert "### Transfer Analysis" in prompt and "Move 1 ..." in prompt
    assert "each move beyond the free transfers costs 4 points" in prompt
    assert "Never use a career total, a best season or an earlier season" in prompt


# ------------------------------------------------------------------ POST /api/query

class CapturingLLM:
    """The LLM manager seen by the handler: records the prompt, answers with fixed text."""
    available = True

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, model_key=None, timeout=None):
        self.prompts.append(prompt)
        return SimpleNamespace(success=True, text="Fixed answer.", error=None)


def run_query(extractor, question, history=()):
    import api_main

    conn = FakeConn()
    llm = CapturingLLM()
    write_projections(os.environ["LIVE_DATA_DIR"])
    index = extractor.player_index
    state = dict(api_main.app_state, neo4j_conn=conn, llm_manager=llm, intent_classifier=IntentClassifier(),
                 entity_extractor=extractor, player_index=index, latest_season=CURRENT_SEASON,
                 avatar_service=None, ml_integration=None)
    inputs = {"predictions": predictions_df(), "photo_codes": {}, "web_names": {},
              "meta": {"next_gameweek": NEXT_GW}}
    chat = [{"role": "user" if i % 2 == 0 else "assistant", "content": text} for i, text in enumerate(history)]
    request = api_main.QueryRequest(question=question, retrieval_method="Baseline", chat_history=chat)
    with patch.dict(api_main.app_state, state, clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=inputs), \
            patch.object(api_main, "get_predictions_df", return_value=predictions_df()):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    return json.loads(response.model_dump_json()), llm.prompts[0]


def test_follow_up_captain_question_through_the_query_endpoint(extractor):
    body, prompt = run_query(extractor, "Is he a better captain than Haaland this week?", ISAK)
    assert body["intent"] == "prediction"
    assert body["entities"]["players"] == ["Alexander Isak", "Erling Haaland"]
    # Both forecasts reach the LLM; neither is said to be missing
    assert f"- GW{NEXT_GW} predicted points: 4.3 vs Crystal Palace (A)" in body["ml_context"]
    assert f"- GW{NEXT_GW} predicted points: 5.4 vs Nott'm Forest (A)" in body["ml_context"]
    assert "Alexander Isak: not in the current FPL game" not in body["ml_context"]
    assert body["ml_context"] in prompt
    # One comparison card for this season, predictions first, with both players' forecasts
    assert [c["type"] for c in body["cards"]] == ["comparison"]
    card = body["cards"][0]
    assert [p["name"] for p in card["players"]] == ["Alexander Isak", "Erling Haaland"]
    assert card["season"] == CURRENT_SEASON and card["emphasis"] == "prediction"
    assert [p["points"] for p in card["predictions"]] == [4.3, 5.4]


def test_two_players_this_week_compare_this_season(extractor):
    body, _prompt = run_query(extractor, "Who's the better pick this week, Salah or Saka?")
    assert body["intent"] == "player_comparison"
    assert "Mohamed Salah (MID, Liverpool, £14.5m)" in body["ml_context"]
    assert "Bukayo Saka (MID, Arsenal, £10.1m)" in body["ml_context"]
    card = body["cards"][0]
    assert card["type"] == "comparison" and card["season"] == CURRENT_SEASON and card["emphasis"] == "prediction"


def test_transfer_question_through_the_query_endpoint(extractor):
    body, prompt = run_query(extractor, BUG_QUESTION)
    assert body["intent"] == "transfer_advice"
    assert body["entities"]["players"] == ["Erling Haaland", "Alexander Isak", "Cole Palmer", "Declan Rice"]
    assert body["cypher_query"] == ""            # no historical query: this season and the forecasts only
    assert "### Transfer Analysis" in prompt and "TRANSFER ADVICE RULES" in prompt
    assert "Move 2 (4-point hit): sell Cole Palmer -> buy Declan Rice" in prompt
    assert "### ML Predictions" not in prompt    # the analysis has its own section
    assert [c["type"] for c in body["cards"]] == ["comparison", "comparison"]
    assert [[p["name"] for p in c["players"]] for c in body["cards"]] == [
        ["Erling Haaland", "Alexander Isak"], ["Cole Palmer", "Declan Rice"]]
    assert all(c["season"] == CURRENT_SEASON for c in body["cards"])
