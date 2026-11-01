"""
Goal minutes and penalties for match pages, from API-Football (api-sports.io; API_FOOTBALL_KEY,
free plan: 100 calls a day). FPL records who scored but not when or how, so this adds each goal's
minute, whether it was a penalty or an own goal, and missed penalties.

  fixtures?league=39&season=YYYY   one call: the season's matches with API-Football ids
  fixtures?ids=a-b-...             up to 20 matches per call, each with its events

pipeline/goal_events.py runs this from the daily job for finished matches without events yet,
within a call budget, and saves $LIVE_DATA_DIR/goal_events/<season>.json; match pages only read
that file (services/matches.py), so a page never calls the API. Matches are paired with ours by
the two clubs (graph names, `graph_team`) and a kickoff within two days. Free plans may not cover
every season: the API then answers with an `errors` entry, which ApiFootballError carries.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from services import season_service as ss

API_URL = "https://v3.football.api-sports.io"
PREMIER_LEAGUE = 39
IDS_PER_CALL = 20
TIMEOUT = 15.0
STORE_DIR = "goal_events"

# API-Football's club names -> the graph's, where they differ
TEAM_ALIASES = {
    "manchester city": "Man City", "manchester united": "Man Utd", "tottenham": "Spurs",
    "tottenham hotspur": "Spurs", "nottingham forest": "Nott'm Forest", "wolves": "Wolves",
    "wolverhampton wanderers": "Wolves", "newcastle": "Newcastle", "newcastle united": "Newcastle",
    "brighton": "Brighton", "brighton & hove albion": "Brighton", "leeds": "Leeds", "leeds united": "Leeds",
    "leicester": "Leicester", "leicester city": "Leicester", "ipswich": "Ipswich", "ipswich town": "Ipswich",
    "sheffield utd": "Sheffield Utd", "sheffield united": "Sheffield Utd", "west ham": "West Ham",
    "west ham united": "West Ham", "luton": "Luton", "luton town": "Luton", "coventry": "Coventry City",
    "hull city": "Hull City", "hull": "Hull City", "west brom": "West Brom", "afc bournemouth": "Bournemouth",
}

Fetch = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class ApiFootballError(Exception):
    """The API answered with `errors` (a bad key, a season the plan doesn't cover, the day's limit)."""


def api_key() -> Optional[str]:
    return os.getenv("API_FOOTBALL_KEY", "").strip() or None


def season_year(season: str) -> int:
    """"2026-27" -> 2026 (API-Football names a season by its first year)."""
    return int(season[:4])


def http_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    key = api_key()
    if not key:
        raise ApiFootballError("API_FOOTBALL_KEY isn't set")
    resp = httpx.get(f"{API_URL}/{path}", params=params, timeout=TIMEOUT, headers={"x-apisports-key": key})
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and len(errors)):
        raise ApiFootballError(json.dumps(errors))
    return data


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"\bfc\b|\bafc\b", "", text)).strip()


def graph_team(api_name: str, teams: Iterable[str]) -> Optional[str]:
    """API-Football's club name as the graph names it (aliases, then an exact or contained match)."""
    n = _norm(api_name)
    if n in TEAM_ALIASES:
        return TEAM_ALIASES[n]
    teams = list(teams)
    for t in teams:
        if _norm(t) == n:
            return t
    for t in teams:
        tn = _norm(t)
        if tn and (tn in n or n in tn):
            return t
    return None


