"""The assistant's answers about a visitor's own FPL team and about chips.

"Who should I captain?", "What transfers should I make?", "Rate my bench", "When should I use my
bench boost?": with the team ID the My team page saved (sent as `fpl_team_id`), the LLM gets the
squad from services/my_team.get_team (each player's next-gameweek prediction, 4-gameweek
projection, prices and flags; the suggested XI, bench order and armband; bank and free transfers),
and for transfer and chip questions the planner's moves (services/my_team.plan_team). Without an
ID a question gets the usual generic answer plus a pointer to the My team page.

Detection and formatting are pure and tested; api_main.py does the I/O.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

MY_TEAM_PATH = "/my-team"
# Appended to a personal question's answer when no team ID came with it
MY_TEAM_HINT = (f"_Want this for your own squad? Load your team on the [My team]({MY_TEAM_PATH}) page "
                "and ask again._")

# Chips: the words, plus wording that asks for advice about them
CHIP_WORDS = re.compile(
    r"\b(?:bench[\s-]*boost(?:s|ing|ed)?|bb|triple[\s-]*captain(?:s|ing|ed|cy)?|tc"
    r"|wild[\s-]*card(?:s|ing|ed)?|free[\s-]*hit(?:s|ting)?|chips?)\b", re.IGNORECASE)
CHIP_ADVICE_WORDS = re.compile(
    r"\b(?:should|when|worth|best|use|using|play|playing|activate|save|saving|my|now|this\s+week|"
    r"candidates?|which|who|whom|time|timing|good|week|gw|gameweek)\b", re.IGNORECASE)

# "my team", "my captain", "my free transfers", ...
MY_WORDS = re.compile(
    r"\bmy\s+(?:fpl\s+)?(?:own\s+)?(team|squad|side|captain(?:cy)?|armband|vice(?:[\s-]*captain)?"
    r"|transfers?|free\s+transfers?|bench|subs|line[\s-]?up|starting\s+(?:xi|11|eleven)|xi|players?"
    r"|keepers?|goalkeepers?|defen[cs]e|defenders?|midfield(?:ers?)?|attack|forwards?|strikers?"
    r"|bank|budget|chips?|rank|picks?)\b", re.IGNORECASE)
# "Who should I captain/bench/sell?", "What transfers should I make?"
SHOULD_I = re.compile(
    r"\b(?:who|which\s+(?:players?|one)|whom)\s+(?:should|do|shall|would|must)\s+i\s+"
    r"(captain|vice|transfer|sell|bench|start|drop|keep|bring|buy|get)\b"
    r"|\bwhat\s+transfers?\s+(?:should|do|shall|would|can|could)\s+i\b"
    r"|\b(?:should|do)\s+i\s+(?:take\s+a\s+hit|roll|bank\s+(?:my|the|a)\s+transfer|save\s+(?:my|the|a)\s+transfer)",
    re.IGNORECASE)

_KIND_OF_WORD = {
    "captain": "captain", "captaincy": "captain", "armband": "captain", "vice": "captain",
    "transfer": "transfers", "transfers": "transfers", "sell": "transfers", "buy": "transfers",
    "bring": "transfers", "get": "transfers", "drop": "transfers", "keep": "transfers",
    "bank": "transfers", "budget": "transfers",
    "bench": "bench", "subs": "bench", "start": "bench",
    "chip": "chips", "chips": "chips",
}

POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
STATUS_LABELS = {"d": "doubtful", "i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad"}


def is_chip_question(question: str) -> bool:
    """Whether the question asks for advice about a chip ("When should I use my bench boost?",
    "Best triple captain this week?"): a chip word plus advice wording."""
    text = question or ""
    return bool(CHIP_WORDS.search(text)) and bool(CHIP_ADVICE_WORDS.search(text))


def question_kind(question: str) -> Optional[str]:
    """What a personal question is about: "chips", "transfers", "captain", "bench" or "team",
    or None when it isn't about the asker's own team (and isn't a chip question)."""
    text = question or ""
    if is_chip_question(text):
        return "chips"
    kinds: List[str] = []
    for match in SHOULD_I.finditer(text):
        word = (match.group(1) or "transfer").lower()
        kinds.append(_KIND_OF_WORD.get(word, "transfers"))
        if re.search(r"\bwhat\s+transfers?\b|take\s+a\s+hit|roll|bank\s+|save\s+", match.group(0), re.IGNORECASE):
            kinds[-1] = "transfers"
    for match in MY_WORDS.finditer(text):
        word = re.sub(r"[\s-]+", " ", match.group(1).lower())
        last = word.split()[-1]
        kinds.append(_KIND_OF_WORD.get(word, _KIND_OF_WORD.get(last, "team")))
    if not kinds:
        return None
    for kind in ("transfers", "captain", "bench"):
        if kind in kinds:
            return kind
    return "team"


# ------------------------------------------------------------------ formatting

def money(tenths: Any) -> str:
    try:
        return f"£{int(tenths) / 10:.1f}m"
    except (TypeError, ValueError):
        return "£?"


def _name(row: Dict[str, Any]) -> str:
    return str(row.get("web_name") or row.get("name") or f"Player {row.get('element')}")


def _opponents(row: Dict[str, Any]) -> str:
    opps = row.get("opponents") or []
    if not opps:
        return "no fixture"
    return " & ".join(f"{o.get('team')} ({'H' if o.get('home') else 'A'})" for o in opps)


def _flag(row: Dict[str, Any]) -> str:
    status = str(row.get("status") or "a")
    if status in ("a", "", "nan", "None"):
        return ""
    chance = row.get("chance")
    chance_text = f", {int(float(chance))}% chance" if chance not in (None, "") and str(chance) != "nan" else ""
    news = str(row.get("news") or "").strip()
    return f" [{STATUS_LABELS.get(status, status)}{chance_text}{': ' + news if news else ''}]"


def _gw_range(team: Dict[str, Any]) -> str:
    weeks = next((r.get("projected") for r in team.get("squad") or [] if r.get("projected")), None)
    if not weeks:
        return ""
    first, last = weeks[0]["gameweek"], weeks[-1]["gameweek"]
    return f"GW{first}-{last}" if last != first else f"GW{first}"


def team_context(team: Dict[str, Any]) -> str:
    """The squad, suggested XI, bench order, armband, bank and free transfers, for the LLM."""
    entry = team.get("entry") or {}
    squad = sorted(team.get("squad") or [], key=lambda r: (POSITION_ORDER.get(r.get("position"), 4),
                                                          -(r.get("predicted_points") or 0)))
    by_el = {r["element"]: r for r in squad}
    gw = team.get("gameweek")
    pred_gw = team.get("predictions_gameweek")
    horizon = _gw_range(team)
    lines = [
        f"Team \"{entry.get('name') or 'your team'}\" (FPL team ID {entry.get('id')}): the squad at the "
        f"GW{team.get('based_on_gameweek')} deadline, planning GW{gw}."
        + (f" Overall rank {entry['overall_rank']:,}." if entry.get("overall_rank") else ""),
        f"Bank {money(team.get('bank'))}, {team.get('free_transfers')} free transfer(s)"
        + (f", team value {money(team['team_value'])}" if team.get("team_value") else "") + ".",
    ]
    if pred_gw is not None and gw is not None and pred_gw != gw:
        lines.append(f"Note: the latest predictions are for GW{pred_gw}, not GW{gw}.")
    for t in team.get("pending_transfers") or []:
        lines.append(f"Already made for GW{t.get('gameweek')}: {_name(by_el.get(t.get('out'), {'element': t.get('out')}))} out, "
                     f"{_name(by_el.get(t.get('in'), {'element': t.get('in')}))} in.")
    header = f"Squad (position, name, club: GW{pred_gw or gw} predicted points and opponents"
    header += f"; {horizon} projected total" if horizon else ""
    header += "; price and selling price; FPL flag):"
    lines.append(header)
    for r in squad:
        proj = f"; {horizon} {r['projected_total']:.1f}" if horizon and r.get("projected_total") is not None else ""
        sell = f" (sells {money(r.get('selling_price'))})" if r.get("selling_price") != r.get("now_cost") else ""
        lines.append(f"- {r.get('position')} {_name(r)}, {r.get('team')}: {float(r.get('predicted_points') or 0):.1f} "
                     f"vs {_opponents(r)}{proj}; {money(r.get('now_cost'))}{sell}{_flag(r)}")
    lineup = team.get("lineup")
    if lineup:
        starters = [by_el[e] for e in lineup.get("starters") or [] if e in by_el]
        bench = [by_el[e] for e in lineup.get("bench") or [] if e in by_el]
        captain, vice = by_el.get(lineup.get("captain")), by_el.get(lineup.get("vice_captain"))
        lines.append(f"Suggested XI ({lineup.get('formation')}), best by predicted points: "
                     + ", ".join(_name(r) for r in starters) + ".")
        lines.append("Bench, in the order to set it: " + ", ".join(_name(r) for r in bench) + ".")
        if captain:
            lines.append(f"Suggested captain: {_name(captain)} ({float(captain.get('predicted_points') or 0):.1f} pts)"
                         + (f", vice-captain: {_name(vice)} ({float(vice.get('predicted_points') or 0):.1f} pts)" if vice else "")
                         + f". The suggested XI is projected {lineup.get('projected_points'):.1f} points with the captain doubled.")
    else:
        lines.append("No suggested XI: the squad isn't a full 15 yet.")
    chips = chips_line(team.get("chips") or [])
    if chips:
        lines.append(chips)
    return "\n".join(lines)


def chips_line(chips: Iterable[Dict[str, Any]]) -> str:
    """"Chips playable now: ...; later: ...; used: ..." from fpl_manager.chip_status rows."""
    now, later, used = [], [], []
    for c in chips:
        label = c.get("label") or c.get("chip")
        window = c.get("window") or []
        span = f" (GW{window[0]}-{window[1]})" if len(window) == 2 else ""
        if c.get("used_gameweek"):
            used.append(f"{label} in GW{c['used_gameweek']}")
        elif c.get("active"):
            now.append(f"{label}{span}")
        elif c.get("available"):
            later.append(f"{label}{span}")
    parts = []
    if now:
        parts.append("playable now: " + ", ".join(now))
    if later:
        parts.append("later: " + ", ".join(later))
    if used:
        parts.append("used: " + ", ".join(used))
    return ("Chips " + "; ".join(parts) + ".") if parts else ""


def plan_context(plan: Dict[str, Any], current_projected: Optional[float] = None) -> str:
    """The planner's moves for this gameweek, its chip and its reason."""
    inputs = plan.get("inputs") or {}
    horizon = inputs.get("horizon") or len(plan.get("horizon") or []) or 4
    hits = inputs.get("max_hits", 0)
    lines = [f"Transfer plan from the site's planner ({'free transfers only' if not hits else f'up to {hits} hit(s)'}, "
             f"looking {horizon} gameweeks ahead, GW{plan.get('gameweek')}):"]
    moves = plan.get("transfers") or []
    if moves:
        for m in moves:
            out, inn = m.get("out") or {}, m.get("in") or {}
            lines.append(f"- Sell {_name(out)} ({out.get('position')}, {out.get('team')}, sells {money(out.get('selling_price'))}) "
                         f"-> buy {_name(inn)} ({inn.get('position')}, {inn.get('team')}, {money(inn.get('price'))})")
    else:
        lines.append("- No transfer: keep the squad and roll the free transfer.")
    if plan.get("hits"):
        lines.append(f"Hits: {plan['hits']} ({plan.get('hit_cost')} points).")
    lines.append(f"Bank after: {money(plan.get('bank_after'))}.")
    projected = plan.get("projected_points")
    if projected is not None:
        before = current_projected if current_projected is not None else plan.get("current_projected")
        lines.append(f"Projected GW{plan.get('gameweek')} points with the plan: {projected:.1f}"
                     + (f" (current squad's best XI: {before:.1f})" if before is not None else "") + ".")
    if "chip" in plan:
        lines.append(f"Chip this gameweek: {chip_label(plan.get('chip')) or 'none'}.")
    squad = plan.get("squad") or []
    captain = next((p for p in squad if p.get("captain")), None)
    if captain:
        lines.append(f"Captain after the plan: {_name(captain)}.")
    if plan.get("reason"):
        lines.append(f"Planner's reason: {plan['reason']}")
    return "\n".join(lines)


CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}


