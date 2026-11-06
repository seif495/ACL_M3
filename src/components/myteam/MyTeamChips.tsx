import { Card, Chip } from '../ui';
import type { MyTeamChip } from '../../services/api';
import { cn } from '../../utils/cn';

function statusOf(c: MyTeamChip, nextGameweek: number): { text: string; tone: 'accent' | 'info' | 'neutral' } {
  if (c.used_gameweek != null) return { text: `Used in GW${c.used_gameweek}`, tone: 'neutral' };
  if (c.active) return { text: `Active in GW${nextGameweek}`, tone: 'info' };
  if (c.available) return { text: 'Available', tone: 'accent' };
  return { text: nextGameweek > c.window[1] ? 'Expired' : 'Not yet available', tone: 'neutral' };
}

/** One of each chip per half of the season: used, available or expired. */
export function MyTeamChips({ chips, nextGameweek }: { chips: MyTeamChip[]; nextGameweek: number }) {
  const halves = new Map<number, MyTeamChip[]>();
  for (const c of chips) halves.set(c.window[1], [...(halves.get(c.window[1]) ?? []), c]);
  const ordered = [...halves.entries()].sort((a, b) => a[0] - b[0]);
  const names = ordered.length === 2 ? ['First half', 'Second half'] : ordered.map(() => '');

  return (
    <Card className="[container-type:inline-size]">
      <div className="space-y-5">
        {ordered.map(([end, items], gi) => {
          const start = Math.min(...items.map((c) => c.window[0]));
          const label = `${names[gi] ? `${names[gi]} · ` : ''}GW${start}–${end}`;
          return (
            <div key={end}>
              <p className="mb-2 text-micro font-semibold uppercase text-fg-subtle">{label}</p>
              <ul aria-label={`Chips, ${label}`} className="grid grid-cols-2 gap-2 [@container(min-width:34rem)]:grid-cols-4">
                {items.map((c) => {
                  const status = statusOf(c, nextGameweek);
                  return (
                    <li
                      key={`${c.chip}-${end}`}
                      className={cn(
                        'rounded-md border p-3',
                        status.tone === 'info' ? 'border-accent-2/60 bg-accent-2/10' : 'border-line bg-surface-2/40',
                      )}
                    >
                      <p className={cn('text-sm font-semibold', status.tone === 'neutral' ? 'text-fg-muted' : 'text-fg')}>{c.label}</p>
                      <Chip tone={status.tone} className="mt-1.5">
                        {status.text}
                      </Chip>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
