import { Link } from 'react-router-dom';
import { cn } from '../../utils/cn';
import { ClubCrest } from './Identity';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { shortName } from '../../utils/playerNames';

/** Full club name from sm up; the three-letter code on phones, where names used to truncate. */
function TeamName({ team }: { team: string }) {
  const code = getClubAbbreviation(team);
  return (
    <span className="truncate text-sm font-semibold text-fg">
      <span className={code ? 'hidden sm:inline' : undefined}>{team}</span>
      {code && (
        <abbr title={team} className="no-underline sm:hidden">
          <span aria-hidden="true">{code}</span>
          <span className="sr-only">{team}</span>
        </abbr>
      )}
    </span>
  );
}

export type FixtureStatus = 'scheduled' | 'live' | 'finished' | 'postponed';

/** One goal under a result: minute only when known; `own_goal` is marked "(og)". */
export interface RowScorer {
  name: string;
  webName?: string | null;
  kind?: 'goal' | 'penalty' | 'own_goal';
  minute?: number | null;
  extra?: number | null;
  count?: number;
}

function scorerText(s: RowScorer): string {
  const name = shortName(s.name, s.webName);
  const minute = s.minute != null ? ` ${s.minute}${s.extra ? `+${s.extra}` : ''}'` : '';
  const tags = `${s.kind === 'penalty' ? ' (pen)' : ''}${s.kind === 'own_goal' ? ' (og)' : ''}`;
  return `${name}${s.count && s.count > 1 ? ` ×${s.count}` : ''}${minute}${tags}`;
}

function ScorerList({ scorers, align }: { scorers: RowScorer[]; align: 'left' | 'right' }) {
  if (!scorers.length) return <div />;
  return (
    <p className={cn('min-w-0 text-micro leading-snug text-fg-subtle', align === 'right' ? 'text-right' : 'text-left')}>
      {scorers.map(scorerText).join(', ')}
    </p>
  );
}

interface FixtureRowProps {
  home: string;
  away: string;
  homeScore?: number | null;
  awayScore?: number | null;
  kickoff?: string | Date | null;
  status?: FixtureStatus;
  /** Include the weekday with the kickoff time (for lists not grouped by day). */
  showDay?: boolean;
  /** Makes the row a link, e.g. to the match page. */
  to?: string;
  /** Goal scorers of a finished match, shown under the score. */
  scorers?: { home: RowScorer[]; away: RowScorer[] } | null;
  className?: string;
}

function formatKickoff(kickoff: string | Date | null | undefined, showDay: boolean): string {
  if (!kickoff) return 'TBC';
  const d = new Date(kickoff);
  if (Number.isNaN(d.getTime())) return 'TBC';
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return showDay ? `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${time}` : time;
}

/** Home crest/name · score or kickoff · away crest/name, centred on the score like FPL's fixtures list. */
export function FixtureRow({ home, away, homeScore, awayScore, kickoff, status = 'scheduled', showDay = false, to, scorers, className }: FixtureRowProps) {
  const hasScore = homeScore != null && awayScore != null && (status === 'finished' || status === 'live');
  const statusLabel = status === 'finished' ? 'FT' : status === 'live' ? 'Live' : status === 'postponed' ? 'Postponed' : null;

  const hasScorers = status === 'finished' && !!scorers && (scorers.home.length > 0 || scorers.away.length > 0);

  const rowClass = cn(
    'grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-x-2 gap-y-1 border-b border-line px-2 py-3 last:border-0 sm:gap-x-4 sm:px-4',
    to && 'transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
    className,
  );
  const content = (
    <>
      <div className="flex min-w-0 items-center justify-end gap-2 text-right">
        <TeamName team={home} />
        <ClubCrest team={home} size={24} />
      </div>

      <div className="flex flex-col items-center">
        {hasScore ? (
          <span
            className={cn(
              'min-w-[4.5rem] rounded-sm px-2 py-1 text-center font-display text-h3 font-bold tabular-nums',
              status === 'live' ? 'bg-accent text-accent-on' : 'bg-brand text-fg',
            )}
          >
            {homeScore}
            <span aria-hidden="true"> – </span>
            <span className="sr-only"> to </span>
            {awayScore}
          </span>
        ) : (
          <span
            className={cn(
              'rounded-sm border border-line px-2 py-1 text-center text-sm font-semibold tabular-nums text-fg-muted',
              showDay ? 'min-w-[7.5rem]' : 'min-w-[4.5rem]',
            )}
          >
            {status === 'postponed' ? 'P–P' : formatKickoff(kickoff, showDay)}
          </span>
        )}
        {statusLabel && (
          <span className={cn('mt-0.5 text-micro font-semibold uppercase', status === 'live' ? 'text-accent' : 'text-fg-subtle')}>
            {statusLabel}
          </span>
        )}
      </div>

      <div className="flex min-w-0 items-center gap-2">
        <ClubCrest team={away} size={24} />
        <TeamName team={away} />
      </div>

      {hasScorers && scorers && (
        <>
          <ScorerList scorers={scorers.home} align="right" />
          <span aria-hidden="true" />
          <ScorerList scorers={scorers.away} align="left" />
        </>
      )}
    </>
  );
  return to ? (
    <Link to={to} className={rowClass}>
      {content}
    </Link>
  ) : (
    <div className={rowClass}>{content}</div>
  );
}
