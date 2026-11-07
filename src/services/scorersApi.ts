import { createApiClient } from './http';

// Same base URL logic as services/api.ts (production uses relative URLs)
const client = createApiClient(30000);

export interface FixtureScorer {
  name: string;
  web_name: string | null;
  kind: 'goal' | 'penalty' | 'own_goal';
  minute: number | null;
  extra: number | null;
  /** More than one only when goal minutes are unknown. */
  count: number;
}

export interface FixtureScorers {
  home: FixtureScorer[];
  away: FixtureScorer[];
}

/** GET /api/fixtures/scorers: finished fixtures of one gameweek, keyed by fixture id. */
export async function getGameweekScorers(gameweek: number, signal?: AbortSignal): Promise<Record<string, FixtureScorers>> {
  const { data } = await client.get<Record<string, FixtureScorers>>('/api/fixtures/scorers', { params: { gameweek }, signal });
  return data;
}
