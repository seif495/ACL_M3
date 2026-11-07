import { apiService } from './api';
import { matchPath } from '../utils/clubColors';

/** A fixture the global search can open, from the ticker and the current gameweek's list. */
export interface SearchFixture {
  id: string;
  gameweek: number;
  home: string;
  away: string;
  kickoff: string | null;
  to: string;
}

/**
 * Clubs and upcoming/current-gameweek fixtures for the header search, all from endpoints the
 * site already caches: /api/teams, /api/fixtures/ticker?gameweeks=10 and /api/fixtures.
 */
export async function loadSearchIndex(): Promise<{ clubs: string[]; fixtures: SearchFixture[] }> {
  const [teams, ticker, current] = await Promise.allSettled([
    apiService.getTeams(),
    apiService.getFixtureTicker(10),
    apiService.getFixtures(),
  ]);
  const byId = new Map<string, SearchFixture>();
  const clubs = new Set<string>();
  if (teams.status === 'fulfilled') teams.value.teams.forEach((t) => clubs.add(t.team));
  if (current.status === 'fulfilled') {
    for (const f of current.value.fixtures) {
      byId.set(f.id, { id: f.id, gameweek: f.gameweek, home: f.home_team, away: f.away_team, kickoff: f.kickoff_time, to: matchPath(f.id) });
    }
  }
  if (ticker.status === 'fulfilled') {
    for (const club of ticker.value.clubs) {
      clubs.add(club.team);
      for (const gw of club.gameweeks) {
        for (const f of gw.fixtures) {
          // each match is listed under both clubs; keep the home side's copy
          if (!f.home || byId.has(f.fixture_id)) continue;
          byId.set(f.fixture_id, {
            id: f.fixture_id,
            gameweek: gw.gameweek,
            home: club.team,
            away: f.opponent,
            kickoff: f.kickoff_time,
            to: matchPath(f.fixture_id),
          });
        }
      }
    }
  }
  if (teams.status === 'rejected' && ticker.status === 'rejected' && current.status === 'rejected') {
    throw new Error('Search index unavailable');
  }
  return { clubs: [...clubs].sort(), fixtures: [...byId.values()] };
}
