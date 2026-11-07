"""Questions about the coming run of fixtures, for the assistant.

- Clubs: "Which teams have the easiest fixtures?", "Arsenal's next 6 fixtures", "fixture swing":
  season_service's fixture ticker (FPL difficulty per match, easiest run first).
- Players over several gameweeks: "Best defenders for the next 5 gameweeks", "Who will score the
  most points over the next 3 weeks?": the model's projections (projections.csv, at most 4
  gameweeks, used only when they start at the predictions' gameweek), summed per player.

Detection and formatting are pure and tested; api_main.py reads the files and the ticker.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

PROJECTION_WEEKS = 4        # projections.csv covers the next 4 gameweeks
DEFAULT_CLUB_WEEKS = 6
MAX_CLUB_WEEKS = 10
PLAYER_ROWS = 10
CLUB_ROWS = 6

_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "couple": 2, "couple of": 2, "few": 4, "several": 5}

HORIZON_RE = re.compile(
    r"\b(?:next|coming|upcoming|following)\s+(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|couple(?:\s+of)?|few|several)\s+"
    r"(?:game\s*weeks?|gws?|weeks?|games|matches|fixtures|rounds)\b"
    r"|\b(?:over|for|in)\s+(?:the\s+)?(\d{1,2})\s+(?:game\s*weeks?|gws?|weeks)\b",
    re.IGNORECASE)
CLUB_RUN_RE = re.compile(
    r"\b(?:easiest|easier|easy|kindest|kinder|nicest|softest|best|good|favou?rable|hardest|harder|toughest|tougher|"
    r"worst|difficult|tricky|trickiest)\s+(?:run\s+of\s+)?(?:fixtures?|schedules?|fixture\s+runs?)\b"
    r"|\b(?:easiest|kindest|softest|hardest|toughest|trickiest)\s+runs?\b"
    r"|\bfixture\s+(?:runs?|swings?|ticker|difficulty|list)\b|\bfdr\b|\brun\s+of\s+fixtures\b",
    re.IGNORECASE)
NEXT_FIXTURES_RE = re.compile(
    r"\b(?:next|upcoming|coming)\s+(?:\d{1,2}\s+|two\s+|three\s+|four\s+|five\s+|six\s+|few\s+)?(?:fixtures|games|matches)\b",
    re.IGNORECASE)
PLAYER_WORDS = re.compile(
    r"\b(?:players?|picks?|options?|targets?|buys?|differentials?|who|best|top|most|score|scorers?|points|"
    r"goalkeepers?|keepers?|gks?|defenders?|defs?|midfielders?|mids?|forwards?|strikers?|fwds?|attackers?)\b",
    re.IGNORECASE)
CLUB_WORDS = re.compile(r"\b(?:teams?|clubs?|sides?)\b", re.IGNORECASE)
HARD_RE = re.compile(r"\b(?:hardest|harder|toughest|tougher|worst|difficult|tricky|trickiest)\b", re.IGNORECASE)


def requested_weeks(question: str) -> Optional[int]:
    """The number of gameweeks a question asks about ("next 5 gameweeks" -> 5), or None."""
    m = HORIZON_RE.search(question or "")
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").lower()
    raw = re.sub(r"\s+", " ", raw)
    n = int(raw) if raw.isdigit() else _NUMBERS.get(raw)
    return n if n and n > 0 else None


def classify(question: str, players: Sequence[str] = (), teams: Sequence[str] = (),
             positions: Sequence[str] = ()) -> Optional[str]:
    """"clubs" (a club's or every club's coming fixtures), "players" (the best players over the
    next few gameweeks) or None. A named player leaves the question to the player paths."""
    text = question or ""
    if players:
        return None
    weeks = requested_weeks(text)
    club_run = bool(CLUB_RUN_RE.search(text))
    if teams and len(teams) == 1 and (club_run or NEXT_FIXTURES_RE.search(text) or (weeks and re.search(r"\bfixtures?\b", text, re.I))):
        return "clubs"
    if club_run:
        # "Which defenders have the easiest fixtures?" ranks players; "which teams" ranks clubs
        if positions and not CLUB_WORDS.search(text):
            return "players"
        return "clubs"
    if weeks and weeks >= 2 and (positions or PLAYER_WORDS.search(text)):
        return "players"
    return None


def wants_hardest(question: str) -> bool:
    return bool(HARD_RE.search(question or ""))


# ------------------------------------------------------------------ clubs

def _fixture_text(item: Dict[str, Any]) -> str:
    diff = item.get("difficulty")
    return f"{item.get('opponent')} ({'H' if item.get('home') else 'A'}, FDR {diff if diff is not None else '?'})"


def club_line(club: Dict[str, Any]) -> str:
    weeks = []
    for w in club.get("gameweeks") or []:
        fx = w.get("fixtures") or []
        if not fx:
            weeks.append(f"GW{w['gameweek']} blank")
        else:
            weeks.append(f"GW{w['gameweek']} " + " & ".join(_fixture_text(f) for f in fx)
                         + (" (double)" if len(fx) > 1 else ""))
    avg = club.get("average_difficulty")
    return (f"{club.get('team')}: average FDR {avg if avg is not None else '?'} over {club.get('fixtures')} fixture(s) - "
            + "; ".join(weeks))


def clubs_context(ticker: Dict[str, Any], teams: Sequence[str] = (), hardest: bool = False,
                  asked_weeks: Optional[int] = None, rows: int = CLUB_ROWS) -> str:
    """The ticker as text: the asked clubs, or the easiest (and hardest) runs."""
    gws = ticker.get("gameweeks") or []
    span = f"GW{gws[0]}-{gws[-1]}" if len(gws) > 1 else (f"GW{gws[0]}" if gws else "the coming gameweeks")
    lines = [f"FPL fixture difficulty (FDR 1 easiest to 5 hardest, from each club's side) for {span}:"]
    if asked_weeks and gws and asked_weeks > len(gws):
        lines.append(f"Note: only {len(gws)} gameweek(s) of fixtures are listed (the season's end or the {MAX_CLUB_WEEKS}-week limit).")
    clubs = ticker.get("clubs") or []
    if teams:
        chosen = [c for c in clubs if c.get("team") in teams]
        if not chosen:
            lines.append("No fixtures found for " + ", ".join(teams) + ".")
        for c in chosen:
            rank = clubs.index(c) + 1
            lines.append(f"{club_line(c)} (ranked {rank} of {len(clubs)} by easiest run)")
        return "\n".join(lines)
    order = list(reversed(clubs)) if hardest else clubs
    lines.append(f"{'Hardest' if hardest else 'Easiest'} runs first:")
    for i, c in enumerate(order[:rows], start=1):
        lines.append(f"{i}. {club_line(c)}")
    other = list(reversed(clubs))[:3] if not hardest else clubs[:3]
    if other and len(clubs) > rows:
        lines.append(f"{'Easiest' if hardest else 'Hardest'} runs, for contrast: "
                     + "; ".join(f"{c.get('team')} (average FDR {c.get('average_difficulty')})" for c in other))
    return "\n".join(lines)


# ------------------------------------------------------------------ players

def player_run_rows(projections: Any, by_element: Dict[int, List[Dict[str, Any]]], position: Optional[str] = None,
                    limit: int = PLAYER_ROWS) -> List[Dict[str, Any]]:
    """The best players by total projected points over the projected gameweeks.

    `by_element` is my_team.projected_by_element's {element: [{gameweek, points}]}; names, clubs,
    prices, opponents and status come from the projections rows."""
    if projections is None or not by_element:
        return []
    info: Dict[int, Dict[str, Any]] = {}
    opponents: Dict[int, Dict[int, str]] = {}
    for r in projections.to_dict("records"):
        try:
            el = int(r.get("element"))
        except (TypeError, ValueError):
            continue
        info.setdefault(el, r)
        try:
            opponents.setdefault(el, {})[int(r.get("gameweek"))] = str(r.get("opponents") or "")
        except (TypeError, ValueError):
            pass
    rows = []
    for el, weeks in by_element.items():
        r = info.get(el)
        if r is None:
            continue
        pos = str(r.get("position") or "")
        if position and pos != position:
            continue
        rows.append({
            "element": el, "name": str(r.get("name") or ""), "web_name": r.get("web_name"), "position": pos,
            "team": r.get("team"), "now_cost": r.get("now_cost"), "status": r.get("status"),
            "total": round(sum(w["points"] for w in weeks), 2), "weeks": weeks,
            "opponents": [opponents.get(el, {}).get(w["gameweek"], "") for w in weeks],
        })
    rows.sort(key=lambda x: (-x["total"], x["name"]))
    return rows[:limit]


def players_context(rows: List[Dict[str, Any]], position_word: Optional[str], asked_weeks: Optional[int],
                    weeks: Sequence[int]) -> str:
    span = f"GW{weeks[0]}-{weeks[-1]}" if len(weeks) > 1 else (f"GW{weeks[0]}" if weeks else "")
    what = position_word or "players"
    lines = [f"Projected points for {what} over {span} ({len(weeks)} gameweek(s)), from the model's projections, "
             "best total first:"]
    if asked_weeks and asked_weeks > len(weeks):
        lines.append(f"Note: the user asked about {asked_weeks} gameweeks, but the projections cover only "
                     f"{len(weeks)}. Say so.")
    for i, r in enumerate(rows, start=1):
        per = ", ".join(f"GW{w['gameweek']} {w['points']:.1f}" + (f" vs {o}" if o else "")
                        for w, o in zip(r["weeks"], r["opponents"]))
        price = f", £{float(r['now_cost']) / 10:.1f}m" if r.get("now_cost") not in (None, "") else ""
        status = str(r.get("status") or "a")
        flag = f" [FPL flag: {status}]" if status not in ("a", "nan", "None", "") else ""
        lines.append(f"{i}. {r['name']} ({r['position']}, {r['team']}{price}): {r['total']:.1f} pts ({per}){flag}")
    if not rows:
        lines.append("No projections match.")
    return "\n".join(lines)


def next_gameweek_fallback(predictions: Any, position: Optional[str], asked_weeks: Optional[int],
                           limit: int = PLAYER_ROWS) -> str:
    """When there are no projections from this run: the next gameweek's predictions only."""
    df = predictions
    if position:
        df = df[df["position"] == position]
    top = df.sort_values("predicted_points", ascending=False).head(limit)
    gw = int(top["gameweek"].iloc[0]) if len(top) and "gameweek" in top.columns else None
    lines = [f"Only next gameweek's predictions are available (GW{gw}); there are no multi-gameweek projections "
             f"from this prediction run{f', so the {asked_weeks}-gameweek question can only be answered for GW{gw}' if asked_weeks else ''}. Say so."]
    for i, (_, r) in enumerate(top.iterrows(), start=1):
        lines.append(f"{i}. {r['name']} ({r['position']}, {r['team']}): {float(r['predicted_points']):.1f} pts vs {r.get('opponent')}")
    return "\n".join(lines)


