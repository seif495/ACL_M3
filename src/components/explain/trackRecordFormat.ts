import type { TrackRecordMissing } from '../../services/trackRecordApi';

/** Consecutive gameweeks with the same reason become one range: [{ label: "1–7", reason }]. */
export function groupMissing(missing: TrackRecordMissing[]): Array<{ label: string; reason: string; count: number }> {
  const sorted = [...missing].sort((a, b) => a.gameweek - b.gameweek);
  const groups: Array<{ from: number; to: number; reason: string; count: number }> = [];
  for (const m of sorted) {
    const last = groups[groups.length - 1];
    if (last && last.reason === m.reason && m.gameweek === last.to + 1) {
      last.to = m.gameweek;
      last.count += 1;
    } else groups.push({ from: m.gameweek, to: m.gameweek, reason: m.reason, count: 1 });
  }
  return groups.map((g) => ({ label: g.from === g.to ? `${g.from}` : `${g.from}–${g.to}`, reason: g.reason, count: g.count }));
}
