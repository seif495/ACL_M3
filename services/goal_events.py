"""
Goal minutes and penalties for match pages, from ESPN's public scoreboard feed (no key). FPL
records who scored but not when or how, so this adds each goal's minute, whether it was a penalty
or an own goal, and missed penalties when ESPN lists them.

  site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=YYYYMMDD-YYYYMMDD

One call returns every Premier League match in the date range, each with `details`: scoring plays
and cards with a clock ("64'", "90'+4'"), the club, the players and penaltyKick/ownGoal flags.
The feed is unofficial and undocumented, so the parser is defensive: a match is kept only when
its goals add up to the final score. ESPN's `team` on an own goal may name either club, so both
readings are tried and the one that matches the score wins; if neither does, nothing is saved and
the match page falls back to FPL's scorers.

pipeline/goal_events.py runs this from the daily job for finished matches without events yet and
saves $LIVE_DATA_DIR/goal_events/<season>.json; match pages only read that file
(services/matches.py), so a page never calls ESPN. Matches are paired with ours by the two clubs
(graph names, `graph_team`) and a kickoff within two days.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from services import season_service as ss

API_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard"
WINDOW_DAYS = 7         # days per scoreboard call
PAUSE = 1.0             # seconds between calls
TIMEOUT = 15.0
STORE_DIR = "goal_events"
SOURCE = "espn"

# ESPN's club names -> the graph's, where they differ
TEAM_ALIASES = {
    "manchester city": "Man City", "manchester united": "Man Utd", "man united": "Man Utd", "man utd": "Man Utd",
    "tottenham": "Spurs", "tottenham hotspur": "Spurs", "nottingham forest": "Nott'm Forest",
    "wolverhampton wanderers": "Wolves", "wolverhampton": "Wolves", "newcastle united": "Newcastle",
    "brighton & hove albion": "Brighton", "brighton and hove albion": "Brighton", "leeds united": "Leeds",
    "leicester city": "Leicester", "ipswich town": "Ipswich", "sheffield united": "Sheffield Utd",
    "west ham united": "West Ham", "luton town": "Luton", "coventry": "Coventry City", "hull": "Hull City",
    "west bromwich albion": "West Brom", "afc bournemouth": "Bournemouth",
}

Fetch = Callable[[Dict[str, Any]], Dict[str, Any]]


def http_get(params: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    resp = httpx.get(API_URL, params=params, timeout=TIMEOUT, headers={"User-Agent": "PitchMe/1.0 (fan site)"})
    resp.raise_for_status()
    return resp.json()


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"\bfc\b|\bafc\b", "", text)).strip()


def graph_team(name: str, teams: Iterable[str]) -> Optional[str]:
    """ESPN's club name as the graph names it (aliases, then an exact or contained match)."""
    n = _norm(name)
    if n in TEAM_ALIASES:
        return TEAM_ALIASES[n]
    teams = list(teams)
    for t in teams:
        if _norm(t) == n:
            return t
    for t in teams:
        tn = _norm(t)
        if tn and n and (tn in n or n in tn):
            return t
    return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def espn_matches(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The scoreboard's matches: id, date, clubs (name and ESPN id), score, finished, details."""
    out = []
    for ev in payload.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0] or {}
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in sides or "away" not in sides:
            continue
        status = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        row: Dict[str, Any] = {"source_id": str(ev.get("id")), "date": ev.get("date") or comp.get("date"),
                               "completed": bool(status.get("completed")), "details": comp.get("details") or []}
        for side in ("home", "away"):
            team = sides[side].get("team") or {}
            row[side] = team.get("displayName") or team.get("name") or team.get("shortDisplayName") or ""
            row[f"{side}_id"] = str(team.get("id"))
            row[f"{side}_score"] = _int(sides[side].get("score"))
        out.append(row)
    return out


