import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import { ClubCrest, PlayerAvatar } from './Identity';
import { getClubAbbreviation } from '../../utils/clubLogos';

// `compact` sizes (for a pitch inside a card, e.g. the assistant's best-XI answer) switch on the
// width of the nearest size container (`[@container(min-width:30rem)]:`) rather than the
// viewport, so five players fit a phone-width card. Class names stay literal for Tailwind.

/** Green striped pitch with markings drawn in CSS/SVG. Children are PitchRow lines, GK first. */
export function Pitch({
  children,
  label,
  className,
  compact = false,
}: {
  children: ReactNode;
  label: string;
  className?: string;
  /** Shorter pitch for cards; use with compact rows and players. */
  compact?: boolean;
}) {
  return (
    <section
      aria-label={label}
      className={cn('relative overflow-hidden rounded-lg border border-line', className)}
      style={{
        background:
          'repeating-linear-gradient(180deg, rgb(var(--pitch-a)) 0 48px, rgb(var(--pitch-b)) 48px 96px)',
      }}
    >
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 100 140"
        preserveAspectRatio="none"
      >
        <g fill="none" stroke="rgb(255 255 255 / 0.28)" strokeWidth="0.5" vectorEffect="non-scaling-stroke">
          <rect x="3" y="3" width="94" height="134" />
          <line x1="3" y1="70" x2="97" y2="70" />
          <circle cx="50" cy="70" r="11" />
          <rect x="24" y="3" width="52" height="20" />
          <rect x="38" y="3" width="24" height="7" />
          <rect x="24" y="117" width="52" height="20" />
          <rect x="38" y="130" width="24" height="7" />
        </g>
      </svg>
      <div
        className={cn(
          'relative flex flex-col justify-around',
          compact
            ? 'min-h-[20rem] gap-2 px-1 py-3 [@container(min-width:30rem)]:min-h-[26rem]'
            : 'min-h-[30rem] gap-3 px-2 py-5 sm:min-h-[38rem] sm:py-8',
        )}
      >
        {children}
      </div>
    </section>
  );
}

export function PitchRow({ children, label, compact = false }: { children: ReactNode; label?: string; compact?: boolean }) {
  return (
    <ul
      aria-label={label}
      className={cn('flex items-start justify-center', compact ? 'gap-0.5 [@container(min-width:30rem)]:gap-2' : 'gap-1 sm:gap-4')}
    >
      {children}
    </ul>
  );
}

export interface PitchOpponent {
  team: string;
  home: boolean;
}

interface PitchPlayerProps {
  name: string;
  /** Name plate text, e.g. FPL's web name; defaults to the last word of `name`. */
  displayName?: string | null;
  /** The player's club: crest on the photo. */
  team?: string | null;
  /** Who they played (or play). Several entries for a double gameweek. */
  opponents?: PitchOpponent[];
  /** Bottom strip, e.g. "12 pts". */
  value?: ReactNode;
  /** Quieter strip under `value`, e.g. the model's prediction next to actual points. */
  detail?: ReactNode;
  /** Small badge on the photo's lower-left corner, e.g. an auto-sub arrow (give icons sr-only text). */
  marker?: ReactNode;
  photo?: string | null;
  captain?: boolean;
  viceCaptain?: boolean;
  /** FPL availability code; doubtful/injured/suspended tint the name plate like FPL does. */
  status?: string | null;
  /** Tooltip/sr text for the status, e.g. the FPL news line. */
  statusNote?: string | null;
  onClick?: () => void;
  /** Accessible name of the button when `onClick` is set (defaults to the player's name). */
  actionLabel?: string;
  /** Smaller player for a compact Pitch. */
  compact?: boolean;
}

const PLATE: Record<string, string> = {
  d: 'bg-warning text-accent-on',
  i: 'bg-danger text-accent-on',
  s: 'bg-danger text-accent-on',
  u: 'bg-fg-subtle text-accent-on',
};
const STATUS_TEXT: Record<string, string> = { d: 'doubtful', i: 'injured', s: 'suspended', u: 'unavailable' };

