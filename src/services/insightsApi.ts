import { createApiClient } from './http';

// Mirrors api.ts: production builds use relative URLs (the site and the API share an origin).
const client = createApiClient(30000);

export type Position = 'GK' | 'DEF' | 'MID' | 'FWD';
export type OwnershipTag = 'differential' | 'safe' | 'template';

export interface InsightFixture {
  opponent: string;
  home: boolean;
}

/** One player's row in the captain and differential lists (services/insights.py `player_table`). */
export interface InsightPlayer {
  element: number | null;
  name: string;
  web_name: string;
  team: string | null;
  position: string | null;
  photo?: string | null;
  predicted_points: number;
  points_4gw: number | null;
  projected: { gameweek: number; points: number }[] | null;
  fixtures: InsightFixture[];
  chance_of_playing: number;
  status: string;
  news: string | null;
  selected_by_percent: number | null;
  ownership: OwnershipTag | null;
  price: number | null;
  points_per_m: number | null;
  points_per_m_4gw: number | null;
  role?: 'captain' | 'vice' | null;
}

export interface CaptainsResponse {
  gameweek: number | null;
  options: InsightPlayer[];
  captain: string | null;
  vice: string | null;
  expected_extra_points: number | null;
  note: string | null;
}

export interface DifferentialsResponse {
  gameweek: number | null;
  max_ownership: number;
  position: Position | null;
  has_projections: boolean;
  differentials: InsightPlayer[];
  budget: Partial<Record<Position, InsightPlayer[]>>;
  budget_caps: Record<Position, number>;
  note: string | null;
}

export interface TransferMove {
  name: string;
  web_name: string;
  team: string | null;
  position: string | null;
  photo?: string | null;
  price: number | null;
  selected_by_percent: number | null;
  transfers?: number;
  change?: number;
}

export interface TransfersResponse {
  transfers_in: TransferMove[];
  transfers_out: TransferMove[];
  risers: TransferMove[];
  fallers: TransferMove[];
  note: string | null;
}

export interface SetPieceTaker {
  order: number;
  name: string;
  web_name: string;
  position: string | null;
}

export interface ClubSetPieces {
  team: string;
  penalties: SetPieceTaker[];
  free_kicks: SetPieceTaker[];
  corners: SetPieceTaker[];
}

export interface SetPiecesResponse {
  teams: ClubSetPieces[];
  note: string | null;
}

export const insightsApi = {
  captains: (limit = 10) =>
    client.get<CaptainsResponse>('/api/insights/captains', { params: { limit } }).then((r) => r.data),
  differentials: (params: { position?: Position; max_ownership: number; limit?: number }) =>
    client.get<DifferentialsResponse>('/api/insights/differentials', { params }).then((r) => r.data),
  transfers: (limit = 15) =>
    client.get<TransfersResponse>('/api/insights/transfers', { params: { limit } }).then((r) => r.data),
  setPieces: (team?: string) =>
    client.get<SetPiecesResponse>('/api/insights/set-pieces', { params: team ? { team } : {} }).then((r) => r.data),
};
