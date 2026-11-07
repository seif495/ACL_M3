import { Ban, Lock, Unlock, User, X } from 'lucide-react';
import { Button, Card, ClubCrest, PositionBadge } from '../../ui';
import { usePlayerDialog } from '../../../hooks/usePlayerDialog';
import { displayName } from '../../../utils/playerNames';
import type { BuilderPlayer } from '../../../services/teamBuilderApi';
import { fmtOwnership } from './builderModel';

interface Props {
  player: BuilderPlayer;
  locked: boolean;
  /** The lock list is full (15), so he can't be locked. */
  lockDisabled: boolean;
  onLock: () => void;
  onUnlock: () => void;
  onExclude: () => void;
  onClose: () => void;
  /** The draft differs from the shown squad: offer Rebuild here too. */
  dirty: boolean;
  loading: boolean;
  onRebuild: () => void;
}

/** What to do with the player just tapped: lock him in, drop him, or open his profile. */
export function BuilderPlayerActions({ player, locked, lockDisabled, onLock, onUnlock, onExclude, onClose, dirty, loading, onRebuild }: Props) {
  const dialog = usePlayerDialog();
  const owned = fmtOwnership(player.ownership);
  return (
    <Card className="space-y-3" role="region" aria-label={`Actions for ${player.name}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate font-display text-h3 font-bold text-fg">{displayName(player.name, player.web_name)}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-data text-fg-muted">
            <PositionBadge position={player.position} />
            <ClubCrest team={player.team} size={14} label="full" />
            <span className="tabular-nums">{player.cost != null ? `£${player.cost.toFixed(1)}m` : ''}</span>
            <span className="tabular-nums">{player.points.toFixed(1)} pts</span>
            {owned && <span className="tabular-nums">{owned}</span>}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close player actions"
          className="-m-1 inline-flex h-8 w-8 items-center justify-center rounded-sm text-fg-subtle hover:text-fg"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {locked ? (
          <Button size="sm" variant="secondary" onClick={onUnlock} icon={<Unlock className="h-4 w-4" aria-hidden="true" />}>
            Unlock
          </Button>
        ) : (
          <Button size="sm" variant="secondary" disabled={lockDisabled} onClick={onLock} icon={<Lock className="h-4 w-4" aria-hidden="true" />}>
            Lock
          </Button>
        )}
        <Button size="sm" variant="secondary" onClick={onExclude} icon={<Ban className="h-4 w-4" aria-hidden="true" />}>
          Exclude
        </Button>
        {dialog && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              dialog.openPlayer({
                name: player.name,
                team: player.team,
                position: player.position,
                photo: player.photo,
                status: player.status,
                news: player.news,
              })
            }
            icon={<User className="h-4 w-4" aria-hidden="true" />}
          >
            View profile
          </Button>
        )}
        {dirty && (
          <Button size="sm" onClick={onRebuild} loading={loading} className="ml-auto">
            Rebuild
          </Button>
        )}
      </div>
    </Card>
  );
}
