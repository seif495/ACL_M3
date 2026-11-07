import { createApiClient } from './http';
import type { PositionCode, TeamOfWeekResponse } from './api';

const http = createApiClient();

export type GameweekState = 'upcoming' | 'live' | 'finished';

/** A player of the gameweek with his identity block; `points` etc. depend on the list. */
export interface GameweekPlayer {
  element: number;
  name: string;
  web_name: string | null;
  team: string | null;
  position: PositionCode | null;
  photo: string | null;
  points?: number;
  minutes?: number;
  bps?: number | null;
  bonus?: number;
  predicted?: number;
  actual?: number;
  gap?: number;
}

export interface GameweekFixture {
  id: string;
  kickoff_time: string | null;
  status: 'scheduled' | 'in_progress' | 'finished';
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  /** Minutes played, from FPL's live feed. */
  minutes?: number | null;
}

export interface GameweekBonusTable {
  fixture_id: string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  status: 'in_progress' | 'finished';
  /** True once FPL has added the bonus to the players' points; until then it is provisional. */
  confirmed: boolean;
  players: GameweekPlayer[];
}

export interface GameweekSquadPlayer {
  name: string;
  web_name: string | null;
  team?: string | null;
  position: PositionCode | null;
  predicted_points?: number | null;
  points: number | null;
  minutes: number | null;
  multiplier: number | null;
  captain?: boolean;
  vice_captain?: boolean;
  subbed_in?: boolean;
  subbed_out?: boolean;
  photo?: string | null;
}

export interface GameweekModelSquad {
  points: number;
  bench_points: number;
  predicted_points: number;
  formation: string;
  fpl_average: number | null;
  fpl_highest: number | null;
  status: string | null;
  live: { fetched_at: string } | null;
  captain: GameweekSquadPlayer | null;
  starters: GameweekSquadPlayer[];
  bench: GameweekSquadPlayer[];
}

export interface GameweekManager {
  status: string | null;
  points: number;
  gross: number | null;
  hit_cost: number | null;
  chip: string | null;
  captain: string | null;
  gw_average: number | null;
  gw_highest: number | null;
  total: number | null;
  live: { fetched_at: string } | null;
  league: { name?: string | null; rank: number | null; size: number | null; percentile: number | null } | null;
  transfers: Array<{ out: string | null; in: string | null }>;
  transfer_count?: number | null;
  players: GameweekSquadPlayer[];
}

export interface GameweekCalls {
  best: GameweekPlayer[];
  worst: GameweekPlayer[];
  missed: GameweekPlayer[];
}

export interface GameweekResponse {
  season: string;
  gameweek: number;
  gameweeks: number[];
  state: GameweekState;
  deadline: string | null;
  fpl_average: number | null;
  fpl_highest: number | null;
  live: { fetched_at: string } | null;
  fixtures: GameweekFixture[];
  top_scorers: GameweekPlayer[];
  bonus: GameweekBonusTable[];
  hauls: Partial<Record<PositionCode, GameweekPlayer[]>>;
  dream_team: TeamOfWeekResponse | null;
  model_squad: GameweekModelSquad | null;
  ml_manager: GameweekManager | null;
  calls: GameweekCalls | null;
  notes: string[];
}

/** One gameweek's page; no number means the current (else next) gameweek. */
export const getGameweek = async (gameweek?: number): Promise<GameweekResponse> =>
  (await http.get<GameweekResponse>(`/api/gameweek/${gameweek ?? 'current'}`)).data;
