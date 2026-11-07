import { createApiClient } from './http';

const http = createApiClient();

/** One club of the predicted final table (services/team_strength.py `simulate_season`). */
export interface ForecastRow {
  team: string;
  /** 1..20 by expected points. */
  predicted_position: number;
  /** Position in today's table. */
  current_position: number | null;
  played: number;
  points: number;
  goal_difference: number;
  /** Matches still to play. */
  remaining: number;
  expected_points: number;
  expected_position: number;
  /** Chances between 0 and 1. */
  p_title: number;
  /** Places 1-4, the Champions League. */
  p_top4: number;
  /** Places 5-6, the Europa / Conference League. */
  p_europe: number;
  /** Places 1-6, any European place. */
  p_top6: number;
  /** The bottom three. */
  p_relegation: number;
  /** Chance of finishing 1st, 2nd, ... 20th. */
  position_probabilities: number[];
}

export interface TeamRating {
  team: string;
  /** Log scale, 0 = league average; higher is better for attack and defence. */
  attack: number;
  defence: number;
  strength: number;
  /** Expected goal difference against an average side on neutral ground. */
  goal_difference: number;
  /** Rated from the relegated clubs' average, having no previous Premier League season. */
  promoted: boolean;
}

export interface TableForecastResponse {
  season: string;
  generated_at: string;
  simulations: number;
  seed: number;
  played_matches: number;
  remaining_matches: number;
  zones: { champions_league: number; europe: number; relegation: number };
  model: {
    home_advantage: number;
    home_goals: number;
    away_goals: number;
    matches: number;
    half_life: number;
    prior_season: boolean;
  };
  ratings: TeamRating[];
  rows: ForecastRow[];
}

export interface LikelyScore {
  home: number;
  away: number;
  probability: number;
}

/** The model's view of one unplayed match. */
export interface MatchForecastResponse {
  fixture_id: string;
  season: string;
  gameweek: number | null;
  kickoff_time: string | null;
  home_team: string;
  away_team: string;
  home_win: number;
  draw: number;
  away_win: number;
  expected_goals_home: number;
  expected_goals_away: number;
  most_likely_scores: LikelyScore[];
  /** Chance each side keeps a clean sheet. */
  clean_sheet_home: number;
  clean_sheet_away: number;
  matches_in_model: number;
  generated_at: string;
}

export const forecastApi = {
  async getTableForecast(signal?: AbortSignal): Promise<TableForecastResponse> {
    const { data } = await http.get<TableForecastResponse>('/api/table/forecast', { signal });
    return data;
  },

  async getMatchForecast(fixtureId: string, signal?: AbortSignal): Promise<MatchForecastResponse> {
    const { data } = await http.get<MatchForecastResponse>(`/api/matches/${encodeURIComponent(fixtureId)}/forecast`, { signal });
    return data;
  },
};
