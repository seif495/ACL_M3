import type { ReactNode } from 'react';
import { ClubCrest, PlayerAvatar, PositionBadge } from '../ui';
import { PlayerNameButton } from '../players/PlayerNameButton';
import { displayName } from '../../utils/playerNames';
import { cn } from '../../utils/cn';

/** One row of a list: photo, name (opens the stats overlay), club and position, a value on the right. */
export function PlayerLine({
  name,
  webName,
  team,
  position,
  photo,
  detail,
  value,
  valueLabel,
  className,
}: {
  name: string;
  webName?: string | null;
  team?: string | null;
  position?: string | null;
  photo?: string | null;
  detail?: ReactNode;
  value: ReactNode;
  valueLabel?: ReactNode;
  className?: string;
}) {
  return (
    <li className={cn('flex items-center gap-3 border-b border-line py-2 last:border-b-0', className)}>
      <PlayerAvatar name={name} src={photo} size={36} />
      <div className="min-w-0 flex-1">
        <PlayerNameButton
          player={{ name, team, position, photo }}
          label={displayName(name, webName)}
          className="text-body font-semibold text-fg"
        />
        <p className="flex items-center gap-1.5 text-micro text-fg-subtle">
          {team && <ClubCrest team={team} size={14} />}
          {position && <PositionBadge position={position} />}
          {detail && <span className="truncate">{detail}</span>}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-display text-h3 font-bold tabular-nums text-fg">{value}</p>
        {valueLabel && <p className="text-micro text-fg-subtle">{valueLabel}</p>}
      </div>
    </li>
  );
}
