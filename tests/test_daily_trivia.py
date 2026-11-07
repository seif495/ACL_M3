"""Daily trivia: deterministic per UTC day, answers only come back from the check. Offline."""
import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import daily_trivia as dt
from trivia.trivia_generator import TriviaCategory, TriviaQuestion

_real_build = dt.build_daily


class FakeGenerator:
    """Draws from `random` like the real one, with no database."""

    def __init__(self, conn):
        pass

    def generate_question(self, category=None, difficulty=None, season=None):
        n = random.randint(0, 10**9)
        options = [f"opt{n}-{i}" for i in range(4)]
        return TriviaQuestion(
            question_id="x", question=f"Question {n}?", correct_answer=options[random.randint(0, 3)],
            options=options, category=TriviaCategory.PLAYER_STATS, difficulty=difficulty,
            explanation="Because.", source_query="MATCH", metadata={})

    def check_answer(self, q, a):
        return q.correct_answer.lower() == a.lower(), ""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    dt._cache.clear()
    monkeypatch.setattr(dt, "TriviaGenerator", FakeGenerator)
    monkeypatch.setattr(dt, "build_daily", lambda conn, date, generator_cls=None: _real_build(conn, date, FakeGenerator))
    yield
    dt._cache.clear()


def client(monkeypatch, date="2026-11-07"):
    monkeypatch.setattr(dt, "today_utc", lambda: date)
    app = FastAPI()
    dt.register(app, lambda: object())
    return TestClient(app)


def test_same_set_for_same_date_and_different_for_another():
    a = _real_build(None, "2026-11-07", FakeGenerator)
    b = _real_build(None, "2026-11-07", FakeGenerator)
    c = _real_build(None, "2026-11-08", FakeGenerator)
    assert [q.question for q in a] == [q.question for q in b]
    assert [q.question for q in a] != [q.question for q in c]
    assert [q.difficulty for q in a] == dt.PLAN
    assert a[0].question_id == "daily-2026-11-07-1"


def test_global_random_state_is_left_alone():
    random.seed(5)
    expected = random.random()
    random.seed(5)
    _real_build(None, "2026-11-07", FakeGenerator)
    assert random.random() == expected


def test_get_has_no_answers(monkeypatch):
    r = client(monkeypatch).get("/api/trivia/daily")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-11-07" and len(body["questions"]) == 5
    assert "correct_answer" not in r.text and "Because." not in r.text
    assert set(body["questions"][0]) == {"question_id", "question", "options", "category", "difficulty"}


def test_check_scores_and_reveals(monkeypatch):
    c = client(monkeypatch)
    qs = c.get("/api/trivia/daily").json()["questions"]
    truth = {q.question_id: q for q in dt._cache["2026-11-07"]}
    answers = [{"question_id": q["question_id"],
                "answer": truth[q["question_id"]].correct_answer if i < 3 else "wrong"} for i, q in enumerate(qs)]
    r = c.post("/api/trivia/daily/check", json={"date": "2026-11-07", "answers": answers})
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 3 and body["total"] == 5
    assert [x["correct"] for x in body["results"]] == [True, True, True, False, False]
    assert all(x["correct_answer"] for x in body["results"])


def test_check_rejects_another_day(monkeypatch):
    c = client(monkeypatch)
    r = c.post("/api/trivia/daily/check", json={"date": "2026-11-06", "answers": []})
    assert r.status_code == 409


def test_set_is_cached_for_the_day(monkeypatch):
    calls = []
    monkeypatch.setattr(dt, "build_daily", lambda conn, date, generator_cls=None: calls.append(date) or _real_build(conn, date, FakeGenerator))
    c = client(monkeypatch)
    c.get("/api/trivia/daily")
    c.get("/api/trivia/daily")
    assert calls == ["2026-11-07"]
