"""The top-predictions context the LLM answers "who should I captain/pick" from."""
import os
import sys

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import api_main  # noqa: E402
from llm.prompts import PromptTemplates  # noqa: E402


def _predictions(rows):
    return pd.DataFrame(
        [
            {"name": name, "position": pos, "predicted_points": pts, "opponent": opp, "is_home": home,
             "status": "a", "news": "", "chance_of_playing_next_round": None}
            for name, pos, pts, opp, home in rows
        ]
    )


# GW10 2026-27 as the site served it: a well-known midfielder sits below a less-known one
GW10 = _predictions([
    ("Alexander Isak", "FWD", 4.3, "Crystal Palace", False),
    ("Cole Palmer", "MID", 4.4, "Sunderland", False),
    ("Erling Haaland", "FWD", 5.4, "Nott'm Forest", False),
    ("Marcus Tavernier", "MID", 4.6, "Ipswich", False),
    ("Francisco Evanilson de Lima Barbosa", "FWD", 4.4, "Ipswich", False),
    ("Bukayo Saka", "MID", 3.9, "Hull City", True),
])


def test_entries_are_numbered_best_first():
    lines = api_main._top_predictions_context(GW10, 5).splitlines()
    assert lines[0] == "ML Predictions - Top 5 performers for next gameweek, ranked best first:"
    assert lines[1] == "1. Erling Haaland (FWD): 5.4 pts vs Nott'm Forest (A)"
    assert lines[2] == "2. Marcus Tavernier (MID): 4.6 pts vs Ipswich (A)"
    assert [line.split(".")[0] for line in lines[1:6]] == ["1", "2", "3", "4", "5"]
    assert "Saka" not in "\n".join(lines)


def test_names_the_best_ranked_player_of_each_position():
    summary = api_main._top_predictions_context(GW10, 5).splitlines()[-1]
    assert summary == (
        "Highest-ranked in this list by position: MID #2 Marcus Tavernier (4.6 pts); "
        "FWD #1 Erling Haaland (5.4 pts)"
    )


def test_a_one_position_list_has_no_position_summary():
    midfielders = GW10[GW10["position"] == "MID"]
    context = api_main._top_predictions_context(midfielders, 5)
    assert context.startswith("ML Predictions - Top 3 performers")
    assert "by position" not in context


def test_availability_flags_stay_on_the_entry():
    flagged = GW10.copy()
    flagged.loc[flagged["name"] == "Marcus Tavernier", ["status", "chance_of_playing_next_round"]] = ["d", 75]
    line = api_main._top_predictions_context(flagged, 5).splitlines()[2]
    assert line == "2. Marcus Tavernier (MID): 4.6 pts vs Ipswich (A) [doubtful, 75% chance of playing]"


def test_prompt_tells_the_llm_to_keep_the_ranking():
    prompt = PromptTemplates.qa_template(
        question="Who should I captain this gameweek?",
        kg_context="",
        ml_context=api_main._top_predictions_context(GW10, 5),
    )
    assert "Never skip a higher-ranked player for a better-known one" in prompt
    # The worked example no longer pairs Haaland with a named second player
    assert "Palmer: 7.5" not in prompt