def chip_label(chip: Optional[str]) -> Optional[str]:
    if not chip:
        return None
    return CHIP_NAMES.get(str(chip), str(chip))


def error_note(kind: str, team_id: int, message: str = "") -> str:
    """A short, honest line when the team couldn't be used, for the LLM to pass on."""
    reasons = {
        "not_found": f"FPL has no team with ID {team_id}.",
        "not_ready": f"Team {team_id} has no squad we can read yet (picks appear after its first deadline).",
        "fpl_down": "FPL isn't answering right now, so the team couldn't be loaded.",
    }
    return (f"The visitor's FPL team couldn't be loaded: {reasons.get(kind, message or 'unknown error')} "
            "Say so in one short sentence, then answer generally from the data below.")


def plan_missing_note(message: str) -> str:
    return (f"No transfer plan this time ({message.rstrip('.')}). Weigh moves from the squad's "
            "projections instead and don't claim a plan was run.")


# ------------------------------------------------------------------ generic chip guidance

def chip_guidance_context(predictions: Any, projections_by_gw: Dict[int, List[Dict[str, Any]]],
                          doubles_blanks: Dict[int, Dict[str, List[str]]]) -> str:
    """Chip guidance without a team: the best triple-captain candidates next gameweek, the best
    projected player of each coming gameweek, and double/blank gameweeks from the fixtures."""
    lines: List[str] = []
    if predictions is not None and len(predictions):
        top = predictions.sort_values("predicted_points", ascending=False).head(5)
        gw = top["gameweek"].iloc[0] if "gameweek" in top.columns else None
        lines.append(f"Triple-captain candidates for GW{gw} (predicted points, best first):")
        for i, (_, r) in enumerate(top.iterrows(), start=1):
            double = " (two matches)" if "fixtures" in top.columns and (r.get("fixtures") or 1) > 1 else ""
            lines.append(f"{i}. {r['name']} ({r['position']}, {r['team']}): {float(r['predicted_points']):.1f} pts "
                         f"vs {r.get('opponent')}{double}{_flag({'status': r.get('status'), 'chance': r.get('chance_of_playing_next_round'), 'news': r.get('news')})}")
    if projections_by_gw:
        lines.append("Best projected player of each coming gameweek (the projections cover at most 4 gameweeks):")
        for gw in sorted(projections_by_gw):
            rows = projections_by_gw[gw]
            if rows:
                best = rows[0]
                lines.append(f"- GW{gw}: {best['name']} {best['points']:.1f} pts")
    special = [(gw, v) for gw, v in sorted(doubles_blanks.items()) if v.get("double") or v.get("blank")]
    if special:
        lines.append("Double and blank gameweeks ahead (from the fixture list):")
        for gw, v in special:
            parts = []
            if v.get("double"):
                parts.append(f"double for {', '.join(v['double'])}")
            if v.get("blank"):
                parts.append(f"blank for {', '.join(v['blank'])}")
            lines.append(f"- GW{gw}: " + "; ".join(parts))
    elif doubles_blanks:
        lines.append(f"No double or blank gameweeks in GW{min(doubles_blanks)}-{max(doubles_blanks)}: every club plays once a week.")
    lines.append("General rules: a bench boost or triple captain is worth most in a double gameweek; a free hit "
                 "covers a blank gameweek; a wildcard suits a squad needing 4 or more transfers.")
    return "\n".join(lines)


