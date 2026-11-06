"""
Entity Extraction for FPL Query Processing using spaCy
"""
from config.settings import SEASONS
import spacy
from spacy.matcher import Matcher
from spacy.tokens import Doc
from typing import Dict, Iterable, List, Optional, Set, Tuple
from dataclasses import dataclass, field
import re

# A season written as "2022-23", "2022/23", "2022-2023" or "22/23".
SEASON_TEXT_RE = re.compile(r"\b(?:19|20)?\d{2}\s*[-/–]\s*(?:19|20)?\d{2}\b")

# A possessive ending, which spaCy can keep inside an entity ("Leeds United's", "Wolves'").
_POSSESSIVE_RE = re.compile(r"['’]s?$")
# Words that join club names inside one spaCy entity ("Aston Villa vs Arsenal").
_CLUB_JOINERS = {"vs", "v", "versus", "and", "or"}

# Pronouns that can point back at players from earlier in the conversation. A singular one
# means one player; a plural one up to two ("Compare Salah and Saka" -> "Who has the better
# fixtures out of them?").
SINGULAR_PRONOUN_RE = re.compile(r"\b(?:he|him|his|himself)\b", re.IGNORECASE)
PLURAL_PRONOUN_RE = re.compile(
    r"\b(?:they|them|their|theirs|themselves|both|(?:these|those|the)\s+two)\b", re.IGNORECASE)
# Plural wording that picks among players ("which of them", "both"), as opposed to a plain
# "they", which after a club question means the club ("How are Arsenal doing?" -> "Who do they
# play next?")
_PARTITIVE_RE = re.compile(
    r"\b(?:both|(?:these|those|the)\s+two|(?:of|between|out\s+of)\s+(?:them|these|those|the\s+two))\b",
    re.IGNORECASE)
# How many earlier exchanges (question + answer) a pronoun looks back through, and how many
# levels of pronouns in those questions are resolved in turn
HISTORY_EXCHANGES = 3
_HISTORY_DEPTH = 2


def history_exchanges(chat_history: Optional[List[Dict[str, str]]],
                      limit: int = HISTORY_EXCHANGES) -> List[Tuple[str, str]]:
    """(user question, assistant answer) pairs from a chat history, newest first.

    A user message starts an exchange and the assistant's reply completes it; an assistant
    message with no question before it (a greeting) is an exchange with an empty question.
    """
    exchanges: List[List[str]] = []
    for msg in chat_history or []:
        role, content = msg.get("role"), str(msg.get("content") or "")
        if role == "user":
            exchanges.append([content, ""])
        elif role == "assistant":
            if exchanges and not exchanges[-1][1]:
                exchanges[-1][1] = content
            else:
                exchanges.append(["", content])
    return [(question, answer) for question, answer in reversed(exchanges)][:limit]


def _unique(names: Iterable[str]) -> List[str]:
    out: List[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def _locate(text: str, name: str) -> Optional[Tuple[int, int]]:
    """(start, end) of a player's name in the text as a whole word: the full name, else the
    surname, else another word of it (3+ letters), case-insensitively; None if absent."""
    words = [w for w in name.split() if len(w) >= 3]
    for key in [name] + words[::-1]:
        match = re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text, re.IGNORECASE)
        if match:
            return match.start(), match.end()
    return None


def _keyword_regex(keys: Iterable[str]) -> "re.Pattern[str]":
    """Match any of the (lowercase) keys as whole words.

    Longer keys are tried first, so the longest key at a position wins and the words it
    covers can't match again: "coventry city" beats "city", "bonus points" beats "points".
    Whole words keep "goal" out of "goalkeepers" and "cs" out of "statistics".
    """
    alternatives = sorted(keys, key=len, reverse=True)
    body = "|".join(r"\s+".join(re.escape(word) for word in key.split()) for key in alternatives)
    return re.compile(rf"\b(?:{body})\b")


