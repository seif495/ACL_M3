"""Questions that aren't in English, or that ask two things (services/question_rewrite.py).

The pipeline's patterns are English and give one route a question, so those two cases are
rewritten first by one short LLM call into standalone English questions; each is answered through
the normal pipeline and the answers are joined (in the user's language). The LLM is faked here.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

import test_assistant_facts as t  # noqa: E402
from config.settings import CURRENT_SEASON  # noqa: E402
from llm.prompts import PromptTemplates  # noqa: E402
from preprocessing.entity_extractor import EntityExtractor  # noqa: E402
from preprocessing.intent_classifier import IntentClassifier  # noqa: E402
from services import question_rewrite as qr  # noqa: E402
from services import season_service as ss  # noqa: E402


@pytest.fixture(autouse=True)
def rewrite_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_REWRITE", "1")
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    ss.clear_cache()
    yield
    ss.clear_cache()


@pytest.mark.parametrize("question, expected", [
    ("Who should I captain this week?", None),
    ("Compare Haaland and Salah", None),                       # one comparison, not two questions
    ("Arsenal and Chelsea fixtures", None),
    ("Should I sell Haaland for Isak and Palmer for Rice next gameweek? I have 1 free transfer.", None),
    ("Erling Haaland Mohamed Salah Bukayo Saka stats", None),   # names aren't a foreign language
    ("haaland salah saka stats", None),
    ("top scorers", None), ("best gk", None), ("", None),
    ("Is Saka injured and who should I captain?", "compound"),
    ("Who is top of the league? And when is the deadline?", "compound"),
    ("Tell me about Saka and show his fixtures", "compound"),
    ("Quien es el mejor capitan esta semana?", "language"),
    ("Qui est le meilleur defenseur a moins de 5m ?", "language"),
    ("Wer ist verletzt bei Arsenal diese Woche?", "language"),
    ("Haaland o Salah para capitan", "language"),
    ("من أفضل قائد هذا الأسبوع؟", "language"),
])
def test_reason(question, expected):
    assert qr.reason(question) == expected


def test_reason_is_off_with_the_switch(monkeypatch):
    monkeypatch.setenv("ASSISTANT_REWRITE", "0")
    assert qr.reason("Quien es el mejor capitan esta semana?") is None


def test_parse_takes_only_the_json_asked_for():
    out = qr.parse('Sure!\n{"language": "Spanish", "questions": ["Who is the best captain this week?"]}')
    assert out == qr.Rewrite("Spanish", ["Who is the best captain this week?"])
    assert qr.parse('{"language": "English", "questions": ["a b c", "  d   e f ", 5, "x"]}') == \
        qr.Rewrite(None, ["a b c", "d e f"])
    many = qr.parse(json.dumps({"language": "en", "questions": [f"question number {i}" for i in range(9)]}))
    assert len(many.questions) == qr.MAX_QUESTIONS
    assert len(qr.parse(json.dumps({"questions": ["q" * 900]})).questions[0]) == qr.MAX_QUESTION_CHARS
    for bad in ("", "no json here", "{not json}", '{"questions": "one"}', '{"questions": []}', '["a question"]'):
        assert qr.parse(bad) is None


class ScriptedLLM:
    """Answers the rewrite prompt with `reply`, anything else with a fixed answer."""
    available = True

    def __init__(self, reply, ok=True):
        self.reply, self.ok, self.prompts, self.models = reply, ok, [], []

    def generate(self, prompt, model_key=None, timeout=None):
        self.prompts.append(prompt)
        self.models.append(model_key)
        if prompt.startswith("You prepare messages"):
            return SimpleNamespace(success=self.ok, text=self.reply, error=None if self.ok else "rate limited")
        asked = prompt.split("### User Question:\n", 1)[1].split("\n", 1)[0]
        return SimpleNamespace(success=True, text=f"Answer to: {asked}", error=None)


def test_rewrite_uses_the_small_model_and_survives_failures():
    llm = ScriptedLLM('{"language": "Spanish", "questions": ["Who is the best captain this week?"]}')
    history = [{"role": "user", "content": "How is Saka doing?"}, {"role": "assistant", "content": "Well."}]
    out = qr.rewrite("Quien es el mejor capitan esta semana?", llm, history)
    assert out == qr.Rewrite("Spanish", ["Who is the best captain this week?"])
    assert llm.models == [qr.MODEL] and "User: How is Saka doing?" in llm.prompts[0]
    assert "Quien es el mejor capitan esta semana?" in llm.prompts[0]
    # One English question back: nothing to change
    assert qr.rewrite("Is Saka fit and well?", ScriptedLLM('{"language": "English", "questions": ["Is Saka fit?"]}')) is None
    assert qr.rewrite("x y z", ScriptedLLM("I think the answer is Haaland.")) is None
    assert qr.rewrite("x y z", ScriptedLLM("{}", ok=False)) is None
    assert qr.rewrite("x y z", None) is None
    assert qr.rewrite("x y z", SimpleNamespace(available=False)) is None

    class Broken:
        available = True

        def generate(self, *args, **kwargs):
            raise TimeoutError("no reply")
    assert qr.rewrite("x y z", Broken()) is None


def test_prompt_answers_in_the_users_language():
    prompt = PromptTemplates.qa_template(question="Who is the best captain this week?", kg_context="",
                                         answer_language="Spanish",
                                         original_question="Quien es el mejor capitan esta semana?")
    assert "### The user's own words (in Spanish):\nQuien es el mejor capitan esta semana?" in prompt
    assert "Write the whole answer in Spanish" in prompt
    assert "### User Question:\nWho is the best captain this week?" in prompt
    assert "LANGUAGE" not in PromptTemplates.qa_template(question="q", kg_context="")


def run(question, llm, tmp_path, stream=False):
    import api_main

    pd.DataFrame(t.status_rows()).to_csv(tmp_path / f"player_status_{CURRENT_SEASON.replace('-', '_')}.csv", index=False)
    (tmp_path / "meta.json").write_text(json.dumps(t.meta()), encoding="utf-8")
    conn, extractor = t.FakeConn(), EntityExtractor()
    extractor.player_index = t.player_index()
    state = dict(api_main.app_state, neo4j_conn=conn, llm_manager=llm, intent_classifier=IntentClassifier(),
                 entity_extractor=extractor, player_index=extractor.player_index, latest_season=CURRENT_SEASON,
                 avatar_service=None, ml_integration=None)
    inputs = {"predictions": t.predictions_df(), "photo_codes": {}, "web_names": {},
              "meta": {"next_gameweek": t.NEXT_GW}}
    request = api_main.QueryRequest(question=question, retrieval_method="Baseline", chat_history=[])

    async def events():
        return [e async for e in api_main._answer_events(request, conn)]

    with patch.dict(api_main.app_state, state, clear=True), \
            patch.object(api_main, "_answer_card_inputs", return_value=inputs), \
            patch.object(api_main, "get_predictions_df", return_value=t.predictions_df()):
        if stream:
            return asyncio.run(events())
        return json.loads(asyncio.run(api_main.query_fpl(request, conn=conn)).model_dump_json())


TWO = '{"language": "English", "questions": ["Is Bukayo Saka injured?", "When is the next deadline?"]}'


def test_two_questions_in_one_message_are_both_answered(tmp_path):
    llm = ScriptedLLM(TWO)
    body = run("Is Saka injured and when is the deadline?", llm, tmp_path)
    assert body["answer"] == "Answer to: Is Bukayo Saka injured?\n\nAnswer to: When is the next deadline?"
    assert "Bukayo Saka (Arsenal): doubtful" in body["ml_context"]
    assert f"Next deadline: Gameweek {t.NEXT_GW}" in body["ml_context"]
    assert "FPL's availability flags and team news" in body["sources"] and "FPL's gameweek deadlines" in body["sources"]
    assert body["entities"]["players"] == ["Bukayo Saka"] and [c["type"] for c in body["cards"]] == ["player"]
    assert len(llm.prompts) == 3                                   # the rewrite, then one answer each
    assert "LANGUAGE" not in llm.prompts[1]


def test_a_spanish_question_is_routed_in_english_and_answered_in_spanish(tmp_path):
    llm = ScriptedLLM('{"language": "Spanish", "questions": ["When is the next deadline?"]}')
    body = run("Cuando es la proxima fecha limite de la jornada?", llm, tmp_path)
    assert body["intent"] == "site_data" and f"Next deadline: Gameweek {t.NEXT_GW}" in body["ml_context"]
    answer_prompt = llm.prompts[1]
    assert "Write the whole answer in Spanish" in answer_prompt
    assert "Cuando es la proxima fecha limite de la jornada?" in answer_prompt


def test_a_failed_rewrite_answers_the_message_as_it_is(tmp_path):
    llm = ScriptedLLM("not json at all")
    body = run("Is Saka injured and when is the deadline?", llm, tmp_path)
    assert body["answer"] == "Answer to: Is Saka injured and when is the deadline?"
    assert len(llm.prompts) == 2


def test_an_ordinary_question_makes_no_extra_call(tmp_path):
    llm = ScriptedLLM(TWO)
    body = run("When is the next deadline?", llm, tmp_path)
    assert body["answer"] == "Answer to: When is the next deadline?" and len(llm.prompts) == 1


def test_the_stream_sends_a_two_part_answer_whole(tmp_path):
    events = run("Is Saka injured and when is the deadline?", ScriptedLLM(TWO), tmp_path, stream=True)
    names = [e.split("\n", 1)[0].removeprefix("event: ") for e in events]
    assert names == ["cards", "delta", "answer", "done"]
    done = json.loads(events[-1].split("data: ", 1)[1])
    assert done["answer"] == "Answer to: Is Bukayo Saka injured?\n\nAnswer to: When is the next deadline?"