def projection_card(rows: List[Dict[str, Any]], weeks: Sequence[int], position: Optional[str],
                    player_ref: Any, photo: Any, codes: Dict[int, int], season: str,
                    position_words: Dict[str, Sequence[str]]) -> Optional[Dict[str, Any]]:
    """A leaderboard card of the projected totals (the existing leaderboard type)."""
    if not rows or not weeks:
        return None
    span = f"GW{weeks[0]}-{weeks[-1]}" if len(weeks) > 1 else f"GW{weeks[0]}"
    columns = [{"key": f"gw{w}", "label": f"GW{w}", "format": "dec1"} for w in weeks]
    out_rows = []
    rank = 0
    last = None
    for i, r in enumerate(rows):
        if r["total"] != last:
            rank, last = i + 1, r["total"]
        out_rows.append({
            "rank": rank,
            "player": player_ref(r["name"], r.get("team"), r.get("position"),
                                 photo(r["name"], r.get("team"), codes.get(r["element"])), r.get("status")),
            "value": round(r["total"], 1),
            "values": {f"gw{w['gameweek']}": round(w["points"], 1) for w in r["weeks"]},
            "detail": None, "group": None,
        })
    plural = position_words.get(position, (None, "players"))[1] if position else "players"
    return {
        "type": "leaderboard",
        "id": f"leaderboard:projection:{position or 'all'}:{span}",
        "title": f"Projected {plural}, {span}",
        "subtitle": f"Next {len(weeks)} gameweeks · model projections",
        "icon": "calendar",
        "metric": {"key": "total", "label": "Projected", "format": "dec1", "unit": "pts"},
        "columns": columns,
        "grouped": False,
        "season": season,
        "rows": out_rows,
        "highlights": [],
        "_follow_ups": [],
    }
