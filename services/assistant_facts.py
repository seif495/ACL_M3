"""
Site facts for the assistant: current-season data the chat question asks for but no Cypher
intent covers.

The intent classifier maps a question to one Cypher query, so ordinary FPL questions fell
through to no data at all ("Is Saka injured?", "When is the deadline?", "Where are Arsenal in the
table?", "Who will win the league?", "How does bonus work?") or to the wrong data ("Best
defenders under 5.0m" got the clean-sheet leaders, "most owned" got gameweek 1's ownership).
Each of those has an answer in data the site already serves on its pages. This module spots them
by their subject words, whatever intent the classifier chose, and returns the facts as text for
the prompt:

    gather(question, conn, players=..., teams=..., positions=..., intent=..., ...) -> [Fact]

Several facts can answer one question ("Is Saka fit and who do Arsenal play next?"). A fact
marked `exclusive` replaces the intent's own query, which would only add unrelated numbers; the
match forecast keeps it (past meetings belong next to the forecast). Named players always come
with their outlook (services/player_outlook.py: prediction, projections, price, flag, fixtures,
season so far), which is also the answer to a question that names a player and matched no
query at all ("What about Mbeumo?", "Is Haaland worth his price?").

Every provider is a pure formatter over a payload plus a thin loader; a provider that fails is
logged and left out, so gather() never raises. Nothing here calls the LLM or the network.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config.settings import CURRENT_SEASON
from services import season_service as ss

logger = logging.getLogger(__name__)

try:  # Windows without tzdata has no zone database: fall back to UTC
    from zoneinfo import ZoneInfo
    _UK = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover - depends on the platform
    _UK = None

MAX_FACTS = 3               # besides the named players' outlook
MAX_OUTLOOK_PLAYERS = 3
LIST_ROWS = 8
FORM_GAMEWEEKS = 4
DIFFERENTIAL_OWNERSHIP = 10.0    # percent: under it a player is a differential
BUDGET_CAPS = {"GK": 4.5, "DEF": 4.5, "MID": 5.5, "FWD": 6.0}   # "budget"/"cheap" without a price
STATUS_WORDS = {"a": "available", "d": "doubtful", "i": "injured", "s": "suspended",
                "u": "unavailable (left the club or not registered)", "n": "not in the squad"}
POSITION_PLURAL = {"GK": "goalkeepers", "DEF": "defenders", "MID": "midfielders", "FWD": "forwards"}

# Intents with no Cypher query of their own (or a generic recommendation list): a named player's
# outlook is then the whole answer
NO_QUERY_INTENTS = {"general_question", "unknown", "recommendation", "best_value"}


@dataclass
class Fact:
    key: str
    text: str
    source: Optional[str] = None    # for the answer's "Based on: ..." line
    exclusive: bool = True          # replaces the intent's Cypher query
    predictions: bool = False       # built from the predictions file (its own "Based on" entry)


# ------------------------------------------------------------------ what a question asks for

def _rx(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern, re.IGNORECASE)


AVAILABILITY_RE = _rx(
    r"\b(?:injur\w*|fit|fitness|unavailable|availability|doubt\w*|flagged|suspend\w*|suspension|banned|"
    r"ruled out|sidelined|return date|team news|(?:yellow|red) flags?)\b"
    r"|\b(?:back|return\w*) from\b|\bwhen (?:is|will|does|do|are) .{0,40}\b(?:back|return\w*)\b")
# "Will Saka play?", "Is Haaland starting?", "Is Palmer out?": about availability only with a player
PLAYING_RE = _rx(
    r"\bwill .{1,30}\b(?:play|start|feature|be fit|be available|be back)\b"
    r"|\b(?:is|are) .{1,30}\b(?:playing|starting|available|fit|back)\b|\b(?:is|are) .{1,30}\bout\s*\??$")
DEADLINE_RE = _rx(
    r"\bdeadline\b|\bwhen (?:is|does|do) (?:the )?(?:next )?(?:gameweek|game week|gw)\b"
    r"|\b(?:what|which) (?:gameweek|game week|gw) (?:is it|are we|is this|is next)\b"
    r"|\bhow long (?:until|till|before|do i have)\b")
TABLE_RE = _rx(
    r"\b(?:league )?table\b|\bstandings\b|\b(?:top|bottom) of the (?:league|table|premier league|prem)\b"
    r"|\b(?:league|table) (?:position|leaders?)\b|\bwhat position\b|\b(?:leading|leads) the (?:league|table)\b"
    r"|\bwhere (?:are|is) .{1,30} in the (?:league|table|premier league)\b|\brelegation zone\b"
    r"|\bhow many points (?:do|does|have|has)\b.{1,30}\b(?:have|got)\b")
SEASON_FORECAST_RE = _rx(
    r"\bwin the (?:league|title|premier league|prem)\b|\btitle (?:race|chances?|odds|favou?rites?|contenders?)\b"
    r"|\b(?:be|get|gets|getting|go|goes|going) (?:relegated|down)\b|\brelegat\w+ (?:battle|fight|chances?|candidates?|odds)\b"
    r"|\bwho (?:will|is going to|'s going to) (?:be |get |go )?(?:relegated|down)\b|\bstay(?:s|ing)? up\b|\bsurviv\w+\b"
    r"|\b(?:finish\w*|make|makes|get|qualify\w*) (?:in |into |for )?(?:the )?(?:top (?:four|4|five|5|six|6)|champions league|europe)\b"
    r"|\b(?:predicted|final|projected) (?:final )?(?:league )?table\b|\bwhere will .{1,30} finish\b"
    r"|\bhow will the (?:league|table|season) (?:end|finish)\b|\bchampions\b.{0,20}\bthis season\b")
# A result question: with one club it is about that club's next match
MATCH_FORECAST_RE = _rx(
    r"\bwho (?:will|is going to|'s going to|would|do you think will) win\b|\bwho wins\b"
    r"|\bwin(?:s|ning)? (?:against|vs\.?|versus|over|at)\b|\bbeat(?:s|ing)?\b|\bscore ?line\b"
    r"|\bfavou?rites?\b|\bchances? (?:of|to) (?:win\w*|beat\w*|a draw)\b|\bwill .{1,25}\b(?:win|lose|draw)\b"
    r"|\b(?:score|result|match|game) predict\w*\b|\bpredict\w*\b.{0,30}\b(?:score|result|match|game|winner)\b")
# With two clubs, any forecast wording ("Predict Arsenal v Chelsea")
TWO_CLUB_FORECAST_RE = _rx(r"\bpredict\w*\b|\bforecast\w*\b|\bwho(?:'s| is) going to win\b|\blikely\b|\bodds\b")
CLUB_FIXTURES_RE = _rx(
    r"\b(?:who|when|what time|where) (?:do|does|did|are|is|will)\b.{0,40}\b(?:play|plays|playing|played|face|faces|facing|kick ?off)\b"
    r"|\bnext (?:game|match|fixture|opponents?)\b|\b(?:last|latest|previous|most recent) (?:game|match|result|fixture|gameweek|gw|week(?:end)?)\b"
    r"|\bhow did .{1,30}\b(?:do|get on|play)\b|\b(?:score|result)\b|\bupcoming (?:fixtures?|games?|matches)\b"
    r"|\bwhen (?:is|are|do|does)\b|\bkick ?off\b")
MODEL_RE = _rx(
    r"\bhow (?:accurate|reliable|good|trustworthy) (?:is|are) (?:your|the|these|this|those)\b|\bcan i trust\b|\btrack record\b"
    r"|\bhow (?:do|does) (?:you|it|the model|the site|this site|pitch me) (?:predict|forecast|work|calculate|come up|make|decide)\b"
    r"|\b(?:what|which) (?:model|algorithm|data|features?) (?:do|does|are|is)\b|\bwhich model\b"
    r"|\bhow (?:are|is) (?:the |your |these )?(?:predictions?|predicted points|forecasts?|projections?) (?:made|calculated|worked out|generated|built)\b"
    r"|\b(?:predictions?|forecasts?|model) (?:are |is )?based on\b|\b(?:your|the|this) (?:ml |ai )?model\b"
    r"|\bhow (?:does|do) (?:the |your )?(?:predictions?|forecasts?|model) work\b|\bmachine learning\b")
MANAGER_RE = _rx(
    r"\b(?:ml|ai|model|robot|bot) manager\b|\bmodel(?:'s)? (?:own )?(?:fpl )?(?:team|squad)\b"
    r"|\byour (?:own )?(?:fpl )?(?:team|squad|rank|manager|points total)\b"
    r"|\bhow (?:is|are|did) (?:the |your )?(?:manager|bot|ai) (?:doing|do|perform\w*)\b")
EXPLAIN_RE = _rx(
    r"\bwhy\b.{0,80}\b(?:predict\w*|forecast\w*|project\w*|expected|rated|ranked|so (?:low|high)|only \d)"
    r"|\bwhat(?:'s| is) (?:behind|driving)\b|\bexplain\b.{0,40}\b(?:prediction|forecast|projection|points)\b"
    r"|\bwhy (?:so|such a) (?:low|high)\b")
RULES_RE = _rx(
    r"\b(?:rules?|scoring system|points system)\b"
    r"|\bhow (?:do|does|are|is) .{0,40}\b(?:work|works|scored|calculated|awarded|decided)\b"
    r"|\bhow many points (?:for|is|are|do you get|do i get|does|do)\b"
    r"|\bwhat(?:'s| is| are| does) (?:a |an |the )?(?:free hit|wildcard|bench boost|triple captain|chips?|bps|"
    r"bonus(?: points?)?(?: system)?|ict(?: index)?|fdr|fixture difficulty(?: rating)?|differentials?|(?:points )?hit|"
    r"auto[- ]?subs?(?:titutions?)?|vice[- ]?captain|price changes?|defensive contributions?|xg|xa|"
    r"expected goals|expected assists|template|double gameweek|blank gameweek|dgw|bgw)\b(?: mean| stand for)?"
    r"|\bexplain (?:the )?(?:rules|scoring|chips?|bonus|bps|ict|fdr|free hit|wildcard|bench boost|triple captain)\b"
    r"|\bhow many (?:free )?transfers\b|\bhow many chips\b|\bhow much (?:is|does) (?:a |the )?hit\b"
    r"|\bselling price\b|\bhow (?:do|does) (?:the )?(?:captain\w*|bench|auto[- ]?subs?)\b")
_NOT_A_PRICE = (r"(?!\s*(?:goals?|assists?|points?|pts|minutes|mins|games?|matches|%|percent|gameweeks?|gws?|weeks?|"
                r"owned|players?|clean))")
PRICE_RE = _rx(
    r"(?:under|below|less than|cheaper than|up to|max(?:imum)?(?: of)?|at most|within|≤|<=?)\s*£?\s*"
    r"(\d{1,2}(?:\.\d)?)(?![\d.])(?:\s*(?:m\b|mil\w*)|" + _NOT_A_PRICE + r")"
    r"|£\s*(\d{1,2}(?:\.\d)?)(?![\d.])\s*m?\b|\b(\d{1,2}(?:\.\d)?)\s*m (?:or (?:less|under|below)|and (?:under|below))")
BUDGET_RE = _rx(r"\b(?:budget|cheap\w*|bargains?|enablers?|low[- ]cost|inexpensive)\b")
DIFFERENTIAL_RE = _rx(r"\bdifferentials?\b|\b(?:low|under|lightly)[- ]owned\b|\bunder the radar\b|\bhidden gems?\b")
OWNERSHIP_RE = _rx(
    r"\bmost (?:owned|selected|popular|picked)\b|\bhighest[- ]owned\b|\bownership\b|\btemplate\b"
    r"|\bwho (?:does|do) (?:everyone|everybody|most (?:people|managers)) (?:own|have)\b"
    r"|\bhow many (?:people|managers) (?:own|have)\b|\bhow (?:owned|popular) is\b")
TRANSFER_TRENDS_RE = _rx(
    r"\bmost transferred\b|\btransferred (?:in|out)\b|\bpopular transfers?\b|\bbandwagons?\b"
    r"|\bwho (?:is|are) (?:everyone|everybody|people|managers) (?:buying|selling|bringing in|getting rid of|transferring)\b"
    r"|\b(?:top|most popular) transfers? (?:in|out)\b")
PRICE_CHANGE_RE = _rx(
    r"\bprice (?:rises?|rose|risers?|increases?|falls?|fell|fallers?|drops?|dropped|changes?|changed)\b"
    r"|\b(?:risen|rose|rising|fallen|fell|falling|dropped|dropping|gone up|gone down|going up|going down) in price\b"
    r"|\bwhose price\b")
DOUBLE_BLANK_RE = _rx(
    r"\bdouble gameweeks?\b|\bblank gameweeks?\b|\bdgws?\b|\bbgws?\b|\bplay(?:s|ing)? twice\b"
    r"|\b(?:two|2) (?:games|matches|fixtures) in (?:a|one|the) (?:gameweek|week)\b|\bdoubles? and blanks?\b")
FORM_RE = _rx(
    r"\bin(?:[- ]| the best )form\b|\bform (?:players?|picks?|table)\b|\brecent form\b|\bon fire\b|\bhot streak\b"
    r"|\b(?:last|past|previous|recent) (?:\d|two|three|four|five|six|few) (?:gameweeks?|gws?|weeks?|games?|matches)\b"
    r"|\blately\b|\brecently\b|\bright now\b.{0,20}\bform\b|\bform\b.{0,20}\bright now\b")
SET_PIECES_RE = _rx(
    r"\b(?:penalt(?:y|ies)|pens?|spot[- ]kicks?|free[- ]kicks?|corners?|set[- ]pieces?)\b.{0,30}\b(?:takers?|takes?|taking|duty|duties)\b"
    r"|\b(?:takes?|taking|on)\b.{0,30}\b(?:penalt(?:y|ies)|pens|spot[- ]kicks?|free[- ]kicks?|corners?|set[- ]pieces?)\b")
# Season totals no Cypher intent ranks: (PLAYED_IN property, label, the words that ask for it)
STAT_LEADERS: Dict[str, Tuple[str, str, "re.Pattern[str]"]] = {
    "penalties_saved": ("penalties_saved", "penalties saved", _rx(r"\bpenalt(?:y|ies) sav\w+|\bsaved (?:the )?most penalties")),
    "saves": ("saves", "saves", _rx(r"\bsaves?\b")),
    "xgi": ("expected_goal_involvements", "expected goal involvements (xGI)", _rx(r"\bxgi\b|\bexpected goal involvements?\b")),
    "xg": ("expected_goals", "expected goals (xG)", _rx(r"\bxg\b|\bexpected goals?\b(?! conceded| involvement)")),
    "xa": ("expected_assists", "expected assists (xA)", _rx(r"\bxa\b|\bexpected assists?\b")),
    "defensive_contribution": ("defensive_contribution", "defensive contributions",
                               _rx(r"\bdefensive contributions?\b|\bdefcons?\b|\bcbit\b")),
    "yellow_cards": ("yellow_cards", "yellow cards", _rx(r"\byellow cards?\b|\bmost booked\b|\bbookings\b|\byellows\b")),
    "red_cards": ("red_cards", "red cards", _rx(r"\bred cards?\b|\bsent off\b|\bsendings? off\b")),
    "bps": ("bps", "BPS (the Bonus Points System score)", _rx(r"\bbps\b")),
    "tackles": ("tackles", "tackles", _rx(r"\btackles?\b")),
    "recoveries": ("recoveries", "ball recoveries", _rx(r"\b(?:ball )?recoveries\b")),
    "own_goals": ("own_goals", "own goals", _rx(r"\bown goals?\b")),
}
_RANKING_RE = _rx(r"\b(?:most|highest|top|best|leaders?|leads?|leading|who has|which \w+ has|ranking|ranked)\b")
_FORM_COUNT = _rx(r"\b(?:last|past|previous|recent) (\d|two|three|four|five|six) (?:gameweeks?|gws?|weeks?|games?|matches)\b")
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_STAT_WORDS = (("goals", _rx(r"\bgoals?\b|\bscor(?:ed|ers?|ing)\b")), ("assists", _rx(r"\bassists?\b")),
               ("bonus", _rx(r"\bbonus\b")), ("minutes", _rx(r"\bminutes\b")))
# "How many points will Palmer get", "how many points do Arsenal have": not a rules question
_NOT_RULES = _rx(r"\bhow many points (?:will|would|did|has|have|is \w+ (?:going|predicted|expected))\b")


def price_cap(question: str, position: Optional[str] = None) -> Optional[float]:
    """The price limit a question sets, in millions: "under 6m", "below £5.5m", "6.0m or less";
    or the usual budget price for the position when it only says "budget" or "cheap"."""
    match = PRICE_RE.search(question)
    if match:
        value = float(next(g for g in match.groups() if g))
        if 3.5 <= value <= 16.0:
            return value
    if BUDGET_RE.search(question):
        return BUDGET_CAPS.get(position or "", 6.0)
    return None


def form_window(question: str) -> int:
    match = _FORM_COUNT.search(question)
    if not match:
        return FORM_GAMEWEEKS
    raw = match.group(1).lower()
    return max(1, min(int(_NUMBER_WORDS.get(raw, raw)), 10))


def form_stat(question: str) -> str:
    return next((stat for stat, rx in _STAT_WORDS if rx.search(question)), "total_points")


# Facts that aren't about one season's data: they answer even when the question names a past season
TIMELESS = ("rules", "model", "manager")
# With these intents the club's own query (top performers, past meetings, results) stays next to its fixtures
CLUB_QUERY_INTENTS = {"team_analysis", "head_to_head", "fixture_results"}


def stat_leader(question: str) -> Optional[str]:
    """The STAT_LEADERS key a ranking question asks for ("Which goalkeeper has the most saves?")."""
    if not _RANKING_RE.search(question or ""):
        return None
    return next((key for key, (_col, _label, rx) in STAT_LEADERS.items() if rx.search(question)), None)


def wanted(question: str, *, players: Sequence[str] = (), teams: Sequence[str] = (),
           positions: Sequence[str] = (), intent: str = "", seasons: Sequence[str] = ()) -> List[str]:
    """The facts a question asks for, most specific first (at most MAX_FACTS, the named players'
    outlook aside). Pure: subject words plus the entities the extractor found. A question about
    a past season ("How did Arsenal do in 2023-24?") gets none of the current season's facts."""
    q = question or ""
    past = bool(seasons) and CURRENT_SEASON not in seasons
    keys: List[str] = []

    def add(key: str, when: Any) -> None:
        if when and key not in keys:
            keys.append(key)

    has_players, n_teams = bool(players), len(teams)
    model = bool(MODEL_RE.search(q))
    add("explain", has_players and EXPLAIN_RE.search(q))
    add("manager", MANAGER_RE.search(q))
    add("model", model and "explain" not in keys and "manager" not in keys)
    add("rules", RULES_RE.search(q) and not model and not has_players and not _NOT_RULES.search(q)
        and not (n_teams and re.search(r"\bhow many points\b", q, re.IGNORECASE)))
    add("deadline", DEADLINE_RE.search(q))
    add("season_forecast", SEASON_FORECAST_RE.search(q) and not re.search(r"\bprice", q, re.IGNORECASE))
    add("match_forecast", not has_players and n_teams >= 1 and "season_forecast" not in keys
        and (MATCH_FORECAST_RE.search(q) or n_teams >= 2 and TWO_CLUB_FORECAST_RE.search(q)))
    add("table", TABLE_RE.search(q) and "season_forecast" not in keys and not has_players)
    add("availability", "rules" not in keys
        and (AVAILABILITY_RE.search(q) or has_players and PLAYING_RE.search(q)))
    add("double_blank", DOUBLE_BLANK_RE.search(q))
    add("club_fixtures", not has_players and n_teams >= 1 and CLUB_FIXTURES_RE.search(q)
        and not ({"table", "season_forecast", "match_forecast", "availability"} & set(keys)))
    add("price_changes", PRICE_CHANGE_RE.search(q))
    add("transfer_trends", TRANSFER_TRENDS_RE.search(q))
    add("differentials", DIFFERENTIAL_RE.search(q) and "rules" not in keys)
    add("ownership", OWNERSHIP_RE.search(q) and "rules" not in keys and "differentials" not in keys)
    position = ss.normalize_position(positions[0]) if positions else None
    add("price_cap", not has_players and price_cap(q, position) is not None
        and not ({"price_changes", "differentials", "rules"} & set(keys)))
    add("form", FORM_RE.search(q) and not ({"table", "rules", "model"} & set(keys)))
    add("set_pieces", SET_PIECES_RE.search(q) and "rules" not in keys)
    add("stat_leaders", not has_players and stat_leader(q) is not None
        and not ({"rules", "model", "form", "set_pieces"} & set(keys)))
    if past:
        return [k for k in keys if k in TIMELESS or k == "stat_leaders"][:MAX_FACTS]
    keys = keys[:MAX_FACTS]
    if has_players and "explain" not in keys and (keys or intent in NO_QUERY_INTENTS):
        keys.append("outlook")
    return keys


