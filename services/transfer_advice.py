"""
Transfer questions: "Should I sell Haaland for Isak and Palmer for Rice next gameweek? I have
1 free transfer."

parse() decides whether a question is about transfers and pairs the players in the order
asked, as (sold, bought) moves:
  * "sell X for Y", "swap/replace X with/for Y", "X for Y" / "X -> Y" with transfer wording;
  * "X out, Y in", "Y in for X", "get Y instead of X";
  * "X and Y for Z and W" (pairs in order) and several moves in one question;
  * a lone "sell X" / "buy Y" as a half move.
It reads "N free transfer(s)" / "N FT" (default 1) and whether a hit is mentioned, and lists
names in a move's place that match no player, so the answer can say so instead of guessing.

context() is the numbers-first prompt section: per move the projected gain over the next
gameweek and the next 4, its cost (free, or a 4-point hit beyond the free transfers) and the
price difference, then each player's outlook (services/player_outlook.py). Pure throughout.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from preprocessing.entity_extractor import EntityExtractor
from services.player_outlook import Outlook, outlook_text

HIT_COST = 4
MAX_MOVES = 5       # moves described in the prompt
MAX_FREE_TRANSFERS = 5

# Wording that makes a question about transfers (the masked forms below count too)
TRANSFER_RE = re.compile(
    r"\b(?:sell|selling|sold|buy|buying|swap|swapping|replace|replacing|offload|offloading)\b"
    r"|\btransfer(?:ring)?\s+(?:in|out)\b"
    r"|\b(?:a|the|my|this|one|next|free|\d)\s*transfers?\b"
    r"|\b\d\s*fts?\b"
    r"|\b(?:take|taking|took|worth)\s+(?:a\s+|the\s+)?(?:[-−]\s?4\s+|4[-\s]?(?:point|pt)s?\s+)?hit\b"
    r"|(?<![\w.])[-−]\s?4\b"
    r"|\bbring(?:ing)?\s+in\b|\bget\s+rid\s+of\b|\bship\s+out\b"
    r"|\b(?:get|getting|pick\s+up|go\s+for)\b[^.?!]*\binstead\s+of\b",
    re.IGNORECASE)

FREE_TRANSFERS_RE = re.compile(
    r"\b(\d+|no|zero|an?|one|a\s+single|two|three|four|five)\s*(?:free\s+transfers?|fts?)\b", re.IGNORECASE)
_NUMBER_WORDS = {"no": 0, "zero": 0, "a": 1, "an": 1, "one": 1, "a single": 1, "two": 2, "three": 3,
                 "four": 4, "five": 5}
HIT_RE = re.compile(r"\bhits?\b|(?<![\w.])[-−]\s?4\b", re.IGNORECASE)

# Building blocks of the move patterns, over the question with each player mention replaced
# by a placeholder (§0§, §1§, ...)
_P = r"§\d+§"
_WORD = r"[^\W\d_][\w'’.\-]*"
_SLOT = rf"(?:{_P}|{_WORD}(?:\s+{_WORD}){{0,2}}?)"
_DET = r"(?:(?:my|the|a|an)\s+)?"
_TO = r"(?:for|with|to|into|->|→|=>)"
# Where a bought player's name ends: punctuation, the end, or a word that starts a new clause
_END = (r"(?=\s*(?:[,.;:!?)]|$)|\s+(?:and|or|but|so|then|if|next|this|before|now|gw|gameweek|week|i|"
        r"for\s+(?:the\s+)?(?:next|this|coming)|in\s+gw|with\s+(?:\d|one|two|a\s+hit|my))\b)")

# Move patterns as (pattern, its (sold, bought) group pairs). Only the ones anchored on a
# transfer verb take a word (an unresolved name) in a player's place, and only when the other
# side of the move is a player: elsewhere "Is Saka out injured in GW5?" would read as a move.
_PATTERNS: List[Tuple["re.Pattern[str]", List[Tuple[str, str]]]] = [
    # "Haaland and Palmer for Isak and Rice"
    (re.compile(rf"(?P<s1>{_P})\s*(?:,|and|&)\s*(?P<s2>{_P})\s+{_TO}\s+(?P<b1>{_P})\s*(?:,|and|&)\s*(?P<b2>{_P})",
                re.IGNORECASE), [("s1", "b1"), ("s2", "b2")]),
    # "sell Haaland for Isak", "replace Haaland with Isak", "swap out Haaland for Isak"
    (re.compile(rf"\b(?:sell|selling|swap|swapping|replace|replacing|switch|switching|transfer\s+out|"
                rf"transferring\s+out|ship\s+out|get\s+rid\s+of|offload|move)\s+(?:out\s+)?(?:from\s+)?{_DET}"
                rf"(?P<s>{_SLOT})\s+(?:{_TO}|and\s+(?:get|buy|bring\s+in))\s+{_DET}(?P<b>{_SLOT}){_END}",
                re.IGNORECASE), [("s", "b")]),
    # "get Isak instead of Haaland"
    (re.compile(rf"\b(?:get|getting|buy|buying|bring\s+in|transfer\s+in|pick\s+up|go\s+for)\s+{_DET}"
                rf"(?P<b>{_SLOT})\s+instead\s+of\s+{_DET}(?P<s>{_SLOT}){_END}", re.IGNORECASE), [("s", "b")]),
    # "Haaland out, Isak in"
    (re.compile(rf"(?P<s>{_P})\s+out\s*(?:[,;/&]\s*|\s+and\s+|\s+)(?P<b>{_P})\s+in\b", re.IGNORECASE), [("s", "b")]),
    # "Isak in for Haaland", "Isak instead of Haaland"
    (re.compile(rf"(?P<b>{_P})\s+(?:in\s+for|instead\s+of)\s+(?P<s>{_P})", re.IGNORECASE), [("s", "b")]),
    # "Haaland for Isak", "Haaland -> Isak" (the question has transfer wording elsewhere)
    (re.compile(rf"(?P<s>{_P})\s+{_TO}\s+(?P<b>{_P})", re.IGNORECASE), [("s", "b")]),
    (re.compile(rf"(?P<b>{_P})\s+over\s+(?P<s>{_P})", re.IGNORECASE), [("s", "b")]),
]
# Transfer notation that is wording in itself
_MASKED_WORDING = re.compile(
    rf"{_P}\s*(?:->|→|=>)\s*{_P}|{_P}\s+out\s*(?:[,;/&]\s*|\s+and\s+|\s+){_P}\s+in\b|{_P}\s+in\s+for\s+{_P}",
    re.IGNORECASE)
# A lone sale or purchase ("Should I sell him?", "buy Isak")
_SELL_ONLY = re.compile(rf"\b(?:sell|selling|transfer\s+out|transferring\s+out|ship\s+out|get\s+rid\s+of|"
                        rf"offload|offloading)\s+{_DET}(?P<s>{_P}|him\b)", re.IGNORECASE)
_BUY_ONLY = re.compile(rf"\b(?:buy|buying|bring\s+in|bringing\s+in|transfer\s+in|transferring\s+in|get\s+in)\s+"
                       rf"{_DET}(?P<b>{_P}|him\b)", re.IGNORECASE)

# Words that can sit in a move's place without being a name ("sell him for someone cheaper")
_NOT_NAMES = {
    "i", "me", "my", "we", "you", "he", "him", "his", "she", "her", "they", "them", "it", "one", "someone",
    "somebody", "anyone", "anybody", "player", "players", "option", "options", "a", "an", "the", "this",
    "that", "these", "those", "who", "what", "which", "should", "would", "could", "can", "is", "it's", "go",
    "get", "sell", "buy", "swap", "replace", "transfer", "out", "in", "for", "to", "with", "and", "or",
    "captain", "keeper", "goalkeeper", "defender", "midfielder", "forward", "striker", "winger", "premium",
    "budget", "cheaper", "cheap", "better", "best", "else", "other", "another", "differential", "pick",
    "worth", "hit", "free", "next", "week", "gameweek", "gw", "now", "then", "yes", "no", "do", "does",
    "make", "move", "plan", "team", "squad", "bench", "money", "cash", "points", "point", "instead", "of",
}
_PRONOUNS = {"he", "him"}


@dataclass
class Move:
    sell: Optional[str]          # the player sold (graph name); None when only a buy was named
    buy: Optional[str]           # the player bought; None when only a sale was named
    position: int = 0            # where the move starts in the question
    missing: Optional[str] = None  # the name in the other side's place that matched no player


@dataclass
class TransferQuestion:
    moves: List[Move] = field(default_factory=list)
    free_transfers: int = 1
    free_transfers_stated: bool = False
    hit_mentioned: bool = False
    unresolved: List[str] = field(default_factory=list)   # names in a move's place that match no player
    others: List[str] = field(default_factory=list)       # players named but not in a move

    @property
    def pairs(self) -> List[Tuple[str, str]]:
        """Complete moves as (sold, bought), in the order asked."""
        return [(m.sell, m.buy) for m in self.moves if m.sell and m.buy]

    @property
    def players(self) -> List[str]:
        """Everyone involved, in order: each move's sold then bought player, then the rest."""
        names: List[str] = []
        for name in [n for m in self.moves for n in (m.sell, m.buy)] + self.others:
            if name and name not in names:
                names.append(name)
        return names


