import type {
  MlManagerChip,
  MlManagerChipState,
  MlManagerGameweek,
  MlManagerResultPlayer,
  MlManagerSquadPlayer,
  PositionCode,
} from '../../services/api';

// Pure helpers for the ML manager tab (prices arrive in tenths of £1m, as the game stores them).

/** Tenths of £1m as money: 1000 → "£100.0m". */
export function money(tenths: number): string {
  const text = (Math.abs(tenths) / 10).toFixed(1);
  return `${tenths < 0 ? '−' : ''}£${text}m`;
}

export const CHIP_NAMES: Record<MlManagerChip, string> = {
  wildcard: 'Wildcard',
  freehit: 'Free Hit',
  bboost: 'Bench Boost',
  '3xc': 'Triple Captain',
};

/** FPL's web name when known, else the last word of the name (as on the pitch's name plates). */
export const shortName = (p: { name: string; web_name: string | null }) => p.web_name || p.name.split(' ').slice(-1)[0] || p.name;

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The captain's multiplier this gameweek: ×3 with Triple Captain. */
export const captainMultiplier = (gw: MlManagerGameweek) => (gw.chip === '3xc' ? 3 : 2);

/** Unlimited free transfers this gameweek (and the banked ones are kept). */
export const unlimitedTransfers = (gw: MlManagerGameweek) => gw.chip === 'wildcard' || gw.chip === 'freehit';

/** Free transfers this gameweek's moves use up (none on a wildcard or free hit). */
export function freeTransfersUsed(gw: MlManagerGameweek): number {
  return unlimitedTransfers(gw) ? 0 : Math.min(gw.transfers_made, gw.free_transfers);
}

/** Free transfers for the gameweek after this one: unused ones roll over, one more each week, capped. */
export function nextFreeTransfers(gw: MlManagerGameweek, max: number): number {
  return Math.max(1, Math.min(gw.free_transfers - freeTransfersUsed(gw) + 1, max));
}

/** Projected points as the pitch shows them: the captain's multiplied, bench players at face value. */
export function shownProjection(p: MlManagerSquadPlayer, gw: MlManagerGameweek): number | null {
  if (p.predicted_points == null) return null;
  return p.predicted_points * (p.captain ? captainMultiplier(gw) : 1);
}

/** Bench slot as the game lays it out: the substitute goalkeeper, then outfield subs 1–3. */
export function benchSlot(p: MlManagerSquadPlayer): { short: string; long: string } {
  if (p.bench_order == null) return { short: '', long: 'Substitute' };
  if (p.bench_order === 1) return { short: 'GK', long: 'Substitute goalkeeper' };
  const n = p.bench_order - 1;
  return { short: String(n), long: `Substitute ${n}` };
}

/** "3-4-3" from a starting XI. */
export function formationOf(players: Array<{ position: PositionCode }>): string {
  const count = (pos: PositionCode) => players.filter((p) => p.position === pos).length;
  return `${count('DEF')}-${count('MID')}-${count('FWD')}`;
}

export interface Lineup {
  /** The XI on the pitch: as picked, or after automatic substitutions once scored. */
  xi: MlManagerSquadPlayer[];
  /** The bench in priority order (a replaced starter takes his replacement's slot). */
  bench: MlManagerSquadPlayer[];
  /** The bench as picked, slot by slot (for the slot labels). */
  benchSlots: MlManagerSquadPlayer[];
  /** Came off the bench → the starter he replaced. */
  cameOnFor: Map<number, MlManagerSquadPlayer>;
  /** Starter who didn't play → the substitute who replaced him. */
  replacedBy: Map<number, MlManagerSquadPlayer>;
  /** Scored points per element. */
  results: Map<number, MlManagerResultPlayer>;
}

