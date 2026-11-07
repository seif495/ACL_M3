import { cn } from '../../utils/cn';
import { ordinal, spokenChance } from './format';
import { ZONE_BAR, zone } from './zones';

/**
 * A club's chance of finishing in each of the 20 places, as 20 thin columns (1st on the left).
 * Heights are relative to the club's likeliest place, coloured by the table's zones. The
 * label says the likeliest place; the full numbers are in the page's "Show the numbers" table.
 */
export function PositionBar({ probs, className }: { probs: number[]; className?: string }) {
  const total = probs.length;
  const peak = Math.max(...probs, 0);
  const best = probs.indexOf(peak);
  const label = peak > 0 ? `Most likely finish ${ordinal(best + 1)}, ${spokenChance(peak)}` : 'No finish data';
  return (
    <span role="img" aria-label={label} className={cn('flex h-7 w-28 items-end gap-px', className)}>
      {probs.map((p, i) => {
        const z = zone(i + 1, total);
        return (
          <span key={i} aria-hidden="true" className="flex h-full flex-1 items-end">
            <span
              className={cn('w-full rounded-t-[1px]', z ? ZONE_BAR[z] : 'bg-fg-subtle', i === best ? 'opacity-100' : 'opacity-60')}
              style={{ height: p > 0 && peak > 0 ? `${Math.max(8, (p / peak) * 100)}%` : '0%' }}
            />
          </span>
        );
      })}
    </span>
  );
}
