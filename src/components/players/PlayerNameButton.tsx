import { Link } from 'react-router-dom';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { cn } from '../../utils/cn';
import { playerPath } from '../../utils/playerPath';
import type { PlayerHint } from './PlayerProfileView';

/** A player's name that opens his stats overlay (plain text outside the app shell). It is a link to
 * his page too, so a new-tab or copy-link click gets /players/<slug>; a plain tap opens the overlay. */
export function PlayerNameButton({
  player,
  className,
  season,
  label,
}: {
  player: PlayerHint;
  className?: string;
  season?: string;
  /** Text to show instead of the full name (a short name); the overlay opens by the full name. */
  label?: string;
}) {
  const dialog = usePlayerDialog();
  if (!dialog) return <span className={className} title={label && label !== player.name ? player.name : undefined}>{label || player.name}</span>;
  return (
    <Link
      to={playerPath(player.name)}
      onClick={(e) => {
        // Ctrl/Cmd/Shift/middle clicks keep their browser meaning (new tab or window)
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        dialog.openPlayer(player, season);
      }}
      title={`${player.name}: show stats`}
      className={cn('inline-block max-w-full truncate text-left hover:text-accent hover:underline focus-visible:text-accent', className)}
    >
      {label || player.name}
    </Link>
  );
}
