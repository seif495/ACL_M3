import axios from 'axios';
import { createApiClient } from './http';
import { apiService, type PlayerProfile, type TickerClub, type TopPerformersResponse } from './api';

// Same base URL rule as services/api.ts (not exported there): production uses relative URLs.
const http = createApiClient(20000);

export const TICKER_WEEKS = 5;
export const PROJECTION_WEEKS = 4;

/** What the model expects from one player over the coming gameweeks (current season only). */
export interface PlayerProjection {
  /** Predicted points in the next gameweek (doubles summed). */
  next: number | null;
  /** Sum over the next gameweeks with projections; null when projections aren't available. */
  total: number | null;
  gameweeks: number[];
}

export function fetchProfile(name: string, season: string | undefined, signal?: AbortSignal): Promise<PlayerProfile> {
  return apiService.getPlayerProfile(name, season, signal);
}

/** The player's row from the top-performers endpoint (`q` is part of a name, so match the exact one). */
export async function fetchProjection(name: string, signal?: AbortSignal): Promise<PlayerProjection | null> {
  try {
    const { data } = await http.get<TopPerformersResponse>('/api/ml/top-performers', {
      params: { q: name, limit: 20, horizon: PROJECTION_WEEKS },
      signal,
    });
    const wanted = name.trim().toLowerCase();
    const row = data.predictions.find((p) => p.player_name.trim().toLowerCase() === wanted);
    if (!row) return null;
    const horizon = data.metadata?.horizon ?? 1;
    return {
      next: Number.isFinite(row.predicted_points) ? row.predicted_points : null,
      total: row.projected_total != null && horizon > 1 ? row.projected_total : null,
      gameweeks: data.metadata?.horizon_gameweeks ?? [],
    };
  } catch (error) {
    if (axios.isCancel(error)) throw error;
    return null; // no predictions file yet: the rows just show a dash
  }
}

export async function fetchTicker(): Promise<TickerClub[]> {
  const res = await apiService.getFixtureTicker(TICKER_WEEKS);
  return res.clubs;
}
