import { Link } from 'react-router-dom';
import type { TeamMatch } from '../../services/api';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { matchPath, teamPath } from '../../utils/clubColors';
import { cn } from '../../utils/cn';
import { ClubCrest, FdrBadge } from '../ui';

const RESULT_STYLE = {
  W: 'bg-accent text-accent-on',
  D: 'bg-fg-subtle text-accent-on',
  L: 'bg-danger text-accent-on',
} as const;
const RESULT_WORD = { W: 'Win', D: 'Draw', L: 'Loss' } as const;

export interface FormItem {
  fixture_id: string;
  result: 'W' | 'D' | 'L';
  opponent: string;
  home: boolean;
  score: string | null;
}

/** W/D/L chips, oldest first; each opens its match page. */
export function FormChips({ items, size = 'md', className }: { items: FormItem[]; size?: 'sm' | 'md'; className?: string }) {
  if (!items.length) return <span className="text-fg-subtle">–</span>;
  return (
    <span className={cn('inline-flex gap-1', className)}>
      <span className="sr-only">Last {items.length}, oldest first: </span>
      {items.map((m) => {
        const label = `${RESULT_WORD[m.result]} ${m.score ?? ''} ${m.home ? 'v' : 'at'} ${m.opponent}`;
        return (
          <Link
            key={m.fixture_id}
            to={matchPath(m.fixture_id)}
            title={label}
            aria-label={label}
            onClick={(e) => e.stopPropagation()}
            className={cn(
              'inline-flex items-center justify-center rounded-full font-bold transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              size === 'sm' ? 'h-5 w-5 text-micro' : 'h-7 w-7 text-data',
              RESULT_STYLE[m.result],
            )}
          >
            <span aria-hidden="true">{m.result}</span>
          </Link>
        );
      })}
    </span>
  );
}

function when(iso: string | null): string {
  if (!iso) return 'TBC';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'TBC';
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

function time(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** A club's matches as rows: date, opponent, score (or kickoff and difficulty), xG; each opens the match. */
export function MatchList({ matches, empty }: { matches: TeamMatch[]; empty: string }) {
  if (!matches.length) return <p className="px-4 py-6 text-data text-fg-subtle">{empty}</p>;
  return (
    <ul>
      {matches.map((m) => (
        <li key={m.fixture_id} className="border-b border-line last:border-0">
          <Link
            to={matchPath(m.fixture_id)}
            className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent sm:grid-cols-[6rem_minmax(0,1fr)_auto_auto]"
          >
            <span className="text-micro text-fg-subtle">
              <span className="block font-semibold text-fg-muted">GW{m.gameweek ?? '–'}</span>
              {when(m.kickoff_time)}
            </span>
            <span className="flex min-w-0 items-center gap-2">
              <span className="w-5 shrink-0 text-center text-micro font-semibold text-fg-subtle">{m.home ? 'H' : 'A'}</span>
              <ClubCrest team={m.opponent} size={22} />
              <span className="truncate text-data font-semibold text-fg">{m.opponent}</span>
            </span>
            <span className="hidden text-right text-micro tabular-nums text-fg-subtle sm:block">
              {m.xg_for != null && m.xg_against != null ? `xG ${m.xg_for.toFixed(1)}–${m.xg_against.toFixed(1)}` : ''}
            </span>
            {m.result ? (
              <span className="flex items-center gap-2">
                <span className="font-display text-body font-bold tabular-nums text-fg">{m.score}</span>
                <span
                  className={cn('inline-flex h-6 w-6 items-center justify-center rounded-full text-micro font-bold', RESULT_STYLE[m.result])}
                  title={RESULT_WORD[m.result]}
                >
                  {m.result}
                </span>
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <span className="text-data tabular-nums text-fg-muted">{time(m.kickoff_time) ?? ''}</span>
                {m.difficulty != null && <FdrBadge difficulty={m.difficulty} />}
              </span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** A club's name and crest linking to its page. */
export function TeamLink({ team, short = false, className }: { team: string; short?: boolean; className?: string }) {
  return (
    <Link
      to={teamPath(team)}
      onClick={(e) => e.stopPropagation()}
      className={cn('inline-flex min-w-0 items-center gap-2 font-semibold hover:text-accent hover:underline', className)}
    >
      <ClubCrest team={team} size={22} />
      <span className={cn('truncate', short && 'hidden sm:inline')}>{team}</span>
      {short && <span className="sm:hidden">{getClubAbbreviation(team) || team}</span>}
    </Link>
  );
}