def pair_fixtures(source: List[Dict[str, Any]], ours: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{our fixture id: source match}: the same two clubs, home and away, within two days."""
    teams = sorted({f["home_team"] for f in ours} | {f["away_team"] for f in ours})
    by_pair: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for f in ours:
        by_pair.setdefault((f["home_team"], f["away_team"]), []).append(f)
    out: Dict[str, Dict[str, Any]] = {}
    for m in source:
        home, away = graph_team(m["home"], teams), graph_team(m["away"], teams)
        when = ss.to_datetime(m["date"]) if m.get("date") else None
        for f in by_pair.get((home, away), []):
            k = f.get("kickoff_time")
            if when is None or k is None or abs(ss.to_datetime(k) - when) <= timedelta(days=2):
                out[str(f["id"])] = m
                break
    return out


def parse_clock(clock: Any) -> Tuple[Optional[int], Optional[int]]:
    """ESPN's clock -> (minute, added time): "64'" -> (64, None), "90'+4'" -> (90, 4); else
    from its seconds."""
    clock = clock or {}
    text = str(clock.get("displayValue") or "")
    m = re.match(r"\s*(\d+)\s*'?\s*(?:\+\s*(\d+))?", text)
    if m:
        return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    secs = clock.get("value")
    if isinstance(secs, (int, float)) and secs > 0:
        return int(math.ceil(secs / 60)), None
    return None, None


def _kind(d: Dict[str, Any]) -> Optional[str]:
    text = str((d.get("type") or {}).get("text") or "").lower()
    if d.get("shootout"):
        return None
    if d.get("scoringPlay"):
        if d.get("ownGoal") or "own goal" in text:
            return "own_goal"
        if d.get("penaltyKick") or "penalty" in text:
            return "penalty"
        return "goal"
    if "penalty" in text and ("miss" in text or "saved" in text):
        return "missed_penalty"
    return None


def goal_rows(match: Dict[str, Any], home_team: str, away_team: str) -> Optional[List[Dict[str, Any]]]:
    """Goals, own goals and missed penalties of one ESPN match, in time order, with `team` the
    graph name of the player's own club (for an own goal, the side that conceded it). None when
    the goals don't add up to the final score under either reading of an own goal's club."""
    side_of = {match["home_id"]: "home", match["away_id"]: "away"}
    names = {"home": home_team, "away": away_team}
    other = {"home": "away", "away": "home"}
    events = []
    for d in match.get("details") or []:
        kind = _kind(d)
        side = side_of.get(str((d.get("team") or {}).get("id")))
        if kind is None or side is None:
            continue
        minute, extra = parse_clock(d.get("clock"))
        athletes = d.get("athletesInvolved") or []
        player = (athletes[0].get("displayName") or athletes[0].get("shortName")) if athletes else None
        events.append((kind, side, minute, extra, player))
    want = (match.get("home_score"), match.get("away_score"))
    if None in want:
        return None
    for og_listed_under_scorer in (True, False):
        rows, score = [], {"home": 0, "away": 0}
        for kind, side, minute, extra, player in events:
            own = side if og_listed_under_scorer or kind != "own_goal" else other[side]
            if kind != "missed_penalty":
                score[other[own] if kind == "own_goal" else own] += 1
            rows.append({"minute": minute, "extra": extra, "kind": kind, "player": player, "assist": None,
                         "team": names[own]})
        if (score["home"], score["away"]) == want:
            rows.sort(key=lambda g: ((g["minute"] or 0), (g["extra"] or 0)))
            return rows
    return None


# ------------------------------------------------------------------ store

def store_path(live_dir: str, season: str) -> str:
    return os.path.join(live_dir, STORE_DIR, f"{season}.json")


def load_store(live_dir: str, season: str) -> Dict[str, Any]:
    try:
        with open(store_path(live_dir, season), encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    store.setdefault("season", season)
    store.setdefault("fixtures", {})
    store.setdefault("unmatched", {})
    return store


def save_store(live_dir: str, season: str, store: Dict[str, Any]) -> None:
    path = store_path(live_dir, season)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    store["source"] = SOURCE
    store["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f)
    os.replace(tmp, path)


def events_for(fixture_id: str, season: str) -> Optional[List[Dict[str, Any]]]:
    """A match's saved goal events (None when not fetched), for match pages."""
    path = store_path(ss.live_dir(), season)
    store = ss._cached(("goal-events", path, ss._mtime(path)), lambda: load_store(ss.live_dir(), season))
    return store.get("fixtures", {}).get(str(fixture_id))


def windows(days: Iterable[date], span: int = WINDOW_DAYS) -> List[Tuple[date, date]]:
    """Date ranges of at most `span` days covering every day in `days`, one day of margin each
    side (ESPN dates matches in US Eastern time)."""
    out: List[Tuple[date, date]] = []
    for d in sorted(set(days)):
        start, end = d - timedelta(days=1), d + timedelta(days=1)
        if out and start <= out[-1][1] and (end - out[-1][0]).days < span:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        elif out and start <= out[-1][1]:
            out.append((out[-1][1] + timedelta(days=1), end))
        else:
            out.append((start, end))
    return out


UNMATCHED_RETRY = timedelta(days=3)     # a match whose goals didn't add up is tried again for 3 days


def update(ours: List[Dict[str, Any]], season: str, live_dir: str, *, fetch: Fetch = http_get,
           max_calls: int = 40, pause: float = PAUSE) -> Dict[str, Any]:
    """Fetch goal events for `ours`' finished matches that have none saved, a week of
    scoreboard per call, within max_calls."""
    store = load_store(live_dir, season)
    now = datetime.now(timezone.utc)
    finished = [f for f in ours if f.get("home_score") is not None and f.get("status") != "scheduled"
                and f.get("kickoff_time") is not None]
    todo = [f for f in finished if str(f["id"]) not in store["fixtures"]
            and (str(f["id"]) not in store["unmatched"]
                 or now - ss.to_datetime(f["kickoff_time"]) < UNMATCHED_RETRY)]
    out = {"season": season, "fetched": 0, "calls": 0, "unpaired": 0, "unmatched": 0, "left": 0}
    if not todo:
        return out
    by_id = {str(f["id"]): f for f in todo}
    for i, (start, end) in enumerate(windows(ss.to_datetime(f["kickoff_time"]).date() for f in todo)):
        if out["calls"] >= max_calls:
            break
        if i and pause:
            time.sleep(pause)
        data = fetch({"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": 200})
        out["calls"] += 1
        for fid, m in pair_fixtures([m for m in espn_matches(data) if m["completed"]], list(by_id.values())).items():
            f = by_id.pop(fid)
            rows = goal_rows(m, f["home_team"], f["away_team"])
            if rows is None or (m["home_score"], m["away_score"]) != (f["home_score"], f["away_score"]):
                store["unmatched"][fid] = {"espn": [m["home_score"], m["away_score"]],
                                           "ours": [f["home_score"], f["away_score"]]}
                out["unmatched"] += 1
                continue
            store["fixtures"][fid] = rows
            store["unmatched"].pop(fid, None)
            out["fetched"] += 1
        for fid, f in list(by_id.items()):      # in this window, but ESPN had no such finished match
            if start <= ss.to_datetime(f["kickoff_time"]).date() <= end:
                store["unmatched"][fid] = {"reason": "not on ESPN's scoreboard"}
                by_id.pop(fid)
                out["unpaired"] += 1
        save_store(live_dir, season, store)
    out["left"] = len(by_id)
    return out
