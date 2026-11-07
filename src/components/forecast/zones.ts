// Season-end zones of the league table, shared by the current and the predicted table.
export type Zone = 'ucl' | 'europe' | 'relegation';

export function zone(position: number, total: number): Zone | null {
  if (position <= 4) return 'ucl';
  if (position <= 6) return 'europe';
  if (position > total - 3) return 'relegation';
  return null;
}

// Literal class names so Tailwind's scanner keeps them.
export const ZONE_BAR = {
  ucl: 'bg-accent',
  europe: 'bg-accent-2',
  relegation: 'bg-danger',
} as const;

export const ZONE_LABEL = {
  ucl: 'Champions League place',
  europe: 'European place',
  relegation: 'Relegation zone',
} as const;
