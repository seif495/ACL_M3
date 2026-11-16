"""
Questions the rule-based pipeline can't read as they are: ones not in English, and ones that ask
two separate things.

The intent classifier, the entity extractor and the site-facts triggers are English patterns, and
each question gets one route. So "¿Quién es el mejor capitán esta semana?" matched nothing, and
"Is Saka injured and who should I captain?" answered half. For those two cases only, one short
LLM call rewrites the message as 1-3 standalone English questions:

    reason(question)            -> "language", "compound" or None (pure; None is the usual case
                                   and costs nothing)
    rewrite(question, llm, ...) -> Rewrite(language, questions) or None

Each question then goes through the normal pipeline; the answers are joined, and written in the
user's language when it isn't English. Anything going wrong (no LLM, a timeout, text that isn't
the JSON asked for) returns None and the question is answered as before. ASSISTANT_REWRITE=0
turns it off.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

MODEL = "gpt-oss-20b"       # the small, fast model: this is a rewrite, not an answer
TIMEOUT_SECONDS = 8
MAX_QUESTIONS = 3
MAX_QUESTION_CHARS = 300

# Function words and FPL words that mark a message as English. Names don't count either way.
_ENGLISH = frozenset("""
a an the is are was were be been do does did will would should could can may might must have has had
i you he she it we they me my your his her their our him them this that these those there here
who what when where why how which whose and or but if than then so not no yes for of in on at to from
with without about over under after before between against during vs v versus per by as into
best top most more less better worse good bad next last this week gameweek season player players team
teams points goals assists captain transfer transfers pick buy sell bench start compare stats fixtures
table price form injured fit worth tell show give me many much any all
""".split())
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_NON_LATIN = re.compile(r"[Ͱ-ϿЀ-ӿ֐-ۿऀ-෿฀-๿぀-ヿ㐀-鿿가-힯]")

_STARTER = (r"(?:who|whom|what|when|where|why|how|which|is|are|was|were|will|would|should|can|could|does|do|did|"
            r"tell|show|give|compare|list|name)")
# "... ? Who should I captain", and "... and who should I captain": a second question, not
# "Haaland and Salah" or "I have 1 free transfer"
_SECOND_SENTENCE = re.compile(rf"[?.!;]\s+{_STARTER}\b(?:\s+\S+){{2,}}", re.IGNORECASE)
_CONNECTOR = re.compile(rf"\b(?:and|also|plus|then)\s+(?:also\s+)?{_STARTER}\b(?:\s+\S+){{2,}}", re.IGNORECASE)


def enabled() -> bool:
    return os.getenv("ASSISTANT_REWRITE", "1").strip().lower() not in ("0", "false", "no", "off")


def looks_english(text: str) -> bool:
    """Whether the pipeline's English patterns can read `text`. Short messages (a name, "stats")
    count as English; a longer one needs a share of English function words."""
    if _NON_LATIN.search(text or ""):
        return False
    found = _WORD.findall(text or "")
    # Capitalised words are mostly names ("Erling Haaland Bukayo Saka stats" is English); the
    # first word counts whatever its case
    words = [w.lower() for i, w in enumerate(found) if i == 0 or not w[0].isupper()]
    if len(words) < 3:
        return True
    hits = sum(w in _ENGLISH for w in words)
    return hits / len(words) >= 0.2


def is_compound(text: str) -> bool:
    return bool(_SECOND_SENTENCE.search(text or "") or _CONNECTOR.search(text or ""))


def reason(question: str) -> Optional[str]:
    """Why a question needs rewriting before the pipeline reads it, or None."""
    if not enabled() or not (question or "").strip():
        return None
    if not looks_english(question):
        return "language"
    return "compound" if is_compound(question) else None


@dataclass
class Rewrite:
    language: Optional[str]     # the user's language when it isn't English, e.g. "Spanish"
    questions: List[str]        # 1-3 standalone English questions


PROMPT = """You prepare messages for a Fantasy Premier League (FPL) assistant that only reads simple English questions.

Rewrite the user's message as standalone questions in English:
- Translate to English if needed. Keep player and club names exactly as written.
- If the message asks for separate things, give one question for each (at most {max_questions}). If it asks one thing, give one question. Keep the players of one comparison together in one question ("Haaland or Salah"), and likewise a list of transfers; a statement of context ("I have 1 free transfer") stays inside the question it belongs to. A comparison followed by an unrelated request is two questions.
- Replace pronouns with the names they refer to, from the message or the conversation.
- Do not answer, explain or add anything.

Conversation so far:
{history}

User's message:
{question}

Reply with JSON only, in this shape:
{{"language": "<the language of the user's message, in English, e.g. English, Arabic, Spanish>", "questions": ["<question 1>", "<question 2>"]}}"""


def _history_text(chat_history: Optional[Sequence[Dict[str, str]]]) -> str:
    turns = []
    for msg in list(chat_history or [])[-4:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        turns.append(f"{role}: {str(msg.get('content') or '')[:300]}")
    return "\n".join(turns) or "(none)"


def parse(text: str) -> Optional[Rewrite]:
    """The Rewrite in an LLM reply, or None when it isn't the JSON asked for."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return None
    questions = [re.sub(r"\s+", " ", q).strip() for q in data["questions"] if isinstance(q, str)]
    questions = [q[:MAX_QUESTION_CHARS] for q in questions if len(q) >= 3][:MAX_QUESTIONS]
    if not questions:
        return None
    language = data.get("language")
    language = language.strip()[:30] if isinstance(language, str) and language.strip() else None
    if language and language.lower() in ("english", "en"):
        language = None
    return Rewrite(language=language, questions=questions)


def rewrite(question: str, llm: Any, chat_history: Optional[Sequence[Dict[str, str]]] = None) -> Optional[Rewrite]:
    """One LLM call (blocking). None when there is no LLM, it fails, or its reply can't be used;
    also when the reply changes nothing (one English question)."""
    if llm is None or not getattr(llm, "available", False):
        return None
    prompt = PROMPT.format(max_questions=MAX_QUESTIONS, history=_history_text(chat_history), question=question)
    try:
        response = llm.generate(prompt, model_key=MODEL, timeout=TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - the question is still answered as it is
        logger.warning("Question rewrite failed", exc_info=True)
        return None
    if not getattr(response, "success", False):
        logger.warning("Question rewrite failed: %s", getattr(response, "error", None))
        return None
    out = parse(getattr(response, "text", "") or "")
    if out is None:
        logger.warning("Question rewrite: unusable reply %r", (getattr(response, "text", "") or "")[:120])
        return None
    if out.language is None and len(out.questions) == 1:
        return None
    return out