def _keyword_spans(regex: "re.Pattern[str]", table: Dict[str, str], text: str) -> List[Tuple[int, int, str]]:
    """(start, end, value) for each of the table's keys found in the text, in order."""
    # Lowercased one character at a time so the offsets stay those of `text` ("İ" lowercases
    # to two characters)
    lowered = [c.lower() for c in text]
    origin = [i for i, low in enumerate(lowered) for _ in low]
    spans: List[Tuple[int, int, str]] = []
    for match in regex.finditer("".join(lowered)):
        value = table.get(" ".join(match.group().split()))
        if value:
            spans.append((origin[match.start()], origin[match.end() - 1] + 1, value))
    return spans


def _find_keywords(regex: "re.Pattern[str]", table: Dict[str, str], text: str) -> List[str]:
    """The table's values for the keys found in the text, in order of appearance, no repeats."""
    found: List[str] = []
    for _start, _end, value in _keyword_spans(regex, table, text):
        if value not in found:
            found.append(value)
    return found


def _names_clubs(text: str, start: int, end: int, clubs: List[Tuple[int, int, str]]) -> bool:
    """Whether text[start:end] is only club names (the spans in `clubs`), allowing "vs"/"and"
    between them and a possessive ending: "Leeds United", "Tottenham's", "Aston Villa vs
    Arsenal". Not "Conor Coventry", though Coventry is a club."""
    end = start + len(_POSSESSIVE_RE.sub("", text[start:end]))
    for word in re.finditer(r"\w+", text[start:end]):
        if word.group().lower() in _CLUB_JOINERS:
            continue
        w_start, w_end = start + word.start(), start + word.end()
        if not any(c_start <= w_start and w_end <= c_end for c_start, c_end, _ in clubs):
            return False
    return True


@dataclass
class ExtractedEntities:
    """Container for extracted entities from a query."""
    players: List[str] = field(default_factory=list)
    teams: List[str] = field(default_factory=list)
    positions: List[str] = field(default_factory=list)
    seasons: List[str] = field(default_factory=list)
    gameweeks: List[int] = field(default_factory=list)
    stats: List[str] = field(default_factory=list)
    numbers: List[int] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "players": self.players,
            "teams": self.teams,
            "positions": self.positions,
            "seasons": self.seasons,
            "gameweeks": self.gameweeks,
            "stats": self.stats,
            "numbers": self.numbers
        }
    
    def has_entities(self) -> bool:
        """Check if any entities were extracted."""
        return any([
            self.players, self.teams, self.positions,
            self.seasons, self.gameweeks, self.stats, self.numbers
        ])