# ------------------------------------------------------------------ small helpers

def _when(value: Any, with_time: bool = True) -> Optional[str]:
    """"Sat 21 Nov 11:00" in UK time (FPL's clock)."""
    moment = ss.to_datetime(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(_UK) if _UK is not None else moment.astimezone(timezone.utc)
    text = f"{local:%a} {local.day} {local:%b}"
    if with_time:
        text += f" {local:%H:%M}" + ("" if _UK is not None else " UTC")
    return text


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _pct(p: Any) -> str:
    value = (_num(p) or 0.0) * 100
    if 0 < value < 1:
        return "<1%"
    if 99 < value < 100:
        return ">99%"
    return f"{value:.0f}%"


def _flag(row: Dict[str, Any]) -> str:
    """"injured, 0% chance of playing: Hamstring injury - Expected back 6 Dec" or "available"."""
    status = (_text(row.get("status")) or "a").lower()
    label = STATUS_WORDS.get(status, status)
    chance = _num(row.get("chance_of_playing_next_round"))
    news = _text(row.get("news"))
    if status == "a" and not news:
        return "available, no flag"
    out = label + (f", {chance:.0f}% chance of playing" if chance is not None and status != "a" else "")
    return out + (f": {news}" if news else "")


def _name(row: Dict[str, Any]) -> str:
    return _text(row.get("web_name")) or _text(row.get("name")) or "?"


def _owned(row: Dict[str, Any]) -> float:
    return _num(row.get("selected_by_percent")) or 0.0


def _lookup(rows: Iterable[Dict[str, Any]], names: Sequence[str]) -> List[Dict[str, Any]]:
    by_name = {_text(r.get("name")).lower(): r for r in rows}
    return [by_name[n.lower()] for n in names if n.lower() in by_name]


# ------------------------------------------------------------------ formatters (pure)

def deadline_text(meta: Dict[str, Any], asked_gameweek: Optional[int] = None,
                  now: Optional[datetime] = None) -> Optional[str]:
    events = {int(e["id"]): e for e in meta.get("events") or [] if e.get("id") is not None}
    nxt, deadline = meta.get("next_gameweek"), meta.get("next_deadline")
    if nxt is None and not events:
        return None
    now = now or datetime.now(timezone.utc)
    lines = ["Gameweek deadlines (UK time; a deadline is 90 minutes before the gameweek's first kickoff):"]
    if nxt is not None and deadline:
        moment = ss.to_datetime(deadline)
        left = ""
        if moment is not None:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            seconds = (moment - now).total_seconds()
            if seconds > 0:
                days, hours = int(seconds // 86400), int(seconds % 86400 // 3600)
                left = f" (in {days} day{'s' if days != 1 else ''} {hours} hour{'s' if hours != 1 else ''})" \
                    if days else f" (in {hours} hour{'s' if hours != 1 else ''} {int(seconds % 3600 // 60)} minutes)"
        lines.append(f"- Next deadline: Gameweek {int(nxt)}, {_when(deadline)}{left}.")
    shown = {int(nxt)} if nxt is not None else set()
    for gw in ([asked_gameweek] if asked_gameweek else []) + ([int(nxt) + 1] if nxt is not None else []):
        event = events.get(int(gw)) if gw is not None else None
        if event and int(gw) not in shown and event.get("deadline_time"):
            shown.add(int(gw))
            lines.append(f"- Gameweek {int(gw)} deadline: {_when(event['deadline_time'])}"
                         + (" (already passed)" if event.get("finished") else "") + ".")
    finished = [gw for gw, e in events.items() if e.get("finished")]
    if finished:
        lines.append(f"- The last finished gameweek is Gameweek {max(finished)}.")
    return "\n".join(lines) if len(lines) > 1 else None


def availability_text(rows: List[Dict[str, Any]], players: Sequence[str] = (), teams: Sequence[str] = ()
                      ) -> Optional[str]:
    if not rows:
        return None
    lines = ["Availability from FPL's official flags and team news (for the next deadline):"]
    if players:
        found = _lookup(rows, players)
        missing = [p for p in players if p.lower() not in {_text(r.get("name")).lower() for r in found}]
        lines += [f"- {r['name']} ({_text(r.get('team'))}): {_flag(r)}" for r in found]
        lines += [f"- {p}: not in the current FPL game (no longer at a Premier League club)" for p in missing]
        return "\n".join(lines)
    flagged = [r for r in rows if (_text(r.get("status")) or "a").lower() in ("i", "d", "s", "n")]
    if teams:
        for team in teams:
            mine = sorted((r for r in flagged if _text(r.get("team")) == team), key=lambda r: -_owned(r))
            if not mine:
                lines.append(f"- {team}: no flagged players.")
            lines += [f"- {_name(r)} ({team}, {_text(r.get('position'))}): {_flag(r)}" for r in mine[:14]]
        return "\n".join(lines)
    top = sorted(flagged, key=lambda r: -_owned(r))[:12]
    if not top:
        return None
    lines[0] = "The most-owned players with an FPL flag (official team news, for the next deadline):"
    lines += [f"- {_name(r)} ({_text(r.get('team'))}, owned by {_owned(r):.1f}%): {_flag(r)}" for r in top]
    return "\n".join(lines)


def _result_line(f: Dict[str, Any], team: str) -> str:
    home = f["home_team"] == team
    opponent = f["away_team"] if home else f["home_team"]
    gf, ga = (f["home_score"], f["away_score"]) if home else (f["away_score"], f["home_score"])
    outcome = "won" if gf > ga else ("drew" if gf == ga else "lost")
    return (f"GW{f['gameweek']}: {outcome} {gf}-{ga} {'at home to' if home else 'away at'} {opponent}"
            f" ({_when(f['kickoff_time'], with_time=False)})")


def _fixture_line(f: Dict[str, Any], team: str) -> str:
    home = f["home_team"] == team
    opponent = f["away_team"] if home else f["home_team"]
    difficulty = f.get("home_difficulty") if home else f.get("away_difficulty")
    when = _when(f["kickoff_time"]) or "date to be confirmed"
    return (f"GW{f['gameweek']}: {opponent} ({'home' if home else 'away'}), {when}"
            + (f", difficulty {int(difficulty)}/5" if difficulty is not None else ""))


def _played(f: Dict[str, Any]) -> bool:
    return f.get("status") == "finished" and f.get("home_score") is not None and f.get("away_score") is not None


def _sorted(fixtures: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    far = datetime.max.replace(tzinfo=timezone.utc)

    def key(f: Dict[str, Any]):
        moment = ss.to_datetime(f.get("kickoff_time"))
        if moment is not None and moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment or far, f.get("gameweek") or 99, str(f.get("id")))

    return sorted(fixtures, key=key)


def club_fixtures_text(fixtures: List[Dict[str, Any]], teams: Sequence[str]) -> Optional[str]:
    """Each club's last two results and next four fixtures; for two clubs, their meetings too."""
    teams = list(teams)[:2]
    ordered = _sorted(fixtures)
    lines: List[str] = []
    if len(teams) == 2:
        a, b = teams
        meetings = [f for f in ordered if {f["home_team"], f["away_team"]} == {a, b}]
        lines.append(f"{a} v {b} in {CURRENT_SEASON}:")
        if not meetings:
            lines.append("- No league meeting is on this season's fixture list.")
        for f in meetings:
            if _played(f):
                lines.append(f"- GW{f['gameweek']}: {f['home_team']} {f['home_score']}-{f['away_score']} "
                             f"{f['away_team']} ({_when(f['kickoff_time'], with_time=False)})")
            else:
                when = _when(f["kickoff_time"]) or "date to be confirmed"
                lines.append(f"- GW{f['gameweek']}: {f['home_team']} v {f['away_team']} at {f['home_team']}'s "
                             f"ground, {when} (not played yet)")
    for team in teams:
        mine = [f for f in ordered if team in (f["home_team"], f["away_team"])]
        if not mine:
            continue
        played = [f for f in mine if _played(f)]
        coming = [f for f in mine if f.get("status") == "scheduled"]
        lines.append(f"{team} ({CURRENT_SEASON}):")
        if played:
            lines.append("- Latest results: " + "; ".join(_result_line(f, team) for f in reversed(played[-2:])))
        if coming:
            lines.append("- Next fixtures: " + "; ".join(_fixture_line(f, team) for f in coming[:4]))
        else:
            lines.append("- No fixtures left this season.")
    return "\n".join(lines) if lines else None


def table_text(rows: List[Dict[str, Any]], teams: Sequence[str] = ()) -> Optional[str]:
    if not rows or not any(r.get("played") for r in rows):
        return None
    lines = [f"Premier League table, {CURRENT_SEASON}, from the results so far "
             "(position. club: points, played, goal difference):"]
    lines += [f"{r['position']}. {r['team']}: {r['points']} pts, {r['played']} played, GD {r['goal_difference']:+d}"
              for r in rows]
    for team in teams:
        r = next((r for r in rows if r["team"] == team), None)
        if r:
            nxt = r.get("next_fixture") or {}
            lines.append(
                f"{team} in detail: position {r['position']} of {len(rows)}, {r['points']} points, won {r['won']} "
                f"drawn {r['drawn']} lost {r['lost']}, goals {r['goals_for']}-{r['goals_against']}, form "
                f"{''.join(r.get('form') or []) or 'n/a'} (oldest first)"
                + (f", next: {nxt['opponent']} ({'home' if nxt.get('home') else 'away'})" if nxt.get("opponent") else ""))
    lines.append("Places 1-4 qualify for the Champions League, 5-6 for Europe, 18-20 are relegated.")
    return "\n".join(lines)


def season_forecast_text(forecast: Dict[str, Any], teams: Sequence[str] = ()) -> Optional[str]:
    rows = forecast.get("rows") or []
    if not rows:
        return None

    def line(r: Dict[str, Any]) -> str:
        return (f"{r['team']}: predicted position {r['predicted_position']}, {r['expected_points']:.0f} expected "
                f"points, title {_pct(r['p_title'])}, top four {_pct(r['p_top4'])}, relegation {_pct(r['p_relegation'])}")

    lines = [f"The site's forecast of the final {CURRENT_SEASON} table: a Poisson model of team strength fitted to "
             f"results only (no bookmakers), the remaining {forecast.get('remaining_matches', '?')} matches simulated "
             f"{forecast.get('simulations', 10000):,} times. Chances, not certainties:"]
    title = sorted(rows, key=lambda r: -r["p_title"])[:4]
    lines.append("- Title: " + ", ".join(f"{r['team']} {_pct(r['p_title'])}" for r in title if r["p_title"] > 0.001))
    top4 = sorted(rows, key=lambda r: -r["p_top4"])[:6]
    lines.append("- Top four: " + ", ".join(f"{r['team']} {_pct(r['p_top4'])}" for r in top4))
    down = sorted(rows, key=lambda r: -r["p_relegation"])[:5]
    lines.append("- Relegation: " + ", ".join(f"{r['team']} {_pct(r['p_relegation'])}" for r in down))
    for team in teams:
        r = next((r for r in rows if r["team"] == team), None)
        if r:
            lines.append("- " + line(r))
    if not teams:
        lines.append("- Predicted top six in order: " + ", ".join(r["team"] for r in rows[:6]))
    return "\n".join(lines)


def match_forecast_text(forecast: Dict[str, Any]) -> str:
    home, away = forecast["home_team"], forecast["away_team"]
    scores = ", ".join(f"{s['home']}-{s['away']} ({_pct(s['probability'])})"
                       for s in (forecast.get("most_likely_scores") or [])[:3])
    return "\n".join([
        f"The site's forecast for {home} v {away} (GW{forecast.get('gameweek')}, {_when(forecast.get('kickoff_time'))}, "
        f"at {home}'s ground): a Poisson model of team strength fitted to results only (no bookmakers).",
        f"- {home} win {_pct(forecast['home_win'])}, draw {_pct(forecast['draw'])}, {away} win {_pct(forecast['away_win'])}",
        f"- Expected goals: {home} {forecast['expected_goals_home']:.1f}, {away} {forecast['expected_goals_away']:.1f}",
        f"- Likeliest scores ({home} first): {scores}",
        f"- Clean-sheet chance: {home} {_pct(forecast['clean_sheet_home'])}, {away} {_pct(forecast['clean_sheet_away'])}",
        "These are chances from past results; they know nothing about injuries or line-ups.",
    ])


def next_meeting(fixtures: List[Dict[str, Any]], teams: Sequence[str]) -> Optional[Dict[str, Any]]:
    """The next unplayed fixture between two clubs, or one club's next fixture."""
    coming = [f for f in _sorted(fixtures) if f.get("status") == "scheduled"]
    if len(teams) >= 2:
        pair = set(teams[:2])
        return next((f for f in coming if {f["home_team"], f["away_team"]} == pair), None)
    if teams:
        return next((f for f in coming if teams[0] in (f["home_team"], f["away_team"])), None)
    return None


def rules_text(season: str = CURRENT_SEASON) -> str:
    return "\n".join([
        f"Fantasy Premier League rules, {season}:",
        "- Squad: 15 players (2 goalkeepers, 5 defenders, 5 midfielders, 3 forwards), a £100.0m budget, at most 3 "
        "from one club. Each gameweek 11 start (1 goalkeeper, at least 3 defenders, 2 midfielders and 1 forward).",
        "- Captain: his points count double; if he doesn't play, the vice-captain's are doubled instead.",
        "- Auto-subs: a starter who doesn't play is replaced by the first bench player in order who did play and "
        "keeps the formation valid (a goalkeeper only by the substitute goalkeeper).",
        "- Points for playing: 1 (under 60 minutes), 2 (60 minutes or more).",
        "- Goals: goalkeeper 10, defender 6, midfielder 5, forward 4. Assist: 3.",
        "- Clean sheet (60+ minutes played): goalkeeper or defender 4, midfielder 1, forward 0.",
        "- Goalkeepers: 1 point per 3 saves, 5 for a penalty save.",
        "- Defensive contributions: 2 points for a defender with 10+ clearances, blocks, interceptions and "
        "tackles in a match, or a midfielder or forward with 12+ (ball recoveries count for them too).",
        "- Bonus: the three best players of each match by the Bonus Points System (BPS, a score built from match "
        "actions) get 3, 2 and 1 extra points.",
        "- Deductions: -1 per 2 goals conceded (goalkeepers and defenders), -1 yellow card, -3 red card, "
        "-2 own goal, -2 missed penalty.",
        "- Transfers: 1 free transfer a gameweek; unused ones are banked, up to 5. Each extra transfer costs "
        "4 points (a 'hit'). You sell a player for his purchase price plus half of any rise, rounded down to £0.1m.",
        "- Chips (one per gameweek; two of each a season, one in each half: gameweeks 1-19 and 20-38): Wildcard "
        "(unlimited free transfers, kept permanently; from gameweek 2), Free Hit (unlimited transfers for one "
        "gameweek, then the old squad returns; from gameweek 2), Bench Boost (the four substitutes' points count "
        "too), Triple Captain (the captain's points count three times).",
        "- Deadline: 90 minutes before the gameweek's first kickoff. A double gameweek is one where a club plays "
        "twice (both matches count); a blank gameweek is one where it doesn't play.",
        "- Prices move by £0.1m at a time with the balance of transfers in and out.",
        "- Terms: FDR is FPL's fixture difficulty rating from 1 (easiest) to 5 (hardest); ICT is FPL's index of "
        "influence, creativity and threat; xG and xA are expected goals and assists (chance quality); a "
        "differential is a player few managers own; the template is the group of players most managers own.",
    ])


def _prediction_lines(rows: List[Dict[str, Any]], owned: Dict[int, float]) -> List[str]:
    out = []
    for i, r in enumerate(rows, start=1):
        price = (_num(r.get("value")) or 0.0) / 10.0
        own = owned.get(int(r["element"])) if _num(r.get("element")) is not None else None
        venue = "H" if bool(r.get("is_home")) else "A"
        flag = "" if (_text(r.get("status")) or "a") == "a" else f" [{_flag(r)}]"
        out.append(f"{i}. {r['name']} ({r['position']}, {r['team']}, £{price:.1f}m"
                   + (f", owned by {own:.1f}%" if own is not None else "")
                   + f"): {float(r['predicted_points']):.1f} predicted points vs {r['opponent']} ({venue}){flag}")
    return out


def _ownership(rows: Iterable[Dict[str, Any]]) -> Dict[int, float]:
    return {int(r["element"]): _owned(r) for r in rows if _num(r.get("element")) is not None}


def _ranked(predictions: Any, position: Optional[str]) -> List[Dict[str, Any]]:
    """Prediction rows with a fixture and points, best first (one position when given)."""
    if predictions is None or not len(predictions):
        return []
    rows = predictions.to_dict("records")
    rows = [r for r in rows if (_num(r.get("predicted_points")) or 0.0) > 0
            and (position is None or r.get("position") == position)]
    return sorted(rows, key=lambda r: (-float(r["predicted_points"]), str(r["name"])))


def price_cap_text(predictions: Any, status_rows: List[Dict[str, Any]], cap: float,
                   position: Optional[str] = None) -> Optional[str]:
    rows = [r for r in _ranked(predictions, position) if (_num(r.get("value")) or 1e9) <= cap * 10 + 1e-9]
    if not rows:
        return None
    gameweek = int(rows[0]["gameweek"]) if _num(rows[0].get("gameweek")) is not None else None
    who = POSITION_PLURAL.get(position or "", "players")
    lines = [f"The best {who} priced £{cap:.1f}m or less by predicted points for "
             f"{'Gameweek ' + str(gameweek) if gameweek else 'the next gameweek'}, ranked best first:"]
    return "\n".join(lines + _prediction_lines(rows[:LIST_ROWS], _ownership(status_rows)))


def differentials_text(predictions: Any, status_rows: List[Dict[str, Any]], position: Optional[str] = None,
                       limit: float = DIFFERENTIAL_OWNERSHIP) -> Optional[str]:
    owned = _ownership(status_rows)
    if not owned:
        return None
    rows = [r for r in _ranked(predictions, position)
            if _num(r.get("element")) is not None and owned.get(int(r["element"]), 100.0) < limit]
    if not rows:
        return None
    who = POSITION_PLURAL.get(position or "", "players")
    lines = [f"Differentials: the best {who} owned by under {limit:.0f}% of managers, by predicted points for the "
             "next gameweek, ranked best first:"]
    return "\n".join(lines + _prediction_lines(rows[:LIST_ROWS], owned))


def ownership_text(status_rows: List[Dict[str, Any]], players: Sequence[str] = (),
                   position: Optional[str] = None) -> Optional[str]:
    rows = [r for r in status_rows if (_text(r.get("status")) or "a").lower() != "u"]
    if not rows or not any(_owned(r) for r in rows):
        return None
    if players:
        found = _lookup(status_rows, players)
        if not found:
            return None
        return "\n".join(["Ownership now (share of FPL managers who own him):"]
                         + [f"- {r['name']} ({_text(r.get('team'))}): {_owned(r):.1f}%" for r in found])
    if position:
        rows = [r for r in rows if r.get("position") == position]
    top = sorted(rows, key=lambda r: -_owned(r))[:10]
    who = POSITION_PLURAL.get(position or "", "players")
    lines = [f"The most-owned {who} in FPL right now (share of managers; the 'template'), ranked:"]
    lines += [f"{i}. {_name(r)} ({_text(r.get('team'))}, {_text(r.get('position'))}, "
              f"£{(_num(r.get('now_cost')) or 0) / 10:.1f}m): {_owned(r):.1f}%" for i, r in enumerate(top, start=1)]
    return "\n".join(lines)


def transfer_trends_text(status_rows: List[Dict[str, Any]]) -> Optional[str]:
    def top(column: str) -> List[Dict[str, Any]]:
        rows = [r for r in status_rows if (_num(r.get(column)) or 0) > 0]
        return sorted(rows, key=lambda r: -(_num(r.get(column)) or 0))[:LIST_ROWS]

    ins, outs = top("transfers_in_event"), top("transfers_out_event")
    if not ins and not outs:
        return None

    def lines(rows: List[Dict[str, Any]], column: str) -> List[str]:
        return [f"{i}. {_name(r)} ({_text(r.get('team'))}): {int(_num(r.get(column)) or 0):,}"
                for i, r in enumerate(rows, start=1)]

    out = ["Transfers since the last deadline (FPL's counts for the coming gameweek)."]
    if ins:
        out += ["Most transferred in:"] + lines(ins, "transfers_in_event")
    if outs:
        out += ["Most transferred out:"] + lines(outs, "transfers_out_event")
    return "\n".join(out)


def price_changes_text(prices: List[Dict[str, Any]]) -> str:
    if not prices:
        return "Price changes: no player's price has changed since the last deadline."
    up = [p for p in prices if p["change"] > 0]
    down = [p for p in prices if p["change"] < 0]

    def lines(rows: List[Dict[str, Any]]) -> List[str]:
        return [f"- {p.get('web_name') or p['name']} ({p.get('team')}): {p['change']:+.1f}m"
                + (f" to £{p['price']:.1f}m" if p.get("price") is not None else "") for p in rows[:LIST_ROWS]]

    out = ["Price changes since the last deadline:"]
    if up:
        out += ["Risers:"] + lines(up)
    if down:
        out += ["Fallers:"] + lines(down)
    return "\n".join(out)


def double_blank_text(specials: Dict[int, Dict[str, List[str]]]) -> Optional[str]:
    if not specials:
        return None
    weeks = sorted(specials)
    lines = [f"Double and blank gameweeks on the current fixture list, GW{weeks[0]}-GW{weeks[-1]}:"]
    found = False
    for gw in weeks:
        doubles, blanks = specials[gw].get("double") or [], specials[gw].get("blank") or []
        if doubles:
            found = True
            lines.append(f"- GW{gw}: double for {', '.join(doubles)}")
        if blanks:
            found = True
            lines.append(f"- GW{gw}: blank for {', '.join(blanks)}")
    if not found:
        lines.append("- None: every club plays exactly once in each of these gameweeks. Doubles and blanks "
                     "appear when matches are rescheduled, usually around cup rounds later in the season.")
    return "\n".join(lines)


MODEL_DESCRIPTION = (
    "How the site's predicted points are made: a gradient-boosted tree model (five models averaged) trained on "
    "every Premier League player-match since 2020-21. For each player and fixture it uses his form over the last "
    "3 and 6 matches and the season so far (minutes, starts, points, goals, assists, xG, xA, bonus, ICT), his "
    "team's and the opponent's attack and defence, home or away, rest days, his price and ownership, transfer "
    "trends, bookmakers' implied match chances once they are published, and FPL's availability flags. A doubtful "
    "player's points are scaled down and a ruled-out player gets 0. It predicts before the deadline, so it never "
    "knows the line-ups, and it gives an average: any single match can go far above or below it."
)


def model_text(record: Optional[Dict[str, Any]], report: Optional[Dict[str, Any]] = None) -> str:
    lines = [MODEL_DESCRIPTION]
    totals = (record or {}).get("totals") or {}
    n = totals.get("gameweeks") or 0
    if n:
        lines.append(f"Track record in {CURRENT_SEASON} (only forecasts saved before each deadline, {n} gameweek"
                     f"{'s' if n != 1 else ''} so far, so a small sample):")
        mae = totals.get("mae") or {}
        if mae.get("model") is not None and mae.get("fpl") is not None:
            lines.append(f"- Average error per player who played: the model {mae['model']:.2f} points, FPL's own "
                         f"forecast {mae['fpl']:.2f} (lower is better).")
        top10 = totals.get("top10") or {}
        if top10.get("model") is not None:
            lines.append(f"- The model's ten highest-predicted players scored {top10['model']:.1f} points a gameweek"
                         + (f", FPL's ten {top10['fpl']:.1f}" if top10.get("fpl") is not None else "") + ".")
        squad = totals.get("squad") or {}
        if squad.get("mean_points") is not None and squad.get("mean_average") is not None:
            lines.append(f"- The squad it picked each week averaged {squad['mean_points']:.1f} points against the "
                         f"average manager's {squad['mean_average']:.1f}, beating it in {squad.get('beat_average')} "
                         f"of {squad.get('of')} gameweeks.")
        captain = totals.get("captain") or {}
        if captain.get("of"):
            lines.append(f"- Its top pick scored {captain.get('threshold', 6)}+ points in {captain.get('hits')} of "
                         f"{captain['of']} gameweeks.")
    else:
        lines.append("Track record: no gameweek with a forecast saved before its deadline has finished yet.")
    test = (((report or {}).get("test") or {}).get("all") or {})
    version = (report or {}).get("version")
    own, baseline = test.get(version or "") or {}, test.get("season_ppg") or {}
    if own.get("top20_points") is not None and baseline.get("top20_points") is not None:
        lines.append(f"On held-out seasons the model's 20 highest-predicted players each gameweek averaged "
                     f"{own['top20_points']:.2f} points, against {baseline['top20_points']:.2f} for picking by "
                     "points per game.")
    lines.append("More on the Model insights page (/model), including the track record tab.")
    return "\n".join(lines)


def manager_text(view: Dict[str, Any]) -> Optional[str]:
    history, totals = view.get("history") or [], view.get("totals") or {}
    if not history and not view.get("current"):
        return None
    start = view.get("start_gameweek")
    lines = [f"The ML manager: the site's model playing FPL under the real rules since Gameweek {start} of "
             f"{view.get('season', CURRENT_SEASON)} (transfers, hits, chips and prices as in the game)."]
    if totals.get("points") is not None:
        vs = totals.get("vs_average")
        league = totals.get("league") or {}
        text = f"- Total: {totals['points']} points after {totals.get('gameweeks')} gameweeks"
        if vs is not None:
            text += f" ({abs(vs)} {'above' if vs >= 0 else 'below'} the average manager over them)"
        if league.get("rank") and league.get("size"):
            text += (f"; rank {league['rank']:,} of {league['size']:,} among managers who started in the same "
                     f"gameweek (ahead of {league.get('percentile', 0):.0f}% of them)")
        lines.append(text + ".")
    if history:
        last = history[-1]
        lines.append(f"- Last gameweek (GW{last['gameweek']}): {last['points']} points, the average manager "
                     f"{last.get('gw_average')}, captain {last.get('captain')}"
                     + (f", {last['transfers']} transfer{'s' if last['transfers'] != 1 else ''}" if last.get("transfers") else "")
                     + (f", chip {last['chip']}" if last.get("chip") else "") + ".")
    current = view.get("current") or {}
    if current.get("status") == "planned":
        lines.append(f"- Plan for GW{current.get('gameweek')}: {current.get('reason') or 'no transfers'}.")
    state = view.get("state") or {}
    if state.get("bank") is not None:
        lines.append(f"- Bank £{state['bank'] / 10:.1f}m, {state.get('free_transfers')} free transfer(s), squad value "
                     f"£{(state.get('team_value') or 0) / 10:.1f}m.")
    chips = [c for c in view.get("chips") or [] if c.get("active") and c.get("available")]
    if chips:
        lines.append("- Chips still available this half: " + ", ".join(sorted({c["label"] for c in chips})) + ".")
    lines.append("Its team is on the Team of the Week page's ML manager tab (/team-of-the-week?mode=manager).")
    return "\n".join(lines)


def explain_text(payload: Dict[str, Any], top: int = 4) -> str:
    player = payload["player"]
    lines = [f"Why the model predicts {payload['predicted_points']:.1f} points for {player['name']} in "
             f"Gameweek {payload['gameweek']} (each number is how much that input moved the prediction, in points):"]
    for fx in payload.get("fixtures") or []:
        venue = "home" if fx.get("is_home") else "away"
        lines.append(f"- vs {fx['opponent']} ({venue}): the average player starts at {fx['base_value']:.2f}; "
                     f"the model's output is {fx['model_points']:.2f}.")
        contributions = [c for c in fx.get("contributions") or [] if abs(c.get("shap") or 0) >= 0.01]
        ups = [c for c in contributions if c["shap"] > 0][:top]
        downs = [c for c in contributions if c["shap"] < 0][:top]
        if ups:
            lines.append("  Raising it: " + "; ".join(f"{c['label']} ({c['shap']:+.2f})" for c in ups))
        if downs:
            lines.append("  Lowering it: " + "; ".join(f"{c['label']} ({c['shap']:+.2f})" for c in downs))
    factor = payload.get("availability_factor")
    if factor is not None and factor < 1:
        lines.append(f"- Availability: FPL flags him ({_flag(player)}), so the points are multiplied by {factor:.2f}.")
    lines.append("The full breakdown is on the Model insights page (/model?tab=player).")
    return "\n".join(lines)


FORM_QUERY = """
MATCH (f:Fixture {season_id: $season})<-[r:PLAYED_IN]-(p:Player)
WHERE f.gameweek IN $gameweeks
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
WITH p, pos, sum(coalesce(r.total_points, 0)) AS total_points, sum(coalesce(r.goals_scored, 0)) AS goals,
     sum(coalesce(r.assists, 0)) AS assists, sum(coalesce(r.bonus, 0)) AS bonus,
     sum(coalesce(r.minutes, 0)) AS minutes, count(r) AS matches, last(collect(r.team)) AS team
WHERE ($position IS NULL OR pos.code = $position) AND ($names IS NULL OR p.name IN $names)
RETURN p.name AS name, pos.code AS position, team, total_points, goals, assists, bonus, minutes, matches
ORDER BY {order_by} DESC, total_points DESC, name
LIMIT $limit
"""
FORM_SORTS = {"total_points": "total_points", "goals": "goals", "assists": "assists", "bonus": "bonus",
              "minutes": "minutes"}


def form_query(gameweeks: Sequence[int], stat: str = "total_points", position: Optional[str] = None,
               names: Optional[Sequence[str]] = None, limit: int = LIST_ROWS,
               season: str = CURRENT_SEASON) -> Tuple[str, Dict[str, Any]]:
    """(cypher, params): players' totals over `gameweeks` of one season, best first by `stat`.
    The only text spliced in is a FORM_SORTS column."""
    order = FORM_SORTS.get(stat, "total_points")
    return FORM_QUERY.replace("{order_by}", order), {
        "season": season, "gameweeks": [int(g) for g in gameweeks], "position": position,
        "names": list(names) if names else None, "limit": int(limit)}


def form_text(rows: List[Dict[str, Any]], gameweeks: Sequence[int], stat: str = "total_points",
              position: Optional[str] = None, named: bool = False) -> Optional[str]:
    if not rows or not gameweeks:
        return None
    span = f"GW{gameweeks[0]}-GW{gameweeks[-1]}" if len(gameweeks) > 1 else f"GW{gameweeks[0]}"
    who = POSITION_PLURAL.get(position or "", "players")
    label = {"total_points": "FPL points", "goals": "goals", "assists": "assists", "bonus": "bonus points",
             "minutes": "minutes"}[FORM_SORTS.get(stat, "total_points")]
    head = (f"Recent form: the named players over the last {len(gameweeks)} finished gameweeks ({span}):" if named
            else f"Recent form: the top {who} by {label} over the last {len(gameweeks)} finished gameweeks ({span}), "
                 "ranked best first:")
    lines = [head]
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {r['name']} ({r.get('position')}, {r.get('team')}): {int(r['total_points'])} points, "
                     f"{int(r['goals'])} goals, {int(r['assists'])} assists, {int(r['minutes'])} minutes in "
                     f"{int(r['matches'])} matches")
    return "\n".join(lines)


SET_PIECE_COLUMNS = (("penalties_order", "penalties"), ("direct_freekicks_order", "direct free kicks"),
                     ("corners_and_indirect_freekicks_order", "corners and indirect free kicks"))


def set_pieces_text(status_rows: List[Dict[str, Any]], players: Sequence[str] = (), teams: Sequence[str] = ()
                    ) -> Optional[str]:
    """FPL's set-piece orders (1 = first choice) from the status file: the named players', the
    named clubs', or every club's first penalty taker."""
    rows = [r for r in status_rows if (_text(r.get("status")) or "a").lower() != "u"]
    if not any(_num(r.get(col)) is not None for r in rows for col, _label in SET_PIECE_COLUMNS):
        return None
    head = "Set-piece takers as FPL lists them (1 = first choice; clubs can change them at any time):"

    def takers(club_rows: List[Dict[str, Any]], column: str, depth: int = 2) -> str:
        ranked = sorted((r for r in club_rows if _num(r.get(column)) is not None), key=lambda r: _num(r.get(column)))
        return ", ".join(f"{int(_num(r.get(column)))}. {_name(r)}" for r in ranked[:depth]) or "not listed"

    if players:
        lines = [head]
        for r in _lookup(rows, players):
            duties = [f"{label} (choice {int(_num(r.get(col)))})" for col, label in SET_PIECE_COLUMNS
                      if _num(r.get(col)) is not None]
            lines.append(f"- {r['name']} ({_text(r.get('team'))}): " + (", ".join(duties) or "not on any set piece"))
        return "\n".join(lines) if len(lines) > 1 else None
    clubs = list(teams) or sorted({_text(r.get("team")) for r in rows if _text(r.get("team"))})
    lines = [head]
    for club in clubs:
        mine = [r for r in rows if _text(r.get("team")) == club]
        if not mine:
            continue
        if teams:
            lines.append(f"- {club}: " + "; ".join(f"{label}: {takers(mine, col)}" for col, label in SET_PIECE_COLUMNS))
        else:
            lines.append(f"- {club}: penalties {takers(mine, 'penalties_order', 1)}")
    return "\n".join(lines) if len(lines) > 1 else None


STAT_LEADERS_QUERY = """
MATCH (f:Fixture {season_id: $season})<-[r:PLAYED_IN]-(p:Player)
OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
WITH p, pos, sum(coalesce(r.{column}, 0)) AS value, sum(coalesce(r.minutes, 0)) AS minutes,
     sum(CASE WHEN coalesce(r.minutes, 0) > 0 THEN 1 ELSE 0 END) AS matches, last(collect(r.team)) AS team
WHERE value > 0 AND ($position IS NULL OR pos.code = $position) AND ($team IS NULL OR team = $team)
RETURN p.name AS name, pos.code AS position, team, value, minutes, matches
ORDER BY value DESC, minutes, name
LIMIT $limit
"""


def stat_leaders_query(stat: str, season: str = CURRENT_SEASON, position: Optional[str] = None,
                       team: Optional[str] = None, limit: int = LIST_ROWS) -> Tuple[str, Dict[str, Any]]:
    """(cypher, params): one season's leaders for a STAT_LEADERS key. The only text spliced in is
    that key's column, never user input."""
    column = STAT_LEADERS[stat][0]
    return STAT_LEADERS_QUERY.replace("{column}", column), {
        "season": season, "position": position, "team": team, "limit": int(limit)}


def stat_leaders_text(rows: List[Dict[str, Any]], stat: str, season: str = CURRENT_SEASON,
                      position: Optional[str] = None, team: Optional[str] = None) -> Optional[str]:
    if not rows:
        return None
    label = STAT_LEADERS[stat][1]
    who = POSITION_PLURAL.get(position or "", "players")
    scope = f"{'so far in ' if season == CURRENT_SEASON else 'in '}{season}" + (f", {team}" if team else "")
    lines = [f"The {who} with the most {label} {scope}, ranked best first:"]
    for i, r in enumerate(rows, start=1):
        value = _num(r.get("value")) or 0.0
        shown = f"{value:.2f}" if stat in ("xg", "xa", "xgi") else f"{value:.0f}"
        lines.append(f"{i}. {r['name']} ({r.get('position')}, {r.get('team')}): {shown} in "
                     f"{int(r.get('matches') or 0)} matches")
    return "\n".join(lines)


# ------------------------------------------------------------------ loaders

def _status_rows() -> List[Dict[str, Any]]:
    from services import news
    return news._status_rows()


def _finished_gameweeks(conn, count: int) -> List[int]:
    fixtures = ss.load_fixtures(conn)
    by_gw: Dict[int, List[Dict[str, Any]]] = {}
    for f in fixtures:
        if f.get("gameweek") is not None:
            by_gw.setdefault(int(f["gameweek"]), []).append(f)
    done = sorted(gw for gw, fs in by_gw.items() if fs and all(_played(f) for f in fs))
    return done[-count:]


def _model_report() -> Optional[Dict[str, Any]]:
    import json
    import os
    from ml import points_model as pm
    try:
        model_path = pm.active_model_paths()[0]
        path = os.path.join(os.path.dirname(model_path), f"points_{pm.MODEL_VERSION}_report.json")
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(pm.__file__), "models", f"points_{pm.MODEL_VERSION}_report.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _build(key: str, question: str, conn, *, players: Sequence[str], teams: Sequence[str],
           position: Optional[str], gameweek: Optional[int], predictions: Any, intent: str = "",
           season: str = CURRENT_SEASON) -> Optional[Fact]:
    stats = f"{CURRENT_SEASON} fixtures and results"
    if key == "deadline":
        text = deadline_text(ss.read_meta(), gameweek)
        return Fact(key, text, "FPL's gameweek deadlines") if text else None
    if key == "availability":
        text = availability_text(_status_rows(), players, teams)
        return Fact(key, text, "FPL's availability flags and team news") if text else None
    if key == "club_fixtures":
        text = club_fixtures_text(ss.load_fixtures(conn), teams)
        return Fact(key, text, stats, exclusive=intent not in CLUB_QUERY_INTENTS) if text else None
    if key == "table":
        text = table_text(ss.get_table(conn)["rows"], teams)
        return Fact(key, text, f"the {CURRENT_SEASON} league table") if text else None
    if key == "season_forecast":
        from services import forecasts
        text = season_forecast_text(forecasts.table_forecast(conn), teams)
        return Fact(key, text, "the site's season forecast (team-strength model)") if text else None
    if key == "match_forecast":
        from services import forecasts
        fixture = next_meeting(ss.load_fixtures(conn), teams)
        if fixture is None:
            return None
        text = match_forecast_text(forecasts.match_forecast(conn, fixture["id"]))
        return Fact(key, text, "the site's match forecast (team-strength model)", exclusive=len(teams) < 2)
    if key == "rules":
        return Fact(key, rules_text(), "FPL's rules")
    if key == "price_cap":
        cap = price_cap(question, position)
        text = price_cap_text(predictions, _status_rows(), cap, position) if cap is not None else None
        return Fact(key, text, "current prices", predictions=True) if text else None
    if key == "differentials":
        text = differentials_text(predictions, _status_rows(), position)
        return Fact(key, text, "current FPL ownership", predictions=True) if text else None
    if key == "ownership":
        text = ownership_text(_status_rows(), players, position)
        return Fact(key, text, "current FPL ownership") if text else None
    if key == "transfer_trends":
        text = transfer_trends_text(_status_rows())
        return Fact(key, text, "FPL's transfer counts for the coming gameweek") if text else None
    if key == "price_changes":
        from services import news
        rows = _status_rows()
        if not rows or "cost_change_event" not in rows[0]:
            return None
        return Fact(key, price_changes_text(news.fpl_news(rows)["prices"]), "FPL's price changes")
    if key == "double_blank":
        from services import my_team_assistant
        text = double_blank_text(my_team_assistant.doubles_and_blanks(ss.get_fixture_ticker(conn, 10)))
        return Fact(key, text, "FPL's fixture list") if text else None
    if key == "model":
        from services import track_record
        return Fact(key, model_text(track_record.get_track_record(), _model_report()), "the model's track record")
    if key == "manager":
        from services import fpl_manager
        text = manager_text(fpl_manager.get_manager_view())
        return Fact(key, text, "the ML manager's season") if text else None
    if key == "explain":
        from services import explain
        element = explain.find_element(players[0])
        if element is None:
            return None
        return Fact(key, explain_text(explain.player(element)), "the model's explanation (SHAP)")
    if key == "form":
        gameweeks = _finished_gameweeks(conn, form_window(question))
        if not gameweeks:
            return None
        stat = form_stat(question)
        query, params = form_query(gameweeks, stat, None if players else position, players or None)
        text = form_text(conn.execute_query(query, params), gameweeks, stat, position, named=bool(players))
        return Fact(key, text, f"{CURRENT_SEASON} gameweek stats") if text else None
    if key == "set_pieces":
        text = set_pieces_text(_status_rows(), players, teams)
        return Fact(key, text, "FPL's set-piece notes") if text else None
    if key == "stat_leaders":
        stat = stat_leader(question)
        if stat is None:
            return None
        team = teams[0] if len(teams) == 1 else None
        query, params = stat_leaders_query(stat, season, position, team)
        text = stat_leaders_text(conn.execute_query(query, params), stat, season, position, team)
        return Fact(key, text, f"{season} match stats") if text else None
    if key == "outlook":
        from services import player_outlook
        if predictions is None:
            return None
        names = list(players)[:MAX_OUTLOOK_PLAYERS]
        outlooks = player_outlook.load_outlooks(conn, names, predictions)
        text = player_outlook.forecast_context([outlooks[name] for name in names])
        # With no query of its own (or only a generic recommendation list) the outlook is the answer
        return Fact(key, text, None, exclusive=intent in NO_QUERY_INTENTS, predictions=True)
    return None