def free_transfers(question: str) -> Tuple[int, bool]:
    """(free transfers, whether the question says): "I have 2 free transfers" -> (2, True);
    "no FT" -> (0, True); nothing said -> (1, False)."""
    match = FREE_TRANSFERS_RE.search(question)
    if not match:
        return 1, False
    word = " ".join(match.group(1).lower().split())
    count = int(word) if word.isdigit() else _NUMBER_WORDS.get(word, 1)
    return min(count, MAX_FREE_TRANSFERS), True


def _mask(question: str, mentions: Sequence[Tuple[int, int, str]]) -> Tuple[str, List[str]]:
    """The question with each player mention replaced by §i§, and the names by i."""
    out, names, last = [], [], 0
    for start, end, name in sorted(mentions):
        if start < last:
            continue  # overlapping mentions: keep the first
        out.append(question[last:start])
        out.append(f"§{len(names)}§")
        names.append(name)
        last = end
    out.append(question[last:])
    return "".join(out), names


def _name_words(text: str) -> Optional[str]:
    """A word slot as a name, without leading filler ("go Bloggs" -> "Bloggs"), or None
    when nothing name-like is left (a pronoun, "someone cheaper", a club, a number)."""
    words = text.split()
    while words and words[0].lower().strip("'’.") in _NOT_NAMES:
        words.pop(0)
    while words and words[-1].lower().strip("'’.") in _NOT_NAMES:
        words.pop()
    if not words or all(w.lower().strip("'’.") in _NOT_NAMES for w in words):
        return None
    name = " ".join(words)
    if len(name) < 3 or EntityExtractor.find_teams(name):
        return None
    return name


