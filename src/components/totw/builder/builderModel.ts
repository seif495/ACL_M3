import { BUILDER_FORMATIONS, BUILDER_LIMITS, type BuildRequest } from '../../../services/teamBuilderApi';

/** What the visitor has asked for. Budget is in tenths of a million (995 = £99.5m), like FPL's prices. */
export interface Constraints {
  budget: number;
  maxPerClub: number;
  /** null = the optimiser picks the formation. */
  formation: string | null;
  locked: string[];
  excluded: string[];
}

export const MIN_BUDGET = BUILDER_LIMITS.minBudget * 10;
export const MAX_BUDGET = BUILDER_LIMITS.maxBudget * 10;

export const DEFAULT_CONSTRAINTS: Constraints = {
  budget: BUILDER_LIMITS.defaultBudget * 10,
  maxPerClub: BUILDER_LIMITS.maxPerClub,
  formation: null,
  locked: [],
  excluded: [],
};

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** Comma-separated names in a URL parameter, without blanks or repeats. */
function names(value: string | null, max: number): string[] {
  const out: string[] = [];
  for (const raw of (value ?? '').split(',')) {
    const name = raw.trim().slice(0, 80);
    if (name && !out.includes(name)) out.push(name);
    if (out.length === max) break;
  }
  return out;
}

/** The constraints in the URL (`?mode=build&budget=995&club=2&formation=4-4-2&lock=a,b&ex=c`); bad values fall back to the defaults. */
export function constraintsFromParams(params: URLSearchParams): Constraints {
  const budget = Math.round(Number(params.get('budget')));
  const club = Math.round(Number(params.get('club')));
  const formation = params.get('formation');
  const locked = names(params.get('lock'), BUILDER_LIMITS.maxLocked);
  return {
    budget: Number.isFinite(budget) && budget > 0 ? clamp(budget, MIN_BUDGET, MAX_BUDGET) : DEFAULT_CONSTRAINTS.budget,
    maxPerClub: Number.isFinite(club) && club > 0 ? clamp(club, 1, BUILDER_LIMITS.maxPerClub) : DEFAULT_CONSTRAINTS.maxPerClub,
    formation: BUILDER_FORMATIONS.some((f) => f === formation) ? formation : null,
    locked,
    excluded: names(params.get('ex'), BUILDER_LIMITS.maxExcluded).filter((n) => !locked.includes(n)),
  };
}

/** URL parameters for the constraints; only what differs from the defaults is written. */
export function constraintsToParams(c: Constraints): Record<string, string> {
  const out: Record<string, string> = { mode: 'build' };
  if (c.budget !== DEFAULT_CONSTRAINTS.budget) out.budget = String(c.budget);
  if (c.maxPerClub !== DEFAULT_CONSTRAINTS.maxPerClub) out.club = String(c.maxPerClub);
  if (c.formation) out.formation = c.formation;
  if (c.locked.length) out.lock = c.locked.join(',');
  if (c.excluded.length) out.ex = c.excluded.join(',');
  return out;
}

/** A stable string for comparing constraints and keying the query. */
export const constraintsKey = (c: Constraints) => JSON.stringify([c.budget, c.maxPerClub, c.formation, c.locked, c.excluded]);

export const isDefaultConstraints = (c: Constraints) => constraintsKey(c) === constraintsKey(DEFAULT_CONSTRAINTS);

export function toRequest(c: Constraints): BuildRequest {
  return {
    budget: c.budget / 10,
    locked: c.locked,
    excluded: c.excluded,
    formation: c.formation,
    max_per_club: c.maxPerClub,
  };
}

/** Locking a player takes him off the excluded list, and the other way round. */
export function withLocked(c: Constraints, name: string): Constraints {
  if (c.locked.includes(name) || c.locked.length >= BUILDER_LIMITS.maxLocked) return c;
  return { ...c, locked: [...c.locked, name], excluded: c.excluded.filter((n) => n !== name) };
}

export function withExcluded(c: Constraints, name: string): Constraints {
  if (c.excluded.includes(name) || c.excluded.length >= BUILDER_LIMITS.maxExcluded) return c;
  return { ...c, excluded: [...c.excluded, name], locked: c.locked.filter((n) => n !== name) };
}

export const withoutLocked = (c: Constraints, name: string): Constraints => ({ ...c, locked: c.locked.filter((n) => n !== name) });
export const withoutExcluded = (c: Constraints, name: string): Constraints => ({ ...c, excluded: c.excluded.filter((n) => n !== name) });

export const fmtMoney = (millions: number | null | undefined) => (millions == null ? '–' : `£${millions.toFixed(1)}m`);

export const fmtOwnership = (pct: number | null | undefined) => (pct == null ? null : `${pct.toFixed(1)}% owned`);

/** "+1.2" / "-0.4" / "0.0" for a points difference. */
export function fmtDelta(delta: number): string {
  const rounded = Math.round(delta * 10) / 10;
  if (rounded === 0) return '0.0';
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded).toFixed(1)}`;
}