def gather(question: str, conn, *, players: Sequence[str] = (), teams: Sequence[str] = (),
           positions: Sequence[str] = (), intent: str = "", gameweek: Optional[int] = None,
           predictions: Any = None, seasons: Sequence[str] = (),
           build: Callable[..., Optional[Fact]] = _build) -> List[Fact]:
    """The facts `question` asks for, in wanted()'s order. Never raises: a provider that fails
    or has nothing is left out. Blocking (Neo4j and files)."""
    position = ss.normalize_position(positions[0]) if positions else None
    position = position if position in POSITION_PLURAL else None
    season = seasons[0] if seasons and CURRENT_SEASON not in seasons else CURRENT_SEASON
    facts: List[Fact] = []
    for key in wanted(question, players=players, teams=teams, positions=positions, intent=intent, seasons=seasons):
        try:
            fact = build(key, question, conn, players=list(players), teams=list(teams), position=position,
                         gameweek=gameweek, predictions=predictions, intent=intent, season=season)
        except Exception as exc:  # noqa: BLE001 - one fact's failure mustn't cost the answer
            logger.warning("Assistant fact %s failed: %s", key, exc, exc_info=True)
            continue
        if fact is not None and fact.text:
            facts.append(fact)
    return facts


def context(facts: Sequence[Fact]) -> str:
    return "\n\n".join(f.text for f in facts)


def sources(facts: Sequence[Fact]) -> List[str]:
    out: List[str] = []
    for f in facts:
        if f.source and f.source not in out:
            out.append(f.source)
    return out


def uses_predictions(facts: Sequence[Fact]) -> bool:
    return any(f.predictions for f in facts)


def replaces_query(facts: Sequence[Fact]) -> bool:
    """Whether the facts answer the question on their own (the intent's Cypher query is skipped)."""
    return any(f.exclusive for f in facts)