function Armband({ letter, label, compact = false }: { letter: 'C' | 'V'; label: string; compact?: boolean }) {
  return (
    <span
      className={cn(
        'absolute -left-1 -top-1 z-[1] flex h-4 w-4 items-center justify-center rounded-full bg-black text-[0.6rem] font-extrabold leading-none text-white ring-1 ring-white/70',
        !compact && 'sm:h-5 sm:w-5 sm:text-[0.65rem]',
      )}
    >
      <span aria-hidden="true">{letter}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** One player on the pitch: photo with crest and armband, name plate, opponent and value strips. */
export function PitchPlayer({
  name,
  displayName,
  team,
  opponents,
  value,
  detail,
  marker,
  photo,
  captain,
  viceCaptain,
  status,
  statusNote,
  onClick,
  actionLabel,
  compact = false,
}: PitchPlayerProps) {
  const shortName = displayName || name.split(' ').slice(-1)[0] || name;
  const code = (status ?? '').toLowerCase();
  const plate = PLATE[code] ?? 'bg-fg text-brand';
  const Inner = onClick ? 'button' : 'div';

  return (
    <li className={compact ? 'w-[3.3rem] [@container(min-width:30rem)]:w-[4.6rem]' : 'w-[4.6rem] sm:w-[6.25rem]'}>
      <Inner
        {...(onClick ? { type: 'button' as const, onClick, 'aria-label': actionLabel } : {})}
        className={cn('relative flex w-full flex-col items-center text-center', onClick && 'group rounded-sm')}
        title={statusNote ? `${name}: ${statusNote}` : name}
      >
        <span className="relative">
          <PlayerAvatar
            name={name}
            src={photo}
            size={compact ? 40 : 56}
            className={cn('rounded-b-none bg-transparent', !compact && 'sm:h-[4.5rem] sm:w-[4.5rem]')}
          />
          {captain && <Armband letter="C" label="Captain" compact={compact} />}
          {!captain && viceCaptain && <Armband letter="V" label="Vice-captain" compact={compact} />}
          {team && (
            <span
              className={cn(
                'absolute drop-shadow-[0_1px_2px_rgb(0_0_0/0.6)]',
                compact ? '-right-1.5 -top-1' : '-right-2.5 -top-1.5',
              )}
            >
              <ClubCrest team={team} size={compact ? 18 : 26} />
            </span>
          )}
          {marker && <span className="absolute -left-1 bottom-0 z-[1] flex">{marker}</span>}
        </span>

        <span className={cn('w-full truncate rounded-t-sm px-1 py-0.5 text-micro font-bold', plate)}>
          <span aria-hidden="true">{shortName}</span>
          <span className="sr-only">
            {name}
            {STATUS_TEXT[code] ? ` (${STATUS_TEXT[code]}${statusNote ? `: ${statusNote}` : ''})` : ''}
          </span>
        </span>

        {opponents && opponents.length > 0 && (
          <span className="flex w-full flex-wrap items-center justify-center gap-x-1 bg-brand-2 px-1 py-0.5 text-micro font-semibold text-fg">
            {opponents.map((o, i) => (
              <span key={`${o.team}-${i}`} className="inline-flex items-center gap-0.5">
                <span className="sr-only">{i === 0 ? 'versus ' : 'and '}</span>
                <ClubCrest team={o.team} size={14} className={compact ? 'hidden' : 'hidden sm:inline-flex'} />
                {getClubAbbreviation(o.team) || o.team.slice(0, 3).toUpperCase()}
                <span aria-hidden="true">({o.home ? 'H' : 'A'})</span>
                <span className="sr-only">{o.home ? ' at home' : ' away'}</span>
              </span>
            ))}
          </span>
        )}

        {value != null && (
          <span className={cn('w-full truncate bg-brand px-1 py-0.5 text-micro font-bold text-fg', detail == null && 'rounded-b-sm')}>
            {value}
          </span>
        )}
        {detail != null && (
          <span className="w-full truncate rounded-b-sm bg-surface px-1 py-0.5 text-micro font-semibold text-fg-muted">
            {detail}
          </span>
        )}
      </Inner>
    </li>
  );
}
