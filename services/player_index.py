"""
In-memory index of every player, used for search-as-you-type, resolving
user-typed names to the exact DB name, and spotting player mentions in questions.

Built once from Neo4j (~2k players) and rebuilt after a data load. All matching
is on accent/punctuation-free names (utils.text.normalize_name), so "odegaard"
finds "Martin Ødegaard". Ties are broken by popularity: current-season players
first, then career FPL points.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from rapidfuzz import fuzz, process

from utils.text import normalize_name, normalize_name_with_offsets

logger = logging.getLogger(__name__)

# Words that are never treated as a player mention on their own.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "than", "then", "have", "has",
    "been", "about", "into", "over", "under", "only", "also", "very", "just", "like",
    "what", "which", "when", "where", "does", "will", "would", "could", "should", "much",
    "many", "more", "most", "least", "best", "worst", "better", "next", "last", "total",
    "highest", "lowest", "average", "score", "scores", "scored", "scorer", "scorers",
    "point", "points", "goal", "goals", "assist", "assists", "clean", "sheet", "sheets",
    "player", "players", "team", "teams", "season", "seasons", "gameweek", "week",
    "game", "games", "match", "matches", "compare", "comparison", "versus", "between",
    "form", "value", "price", "cost", "predict", "prediction", "predicted", "captain",
    "pick", "tell", "show", "give", "list", "find", "minutes", "bonus", "fixture",
    "fixtures", "league", "premier", "fantasy", "stats", "statistics", "performance",
    "record", "records", "history", "yellow", "cards", "card", "saves", "save",
    "keeper", "goalkeeper", "goalkeepers", "defender", "defenders", "midfielder",
    "midfielders", "forward", "forwards", "striker", "strikers", "winger", "wingers",
    "home", "away", "against", "good", "great", "nice", "thanks", "hello", "please",
    "young", "city", "united", "town", "rovers", "wanderers", "athletic", "albion",
}

# Everyday words of chat questions that sit one typo from a surname and share its first letter
# ("there" -> Thiers, "start" -> Stuart): never typo-corrected into a player in free text. An
# exact surname still matches ("white", "wood"), and the search box corrects any word.
_COMMON_WORDS = {
    "there", "their", "these", "those", "think", "thing", "things", "three", "other", "another",
    "after", "before", "until", "since", "while", "still", "every", "first", "second", "third",
    "start", "starts", "started", "starting", "bench", "benched", "going", "gonna", "right",
    "worth", "money", "budget", "cheap", "cheaper", "chance", "chances", "choice", "likely",
    "really", "might", "maybe", "today", "tonight", "tomorrow", "weekend", "table", "title",
    "relegated", "relegation", "injury", "injured", "injuries", "doubt", "doubtful", "deadline",
    "wildcard", "triple", "boost", "chips", "squad", "selling", "buying", "bring", "taking",
    "transfer", "transfers", "differential", "differentials", "owned", "ownership", "rules",
    "model", "manager", "accurate", "rising", "falling", "double", "blank", "banned", "suspended",
    "return", "returns", "available", "playing", "plays", "played", "facing", "faces", "winner",
    "winning", "wins", "beats", "draws", "loses", "result", "results", "finish", "finishes",
    "europe", "champions", "explain", "expected", "earlier", "later", "lately", "recent", "recently",
}

_MIN_TOKEN_LEN = 4          # shorter words are too ambiguous ("son", "kai") to match alone
_FUZZY_TOKEN_CUTOFF = 88    # typo tolerance for single words ("haland" -> "haaland")
_FUZZY_SEARCH_CUTOFF = 80   # typo tolerance for words typed in the search box
_WORD = re.compile(r"\S+")


@dataclass
class PlayerEntry:
    player_name: str
    position: Optional[str]
    element_id: Optional[int]
    team_name: Optional[str]
    is_current: bool
    career_points: int
    normalized: str
    tokens: Tuple[str, ...]

    @property
    def popularity(self) -> Tuple[int, int]:
        return (1 if self.is_current else 0, self.career_points)

    def to_row(self) -> Dict[str, Any]:
        return {
            "player_name": self.player_name,
            "position": self.position,
            "element_id": self.element_id,
            "team_name": self.team_name,
        }


class Mention(NamedTuple):
    """A player mentioned in free text: text[start:end] is what named them."""
    start: int
    end: int
    player_name: str
    full_name: bool  # named in full, not by a single (possibly misspelt) word of the name


class PlayerIndex:
    LOAD_QUERY = """
    MATCH (p:Player)
    OPTIONAL MATCH (p)-[:PLAYS_POSITION]->(pos:Position)
    OPTIONAL MATCH (p)-[:PLAYS_FOR]->(t:Team)
    OPTIONAL MATCH (p)-[st:SEASON_STATS]->(:Season)
    RETURN p.name AS player_name, pos.code AS position, p.element_id AS element_id,
           coalesce(t.name, p.last_team) AS team_name, t IS NOT NULL AS is_current,
           coalesce(sum(st.total_points), 0) AS career_points
    """

    def __init__(self, rows: List[Dict[str, Any]]):
        self.entries: List[PlayerEntry] = []
        self._by_name: Dict[str, PlayerEntry] = {}
        self._by_normalized: Dict[str, PlayerEntry] = {}
        self._by_token: Dict[str, List[PlayerEntry]] = {}

        for row in rows:
            name = row.get("player_name")
            if not name:
                continue
            normalized = normalize_name(name)
            entry = PlayerEntry(
                player_name=name,
                position=row.get("position"),
                element_id=row.get("element_id"),
                team_name=row.get("team_name"),
                is_current=bool(row.get("is_current")),
                career_points=int(row.get("career_points") or 0),
                normalized=normalized,
                tokens=tuple(normalized.split()),
            )
            self.entries.append(entry)
            self._by_name[name] = entry
            existing = self._by_normalized.get(normalized)
            if existing is None or entry.popularity > existing.popularity:
                self._by_normalized[normalized] = entry
            for token in set(entry.tokens):
                self._by_token.setdefault(token, []).append(entry)

        for bucket in self._by_token.values():
            bucket.sort(key=lambda e: e.popularity, reverse=True)
        self._token_vocab = [t for t in self._by_token if len(t) >= _MIN_TOKEN_LEN]
        # A typo almost always keeps the first letter ("haland", "sallah"), while an ordinary word
        # one letter from a surname usually doesn't ("right" isn't Wright, "there" isn't Mathers)
        self._vocab_by_initial: Dict[str, List[str]] = {}
        for token in self._token_vocab:
            self._vocab_by_initial.setdefault(token[0], []).append(token)

    @classmethod
    def load(cls, conn) -> "PlayerIndex":
        index = cls(conn.execute_query(cls.LOAD_QUERY, {}))
        logger.info("Player index built with %d players", len(index))
        return index

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------ lookups

    def get(self, name: str) -> Optional[PlayerEntry]:
        """Exact or accent-insensitive full-name lookup."""
        if not name:
            return None
        return self._by_name.get(name) or self._by_normalized.get(normalize_name(name))

    def team_of(self, name: str) -> Optional[str]:
        entry = self.get(name)
        return entry.team_name if entry else None

    def resolve(self, name: str) -> Optional[str]:
        """Map user-typed text to an exact DB player name, if we're confident."""
        entry = self.get(name)
        if entry:
            return entry.player_name
        for query in (name, self._correct_typos(name)):
            if not query:
                continue
            ranked = self._rank(query)
            if ranked and ranked[0][0] >= 2:  # every typed word matched the name
                return ranked[0][1].player_name
        return None

    # ------------------------------------------------------------------ search

    def _rank(self, query: str) -> List[Tuple[int, PlayerEntry]]:
        """Score every player against the query.

        Tiers: 4 exact, 3 every word is a prefix of a name word (in order),
        2 every word appears inside the name, 1 some words match. Within a tier,
        popularity decides, so "salah" ranks Mohamed Salah first. Partial (tier 1)
        matches are only returned when nothing matched every word.
        """
        q = normalize_name(query)
        q_tokens = q.split()
        if not q_tokens:
            return []

        scored: List[Tuple[Tuple, PlayerEntry]] = []
        for entry in self.entries:
            if entry.normalized == q:
                tier, matched = 4, len(q_tokens)
            else:
                prefix = sum(any(t.startswith(qt) for t in entry.tokens) for qt in q_tokens)
                # Substring matches only for 3+ letters, so "de" doesn't match "Foden"
                inside = sum(
                    any(t.startswith(qt) or (len(qt) >= 3 and qt in t) for t in entry.tokens)
                    for qt in q_tokens
                )
                if inside == 0:
                    continue
                if prefix == len(q_tokens) and _in_order(q_tokens, entry.tokens):
                    tier = 3
                elif inside == len(q_tokens):
                    tier = 2
                else:
                    tier = 1
                matched = inside
            scored.append(((tier, matched, *entry.popularity), entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0][0] >= 2:
            scored = [item for item in scored if item[0][0] >= 2]
        return [(key[0], entry) for key, entry in scored]

    def _correct_typos(self, query: str) -> Optional[str]:
        """Replace unknown words with the closest name word ("haland" -> "haaland")."""
        tokens = normalize_name(query).split()
        corrected = []
        for token in tokens:
            if len(token) >= _MIN_TOKEN_LEN and token not in self._by_token and                     not any(v.startswith(token) for v in self._token_vocab):
                hit = process.extractOne(token, self._token_vocab, scorer=fuzz.ratio,
                                         score_cutoff=_FUZZY_SEARCH_CUTOFF)
                if hit:
                    token = hit[0]
            corrected.append(token)
        result = " ".join(corrected)
        return result if result != " ".join(tokens) else None

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search-box results; typo correction only kicks in when nothing matched fully."""
        ranked = self._rank(query)
        if not ranked or ranked[0][0] < 2:
            corrected = self._correct_typos(query)
            if corrected:
                ranked = self._rank(corrected) or ranked
        return [entry.to_row() for _tier, entry in ranked[:limit]]

    # ---------------------------------------------------------------- mentions

    def mentions(self, text: str, limit: int = 2) -> List[str]:
        """Player names mentioned in free text, in the order they appear (see mention_spans)."""
        names: List[str] = []
        for mention in self.mention_spans(text):
            if mention.player_name not in names:
                names.append(mention.player_name)
        return names[:limit]

    def mention_spans(self, text: str) -> List[Mention]:
        """Every player mention in free text, in order, with where it is in the text.

        Full names win over single words ("Mohamed Salah" is one mention, not two);
        a lone surname/first name maps to the most popular player with that word,
        with a little typo tolerance for longer words. A player named twice is listed
        twice. The spans let a caller drop words that mean something else in context,
        like the "Coventry" of Coventry City.
        """
        normalized, offsets = normalize_name_with_offsets(text)
        if not normalized:
            return []
        padded = f" {normalized} "

        # Spans index `normalized` until `offsets` maps them back to `text` at the end
        spans: List[Tuple[int, int, PlayerEntry]] = []
        for name, entry in self._by_normalized.items():
            start = padded.find(f" {name} ")
            if start != -1:
                spans.append((start, start + len(name), entry))
        spans.sort(key=lambda s: s[1] - s[0], reverse=True)
        chosen: List[Tuple[int, int, PlayerEntry, bool]] = []
        for start, end, entry in spans:
            if not any(start < c[1] and c[0] < end for c in chosen):
                chosen.append((start, end, entry, True))

        for match in _WORD.finditer(normalized):
            start, end, word = match.start(), match.end(), match.group()
            if len(word) < _MIN_TOKEN_LEN or word in _STOPWORDS:
                continue
            if any(start < c[1] and c[0] < end for c in chosen):
                continue
            candidates = self._by_token.get(word)
            if not candidates and len(word) >= 5 and word not in _COMMON_WORDS:
                hit = process.extractOne(word, self._vocab_by_initial.get(word[0], ()), scorer=fuzz.ratio,
                                         score_cutoff=_FUZZY_TOKEN_CUTOFF)
                candidates = self._by_token.get(hit[0]) if hit else None
            if candidates:
                chosen.append((start, end, candidates[0], False))

        return [Mention(offsets[start], offsets[end - 1] + 1, entry.player_name, full_name)
                for start, end, entry, full_name in sorted(chosen, key=lambda c: c[0])]


def _in_order(query_tokens: List[str], candidate_tokens: Tuple[str, ...]) -> bool:
    i = 0
    for qt in query_tokens:
        while i < len(candidate_tokens) and not candidate_tokens[i].startswith(qt):
            i += 1
        if i == len(candidate_tokens):
            return False
        i += 1
    return True
