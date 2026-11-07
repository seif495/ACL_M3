import { createContext, useContext, useId, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  Clock,
  Download,
  Flame,
  Home,
  Loader2,
  Medal,
  Share2,
  Shield,
  Star,
  Target,
  TrendingDown,
  TrendingUp,
  Trophy,
  UserRound,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import type {
  CardHighlight,
  CardIcon,
  CardMatch,
  CardOpponent,
  CardPlayer,
  CardTone,
  CardValueFormat,
} from '../../../services/api';
import { Card, Chip, ClubCrest, FdrBadge, Logo, PositionBadge, StatusBadge } from '../../ui';
import { usePlayerAvatars } from '../../../hooks/usePlayerAvatars';
import { getClubAbbreviation } from '../../../utils/clubLogos';
import { cn } from '../../../utils/cn';
import { useExport } from '../../share/exportContext';

export const CARD_ICONS: Record<CardIcon, LucideIcon> = {
  trophy: Trophy,
  flame: Flame,
  medal: Medal,
  star: Star,
  zap: Zap,
  clock: Clock,
  home: Home,
  'trend-up': TrendingUp,
  'trend-down': TrendingDown,
  shield: Shield,
  target: Target,
  calendar: CalendarDays,
  alert: AlertTriangle,
};

const TONES: Record<CardTone, string> = {
  accent: 'bg-accent/15 text-accent',
  info: 'bg-info/15 text-info',
  warning: 'bg-warning/15 text-warning',
  danger: 'bg-danger/15 text-danger',
  neutral: 'bg-surface-2 text-fg-muted',
};

const compact = new Intl.NumberFormat('en-GB', { notation: 'compact', maximumFractionDigits: 1 });

export function formatValue(value: number | null | undefined, format: CardValueFormat = 'int'): string {
  if (value == null || Number.isNaN(value)) return '–';
  switch (format) {
    case 'dec1':
      return value.toFixed(1);
    case 'dec2':
      return value.toFixed(2);
    case 'money':
      return `£${value.toFixed(1)}m`;
    case 'compact':
      return compact.format(value);
    default:
      return Math.round(value).toLocaleString('en-GB');
  }
}

export function formatKickoff(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function teamCode(team: string | null | undefined): string {
  return team ? getClubAbbreviation(team) || team.slice(0, 3).toUpperCase() : '';
}

/** "2026-27" with a non-breaking hyphen, so a season never splits across lines. */
export function keepSeasons(text: string): string {
  return text.replace(/\b(\d{4})-(\d{2})\b/g, '$1‑$2');
}

export function ordinal(n: number): string {
  const rem100 = n % 100;
  const suffix = rem100 >= 11 && rem100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${suffix}`;
}

// --- Photos -----------------------------------------------------------------------------

/** Off on /design, where the sample players are made up and must not trigger lookups. */
const PhotoLookupContext = createContext(true);

export function PhotoLookupProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  return <PhotoLookupContext.Provider value={enabled}>{children}</PhotoLookupContext.Provider>;
}

// --- Sharing a card as an image ------------------------------------------------------------

export { ExportProvider, useExport } from '../../share/exportContext';
export type { ShareState } from '../../share/exportContext';

/** Other hosts' photos via our image proxy, so the image's canvas isn't tainted by them. */
function sameOrigin(url: string | null | undefined): string | null | undefined {
  if (!url || url.startsWith('/') || url.startsWith('data:')) return url;
  try {
    if (new URL(url).origin === window.location.origin) return url;
  } catch {
    return url;
  }
  return `/api/images/proxy?url=${encodeURIComponent(url)}`;
}

/**
 * Photo for each card player: the one the backend sent (cache or FPL code, no network
 * call there), else a lookup shared with the rest of the app (`['player-avatar', name]`).
 */
export function useCardPhotos(players: CardPlayer[]): (player: CardPlayer) => string | null | undefined {
  const enabled = useContext(PhotoLookupContext);
  const { exporting } = useExport();
  const missing = enabled ? players.filter((p) => !p.photo).map((p) => p.name) : [];
  const looked = usePlayerAvatars(missing);
  return (player) => {
    const url = player.photo ?? looked[player.name];
    return exporting ? sameOrigin(url) : url;
  };
}

// --- Asking about a player --------------------------------------------------------------

interface AskContextValue {
  ask?: (question: string) => void;
  disabled?: boolean;
}

/** Set by AnswerCards in the chat; on /design there is no one to ask. */
const AskContext = createContext<AskContextValue>({});

export function AskProvider({ ask, disabled, children }: AskContextValue & { children: ReactNode }) {
  return <AskContext.Provider value={{ ask, disabled }}>{children}</AskContext.Provider>;
}

export function useAsk(): AskContextValue {
  return useContext(AskContext);
}

// --- Links ------------------------------------------------------------------------------

/** Router state that opens a player's profile on /players (Back returns here). */
export function playerProfileState(player: CardPlayer, season: string | null) {
  return {
    player: {
      player_name: player.name,
      team_name: player.team ?? undefined,
      position: player.position ?? undefined,
      avatar: player.photo ?? undefined,
      status: player.status ?? undefined,
      chance_of_playing_next_round: player.chance,
      news: player.news ?? undefined,
    },
    season: season ?? 'All seasons',
    returnTo: 'history' as const,
  };
}

/** The question a tapped player sends: their stats in the card's season (all seasons when null). */
export function playerQuestion(name: string, season: string | null): string {
  return season ? `${name} stats in ${season}` : `${name} stats`;
}

/** The question a tapped player on a prediction card sends. */
export function predictionQuestion(name: string): string {
  return `Predicted points for ${name} next gameweek`;
}

interface PlayerActionProps {
  player: CardPlayer;
  /** Season of the profile link and of the default question; null = all seasons. */
  season: string | null;
  /** The question to ask instead of the player's stats. */
  question?: string;
  /** 'row': the profile icon sits after the content; 'stack': in a top corner, for narrow columns. */
  layout?: 'row' | 'stack';
  /** Corner of the profile icon in the stack layout. */
  iconSide?: 'left' | 'right';
  /** Classes for the tappable area (its layout). */
  className?: string;
  children: ReactNode;
}

/**
 * A player on a card. In the chat, tapping asks the assistant about them and a small icon opens
 * their profile; where there is no one to ask (/design) the whole thing links to the profile.
 */
export function PlayerAction({ player, season, question, layout = 'row', iconSide = 'right', className, children }: PlayerActionProps) {
  const { ask, disabled } = useAsk();
  const profile = playerProfileState(player, season);
  if (!ask) {
    return (
      <Link to="/players" state={profile} className={cn('group', className)}>
        {children}
      </Link>
    );
  }
  const q = question ?? playerQuestion(player.name, season);
  const stack = layout === 'stack';
  return (
    <div className={cn('min-w-0', stack ? 'relative' : 'flex items-center gap-1')}>
      <button
        type="button"
        onClick={() => ask(q)}
        disabled={disabled}
        title={`Ask: ${q}`}
        className={cn('group min-w-0 text-left disabled:cursor-not-allowed', stack ? 'w-full' : 'flex-1', className)}
      >
        <span className="sr-only">Ask about </span>
        {children}
      </button>
      <Link
        to="/players"
        state={profile}
        data-export-hide
        aria-label={`Open ${player.name}'s profile`}
        title="Open profile"
        className={cn(
          'inline-flex shrink-0 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-surface-2 hover:text-accent',
          stack ? cn('absolute top-0 h-6 w-6', iconSide === 'right' ? 'right-0' : 'left-0') : 'h-8 w-8',
        )}
      >
        <UserRound className={stack ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" />
      </Link>
    </div>
  );
}

export function CardLink({ to, state, children, label }: { to: string; state?: unknown; children: ReactNode; label?: string }) {
  return (
    <Link
      to={to}
      state={state}
      aria-label={label}
      className="inline-flex items-center gap-1 rounded-sm px-2 py-1.5 text-data font-semibold text-accent transition-colors hover:bg-surface-2 hover:underline"
    >
      {children}
      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
    </Link>
  );
}

// --- Frame ------------------------------------------------------------------------------

interface CardFrameProps {
  icon?: CardIcon;
  eyebrow: ReactNode;
  title?: ReactNode;
  /** Right side of the header, e.g. a season chip or a big number. */
  aside?: ReactNode;
  /** Replaces the icon + title block (player and team cards lead with identity); it
      must give `titleId` to its heading. */
  identity?: (titleId: string) => ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}

/**
 * The shell every answer card shares: a header, stacked sections, and a links footer.
 * It is a size container, so grids inside switch columns on the card's own width
 * (`[@container(min-width:…)]:`) rather than the viewport's: the chat column narrows
 * when the details panel opens.
 */
export function CardFrame({ icon, eyebrow, title, aside, identity, children, footer, className }: CardFrameProps) {
  const titleId = useId();
  const Icon = icon ? CARD_ICONS[icon] : null;
  return (
    <Card padded={false} className={cn('overflow-hidden [container-type:inline-size]', className)}>
      <article aria-labelledby={titleId}>
        <header className="flex items-start gap-3 px-4 pb-3 pt-4 sm:px-5">
          {identity ? (
            <div className="min-w-0 flex-1">
              <p className="mb-2 text-micro font-semibold uppercase text-accent">{eyebrow}</p>
              {identity(titleId)}
            </div>
          ) : (
            <>
              {Icon && (
                <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand text-accent ring-1 ring-brand-2">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-micro font-semibold uppercase text-accent">{eyebrow}</p>
                <h3 id={titleId} className="font-display text-h3 font-bold text-fg">
                  {title}
                </h3>
              </div>
            </>
          )}
          {aside && <div className="shrink-0 text-right">{aside}</div>}
        </header>
        {children}
        <CardFooter>{footer}</CardFooter>
      </article>
    </Card>
  );
}

/** Links and the share button; in the image, the Pitch Me mark and the site's address instead. */
function CardFooter({ children }: { children?: ReactNode }) {
  const { exporting, share, shareState = 'idle', shareMode = 'save' } = useExport();
  if (exporting) {
    return (
      <footer className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5 sm:px-5">
        <Logo size={16} />
        <span className="truncate text-micro text-fg-subtle">{window.location.host} · not affiliated with the Premier League</span>
      </footer>
    );
  }
  if (!children && !share) return null;
  const ShareIcon = shareState === 'working' ? Loader2 : shareMode === 'share' ? Share2 : Download;
  const label = {
    idle: shareMode === 'share' ? 'Share as image' : 'Save as image',
    working: 'Creating image…',
    ready: 'Image ready: tap to share',
    failed: "Couldn't create the image",
  }[shareState];
  return (
    <footer className="flex flex-wrap items-center gap-x-1 border-t border-line px-2 py-1 sm:px-3">
      {children}
      {share && (
        <button
          type="button"
          onClick={share}
          disabled={shareState === 'working'}
          className={cn(
            'ml-auto inline-flex items-center gap-1.5 rounded-sm px-2 py-1.5 text-data font-semibold transition-colors hover:bg-surface-2 hover:text-fg disabled:cursor-wait',
            shareState === 'ready' ? 'text-accent' : 'text-fg-muted',
          )}
        >
          <ShareIcon className={cn('h-3.5 w-3.5', shareState === 'working' && 'animate-spin')} aria-hidden="true" />
          {label}
        </button>
      )}
      <span className="sr-only" role="status">
        {shareState === 'ready' || shareState === 'failed' ? label : ''}
      </span>
    </footer>
  );
}

export function Section({ title, children, className, aside }: { title?: ReactNode; children: ReactNode; className?: string; aside?: ReactNode }) {
  return (
    <section className={cn('border-t border-line px-4 py-3 sm:px-5', className)}>
      {(title || aside) && (
        <div className="mb-2 flex items-baseline justify-between gap-2">
          {title && <h4 className="text-micro font-semibold uppercase text-fg-subtle">{title}</h4>}
          {aside && <div className="text-micro text-fg-subtle">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

// --- Highlights ---------------------------------------------------------------------------

/** "Notable stats": streaks, ranks and records the backend computed from the data. */
export function Highlights({ items, className }: { items: CardHighlight[]; className?: string }) {
  if (!items?.length) return null;
  return (
    <Section title="Notable" className={className}>
      <ul className="grid gap-2 [@container(min-width:30rem)]:grid-cols-2">
        {items.map((h, i) => {
          const Icon = CARD_ICONS[h.icon] ?? Star;
          return (
            <li key={`${h.label}-${i}`} className="flex min-w-0 items-start gap-2.5 rounded-md border border-line bg-bg/40 px-3 py-2">
              <span className={cn('mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md', TONES[h.tone] ?? TONES.neutral)}>
                <Icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="text-micro font-semibold uppercase text-fg-subtle">{h.label}</p>
                <p className="break-words font-display text-body font-bold text-fg">{keepSeasons(h.value)}</p>
                {h.detail && <p className="break-words text-data text-fg-muted">{keepSeasons(h.detail)}</p>}
              </div>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

// --- Small pieces ---------------------------------------------------------------------------

/** "IPS (H)" coloured by fixture difficulty when FPL rates it, neutral otherwise. */
export function OpponentChip({ opponent, className }: { opponent: CardOpponent; className?: string }) {
  const label = `${teamCode(opponent.team)} (${opponent.home ? 'H' : 'A'})`;
  const spoken = `${opponent.home ? 'home to' : 'away at'} ${opponent.team}${
    opponent.difficulty != null ? `, difficulty ${opponent.difficulty} of 5` : ''
  }`;
  const content = (
    <>
      <span aria-hidden="true">{label}</span>
      <span className="sr-only">{spoken}</span>
    </>
  );
  return opponent.difficulty != null ? (
    <FdrBadge difficulty={opponent.difficulty} className={className}>
      {content}
    </FdrBadge>
  ) : (
    <Chip tone="neutral" title={opponent.team} className={className}>
      {content}
    </Chip>
  );
}

/** Upcoming matches as difficulty chips, each labelled with its gameweek. */
export function FixtureStrip({ fixtures, className }: { fixtures: CardOpponent[]; className?: string }) {
  if (!fixtures.length) return null;
  return (
    <ol className={cn('flex flex-wrap gap-2', className)}>
      {fixtures.map((f, i) => (
        <li key={`${f.team}-${f.gameweek}-${i}`} className="flex flex-col items-start gap-0.5">
          <span className="text-micro text-fg-subtle">{f.gameweek ? `GW ${f.gameweek}` : formatKickoff(f.kickoff_time) ?? 'TBC'}</span>
          <OpponentChip opponent={f} />
        </li>
      ))}
    </ol>
  );
}

interface TrendLineProps {
  values: number[];
  /** One description per value: hover title and screen-reader text. */
  labels: string[];
  caption: string;
  height?: number;
  className?: string;
}

/**
 * Single-series sparkline: a 2px line over a 10% wash with an end dot (drawn as HTML so it
 * stays round when the SVG stretches). Each point has a hover title; the caption lists all.
 */
export function TrendLine({ values, labels, caption, height = 40, className }: TrendLineProps) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const x = (i: number) => (i / (values.length - 1)) * 100;
  // 10% headroom top and bottom; a flat series sits in the middle
  const y = (v: number) => (span === 0 ? 50 : 90 - ((v - min) / span) * 80);
  const line = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const last = values.length - 1;
  return (
    <figure className={cn('min-w-0', className)}>
      <figcaption className="sr-only">
        {caption}: {labels.join('; ')}.
      </figcaption>
      <div aria-hidden="true" className="relative" style={{ height }}>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
          <polygon points={`0,100 ${line} 100,100`} fill="rgb(var(--accent) / 0.1)" />
          <polyline
            points={line}
            fill="none"
            stroke="rgb(var(--accent))"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <span
          className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-surface"
          style={{ left: `${x(last)}%`, top: `${y(values[last])}%` }}
        />
        <div className="absolute inset-0 flex">
          {labels.map((label, i) => (
            <span key={i} title={label} className="h-full flex-1" />
          ))}
        </div>
      </div>
    </figure>
  );
}

/** Crest, club, position and availability under a player's name. */
export function PlayerMeta({ player, showTeamName = false, className }: { player: CardPlayer; showTeamName?: boolean; className?: string }) {
  return (
    <span className={cn('flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-micro text-fg-subtle', className)}>
      {player.team && <ClubCrest team={player.team} size={14} label={showTeamName ? 'full' : 'short'} className="max-w-full" />}
      <PositionBadge position={player.position} className="w-auto px-1" />
      <StatusBadge status={player.status} chance={player.chance} news={player.news} />
    </span>
  );
}

// Brand ink on mint/pink clears 4.5:1 for the small bold letter (white on pink would not)
const RESULT_CLASSES: Record<string, string> = {
  W: 'bg-accent text-accent-on',
  D: 'bg-surface-2 text-fg',
  L: 'bg-danger text-accent-on',
};
const RESULT_WORDS: Record<string, string> = { W: 'Won', D: 'Drew', L: 'Lost' };

/** W / D / L pill: the letter carries the meaning, colour only repeats it. */
export function ResultPill({ result, title, className }: { result: string | null; title?: string; className?: string }) {
  if (!result) return null;
  return (
    <span
      title={title}
      className={cn('inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-micro font-bold', RESULT_CLASSES[result], className)}
    >
      <span aria-hidden="true">{result}</span>
      <span className="sr-only">{RESULT_WORDS[result] ?? result}</span>
    </span>
  );
}

/** Thin bar under a list value, relative to the list's largest value (decorative). */
export function ValueBar({ value, max, tone = 'accent', className }: { value: number | null; max: number; tone?: 'accent' | 'accent-2'; className?: string }) {
  const pct = value != null && max > 0 ? Math.max(2, Math.min(100, (Math.abs(value) / max) * 100)) : 0;
  return (
    <span aria-hidden="true" className={cn('block h-1 w-full overflow-hidden rounded-full bg-surface-2', className)}>
      <span className={cn('block h-full rounded-full', tone === 'accent' ? 'bg-accent' : 'bg-accent-2')} style={{ width: `${pct}%` }} />
    </span>
  );
}

// --- Mini bar chart -------------------------------------------------------------------------

export interface MiniBar {
  key: string;
  value: number;
  /** Direct label above the bar ('' for none). */
  display: string;
  /** Axis label under the bar. */
  label: string;
  /** Full description: tooltip and screen-reader text. */
  title: string;
  /** Did-not-play and similar: drawn as a stub. */
  muted?: boolean;
  /** Emphasised bar (the others recede). */
  focus?: boolean;
}

interface MiniBarsProps {
  bars: MiniBar[];
  /** Screen-reader summary, followed by every bar's title. */
  caption: string;
  tone?: 'accent' | 'accent-2';
  /** Plot height in px, labels excluded. */
  height?: number;
  /** The scale's top is at least this, so small values don't fill the chart. */
  floor?: number;
  className?: string;
}

/**
 * Small column chart (form, points by season). Bars are at most 24px wide with a 2px gap,
 * rounded at the data end and square at the baseline; every value is labelled directly,
 * so the hover title only adds detail.
 */
export function MiniBars({ bars, caption, tone = 'accent', height = 64, floor = 10, className }: MiniBarsProps) {
  const { exporting } = useExport();
  const reduceMotion = useReducedMotion() || exporting; // an image needs the bars drawn in full
  const top = Math.max(floor, ...bars.map((b) => b.value));
  const anyFocus = bars.some((b) => b.focus);
  const fill = tone === 'accent' ? 'bg-accent' : 'bg-accent-2';
  const recede = tone === 'accent' ? 'bg-accent/35' : 'bg-accent-2/35';

  return (
    <figure className={cn('min-w-0', className)}>
      <figcaption className="sr-only">
        {caption}: {bars.map((b) => b.title).join('; ')}.
      </figcaption>
      <div aria-hidden="true">
        <div className="flex items-end gap-[2px] border-b border-line" style={{ height: height + 16 }}>
          {bars.map((b, i) => {
            const px = b.muted || b.value <= 0 ? 2 : Math.max(3, Math.round((b.value / top) * height));
            return (
              <div key={b.key} title={b.title} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end">
                <span className={cn('mb-0.5 text-micro', b.focus ? 'font-bold text-fg' : 'text-fg-muted')}>{b.display}</span>
                <motion.span
                  className={cn(
                    'block w-full max-w-6 origin-bottom rounded-t-[4px]',
                    b.muted || b.value <= 0 ? 'bg-fg-subtle/40' : anyFocus && !b.focus ? recede : fill,
                  )}
                  style={{ height: px }}
                  initial={reduceMotion ? false : { scaleY: 0 }}
                  animate={{ scaleY: 1 }}
                  transition={{ duration: 0.35, delay: reduceMotion ? 0 : i * 0.03, ease: 'easeOut' }}
                />
              </div>
            );
          })}
        </div>
        <div className="mt-1 flex gap-[2px]">
          {bars.map((b) => (
            <span key={b.key} className={cn('min-w-0 flex-1 truncate text-center text-micro', b.focus ? 'font-bold text-fg' : 'text-fg-subtle')}>
              {b.label}
            </span>
          ))}
        </div>
      </div>
    </figure>
  );
}

/** A player's recent matches as MiniBars: points on top, opponent below. */
export function FormBars({
  matches,
  focusGameweek,
  tone = 'accent',
  caption,
  height,
}: {
  matches: CardMatch[];
  focusGameweek?: number | null;
  tone?: 'accent' | 'accent-2';
  caption: string;
  height?: number;
}) {
  const bars: MiniBar[] = matches.map((m, i) => {
    const played = m.minutes > 0;
    const venue = m.home == null ? '' : m.home ? ' (H)' : ' (A)';
    const versus = m.opponent ? ` v ${m.opponent}${venue}` : '';
    const score = m.score ? `, ${m.result === 'W' ? 'won' : m.result === 'L' ? 'lost' : 'drew'} ${m.score}` : '';
    const returns = [m.goals ? `${m.goals} goal${m.goals > 1 ? 's' : ''}` : '', m.assists ? `${m.assists} assist${m.assists > 1 ? 's' : ''}` : '']
      .filter(Boolean)
      .join(', ');
    return {
      key: `${m.season}-${m.gameweek}-${i}`,
      value: m.points,
      display: played ? String(m.points) : '–',
      label: m.opponent ? teamCode(m.opponent) : `GW${m.gameweek ?? ''}`,
      title: `GW ${m.gameweek ?? '?'} ${m.season}${versus}${score}: ${
        played ? `${m.points} pts in ${m.minutes} min${returns ? ` (${returns})` : ''}` : 'did not play'
      }`,
      muted: !played,
      focus: focusGameweek != null && m.gameweek === focusGameweek,
    };
  });
  return <MiniBars bars={bars} caption={caption} tone={tone} height={height} />;
}