def doubles_and_blanks(ticker: Dict[str, Any]) -> Dict[int, Dict[str, List[str]]]:
    """{gameweek: {"double": [clubs], "blank": [clubs]}} from season_service.fixture_ticker."""
    out: Dict[int, Dict[str, List[str]]] = {gw: {"double": [], "blank": []} for gw in ticker.get("gameweeks") or []}
    for club in ticker.get("clubs") or []:
        for week in club.get("gameweeks") or []:
            n = len(week.get("fixtures") or [])
            if n >= 2:
                out[week["gameweek"]]["double"].append(club["team"])
            elif n == 0:
                out[week["gameweek"]]["blank"].append(club["team"])
    for v in out.values():
        v["double"].sort()
        v["blank"].sort()
    return out


# ------------------------------------------------------------------ the pitch card

def pitch_team(team: Dict[str, Any], photo: Callable[[str, Optional[str], Optional[int]], Optional[str]]
               ) -> Optional[Dict[str, Any]]:
    """The suggested XI as a predicted Team of the Week payload for answer_cards.pitch_card."""
    lineup = team.get("lineup")
    if not lineup:
        return None
    by_el = {r["element"]: r for r in team.get("squad") or []}

    def slot(el: int) -> Optional[Dict[str, Any]]:
        r = by_el.get(el)
        if r is None:
            return None
        return {
            "name": r.get("name"), "team": r.get("team"), "position": r.get("position"),
            "photo": photo(r.get("name") or "", r.get("team"), r.get("_code")),
            "points": float(r.get("predicted_points") or 0.0), "cost": (r.get("now_cost") or 0) / 10,
            "captain": el == lineup.get("captain"), "vice_captain": el == lineup.get("vice_captain"),
            "opponents": [{"team": o.get("team"), "home": bool(o.get("home"))} for o in r.get("opponents") or []],
            "status": r.get("status"), "chance_of_playing_next_round": r.get("chance"), "news": r.get("news"),
        }

    starters = [s for s in (slot(e) for e in lineup.get("starters") or []) if s]
    bench = [s for s in (slot(e) for e in lineup.get("bench") or []) if s]
    total_cost = round(sum(s["cost"] for s in starters + bench), 1)
    return {
        "mode": "predicted", "gameweek": team.get("predictions_gameweek") or team.get("gameweek"),
        "formation": lineup.get("formation"), "total_points": lineup.get("projected_points"),
        "total_cost": total_cost, "budget": round(total_cost + (team.get("bank") or 0) / 10, 1),
        "starters": starters, "bench": bench,
    }


def plan_moves(plan: Optional[Dict[str, Any]]) -> List[List[str]]:
    """[[sold, bought], ...] full names of the plan's transfers, for comparison cards."""
    if not plan:
        return []
    out = []
    for m in plan.get("transfers") or []:
        sold, bought = (m.get("out") or {}).get("name"), (m.get("in") or {}).get("name")
        if sold and bought:
            out.append([sold, bought])
    return out


def squad_names(team: Dict[str, Any]) -> Sequence[str]:
    return [r.get("name") for r in team.get("squad") or [] if r.get("name")]
