// The daily challenge's record, kept only in this browser (no accounts). Storage can throw
// (private windows, blocked site data), so every access is wrapped.

export const DAILY_KEY = 'pm-trivia-daily';

export interface DailyRecord {
  /** UTC date (YYYY-MM-DD) of the last challenge played. */
  last: string;
  streak: number;
  best: number;
  score: number;
  total: number;
  /** One flag per question: answered correctly. */
  marks: boolean[];
}

export function readDaily(): DailyRecord | null {
  try {
    const raw = window.localStorage.getItem(DAILY_KEY);
    if (!raw) return null;
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== 'object') return null;
    const r = v as Partial<DailyRecord>;
    if (typeof r.last !== 'string' || typeof r.streak !== 'number' || typeof r.best !== 'number') return null;
    const marks = Array.isArray(r.marks) ? r.marks.map(Boolean) : [];
    return {
      last: r.last,
      streak: r.streak,
      best: r.best,
      score: Number(r.score) || 0,
      total: Number(r.total) || marks.length,
      marks,
    };
  } catch {
    return null;
  }
}

/** The UTC day before `date` (YYYY-MM-DD). */
export function previousDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/** The record after playing `date`: the streak continues only from the day before. */
export function nextRecord(prev: DailyRecord | null, date: string, marks: boolean[]): DailyRecord {
  const streak = prev && prev.last === previousDay(date) ? prev.streak + 1 : 1;
  const best = Math.max(prev?.best ?? 0, streak);
  return { last: date, streak, best, score: marks.filter(Boolean).length, total: marks.length, marks };
}

export function saveDaily(record: DailyRecord): void {
  try {
    window.localStorage.setItem(DAILY_KEY, JSON.stringify(record));
  } catch {
    /* not remembered */
  }
}

/** The streak to show: it has lapsed if the last play was before yesterday. */
export function liveStreak(record: DailyRecord | null, today: string): number {
  if (!record) return 0;
  return record.last === today || record.last === previousDay(today) ? record.streak : 0;
}

export function shareText(date: string, record: Pick<DailyRecord, 'score' | 'total' | 'marks'>, url: string): string {
  const squares = record.marks.map((m) => (m ? '\u{1F7E9}' : '\u{1F7E5}')).join('');
  return `Pitch Me daily ${date}: ${record.score}/${record.total} ${squares} ${url}`;
}