export function lineupOf(gw: MlManagerGameweek): Lineup {
  const starters = gw.squad.filter((p) => p.role === 'starter');
  const bench = gw.squad
    .filter((p) => p.role !== 'starter')
    .sort((a, b) => (a.bench_order ?? 99) - (b.bench_order ?? 99));
  const result = gw.status === 'scored' ? gw.result : null;
  // Auto-subs name the players; match on the full name, else the web name.
  const byName = new Map<string, MlManagerSquadPlayer>();
  for (const p of gw.squad) {
    if (p.web_name && !byName.has(p.web_name)) byName.set(p.web_name, p);
  }
  for (const p of gw.squad) byName.set(p.name, p);
  const cameOnFor = new Map<number, MlManagerSquadPlayer>();
  const replacedBy = new Map<number, MlManagerSquadPlayer>();
  // With Bench Boost every player counts, so nobody is substituted.
  if (result && gw.chip !== 'bboost') {
    for (const [outName, inName] of result.auto_subs ?? []) {
      const out = byName.get(outName);
      const on = byName.get(inName);
      if (out && on) {
        replacedBy.set(out.element, on);
        cameOnFor.set(on.element, out);
      }
    }
  }
  return {
    xi: starters.map((p) => replacedBy.get(p.element) ?? p),
    bench: bench.map((p) => cameOnFor.get(p.element) ?? p),
    benchSlots: bench,
    cameOnFor,
    replacedBy,
    results: new Map((result?.players ?? []).map((r) => [r.element, r])),
  };
}

/**
 * This gameweek's opponents for the pitch strip. They should be `{team, home}` objects (as in the
 * model's saved picks); "Arsenal (H)" strings are parsed too. Anything without home/away (e.g. a
 * bare "Chelsea & Everton") is left out rather than shown with a guessed venue.
 */
export function opponentsOf(p: MlManagerSquadPlayer): Array<{ team: string; home: boolean }> | undefined {
  const raw: unknown = p.opponents;
  if (!Array.isArray(raw)) return undefined;
  const out = raw.flatMap((o: unknown) => {
    if (o && typeof o === 'object') {
      const { team, home } = o as { team?: unknown; home?: unknown };
      return typeof team === 'string' && typeof home === 'boolean' ? [{ team, home }] : [];
    }
    const m = typeof o === 'string' ? /^(.+?)\s*\(([HA])\)$/i.exec(o.trim()) : null;
    return m ? [{ team: m[1], home: m[2].toUpperCase() === 'H' }] : [];
  });
  return out.length ? out : undefined;
}

/** FPL's team value: the squad at current prices, without the bank (tenths). */
export function teamValueOf(gw: MlManagerGameweek): number {
  return gw.team_value ?? gw.squad.reduce((sum, p) => sum + p.now_cost, 0);
}

export type ChipPhase = 'planned' | 'played' | 'used' | 'available' | 'upcoming' | 'expired';

export interface ChipStatus {
  phase: ChipPhase;
  text: string;
}

/** Where a chip stands for the gameweek on show. */
export function chipStatus(c: MlManagerChipState, gw: MlManagerGameweek): ChipStatus {
  const [first, last] = c.window;
  const inWindow = gw.gameweek >= first && gw.gameweek <= last;
  const usedIn = c.used_gameweek ?? (inWindow && gw.chip === c.chip ? gw.gameweek : null);
  // A chip in the next gameweek's plan reads as planned from any gameweek on show (e.g. the live one).
  if (usedIn != null && (c.planned || (gw.status === 'planned' && usedIn === gw.gameweek))) {
    return { phase: 'planned', text: `Planned for GW${usedIn}` };
  }
  if (usedIn === gw.gameweek) return { phase: 'played', text: `Played in GW${usedIn}` };
  if (usedIn != null) return { phase: 'used', text: `Used in GW${usedIn}` };
  if (gw.gameweek < first) return { phase: 'upcoming', text: `From GW${first}` };
  if (gw.gameweek > last || !c.available) return { phase: 'expired', text: `Expired after GW${last}` };
  return { phase: 'available', text: `Available until GW${last}` };
}

/** "top 4.1%" from a percentile of 95.9 (the share of managers below). */
export function topShare(percentile: number | null | undefined): string | null {
  if (percentile == null || !Number.isFinite(percentile)) return null;
  const top = Math.max(0, 100 - percentile);
  if (top < 0.1) return 'top 0.1%';
  return `top ${top < 10 ? top.toFixed(1) : Math.round(top)}%`;
}

/** Rank with thousands separators, e.g. "41,230". */
export const rankText = (rank: number) => rank.toLocaleString();
