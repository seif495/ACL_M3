import { createApiClient } from './http';

const http = createApiClient();

/** Mean absolute error of one forecast, in points per player; `fpl` is null without FPL's forecast. */
export interface TrackRecordMae {
  model: number | null;
  fpl: number | null;
  /** Players it was scored on. */
  n: number;
}

/** Actual points of each method's top picks (sums over `n` players). */
export interface TrackRecordTop10 {
  model: number;
  fpl: number | null;
  best: number;
  n: number;
}

export interface TrackRecordCaptain {
  element: number;
  name: string;
  predicted: number | null;
  points: number;
  hit: boolean;
}

export interface TrackRecordSquad {
  points: number;
  bench_points: number;
  predicted: number;
  average: number | null;
  highest: number | null;
  formation: string;
}

export interface TrackRecordGameweek {
  gameweek: number;
  deadline: string;
  archived_at: string | null;
  model: string | null;
  /** Where FPL's forecast came from; null when none was saved before the deadline. */
  fpl_source: 'element_snapshots' | 'xp_snapshots' | null;
  mae: TrackRecordMae;
  top10: TrackRecordTop10 | null;
  captain: TrackRecordCaptain | null;
  /** Players with a fixture that gameweek. */
  players: number;
  squad: TrackRecordSquad | null;
}

export interface TrackRecordMissing {
  gameweek: number;
  reason: string;
}

export interface TrackRecordBin {
  label: string;
  low: number;
  high: number | null;
  n: number;
  predicted: number | null;
  actual: number | null;
}

export interface TrackRecordTotals {
  gameweeks: number;
  /** Gameweeks that have FPL's forecast to compare with. */
  compared_gameweeks: number;
  mae: { model: number | null; fpl: number | null; n: number; gameweeks: number };
  top10: { model: number | null; fpl: number | null; best: number | null; gameweeks: number };
  captain: { hits: number; of: number; rate: number | null; mean_points: number | null; threshold: number };
  squad: { mean_points: number | null; mean_average: number | null; beat_average: number; of: number; gameweeks: number };
  beating_fpl: { mae: { wins: number; of: number }; top10: { wins: number; of: number } };
}

export interface TrackRecord {
  season: string;
  gameweeks: TrackRecordGameweek[];
  missing: TrackRecordMissing[];
  totals: TrackRecordTotals | null;
  calibration: TrackRecordBin[];
  /** Set when there is nothing to show (or it couldn't be worked out). */
  note: string | null;
}

/** GET /api/ml/track-record: the model's forecasts against what happened, per finished gameweek. */
export async function getTrackRecord(season?: string, signal?: AbortSignal): Promise<TrackRecord> {
  const { data } = await http.get<TrackRecord>('/api/ml/track-record', { params: season ? { season } : undefined, signal });
  return data;
}