class EntityExtractor:
    """
    Extracts FPL-specific entities from user queries.
    Uses pattern matching and known entity lists.
    """
    
    # Every club in the graph (2020-21 onwards, including the 2026-27 promoted clubs), keyed
    # by lowercase name or nickname and mapped to the graph's Team name (FPL short names:
    # "Ipswich", "Man Utd"). Matched as whole words, longest key first (find_teams), so a
    # full name like "Coventry City" or "Leeds United" wins over the nicknames "city"/"united".
    TEAMS = {
        # Full names and common variations
        "arsenal": "Arsenal",
        "aston villa": "Aston Villa",
        "villa": "Aston Villa",
        "bournemouth": "Bournemouth",
        "brentford": "Brentford",
        "brighton": "Brighton",
        "brighton & hove albion": "Brighton",
        "burnley": "Burnley",
        "chelsea": "Chelsea",
        "coventry": "Coventry City",
        "coventry city": "Coventry City",
        "crystal palace": "Crystal Palace",
        "palace": "Crystal Palace",
        "everton": "Everton",
        "fulham": "Fulham",
        "hull": "Hull City",
        "hull city": "Hull City",
        "ipswich": "Ipswich",
        "ipswich town": "Ipswich",
        "leeds": "Leeds",
        "leeds united": "Leeds",
        "leicester": "Leicester",
        "leicester city": "Leicester",
        "liverpool": "Liverpool",
        "luton": "Luton",
        "luton town": "Luton",
        "manchester city": "Man City",
        "man city": "Man City",
        "city": "Man City",
        "manchester united": "Man Utd",
        "man utd": "Man Utd",
        "man united": "Man Utd",
        "united": "Man Utd",
        "newcastle": "Newcastle",
        "newcastle united": "Newcastle",
        "nottingham forest": "Nott'm Forest",
        "nott'm forest": "Nott'm Forest",
        "forest": "Nott'm Forest",
        "norwich": "Norwich",
        "norwich city": "Norwich",
        "sheffield united": "Sheffield Utd",
        "sheffield utd": "Sheffield Utd",
        "southampton": "Southampton",
        "spurs": "Spurs",
        "sunderland": "Sunderland",
        "tottenham": "Spurs",
        "tottenham hotspur": "Spurs",
        "watford": "Watford",
        "west brom": "West Brom",
        "west bromwich": "West Brom",
        "west bromwich albion": "West Brom",
        "west ham": "West Ham",
        "west ham united": "West Ham",
        "wolves": "Wolves",
        "wolverhampton": "Wolves",
        "wolverhampton wanderers": "Wolves",
    }
    _TEAM_RE = _keyword_regex(TEAMS)
    
    # Position variations
    POSITIONS = {
        "goalkeeper": "GK",
        "goalkeepers": "GK",
        "gk": "GK",
        "keeper": "GK",
        "keepers": "GK",
        "goalie": "GK",
        "goalies": "GK",
        "defender": "DEF",
        "def": "DEF",
        "defenders": "DEF",
        "defence": "DEF",
        "defense": "DEF",
        "cb": "DEF",
        "rb": "DEF",
        "lb": "DEF",
        "fullback": "DEF",
        "centre back": "DEF",
        "midfielder": "MID",
        "mid": "MID",
        "midfielders": "MID",
        "midfield": "MID",
        "cm": "MID",
        "cam": "MID",
        "cdm": "MID",
        "winger": "MID",
        "forward": "FWD",
        "fwd": "FWD",
        "forwards": "FWD",
        "striker": "FWD",
        "strikers": "FWD",
        "attacker": "FWD",
        "attackers": "FWD",
        "cf": "FWD",
        "st": "FWD",
    }
    
    # Stats variations, matched as whole words like TEAMS (a key doesn't match inside a
    # longer word, so word forms worth catching are listed: "goalscorers", "assisters")
    STATS = {
        "goals": "goals_scored",
        "goal": "goals_scored",
        "scored": "goals_scored",
        "goalscorer": "goals_scored",
        "goalscorers": "goals_scored",
        "goalscoring": "goals_scored",
        "assists": "assists",
        "assist": "assists",
        "assisted": "assists",
        "assister": "assists",
        "assisters": "assists",
        "points": "total_points",
        "total points": "total_points",
        "fpl points": "total_points",
        "clean sheets": "clean_sheets",
        "clean sheet": "clean_sheets",
        "cleansheets": "clean_sheets",
        "cs": "clean_sheets",
        "bonus": "bonus",
        "bonus points": "bonus",
        "bps": "bps",
        "minutes": "minutes",
        "mins": "minutes",
        "ict": "ict_index",
        "ict index": "ict_index",
        "influence": "influence",
        "creativity": "creativity",
        "threat": "threat",
        "value": "value",
        "price": "value",
        "cost": "value",
        "form": "form",
        "saves": "saves",
        "save": "saves",
        "yellow cards": "yellow_cards",
        "yellows": "yellow_cards",
        "red cards": "red_cards",
        "reds": "red_cards",
        "cards": "yellow_cards",
        "transfers": "transfers_in",
        "transfers in": "transfers_in",
        "transfers out": "transfers_out",
        "selected": "selected",
        "ownership": "selected",
        "owned": "selected",
    }
    _STAT_RE = _keyword_regex(STATS)
    
    # Season patterns
    SEASON_PATTERNS = [
        r"20(\d{2})[-/](\d{2})",  # 2021-22, 2021/22
        r"20(\d{2})",  # 2021, 2022, 2023
        r"(\d{2})[-/](\d{2})\s+season",  # 21-22 season
        r"last season",
        r"this season",
        r"previous season",
    ]
    
    def __init__(self, known_players: Optional[Set[str]] = None):
        """
        Initialize entity extractor with spaCy.
        
        Args:
            known_players: Optional set of known player names for better extraction
        """
        self.known_players = known_players or set()
        # Optional services.player_index.PlayerIndex: accent/typo-tolerant matching that
        # prefers popular players for ambiguous surnames. Set by the API after startup.
        self.player_index = None
        # Load spaCy model
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            print("Warning: spaCy model 'en_core_web_sm' not found. Attempting to download...")
            try:
                import subprocess
                import sys
                # Use -q flag for quiet download to reduce output
                subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], 
                               check=True, capture_output=True, timeout=120)  # 2 minute timeout
                self.nlp = spacy.load("en_core_web_sm")
            except Exception as e:
                print(f"Error downloading spaCy model: {e}")
                # Fallback to a blank model if download fails
                self.nlp = spacy.blank("en")
                print("Using blank English model as fallback.")

                self.nlp = spacy.blank("en")
        
        # Initialize matcher for patterns
        self.matcher = Matcher(self.nlp.vocab)
        self._add_patterns()
    
    def _add_patterns(self):
        """Add spaCy matcher patterns for FPL entities."""
        # Gameweek patterns
        gameweek_patterns = [
            [{"LOWER": {"IN": ["gw", "gameweek"]}}, {"IS_DIGIT": True}],
            [{"LOWER": "game"}, {"LOWER": "week"}, {"IS_DIGIT": True}],
            [{"LOWER": "week"}, {"IS_DIGIT": True}]
        ]
        for pattern in gameweek_patterns:
            self.matcher.add("GAMEWEEK", [pattern])
        
        # Season patterns
        season_patterns = [
            [{"SHAPE": "dddd"}, {"TEXT": "-"}, {"SHAPE": "dd"}],  # 2021-22
            [{"SHAPE": "dddd"}, {"TEXT": "/"}, {"SHAPE": "dd"}],  # 2021/22
            [{"SHAPE": "dddd"}, {"TEXT": "-"}, {"SHAPE": "dddd"}],  # 2021-2022
            [{"LOWER": {"IN": ["last", "this", "previous"]}}, {"LOWER": "season"}]
        ]
        for pattern in season_patterns:
            self.matcher.add("SEASON", [pattern])
    
    def set_known_players(self, players: Set[str]):
        """
        Set the known players list for better extraction.
        
        Args:
            players: Set of known player names
        """
        self.known_players = players
    
    def extract(self, query: str, chat_history: Optional[List[Dict[str, str]]] = None) -> ExtractedEntities:
        """
        Extract all entities from a query using spaCy.
        
        Args:
            query: User's input query
            chat_history: Optional conversation history for pronoun resolution
            
        Returns:
            ExtractedEntities with all found entities
        """
        entities = ExtractedEntities()
        
        # Process with spaCy
        doc = self.nlp(query)
        
        # Extract teams
        entities.teams = self._extract_teams(doc)
        
        # Extract positions
        entities.positions = self._extract_positions(doc)
        
        # Extract seasons
        entities.seasons = self._extract_seasons(doc)
        
        # Extract gameweeks
        entities.gameweeks = self._extract_gameweeks(doc)
        
        # Extract stats
        entities.stats = self._extract_stats(doc)
        
        # Extract numbers (after gameweeks to avoid duplication)
        entities.numbers = self._extract_numbers(doc, entities.gameweeks)
        
        # Extract players (do this last, using other entities for context)
        entities.players = self._extract_players(doc, entities)

        # Pronouns that point back at players from earlier exchanges ("Is he a better captain
        # than Haaland?" after a question about Isak: Isak, then Haaland)
        if chat_history:
            entities.players = self.resolve_pronouns(query, entities.players, history_exchanges(chat_history))

        return entities

    def resolve_pronouns(self, text: str, players: List[str], exchanges: List[Tuple[str, str]],
                         depth: int = _HISTORY_DEPTH) -> List[str]:
        """`players` (named in `text`) plus the players its pronouns refer to.

        `exchanges` are the earlier (question, answer) pairs, newest first (history_exchanges).
        - A singular pronoun (he/him/his) before any named player, or with none named, is a
          player from the history, put first: "Is he a better captain than Haaland?". After a
          named player it is that player: "Haaland is in form, is he worth captaining?".
        - A plural pronoun (they/them/both/"the two") with no player named takes up to two
          players from the history: "Who has the better fixtures out of them?". Not when a
          club is named before it ("Arsenal are flying, are they worth a triple-up?").
        """
        singular = SINGULAR_PRONOUN_RE.search(text)
        plural = PLURAL_PRONOUN_RE.search(text)
        if not exchanges or depth < 0 or not (singular or plural):
            return players
        first_named = self.first_player_position(text, players)
        if singular and (first_named is None or singular.start() < first_named):
            earlier = self._antecedent(exchanges, 1, partitive=False, depth=depth)
            return _unique(earlier + list(players))
        if plural and not players and not any(start < plural.start() for start, _end, _team in self.find_team_spans(text)):
            return self._antecedent(exchanges, 2, partitive=bool(_PARTITIVE_RE.search(text)), depth=depth)
        return players

    def _antecedent(self, exchanges: List[Tuple[str, str]], count: int, partitive: bool, depth: int) -> List[str]:
        """Up to `count` players a pronoun refers to, from the newest exchange that has any.

        Within an exchange the players the user's own question named come first (its pronouns
        resolved against the exchanges before it); the assistant's answer is used only when the
        question named none, since an answer also names rivals and alternatives. After a club
        question ("How are Arsenal doing?") a plain "they" is the club, so no player.
        """
        for i, (question, answer) in enumerate(exchanges):
            named = self.players_in(question)
            if depth > 0:
                named = self.resolve_pronouns(question, named, exchanges[i + 1:], depth - 1)
            if named:
                return named[:count]
            if count > 1 and not partitive and question and self.find_teams(question):
                return []
            found = self.players_in(answer, limit=count)
            if found:
                return found
        return []

    def players_in(self, text: str, limit: int = 2) -> List[str]:
        """Players named in a piece of text (an earlier question or answer), in order."""
        if not text:
            return []
        if self.player_index is not None:
            return self._player_mentions(text, limit=limit)
        return self._extract_players(self.nlp(text), ExtractedEntities())[:limit]

    def player_mention_spans(self, text: str) -> List[Tuple[int, int, str]]:
        """Every player mention in the text as (start, end, player name), in order, without
        the limit of two that `extract` keeps (a transfer question can name four or more).
        Words of a club's name are skipped as in `extract`. Without the player index, the
        players `extract` finds are located in the text by name or surname."""
        if self.player_index is not None:
            return self._player_mention_spans(text)
        spans = []
        for name in self._extract_players(self.nlp(text), ExtractedEntities()):
            located = _locate(text, name)
            if located:
                spans.append((located[0], located[1], name))
        return sorted(spans)

    def first_player_position(self, text: str, players: List[str]) -> Optional[int]:
        """Where the first of `players` is named in the text; None when no player is named.
        A player that can't be located counts as named at the start, so a pronoun is then
        read as referring to him (the safe reading: no player from the history is added)."""
        if not players:
            return None
        if self.player_index is not None:
            starts = [start for start, _end, name in self._player_mention_spans(text) if name in players]
            if starts:
                return min(starts)
        located = [found[0] for found in (_locate(text, name) for name in players) if found]
        return min(located) if located else 0
    
    @classmethod
    def find_teams(cls, text: str) -> List[str]:
        """Graph team names mentioned in the text, in order ("Hull City vs Man City" ->
        ["Hull City", "Man City"]; "Leeds United" -> ["Leeds"], not Man Utd as well)."""
        return _find_keywords(cls._TEAM_RE, cls.TEAMS, text)

    @classmethod
    def find_team_spans(cls, text: str) -> List[Tuple[int, int, str]]:
        """The matches behind find_teams, as (start, end, graph team name) indexing the text:
        "Who is the Coventry City goalkeeper?" -> [(11, 24, "Coventry City")]."""
        return _keyword_spans(cls._TEAM_RE, cls.TEAMS, text)

    def _player_mentions(self, text: str, limit: int) -> List[str]:
        """Players the index finds in the text, skipping words that belong to a club's name.

        In "Who is the Coventry City goalkeeper?" Coventry is the club, not Conor Coventry,
        and "Leeds" isn't a misspelt Lees-Melou. A player named in full still counts, even
        when a club shares a word of the name ("How did Conor Coventry do?").
        """
        return _unique(name for _start, _end, name in self._player_mention_spans(text))[:limit]

    def _player_mention_spans(self, text: str) -> List[Tuple[int, int, str]]:
        """The index's mentions in the text as (start, end, name), minus club words (see
        _player_mentions). A player named twice is listed twice."""
        if not text:
            return []
        clubs = self.find_team_spans(text)
        spans: List[Tuple[int, int, str]] = []
        for start, end, name, full_name in self.player_index.mention_spans(text):
            if not full_name and any(start < c_end and c_start < end for c_start, c_end, _ in clubs):
                continue
            spans.append((start, end, name))
        return spans

    def _extract_teams(self, doc: Doc) -> List[str]:
        """Extract team names from the query, in the order they're mentioned.

        Substring matching used to read "Coventry City" as Man City ("city") and "Leeds
        United" as Leeds and Man Utd ("united"). spaCy's ORG entities added nothing the
        whole-word match doesn't find, so they're no longer consulted.
        """
        return self.find_teams(doc.text)
    
    def _extract_positions(self, doc: Doc) -> List[str]:
        """Extract positions from query using spaCy tokens."""
        positions = []
        
        for token in doc:
            normalized = self.POSITIONS.get(token.text.lower())
            if normalized and normalized not in positions:
                positions.append(normalized)
        
        # Check for multi-word positions
        text_lower = doc.text.lower()
        for pos_key, pos_name in self.POSITIONS.items():
            if " " in pos_key and pos_key in text_lower:
                if pos_name not in positions:
                    positions.append(pos_name)
        
        return positions
    
    def _extract_seasons(self, doc: Doc) -> List[str]:
        """Extract seasons ("2024-25", "2024/25", "2024-2025", "2023", "last season").

        Only seasons present in config.settings.SEASONS are returned.
        """
        seasons: List[str] = []
        text_lower = doc.text.lower()

        def add(start_year: int) -> bool:
            season = f"{start_year}-{str(start_year + 1)[2:]}"
            if season in SEASONS and season not in seasons:
                seasons.append(season)
                return True
            return False

        # "season that ended in 2022" -> the season that finished that year (2021-22)
        ended_years = set()
        for year_text in re.findall(r"ended\s+in\s+(20\d{2})", text_lower):
            ended_years.add(year_text)
            add(int(year_text) - 1)

        # Explicit ranges: 2021-22, 2021/22, 2021-2022, 2021 - 22
        spans = []
        for m in re.finditer(r"\b(20\d{2})\s*[-/–]\s*(20\d{2}|\d{2})\b", text_lower):
            start_year, end_text = int(m.group(1)), m.group(2)
            end_year = int(end_text) if len(end_text) == 4 else 2000 + int(end_text)
            if end_year == start_year + 1:
                add(start_year)
                spans.append(m.span())

        # Relative phrases: SEASONS[-1] is the season in progress, so "this/current
        # season" is that one and "last/previous season" the one before it.
        relative = []
        if re.search(r"\b(this|current)\s+season\b", text_lower):
            relative.append(SEASONS[-1])
        if re.search(r"\b(last|previous)\s+season\b", text_lower):
            relative.append(SEASONS[-2] if len(SEASONS) > 1 else SEASONS[-1])
        for season in relative:
            if season not in seasons:
                seasons.append(season)

        # A lone year: the season starting that year, else the one ending in it
        for m in re.finditer(r"\b(20\d{2})\b", text_lower):
            if m.group(1) in ended_years or any(a <= m.start() < b for a, b in spans):
                continue
            year = int(m.group(1))
            if not add(year):
                add(year - 1)

        return seasons
    
    def _extract_gameweeks(self, doc: Doc) -> List[int]:
        """Extract gameweek numbers from query using spaCy matcher."""
        gameweeks = []
        matches = self.matcher(doc)
        
        for match_id, start, end in matches:
            if self.nlp.vocab.strings[match_id] == "GAMEWEEK":
                span = doc[start:end]
                # Extract the number from the span
                for token in span:
                    if token.like_num:
                        gw = int(token.text)
                        if 1 <= gw <= 38 and gw not in gameweeks:
                            gameweeks.append(gw)
        
        return gameweeks
    
    def _extract_stats(self, doc: Doc) -> List[str]:
        """Extract stat names from the query, in the order they're mentioned.

        Whole words only: substring matching found "goal" in "goalkeepers", so "top 3
        goalkeepers by points" ranked keepers by goals. A multi-word key covers its words,
        so "bonus points" is bonus alone, not total points too.
        """
        return _find_keywords(self._STAT_RE, self.STATS, doc.text)
    
    def _extract_numbers(self, doc: Doc, exclude_gws: List[int]) -> List[int]:
        """Extract numbers from query using spaCy (excluding gameweeks)."""
        numbers = []
        # Digits inside a season ("2022-23", "2023/24", "22/23") are not counts: spaCy splits
        # "2022-23" into "2022", "-", "23", and the "23" used to become the result limit.
        season_spans = [m.span() for m in SEASON_TEXT_RE.finditer(doc.text)]

        def usable(text: str, start: int) -> Optional[int]:
            if not text.isdigit() or any(a <= start < b for a, b in season_spans):
                return None
            num = int(text)
            if num in exclude_gws or 1900 <= num <= 2100:  # gameweeks and years
                return None
            return num

        for token in doc:
            if token.like_num:
                num = usable(token.text, token.idx)
                if num is not None and num not in numbers:
                    numbers.append(num)

        # Also check CARDINAL entities
        for ent in doc.ents:
            if ent.label_ == "CARDINAL":
                num = usable(ent.text, ent.start_char)
                if num is not None and num not in numbers:
                    numbers.append(num)

        return numbers
    
    def _extract_players(self, doc: Doc, entities: ExtractedEntities) -> List[str]:
        """
        Extract player names from query using spaCy NER.
        Uses known players list and spaCy's PERSON entities.
        Supports partial matching (e.g., "Salah" matches "Mohamed Salah").
        """
        players = []
        text_lower = doc.text.lower()
        clubs = self.find_team_spans(doc.text)

        if self.player_index is not None:
            players = self._player_mentions(doc.text, limit=2)
        
        # First, check against known players (exact full name match)
        elif self.known_players:
            for player in self.known_players:
                if player.lower() in text_lower:
                    players.append(player)
        
        # If no exact match, try partial matching (last name or common nicknames)
        if not players and self.known_players and self.player_index is None:
            query_words = set(text_lower.split())
            # Common words to exclude from matching
            common_words = {
                'the', 'a', 'an', 'in', 'on', 'at', 'for', 'to', 'of', 'and', 'or',
                'how', 'did', 'do', 'does', 'was', 'were', 'is', 'are', 'what', 'who',
                'most', 'best', 'top', 'all', 'get', 'show', 'find', 'gameweek', 'season',
                'stats', 'points', 'goals', 'assists', 'scored', 'performance'
            }
            query_words = query_words - common_words
            # Nor the words of a club's name ("Coventry" in "Coventry City fixtures")
            query_words -= {w for start, end, _ in clubs for w in doc.text[start:end].lower().split()}
            
            for player in self.known_players:
                player_parts = player.lower().split()
                # Check if any part of the player name (especially last name) matches query words
                for part in player_parts:
                    if len(part) >= 4 and part in query_words:  # Minimum 4 chars to avoid false positives
                        players.append(player)
                        break
                if players:
                    break  # Found a match, stop searching
        
        # If no known players found, use spaCy's PERSON entity recognition
        if not players:
            # Filter out common non-name words
            non_names = {
                "Premier", "League", "Season", "Gameweek", "Week",
                "Top", "Best", "Most", "Points", "Goals", "Assists",
                "Who", "What", "When", "Where", "How", "Which",
                "The", "And", "For", "With", "From", "This", "That",
                "Get", "Show", "Find", "List", "Tell", "Give", "Compare",
                "Stats", "Statistics", "Performance", "Data", "Info",
                "Comprehensive", "Detailed", "All", "Seasons", "Overall",
                "Player", "Players", "Team", "Teams", "Position", "Positions"
            }
            
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    # Check if not a club (spaCy tags "Leeds United" and "Tottenham's" as
                    # PERSON too) or common word
                    if (not _names_clubs(doc.text, ent.start_char, ent.end_char, clubs) and
                        ent.text not in non_names and
                        len(ent.text) > 2):
                        players.append(ent.text)
        
        return players[:2]  # Limit to 2 players for comparisons
    
    def get_query_parameters(self, entities: ExtractedEntities) -> Dict:
        """
        Convert extracted entities to query parameters.
        
        Args:
            entities: Extracted entities
            
        Returns:
            Dictionary of parameters for Cypher queries
        """
        params = {}
        
        if entities.players:
            # What a player search (search_players_by_name) looks for. Without it "Who is
            # Bukayo Saka?" missed a required param and got the top-players fallback.
            params["name_pattern"] = entities.players[0]
            if len(entities.players) >= 2:
                params["player1"] = entities.players[0]
                params["player2"] = entities.players[1]
            else:
                params["player_name"] = entities.players[0]
        
        if entities.teams:
            if len(entities.teams) >= 2:
                params["team1"] = entities.teams[0]
                params["team2"] = entities.teams[1]
            else:
                params["team_name"] = entities.teams[0]
        
        if entities.positions:
            params["position"] = entities.positions[0]
        
        # Only set season if explicitly mentioned - don't set a default
        # This allows "all seasons" queries to work properly
        if entities.seasons:
            params["season"] = entities.seasons[0]
        
        if entities.gameweeks:
            params["gameweek"] = entities.gameweeks[0]
        
        if entities.numbers:
            # Use first number as limit if reasonable
            if entities.numbers[0] <= 50:
                params["limit"] = entities.numbers[0]
        
        # Derive sort_by from stats
        if entities.stats:
            stat_map = {
                "goals_scored": "goals",
                "assists": "assists",
                "total_points": "total_points",
                "bonus": "bonus",
                "ict_index": "ict_index",
                "clean_sheets": "clean_sheets",
                "transfers_in": "transfers_in",
                "transfers_out": "transfers_out",
                "selected": "selected",
                "value": "value"
            }
            # Use the first relevant stat found
            for stat in entities.stats:
                if stat in stat_map:
                    params["sort_by"] = stat_map[stat]
                    break
                    
        return params
