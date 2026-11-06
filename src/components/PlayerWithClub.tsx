import type { ReactNode } from 'react';
import { ClubCrest, PlayerAvatar } from './ui';
import { getClubAbbreviation } from '../utils/clubLogos';
import { cn } from '../utils/cn';

interface PlayerWithClubProps {
  playerName: string;
  /** Text to show instead of playerName (a short name); playerName stays the photo's name and tooltip. */
  displayName?: string;
  teamName?: string | null;
  showAbbreviation?: boolean;
  logoSize?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  className?: string;
  nameClassName?: string;
  clubClassName?: string;
  /**
   * Player photo. Passing the prop (even `null` or `undefined` while it loads) switches to
   * the two-line layout: photo, then name over crest + club; omit it for the inline layout.
   */
  avatar?: string | null;
  avatarSize?: number;
  /** Extra content on the club line of the two-line layout, e.g. a position or status badge. */
  meta?: ReactNode;
}

const CREST_PX: Record<NonNullable<PlayerWithClubProps['logoSize']>, number> = {
  sm: 16,
  md: 20,
  lg: 24,
  xl: 40,
  '2xl': 56,
};

/** A player name with their club crest (and optionally photo + abbreviation). */
export function PlayerWithClub(props: PlayerWithClubProps) {
  const {
    playerName,
    teamName,
    showAbbreviation = true,
    logoSize = 'md',
    className,
    nameClassName,
    clubClassName,
    avatar,
    avatarSize = 36,
    meta,
    displayName,
  } = props;
  const shown = displayName || playerName;
  const fullTitle = shown !== playerName ? playerName : undefined;
  const abbreviation = getClubAbbreviation(teamName) || (teamName ?? '').slice(0, 3).toUpperCase();
  const crestPx = CREST_PX[logoSize];
  const twoLine = 'avatar' in props || meta != null;

  if (twoLine) {
    return (
      <span className={cn('flex min-w-0 items-center gap-2.5', className)}>
        <PlayerAvatar name={playerName} src={avatar} size={avatarSize} />
        <span className="min-w-0">
          <span className={cn('block truncate font-semibold text-fg', nameClassName)} title={fullTitle}>{shown}</span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-data text-fg-subtle">
            {teamName && <ClubCrest team={teamName} size={Math.min(crestPx, 16)} />}
            {teamName && (
              <span className={cn('truncate', clubClassName)}>
                {showAbbreviation ? abbreviation : teamName}
              </span>
            )}
            {meta}
          </span>
        </span>
      </span>
    );
  }

  return (
    <span className={cn('flex min-w-0 items-center gap-2', className)}>
      {teamName && <ClubCrest team={teamName} size={crestPx} />}
      <span className={cn('truncate', nameClassName)} title={fullTitle}>{shown}</span>
      {showAbbreviation && teamName && (
        <span className={cn('shrink-0 text-micro font-semibold text-fg-subtle', clubClassName)}>{abbreviation}</span>
      )}
    </span>
  );
}