def _slot(text: str, names: List[str], pronoun: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(player, unresolved name) for one side of a move."""
    found = re.fullmatch(r"§(\d+)§", text.strip())
    if found:
        return names[int(found.group(1))], None
    if text.strip().lower() in _PRONOUNS:
        return pronoun, None
    return None, _name_words(text)


def parse(question: str, mentions: Sequence[Tuple[int, int, str]],
          pronoun_players: Sequence[str] = ()) -> Optional["TransferQuestion"]:
    """The transfer question in `question`, or None when it isn't one.

    `mentions` are the players named in it as (start, end, name) (EntityExtractor.
    player_mention_spans); `pronoun_players` are players a "him" refers to (from the history).
    It needs transfer wording and a player or a name in a move's place: "What transfer should
    I make?" names nobody, so it is left to the usual pipeline.
    """
    masked, names = _mask(question, mentions)
    if not (TRANSFER_RE.search(question) or _MASKED_WORDING.search(masked)):
        return None
    pronoun = next(iter(pronoun_players), None)

    moves: List[Move] = []
    unresolved: List[str] = []
    taken: List[Tuple[int, int]] = []
    for pattern, groups in _PATTERNS:
        for match in pattern.finditer(masked):
            slots = [g for pair in groups for g in pair]
            span = (min(match.start(g) for g in slots), max(match.end(g) for g in slots))
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            found = []
            for sold_group, bought_group in groups:
                sold, sold_missing = _slot(match.group(sold_group), names, pronoun)
                bought, bought_missing = _slot(match.group(bought_group), names, pronoun)
                # Each side is a player or a name that matched none, and one side is a player
                if not (sold or bought) or not (sold or sold_missing) or not (bought or bought_missing):
                    found = []
                    break
                found.append((Move(sold, bought, match.start(sold_group), sold_missing or bought_missing),
                              [sold_missing, bought_missing]))
            if not found:
                continue
            taken.append(span)
            for move, missing in found:
                moves.append(move)
                unresolved += [m for m in missing if m and m not in unresolved]

    used = {n for m in moves for n in (m.sell, m.buy) if n}
    for pattern, side in ((_SELL_ONLY, "s"), (_BUY_ONLY, "b")):
        for match in pattern.finditer(masked):
            name, _missing = _slot(match.group(side), names, pronoun)
            if not name or name in used:
                continue
            used.add(name)
            moves.append(Move(name, None, match.start(side)) if side == "s" else Move(None, name, match.start(side)))
    moves.sort(key=lambda m: m.position)

    others = []
    for name in list(pronoun_players) + names:
        if name not in used and name not in others:
            others.append(name)
    if not moves and not others and not unresolved:
        return None
    count, stated = free_transfers(question)
    return TransferQuestion(moves=moves, free_transfers=count, free_transfers_stated=stated,
                            hit_mentioned=bool(HIT_RE.search(question)), unresolved=unresolved, others=others)


# ------------------------------------------------------------------ prompt context

def _signed(value: float) -> str:
    return f"{value:+.1f}"


def _surname(name: str) -> str:
    return name.split()[-1] if name else name


def _move_lines(index: int, sold: Outlook, bought: Outlook, cost: int) -> List[str]:
    """The numbers that decide one move: gain next gameweek and over the horizon, its cost,
    the price difference."""
    a, b = _surname(sold.name), _surname(bought.name)
    if a == b:
        a, b = sold.name, bought.name
    paid = "free transfer" if cost == 0 else f"{cost}-point hit"
    lines = [f"Move {index} ({paid}): sell {sold.name} -> buy {bought.name}"]
    for o in (sold, bought):
        if o.in_game is False:
            lines.append(f"- {o.name} is not in the current FPL game, so this move can't be weighed on forecasts.")
    if sold.in_game and bought.in_game:
        gain = bought.points - sold.points
        lines.append(f"- GW{bought.gameweek} predicted points: {a} {sold.points:.1f}, {b} {bought.points:.1f}: "
                     f"gain {_signed(gain)}")
        gameweeks = [gw for gw, _, _ in bought.projections if gw in {g for g, _, _ in sold.projections}]
        if len(gameweeks) > 1:
            out_total, in_total = sold.projected(gameweeks), bought.projected(gameweeks)
            horizon = in_total - out_total
            text = (f"- Projected GW{gameweeks[0]}-GW{gameweeks[-1]}: {a} {out_total:.1f}, {b} {in_total:.1f}: "
                    f"gain {_signed(horizon)}")
            if cost:
                text += f"; after the {cost}-point hit {_signed(horizon - cost)}"
            lines.append(text)
        else:
            lines.append("- No projection beyond the next gameweek; judge the later gameweeks on the fixtures below.")
            if cost:
                lines[-2] += f"; after the {cost}-point hit {_signed(gain - cost)}"
    if sold.price is not None and bought.price is not None:
        diff = sold.price - bought.price
        money = f"frees £{diff:.1f}m" if diff >= 0 else f"costs £{-diff:.1f}m more"
        lines.append(f"- Price: {a} £{sold.price:.1f}m, {b} £{bought.price:.1f}m: {money}")
    return lines


def context(question: TransferQuestion, outlooks: Dict[str, Outlook]) -> str:
    """The Transfer Analysis section of the prompt."""
    def outlook(name: str) -> Outlook:
        return outlooks.get(name) or Outlook(name=name, in_game=None)

    pairs = question.pairs[:MAX_MOVES]
    free = question.free_transfers
    said = "as the user says" if question.free_transfers_stated else "assumed; the user didn't say"
    moves = (f"{len(pairs)} move{'s' if len(pairs) != 1 else ''} (sell -> buy), in the order asked" if pairs
             else "no complete move (sell X -> buy Y) named; the players asked about are below")
    lines = [f"Transfer question: {moves}. "
             f"Free transfers: {free} ({said}); each move beyond {free} costs {HIT_COST} points."]
    if question.hit_mentioned:
        lines.append("The user mentions taking a hit.")
    lines.append("Prices are current FPL prices; the user's selling price can be lower than the current price.")

    hits, gain_next, gain_horizon, money, complete = 0, 0.0, 0.0, 0.0, True
    for i, (sold_name, bought_name) in enumerate(pairs, start=1):
        sold, bought = outlook(sold_name), outlook(bought_name)
        cost = HIT_COST if i > free else 0
        hits += cost
        lines.append("")
        lines += _move_lines(i, sold, bought, cost)
        if sold.in_game and bought.in_game:
            gain_next += bought.points - sold.points
            gameweeks = [gw for gw, _, _ in bought.projections if gw in {g for g, _, _ in sold.projections}]
            if len(gameweeks) > 1:
                gain_horizon += bought.projected(gameweeks) - sold.projected(gameweeks)
            else:
                complete = False
            if sold.price is not None and bought.price is not None:
                money += sold.price - bought.price
        else:
            complete = False
    if len(pairs) > 1:
        summary = f"Whole plan: next-gameweek gain {_signed(gain_next)}"
        if complete:
            summary += f", projected gain over the horizon {_signed(gain_horizon)}"
        summary += f", hits {hits} points"
        if complete:
            summary += f", net {_signed(gain_horizon - hits)}"
        summary += f", budget {'frees' if money >= 0 else 'needs'} £{abs(money):.1f}m."
        lines += ["", summary]

    halves = [m for m in question.moves if not (m.sell and m.buy)]
    if halves:
        lines.append("")
        for m in halves:
            if m.missing:
                lines.append(f'Sell "{m.missing}" (no such player in the data) -> buy {m.buy}' if m.buy
                             else f'Sell {m.sell} -> buy "{m.missing}" (no such player in the data)')
            else:
                lines.append(f"Named to sell, with no replacement named: {m.sell}" if m.sell
                             else f"Named to buy, with no player named to sell: {m.buy}")
    if question.unresolved:
        lines.append("")
        lines.append("Not found in the data (say so plainly; don't guess who was meant): "
                     + ", ".join(f'"{name}"' for name in question.unresolved))

    lines += ["", "Players:"]
    lines += [outlook_text(outlook(name)) for name in question.players]
    return "\n".join(lines) + "\n"