def api_fixtures(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for r in payload.get("response") or []:
        fx, teams = r.get("fixture") or {}, r.get("teams") or {}
        out.append({"api_id": fx.get("id"), "date": fx.get("date"), "status": (fx.get("status") or {}).get("short"),
                    "home": (teams.get("home") or {}).get("name"), "away": (teams.get("away") or {}).get("name"),
                    "events": r.get("events")})
    return out


def pair_fixtures(api: List[Dict[str, Any]], ours: List[Dict[str, Any]]) -> Dict[str, int]:
    """{our fixture id: API-Football id}: the same two clubs, home and away, within two days."""
    teams = sorted({f["home_team"] for f in ours} | {f["away_team"] for f in ours})
    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for f in ours:
        by_pair.setdefault((f["home_team"], f["away_team"]), []).append(f)
    out: Dict[str, int] = {}
    for a in api:
        home, away = graph_team(a["home"] or "", teams), graph_team(a["away"] or "", teams)
        when = ss.to_datetime(a["date"])
        for f in by_pair.get((home, away), []):
            k = f.get("kickoff_time")
            if when is None or k is None or abs(ss.to_datetime(k) - when) <= timedelta(days=2):
                out[str(f["id"])] = int(a["api_id"])
                break
    return out


KINDS = {"normal goal": "goal", "penalty": "penalty", "own goal": "own_goal", "missed penalty": "missed_penalty"}


def goal_rows(events: Iterable[Dict[str, Any]], teams: Iterable[str]) -> List[Dict[str, Any]]:
    """Goals, own goals and missed penalties from a fixture's events, in time order. `team` is
    the graph name of the player's own club (for an own goal, the side that conceded it)."""
    teams = list(teams)
    out = []
    for e in events or []:
        detail = str(e.get("detail") or "").lower()
        kind = KINDS.get(detail)
        if e.get("type") != "Goal" and kind != "missed_penalty":
            continue
        if kind is None:
            continue
        t = e.get("time") or {}
        out.append({"minute": t.get("elapsed"), "extra": t.get("extra"), "kind": kind,
                    "player": (e.get("player") or {}).get("name"), "assist": (e.get("assist") or {}).get("name"),
                    "team": graph_team((e.get("team") or {}).get("name") or "", teams)})
    out.sort(key=lambda g: ((g["minute"] or 0), (g["extra"] or 0)))
    return out


# ------------------------------------------------------------------ store

def store_path(live_dir: str, season: str) -> str:
    return os.path.join(live_dir, STORE_DIR, f"{season}.json")


def load_store(live_dir: str, season: str) -> Dict[str, Any]:
    try:
        with open(store_path(live_dir, season), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"season": season, "fixtures": {}, "api_ids": {}}


def save_store(live_dir: str, season: str, store: Dict[str, Any]) -> None:
    path = store_path(live_dir, season)
    os.makedirs(os.path.dirname(path), exist_ok=True)
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


def update(ours: List[Dict[str, Any]], season: str, live_dir: str, *, fetch: Fetch = http_get,
           max_calls: int = 30) -> Dict[str, Any]:
    """Fetch events for `ours`' finished matches that have none saved, within max_calls. One
    call lists the season (only when a finished match has no API id yet), then 20 matches a call."""
    store = load_store(live_dir, season)
    finished = [f for f in ours if f.get("home_score") is not None and f.get("status") != "scheduled"]
    todo = [f for f in finished if str(f["id"]) not in store["fixtures"]]
    calls = 0
    if not todo:
        return {"season": season, "fetched": 0, "missing": 0, "calls": 0}
    if any(str(f["id"]) not in store["api_ids"] for f in todo):
        data = fetch("fixtures", {"league": PREMIER_LEAGUE, "season": season_year(season)})
        calls += 1
        store["api_ids"].update(pair_fixtures(api_fixtures(data), ours))
        save_store(live_dir, season, store)
    teams = sorted({f["home_team"] for f in ours} | {f["away_team"] for f in ours})
    ids = [(str(f["id"]), store["api_ids"][str(f["id"])]) for f in todo if str(f["id"]) in store["api_ids"]]
    by_api = {api_id: ours_id for ours_id, api_id in ids}
    fetched = 0
    for i in range(0, len(ids), IDS_PER_CALL):
        if calls >= max_calls:
            break
        chunk = [api_id for _, api_id in ids[i:i + IDS_PER_CALL]]
        data = fetch("fixtures", {"ids": "-".join(str(x) for x in chunk)})
        calls += 1
        for a in api_fixtures(data):
            if a["events"] is None or a["api_id"] not in by_api:
                continue
            store["fixtures"][by_api[a["api_id"]]] = goal_rows(a["events"], teams)
            fetched += 1
        save_store(live_dir, season, store)
    return {"season": season, "fetched": fetched, "calls": calls,
            "missing": len(todo) - fetched, "unpaired": len(todo) - len(ids)}
