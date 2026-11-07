"""The assistant's "my team", chip and fixture-run questions, the sources line and the
remaining-question count.

services.my_team's network calls are monkeypatched; Neo4j is faked by the transfer tests'
FakeConn and nothing reads the live data directory.
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import test_followups_and_transfers as base  # noqa: E402
from config.settings import CURRENT_SEASON  # noqa: E402
from llm.prompts import PromptTemplates  # noqa: E402
from preprocessing.intent_classifier import IntentClassifier  # noqa: E402
from services import answer_sources  # noqa: E402
from services import fixture_runs as fr  # noqa: E402
from services import my_team  # noqa: E402
from services import my_team_assistant as mta  # noqa: E402
from services import season_service as ss  # noqa: E402
from services.security import RateLimiter  # noqa: E402

NEXT_GW = base.NEXT_GW
extractor = base.extractor          # module fixture, reused


@pytest.fixture(autouse=True)
def fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    ss.clear_cache()
    yield
    ss.clear_cache()


# ------------------------------------------------------------------ detection

@pytest.mark.parametrize("question,kind", [
    ("Who should I captain this gameweek?", "captain"),
    ("Who should I captain from my team?", "captain"),
    ("What transfers should I make?", "transfers"),
    ("Should I take a hit this week?", "transfers"),
    ("Who should I bench?", "bench"),
    ("Is my bench any good?", "bench"),
    ("Rate my team", "team"),
    ("How many free transfers do I have in my squad?", "team"),
    ("When should I use my bench boost?", "chips"),
    ("Is this a good week to triple captain?", "chips"),
    ("Should I play my wildcard now?", "chips"),
])
def test_my_team_question_kinds(question, kind):
    assert mta.question_kind(question) == kind


@pytest.mark.parametrize("question", [
    "Who scored the most goals in 2022-23?", "Compare Salah and Saka this season",
    "Top 5 midfielders by points this season", "Best team for next gameweek",
])
def test_other_questions_are_not_about_my_team(question):
    assert mta.question_kind(question) is None


@pytest.mark.parametrize("question,teams,positions,kind", [
    ("Best defenders for the next 5 gameweeks", [], ["DEF"], "players"),
    ("Who will score the most points over the next 3 weeks?", [], [], "players"),
    ("Which teams have the easiest fixtures?", [], [], "clubs"),
    ("Who has the toughest run?", [], [], "clubs"),
    ("Arsenal's next 6 fixtures", ["Arsenal"], [], "clubs"),
    ("Which defenders have the easiest fixtures?", [], ["DEF"], "players"),
    ("Is Arsenal on a good run?", ["Arsenal"], [], None),
    ("Who should I captain this gameweek?", [], [], None),
    ("Predicted points for midfielders next gameweek", [], ["MID"], None),
])
def test_fixture_run_questions(question, teams, positions, kind):
    assert fr.classify(question, [], teams, positions) == kind


def test_requested_weeks():
    assert fr.requested_weeks("best defenders for the next 5 gameweeks") == 5
    assert fr.requested_weeks("over the next three weeks") == 3
    assert fr.requested_weeks("next few GWs") == 4
    assert fr.requested_weeks("next gameweek") is None


# ------------------------------------------------------------------ the team

def squad_row(element, name, position, team, points, cost, projected, status="a", chance=None, news=""):
    weeks = [{"gameweek": NEXT_GW + i, "points": p} for i, p in enumerate(projected)]
    return {"element": element, "name": name, "web_name": name.split()[-1], "team": team, "position": position,
            "now_cost": cost, "purchase_price": cost - 2, "selling_price": cost - 1, "purchase_source": "transfer",
            "status": status, "chance": chance, "news": news, "predicted_points": points,
            "opponents": [{"team": "Fulham", "home": True}], "projected": weeks,
            "projected_total": round(sum(projected), 2), "_code": None}


def fake_team():
    squad = []
    spec = ([("GK", 2)], [("DEF", 5)], [("MID", 5)], [("FWD", 3)])
    element = 100
    for group in spec:
        for position, n in group:
            for i in range(n):
                element += 1
                squad.append(squad_row(element, f"Player {position}{i}", position, "Arsenal" if i % 2 else "Chelsea",
                                       2.0 + i, 50 + i, [2.0 + i, 2.5, 3.0, 2.0]))
    squad[0] = squad_row(101, "David Raya", "GK", "Arsenal", 4.1, 56, [4.1, 3.9, 4.0, 3.5])
    squad[-1] = squad_row(115, "Erling Haaland", "FWD", "Man City", 8.2, 150, [8.2, 7.0, 6.5, 7.1],
                          status="d", chance=75, news="Knock")
    starters = [101] + [r["element"] for r in squad if r["position"] == "DEF"][:4] \
        + [r["element"] for r in squad if r["position"] == "MID"][:4] + [113, 114, 115][-2:]
    bench = [r["element"] for r in squad if r["element"] not in starters]
    return {
        "entry": {"id": 1234, "name": "Pitch Perfect", "overall_rank": 123456, "last_gameweek": NEXT_GW - 1},
        "gameweek": NEXT_GW, "deadline": None, "predictions_gameweek": NEXT_GW, "based_on_gameweek": NEXT_GW - 1,
        "pending_transfers": [], "bank": 15, "team_value": 1012, "free_transfers": 2,
        "chips": [{"chip": "bboost", "label": "Bench Boost", "half": 1, "window": [1, 19], "used_gameweek": None,
                   "planned": False, "available": True, "active": True},
                  {"chip": "wildcard", "label": "Wildcard", "half": 1, "window": [2, 19], "used_gameweek": 4,
                   "planned": False, "available": False, "active": False}],
        "squad": squad,
        "lineup": {"starters": starters, "bench": bench, "captain": 115, "vice_captain": 101,
                   "formation": "4-4-2", "projected_points": 58.4},
        "history": [],
    }


def fake_plan(chip=None):
    return {
        "gameweek": NEXT_GW, "chip": chip,
        "transfers": [{"out": {"element": 108, "name": "Bukayo Saka", "web_name": "Saka", "position": "MID",
                               "team": "Arsenal", "selling_price": 100},
                       "in": {"element": 109, "name": "Cole Palmer", "web_name": "Palmer", "position": "MID",
                              "team": "Chelsea", "price": 105}}],
        "hits": 0, "hit_cost": 0, "bank_before": 15, "bank_after": 10, "squad": [
            {"element": 115, "name": "Erling Haaland", "web_name": "Haaland", "captain": True}],
        "projected_points": 61.3, "reason": "Palmer's next four fixtures are easier.",
        "inputs": {"free_transfers": 2, "max_hits": 0, "horizon": 4, "chips": chip is not None},
    }


def test_team_context_lists_squad_xi_armband_bank_and_chips():
    text = mta.team_context(fake_team())
    assert 'Team "Pitch Perfect" (FPL team ID 1234)' in text
    assert "Bank £1.5m, 2 free transfer(s), team value £101.2m." in text
    assert f"- FWD Haaland, Man City: 8.2 vs Fulham (H); GW{NEXT_GW}-{NEXT_GW + 3} 28.8; £15.0m (sells £14.9m)" \
           " [doubtful, 75% chance: Knock]" in text
    assert "Suggested XI (4-4-2)" in text
    assert "Suggested captain: Haaland (8.2 pts), vice-captain: Raya (4.1 pts)" in text
    assert "Chips playable now: Bench Boost (GW1-19); used: Wildcard in GW4." in text


def test_plan_context_has_moves_chip_and_reason():
    text = mta.plan_context(fake_plan("bboost"), 58.4)
    assert "free transfers only" in text
    assert "- Sell Saka (MID, Arsenal, sells £10.0m) -> buy Palmer (MID, Chelsea, £10.5m)" in text
    assert f"Projected GW{NEXT_GW} points with the plan: 61.3 (current squad's best XI: 58.4)." in text
    assert "Chip this gameweek: Bench Boost." in text
    assert "Planner's reason: Palmer's next four fixtures are easier." in text
    assert mta.plan_moves(fake_plan()) == [["Bukayo Saka", "Cole Palmer"]]


def test_pitch_team_for_the_card():
    payload = mta.pitch_team(fake_team(), lambda *a: None)
    assert len(payload["starters"]) == 11 and len(payload["bench"]) == 4
    assert payload["budget"] == round(payload["total_cost"] + 1.5, 1)
    assert next(s for s in payload["starters"] if s["captain"])["name"] == "Erling Haaland"


def test_chip_guidance_without_a_team():
    ticker = ss.fixture_ticker(base.fixture_rows(), NEXT_GW, 4)
    specials = mta.doubles_and_blanks(ticker)
    text = mta.chip_guidance_context(base.predictions_df(), {NEXT_GW: [{"name": "Salah", "points": 6.0}]}, specials)
    assert f"Triple-captain candidates for GW{NEXT_GW}" in text
    assert "1. Mohamed Salah (MID, Liverpool): 6.0 pts" in text
    assert f"- GW{NEXT_GW}: Salah 6.0 pts" in text
    assert "bench boost or triple captain is worth most in a double gameweek" in text


# ------------------------------------------------------------------ fixture runs

def test_clubs_context_easiest_first_and_one_club():
    ticker = ss.fixture_ticker(base.fixture_rows(), NEXT_GW, 4)
    text = fr.clubs_context(ticker, asked_weeks=6)
    assert text.startswith(f"FPL fixture difficulty (FDR 1 easiest to 5 hardest, from each club's side) for GW{NEXT_GW}-")
    assert "Note: only 4 gameweek(s)" in text
    assert "Easiest runs first:" in text
    one = fr.clubs_context(ticker, ["Arsenal"])
    assert "Arsenal: average FDR" in one and "Hull City (H, FDR 2)" in one


def test_players_context_from_projections(tmp_path):
    base.write_projections(str(tmp_path))
    proj = my_team.load_projections()
    by_el = my_team.projected_by_element(proj, NEXT_GW, 4)
    rows = fr.player_run_rows(proj, by_el, "MID")
    assert [r["name"] for r in rows][:2] == ["Mohamed Salah", "Cole Palmer"]
    text = fr.players_context(rows, "midfielders", 6, [NEXT_GW + i for i in range(4)])
    assert "Projected points for midfielders over" in text
    assert "the projections cover only 4" in text
    assert "1. Mohamed Salah (MID, Liverpool, £14.5m): 23.1 pts" in text


# ------------------------------------------------------------------ sources

def test_sources_line():
    out = answer_sources.build_sources(
        {"season": CURRENT_SEASON, "predictions": True, "projections": True, "team": True, "plan": True},
        current_season=CURRENT_SEASON, predictions_gameweek=10, predictions_mtime=1762416120.0,  # 2025-11-06 08:02 UTC
        projection_weeks=[10, 11, 12, 13], team_gameweek=9)
    assert out[0] == f"{CURRENT_SEASON} season stats"
    assert out[1] in ("GW10 predictions (updated 6 Nov 08:02)", "GW10 predictions (updated 6 Nov 08:02 UTC)")
    assert out[2:] == ["GW10-13 projections", "your team at the GW9 deadline", "the site's transfer planner"]
    assert answer_sources.build_sources({"team": True, "team_error": True}, current_season=CURRENT_SEASON) == []


# ------------------------------------------------------------------ prompt

def test_prompt_is_decision_first_and_keeps_its_rules():
    prompt = PromptTemplates.qa_template(question="q", kg_context="", ml_context="x")
    assert "The FIRST sentence is the direct answer or the recommendation" in prompt
    assert "at most 2-3 supporting numbers" in prompt
    assert 'Don\'t list averages or index values (e.g. "average influence 23.89"' in prompt
    assert "Never say a player has no forecast when he has a line above" in prompt
    assert "YOUR-TEAM RULES" not in prompt and "### Fixtures and Projections" not in prompt
    team = PromptTemplates.qa_template(question="q", kg_context="", team_context="Team X", fixture_context="Run Y")
    assert "### Your FPL Team" in team and "Team X" in team and "YOUR-TEAM RULES" in team
    assert "### Fixtures and Projections" in team and "Run Y" in team
    transfer = PromptTemplates.qa_template(question="q", kg_context="", transfer_context="Move 1")
    assert "each move beyond the free transfers costs 4 points" in transfer


# ------------------------------------------------------------------ request plumbing

def test_fpl_team_id_is_optional_and_bounded():
    import api_main
    assert api_main.QueryRequest(question="hi").fpl_team_id is None
    assert api_main.QueryRequest(question="hi", fpl_team_id=1234).fpl_team_id == 1234
    for bad in (0, 100_000_000, -5):
        with pytest.raises(ValidationError):
            api_main.QueryRequest(question="hi", fpl_team_id=bad)


def run_query(extractor, question, team_id=None, team=None, plan=None, team_error=None, plan_error=None):
    import api_main

    calls = {"get_team": [], "plan_team": []}

    def get_team(entry_id):
        calls["get_team"].append(entry_id)
        if team_error:
            raise team_error
        return team or fake_team()

    def plan_team(entry_id, max_hits=0, chips=False, **_kw):
        calls["plan_team"].append((entry_id, max_hits, chips))
        if plan_error:
            raise plan_error
        return plan or fake_plan("bboost" if chips else None)

    conn = base.FakeConn()
    llm = base.CapturingLLM()
    base.write_projections(os.environ["LIVE_DATA_DIR"])
    state = dict(api_main.app_state, neo4j_conn=conn, llm_manager=llm, intent_classifier=IntentClassifier(),
                 entity_extractor=extractor, player_index=extractor.player_index, latest_season=CURRENT_SEASON,
                 avatar_service=None, ml_integration=None)
    inputs = {"predictions": base.predictions_df(), "photo_codes": {}, "web_names": {},
              "meta": {"next_gameweek": NEXT_GW}}
    request = api_main.QueryRequest(question=question, retrieval_method="Baseline", fpl_team_id=team_id)
    with patch.dict(api_main.app_state, state, clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=inputs), \
            patch.object(api_main, "get_predictions_df", return_value=base.predictions_df()), \
            patch.object(my_team, "get_team", get_team), patch.object(my_team, "plan_team", plan_team):
        response = asyncio.run(api_main.query_fpl(request, conn=conn))
    return json.loads(response.model_dump_json()), llm.prompts[0], calls


def test_transfers_with_a_team_id_use_the_team_and_the_plan(extractor):
    body, prompt, calls = run_query(extractor, "What transfers should I make?", team_id=1234)
    assert body["intent"] == "my_team"
    assert calls == {"get_team": [1234], "plan_team": [(1234, 0, False)]}
    assert body["cypher_query"] == ""
    assert "### Your FPL Team" in prompt and "YOUR-TEAM RULES" in prompt
    assert "- Sell Saka (MID, Arsenal, sells £10.0m) -> buy Palmer" in prompt
    assert body["cards"][0]["type"] == "pitch" and body["cards"][0]["variant"] == "my_team"
    assert "your team at the GW9 deadline" in body["sources"]
    assert "the site's transfer planner" in body["sources"]
    assert any(s.startswith(f"GW{NEXT_GW} predictions") for s in body["sources"])
    assert "My team" not in body["answer"]      # no hint: the team was used


def test_captain_with_a_team_id_skips_the_plan(extractor):
    body, prompt, calls = run_query(extractor, "Who should I captain?", team_id=1234)
    assert body["intent"] == "my_team" and calls["plan_team"] == []
    assert "Suggested captain: Haaland" in prompt


def test_chips_with_a_team_id_run_the_planner_with_chips(extractor):
    body, prompt, calls = run_query(extractor, "When should I use my bench boost?", team_id=1234)
    assert body["intent"] == "chip_advice"
    assert calls["plan_team"] == [(1234, 0, True)]
    assert "Chip this gameweek: Bench Boost." in prompt and "Chips playable now: Bench Boost" in prompt


def test_busy_planner_answers_from_the_squad(extractor):
    body, prompt, _ = run_query(extractor, "What transfers should I make?", team_id=1234,
                                plan_error=my_team.PlanBusy("Lots of people are planning"))
    assert "No transfer plan this time (Lots of people are planning)" in prompt
    assert "the site's transfer planner" not in body["sources"]


def test_unknown_team_gives_a_short_honest_note(extractor):
    body, prompt, _ = run_query(extractor, "Who should I captain?", team_id=99,
                                team_error=my_team.TeamNotFound("no such team"))
    assert "FPL has no team with ID 99." in prompt
    assert not any("your team" in s for s in body["sources"])
    assert all(c["type"] != "pitch" for c in body["cards"])


def test_without_a_team_id_the_answer_points_to_my_team(extractor):
    body, _prompt, calls = run_query(extractor, "Who should I captain this gameweek?")
    assert calls["get_team"] == []
    assert body["answer"].endswith(mta.MY_TEAM_HINT)
    assert "(/my-team)" in body["answer"]


def test_chips_without_a_team_id_get_generic_guidance(extractor):
    body, prompt, _ = run_query(extractor, "When should I use my triple captain?")
    assert body["intent"] == "chip_advice"
    assert f"Triple-captain candidates for GW{NEXT_GW}" in prompt
    assert body["answer"].endswith(mta.MY_TEAM_HINT)


def test_best_defenders_over_several_gameweeks(extractor):
    body, prompt, _ = run_query(extractor, "Best midfielders for the next 6 gameweeks")
    assert body["intent"] == "fixture_run"
    assert "### Fixtures and Projections" in prompt and "the projections cover only 4" in prompt
    card = body["cards"][0]
    assert card["type"] == "leaderboard" and card["rows"][0]["player"]["name"] == "Mohamed Salah"
    assert f"GW{NEXT_GW}-{NEXT_GW + 3} projections" in body["sources"]


def test_easiest_fixtures_use_the_ticker(extractor):
    body, prompt, _ = run_query(extractor, "Which teams have the easiest fixtures?")
    assert body["intent"] == "fixture_run"
    assert "Easiest runs first:" in prompt
    assert "FPL fixture difficulty" in body["sources"]


# ------------------------------------------------------------------ remaining questions

def test_remaining_does_not_record_a_hit():
    now = [0.0]
    limiter = RateLimiter([(3, 60), (5, 3600)], clock=lambda: now[0])
    assert limiter.remaining("a") == (3, 60)
    limiter.check("a")
    assert limiter.remaining("a") == (2, 60)
    assert limiter.remaining("a") == (2, 60)
    limiter.check("a")
    limiter.check("a")
    now[0] = 120.0           # the minute window has passed, the hour hasn't
    assert limiter.remaining("a") == (2, 3600)


def test_query_endpoints_send_the_remaining_count():
    import api_main
    limiter = RateLimiter([(10, 60), (120, 3600)])
    client = TestClient(api_main.app)
    state = dict(api_main.app_state, neo4j_conn=object())
    with patch.object(api_main, "query_limiter", limiter), patch.dict(api_main.app_state, state):
        body = {"question": "hi", "is_first_message": True, "fpl_team_id": 1234}
        first = client.post("/api/query", json=body, headers={"X-Forwarded-For": "7.7.7.7"})
        stream = client.post("/api/query/stream", json=body, headers={"X-Forwarded-For": "7.7.7.7"})
    assert first.status_code == 200 and first.headers["X-RateLimit-Remaining"] == "9"
    assert first.headers["X-RateLimit-Window"] == "60"
    assert first.json()["sources"] == []
    assert stream.status_code == 200 and stream.headers["X-RateLimit-Remaining"] == "8"
