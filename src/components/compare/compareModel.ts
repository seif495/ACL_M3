import type { PlayerProfile, TickerClub } from '../../services/api';
import type { PlayerProjection } from '../../services/compareApi';

export const MAX_PLAYERS = 4;
export const MIN_PLAYERS = 2;

/** One colour per slot, as design-token names (mint, cyan, yellow, magenta). */
const COLOR_VARS = ['accent', 'accent-2', 'warning', 'glow-3'] as const;
export const playerColor = (i: number) => `rgb(var(--${COLOR_VARS[i % COLOR_VARS.length]}))`;
export const playerTint = (i: number, alpha: number) => `rgb(var(--${COLOR_VARS[i % COLOR_VARS.length]}) / ${alpha})`;

export interface ComparedPlayer {
  profile: PlayerProfile;
  projection: PlayerProjection | null;
  /** The player's club in the fixture ticker (current season only). */
  ticker: TickerClub | null;
}

export type Section = 'season' | 'rates' | 'outlook';

export interface MetricRow {
  key: string;
  label: string;
  section: Section;
  values: (number | null)[];
  format: (v: number) => string;
  /** 'high' or 'low' is the better end; null rows are shown but never highlighted. */
  better: 'high' | 'low' | null;
}

const fixed = (d: number) => (v: number) => v.toFixed(d);
const whole = (v: number) => Math.round(v).toLocaleString();

const total = (p: PlayerProfile, k: string): number | null => {
  const v = p.totals?.[k];
  return v == null || !Number.isFinite(Number(v)) ? null : Number(v);
};
const rate = (p: PlayerProfile, k: string): number | null => {
  const v = p.per90?.[k];
  return v == null || !Number.isFinite(Number(v)) ? null : Number(v);
};

export function averageFdr(club: TickerClub | null): number | null {
  if (!club) return null;
  const ds = club.gameweeks.flatMap((g) => g.fixtures.map((f) => f.difficulty)).filter((d): d is number => d != null);
  return ds.length ? ds.reduce((a, b) => a + b, 0) / ds.length : null;
}

export function buildRows(players: ComparedPlayer[], current: boolean): MetricRow[] {
  const col = (fn: (p: ComparedPlayer) => number | null) => players.map(fn);
  const rows: MetricRow[] = [
    { key: 'pts', label: 'Total points', section: 'season', values: col((p) => total(p.profile, 'total_points')), format: whole, better: 'high' },
    { key: 'ppg', label: 'Points per game', section: 'season', values: col((p) => p.profile.points_per_game), format: fixed(1), better: 'high' },
    { key: 'goals', label: 'Goals', section: 'season', values: col((p) => total(p.profile, 'goals')), format: whole, better: 'high' },
    { key: 'assists', label: 'Assists', section: 'season', values: col((p) => total(p.profile, 'assists')), format: whole, better: 'high' },
    { key: 'cs', label: 'Clean sheets', section: 'season', values: col((p) => total(p.profile, 'clean_sheets')), format: whole, better: 'high' },
    { key: 'bonus', label: 'Bonus points', section: 'season', values: col((p) => total(p.profile, 'bonus')), format: whole, better: 'high' },
    { key: 'min', label: 'Minutes', section: 'season', values: col((p) => total(p.profile, 'minutes')), format: whole, better: 'high' },
    { key: 'apps', label: 'Appearances', section: 'season', values: col((p) => p.profile.appearances), format: whole, better: 'high' },
    {
      key: 'ict',
      label: 'ICT index per appearance',
      section: 'season',
      values: col((p) => {
        const ict = total(p.profile, 'ict_index');
        return ict != null && p.profile.appearances ? ict / p.profile.appearances : null;
      }),
      format: fixed(1),
      better: 'high',
    },
    { key: 'xg90', label: 'xG per 90', section: 'rates', values: col((p) => rate(p.profile, 'xg')), format: fixed(2), better: 'high' },
    { key: 'xa90', label: 'xA per 90', section: 'rates', values: col((p) => rate(p.profile, 'xa')), format: fixed(2), better: 'high' },
    { key: 'xgi90', label: 'xGI per 90', section: 'rates', values: col((p) => rate(p.profile, 'xgi')), format: fixed(2), better: 'high' },
    { key: 'dc90', label: 'Defensive contributions per 90', section: 'rates', values: col((p) => rate(p.profile, 'defensive_contribution')), format: fixed(1), better: 'high' },
    { key: 'pts90', label: 'Points per 90', section: 'rates', values: col((p) => rate(p.profile, 'total_points')), format: fixed(2), better: 'high' },
  ];
  if (current) {
    rows.push(
      { key: 'next', label: 'Predicted next gameweek', section: 'outlook', values: col((p) => p.profile.predicted_points ?? p.projection?.next ?? null), format: fixed(1), better: 'high' },
      { key: 'proj', label: 'Projected next 4 gameweeks', section: 'outlook', values: col((p) => p.projection?.total ?? null), format: fixed(1), better: 'high' },
      { key: 'price', label: 'Price now', section: 'outlook', values: col((p) => p.profile.price), format: (v) => `£${v.toFixed(1)}m`, better: null },
      {
        key: 'ppm',
        label: 'Points per £m',
        section: 'outlook',
        values: col((p) => {
          const pts = total(p.profile, 'total_points');
          return pts != null && p.profile.price ? pts / p.profile.price : null;
        }),
        format: fixed(1),
        better: 'high',
      },
      { key: 'fdr', label: 'Average FDR, next 5', section: 'outlook', values: col((p) => averageFdr(p.ticker)), format: fixed(1), better: 'low' },
    );
  }
  // Drop rows nobody has a value for (xG before 2023-24, say)
  return rows.filter((r) => r.values.some((v) => v != null));
}

/** Indices holding the best value of the row; empty when all are equal or the row isn't scored. */
export function bestIndices(row: MetricRow): number[] {
  if (!row.better) return [];
  const present = row.values.filter((v): v is number => v != null);
  if (present.length < 2) return [];
  const best = row.better === 'high' ? Math.max(...present) : Math.min(...present);
  if (present.every((v) => v === best)) return [];
  // compare at displayed precision so 5.01 vs 5.0 shown as equal ties
  const shown = (v: number) => row.format(v);
  return row.values.flatMap((v, i) => (v != null && shown(v) === shown(best) ? [i] : []));
}

/** How many scored rows each player leads. */
export function leadCounts(rows: MetricRow[], n: number): number[] {
  const wins = Array<number>(n).fill(0);
  for (const r of rows) for (const i of bestIndices(r)) wins[i] += 1;
  return wins;
}

export const nameOf = (p: ComparedPlayer) => p.profile.name;
