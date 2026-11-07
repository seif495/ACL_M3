import { Star } from 'lucide-react';
import { useWatchlist } from '../../lib/personal';
import { Button } from '../ui';

/** Adds or removes a player from the browser-only watchlist (shown on the home page). */
export function WatchButton({ name }: { name: string }) {
  const { has, toggle, full } = useWatchlist();
  const watching = has(name);
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-pressed={watching}
      disabled={!watching && full}
      title={!watching && full ? 'Your watchlist is full (30 players)' : 'Kept in this browser only'}
      onClick={() => toggle(name)}
      icon={<Star className={watching ? 'h-4 w-4 fill-current text-accent' : 'h-4 w-4'} aria-hidden="true" />}
    >
      {watching ? 'Watching' : 'Watch'}
    </Button>
  );
}
