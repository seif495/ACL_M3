import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ExternalLink, X } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { PlayerDialogContext, type PlayerDialogApi } from '../../hooks/usePlayerDialog';
import { ButtonLink } from '../ui';
import { playerPath } from '../../utils/playerPath';
import { PlayerProfileView, type PlayerHint } from './PlayerProfileView';
import { WatchButton } from './WatchButton';

const FOCUSABLE = 'a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

function PlayerDialog({ player, season, onClose }: { player: PlayerHint; season?: string; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const headingId = useId();

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLElement>('[data-dialog-close]')?.focus();
    // The page behind doesn't scroll while the overlay is open.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
      opener?.focus?.();
    };
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab' || !panelRef.current) return;
    // Keep Tab inside the dialog.
    const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/65 px-2 py-4 backdrop-blur-[2px] sm:px-4 sm:py-[5vh]"
      // A click on the backdrop (outside the panel) closes it.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onKeyDown={onKeyDown}
        className="relative w-full max-w-4xl rounded-lg border border-line bg-bg shadow-pop"
      >
        <button
          type="button"
          data-dialog-close
          onClick={onClose}
          aria-label="Close player stats"
          className="absolute right-2 top-2 z-20 flex h-9 w-9 items-center justify-center rounded-full border border-line bg-surface text-fg-muted hover:border-line-strong hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
        <div className="p-3 pt-4 sm:p-5">
          <PlayerProfileView
            player={player}
            initialSeason={season}
            compact
            headingId={headingId}
            toolbar={
              <div className="flex flex-wrap items-center gap-2">
                <WatchButton name={player.name} />
                <ButtonLink
                  to={`${playerPath(player.name)}${season && season !== 'All seasons' ? `?season=${encodeURIComponent(season)}` : ''}`}
                  variant="ghost"
                  size="sm"
                  icon={<ExternalLink className="h-4 w-4" aria-hidden="true" />}
                >
                  Open full page
                </ButtonLink>
              </div>
            }
          />
        </div>
      </div>
    </div>
  );
}

/** Lets any page open a player's stats in an overlay (`usePlayerDialog().openPlayer`). Closes
 * on the X, Escape, a click outside the panel, or a route change. */
export function PlayerDialogProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<{ player: PlayerHint; season?: string } | null>(null);
  const location = useLocation();
  const close = useCallback(() => setOpen(null), []);

  useEffect(() => {
    setOpen(null);
  }, [location.pathname]);

  const api = useMemo<PlayerDialogApi>(() => ({ openPlayer: (player, season) => setOpen({ player, season }) }), []);

  return (
    <PlayerDialogContext.Provider value={api}>
      {children}
      {open && <PlayerDialog key={open.player.name} player={open.player} season={open.season} onClose={close} />}
    </PlayerDialogContext.Provider>
  );
}
