import { useId, useState, type ReactNode } from 'react';
import { ArrowLeftRight, ArrowUp, CalendarClock, CheckCircle2, Lock, Radio, type LucideIcon } from 'lucide-react';
import {
  handleApiError,
  isNotFoundError,
  type MlManagerGameweek,
  type MlManagerHistoryRow,
  type MlManagerView,
} from '../../services/api';
import { cn } from '../../utils/cn';
import {
  Card,
  Chip,
  Countdown,
  DataTable,
  EmptyState,
  ErrorState,
  Pitch,
  PitchPlayer,
  PitchRow,
  SectionHeading,
  Skeleton,
  StatCard,
  type Column,
} from '../ui';
import { formatDateTime, signed } from '../explain/format';
import { PITCH_LINES } from './pitchLines';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { ShareImageButton } from '../share/ShareImage';
import { useExportPhoto } from '../share/exportPhoto';
import { useExport } from '../share/exportContext';
import { PointsChart } from './MlManagerChart';
import { BenchCard, ChipsPanel, MovesCard } from './MlManagerPanels';
import {
  CHIP_NAMES,
  captainMultiplier,
  formationOf,
  lineupOf,
  money,
  nextFreeTransfers,
  opponentsOf,
  plural,
  rankText,
  shownProjection,
  teamValueOf,
  topShare,
  unlimitedTransfers,
  type Lineup,
} from './mlManagerFormat';

// "ML manager" view of the Team of the Week page: a season-long team the points model runs under
// the game's rules from GW9 on (transfers, hits, chips), compared with the managers who started
// that same gameweek. The backend plans, locks and scores each gameweek; this only lays it out.

/** What the gameweek on show is: a plan that can still change, a locked team, or a scored one. */
// live: locked, with a provisional result from FPL's live points while the gameweek is played.
type Phase = 'planned' | 'locked' | 'live' | 'scored';
const hasPoints = (phase: Phase) => phase === 'scored' || phase === 'live';

const multiplierWord = (m: number) => (m === 3 ? 'tripled' : 'doubled');

/** Small round badge on a pitch photo (give it a label: it's the only text). */
function PhotoMarker({ icon: Icon, label, tone }: { icon: LucideIcon; label: string; tone: 'accent' | 'accent-2' }) {
  return (
    <span
      title={label}
      className={cn(
        'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-accent-on ring-1 ring-fg/70',
        tone === 'accent' ? 'bg-accent' : 'bg-accent-2',
      )}
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={3} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

function StatusBanner({ gw, phase, nextDeadline }: { gw: MlManagerGameweek; phase: Phase; nextDeadline: string | null }) {
  const deadline = formatDateTime(gw.deadline);
  const plannedAt = formatDateTime(gw.planned_at);
  let icon: ReactNode;
  let title: ReactNode;
  let detail: ReactNode;
  let side: ReactNode = null;

  if (phase === 'planned') {
    icon = <CalendarClock className="h-6 w-6" aria-hidden="true" />;
    title = (
      <>
        Planned for GW{gw.gameweek}
        <span className="font-normal text-fg-muted"> – can change until the deadline{deadline ? `, ${deadline}` : ''}</span>
      </>
    );
    detail = `Replanned after every data update with the latest predictions and team news; the last plan before the deadline is the one that counts.${plannedAt ? ` Last planned ${plannedAt}.` : ''}`;
    if (gw.deadline) {
      side = (
        <div className="shrink-0 sm:text-right">
          <p className="text-micro font-semibold uppercase text-fg-subtle">Deadline in</p>
          <Countdown target={gw.deadline} passedLabel="Deadline passed" />
        </div>
      );
    }
  } else if (phase === 'live') {
    const result = gw.result;
    const at = result?.live ? new Date(result.live.fetched_at) : null;
    icon = <Radio className="h-6 w-6" aria-hidden="true" />;
    title = (
      <>
        GW{gw.gameweek} live: {result ? plural(result.points, 'point') : '–'} so far
        <span className="font-normal text-fg-muted"> – the team locked at the deadline{deadline ? ` (${deadline})` : ''}</span>
      </>
    );
    detail = `FPL's live points${at ? `, updated ${at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}` : ''} and every minute while matches are on. Bonus points appear once FPL adds them, auto-subs once a player's matches are over; the gameweek is scored for good when FPL finishes it.`;
    side = (
      <Chip tone="danger" className="shrink-0 self-start sm:self-center">
        Live
      </Chip>
    );
  } else if (phase === 'locked') {
    icon = <Lock className="h-6 w-6" aria-hidden="true" />;
    title = (
      <>
        Locked for GW{gw.gameweek}
        <span className="font-normal text-fg-muted"> – the deadline passed{deadline ? ` (${deadline})` : ''}</span>
      </>
    );
    detail = 'This is the team that plays: nothing changes after the deadline. Its points are counted once every match of the gameweek has finished.';
  } else {
    const result = gw.result;
    icon = <CheckCircle2 className="h-6 w-6" aria-hidden="true" />;
    title = (
      <>
        GW{gw.gameweek} scored: {result ? plural(result.points, 'point') : '–'}
        {result?.gw_average != null && <span className="font-normal text-fg-muted"> against an average of {result.gw_average}</span>}
      </>
    );
    const next = formatDateTime(nextDeadline);
    detail = next
      ? `The plan for the next gameweek appears after the next data update, before the ${next} deadline.`
      : 'The plan for the next gameweek appears after the next data update.';
  }

  return (
    <Card tone="brand" className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span className="mt-0.5 shrink-0 text-accent">{icon}</span>
        <div className="min-w-0">
          <p className="font-display text-h3 font-bold text-fg">{title}</p>
          <p className="mt-0.5 text-data text-fg-muted">{detail}</p>
        </div>
      </div>
      {side}
    </Card>
  );
}

function ManagerPitch({ gw, lineup, phase }: { gw: MlManagerGameweek; lineup: Lineup; phase: Phase }) {
  const dialog = usePlayerDialog();
  // In the shared image: photos through the proxy, no click targets
  const { exporting } = useExport();
  const photoOf = useExportPhoto();
  const bought = new Set(gw.transfers.map((t) => t.in.element));
  const label =
    hasPoints(phase)
      ? `ML manager’s team for gameweek ${gw.gameweek}, after automatic substitutions${phase === 'live' ? ', live' : ''}`
      : `ML manager’s team for gameweek ${gw.gameweek}, ${phase === 'planned' ? 'as planned' : 'as locked at the deadline'}`;

  return (
    <Pitch label={label}>
      {PITCH_LINES.map((line) => (
        <PitchRow key={line.position} label={line.label}>
          {lineup.xi
            .filter((p) => p.position === line.position)
            .map((p) => {
              const projection = shownProjection(p, gw);
              const replaced = lineup.cameOnFor.get(p.element);
              let value: ReactNode;
              let detail: ReactNode;
              let marker: ReactNode;

              if (hasPoints(phase)) {
                const r = lineup.results.get(p.element);
                const m = Math.max(1, r?.multiplier ?? 1);
                const counted = r?.points != null ? r.points * m : null;
                value = (
                  <>
                    <span aria-hidden="true" className="text-data font-extrabold tabular-nums">
                      {counted ?? '–'}
                    </span>
                    <span className="sr-only">
                      {counted == null || r?.points == null
                        ? 'No points recorded'
                        : `${counted} points${m > 1 ? ` (${r.points} ${multiplierWord(m)})` : ''}`}
                    </span>
                  </>
                );
                detail =
                  projection != null ? (
                    <>
                      <span aria-hidden="true">pred {projection.toFixed(1)}</span>
                      <span className="sr-only">, predicted {projection.toFixed(1)}</span>
                    </>
                  ) : undefined;
                marker = replaced ? (
                  <PhotoMarker icon={ArrowUp} tone="accent" label={`Came off the bench for ${replaced.name}`} />
                ) : undefined;
              } else {
                value = (
                  <>
                    <span aria-hidden="true">{projection != null ? `${projection.toFixed(1)} pts` : '–'}</span>
                    <span className="sr-only">
                      {projection == null
                        ? 'No projection'
                        : `projected ${projection.toFixed(1)} points${p.captain ? ` (${multiplierWord(captainMultiplier(gw))})` : ''}`}
                    </span>
                  </>
                );
                detail = (
                  <>
                    <span className="sr-only">, price </span>
                    {money(p.now_cost)}
                  </>
                );
                marker = bought.has(p.element) ? (
                  <PhotoMarker icon={ArrowLeftRight} tone="accent-2" label="Bought this gameweek" />
                ) : undefined;
              }

              return (
                // No availability tint: the payload has none, and the reason line covers team news.
                <PitchPlayer
                  key={p.element}
                  name={p.name}
                  displayName={p.web_name}
                  team={p.team}
                  photo={photoOf(p.photo)}
                  opponents={opponentsOf(p)}
                  captain={p.captain}
                  viceCaptain={p.vice_captain}
                  marker={marker}
                  value={value}
                  detail={detail}
                  onClick={
                    dialog && !exporting
                      ? () => dialog.openPlayer({ name: p.name, team: p.team, position: p.position, photo: p.photo })
                      : undefined
                  }
                  actionLabel={`${p.name}: show stats`}
                />
              );
            })}
        </PitchRow>
      ))}
    </Pitch>
  );
}

function GameweekStats({ gw, phase, maxFreeTransfers, isStart }: { gw: MlManagerGameweek; phase: Phase; maxFreeTransfers: number; isStart: boolean }) {
  const next = nextFreeTransfers(gw, maxFreeTransfers);
  const result = hasPoints(phase) ? gw.result : null;

  let pointsDetail: string;
  if (gw.hit_cost > 0) pointsDetail = `After a −${gw.hit_cost} hit`;
  else if (gw.chip === '3xc') pointsDetail = 'Captain counted three times';
  else if (gw.chip === 'bboost') pointsDetail = 'All 15 players count';
  else pointsDetail = 'Captain counted twice';

  return (
    <div className="grid grid-cols-2 gap-3">
      {result ? (
        <StatCard
          label={phase === 'live' ? 'Points so far' : 'Points'}
          value={result.points}
          detail={
            result.hit_cost > 0
              ? `${result.gross} before a −${result.hit_cost} hit`
              : phase === 'live' && result.gw_average == null
                ? 'Provisional, while matches are on'
                : `Average ${result.gw_average ?? '–'} · highest ${result.gw_highest ?? '–'}`
          }
          highlight
        />
      ) : (
        <StatCard
          label="Projected"
          value={gw.projected_points != null ? gw.projected_points.toFixed(1) : '–'}
          detail={pointsDetail}
          highlight
        />
      )}
      <StatCard
        label="Free transfers"
        value={unlimitedTransfers(gw) ? <span className="text-h3">Unlimited</span> : gw.free_transfers}
        detail={
          isStart
            ? `Free starting squad · ${next} next GW`
            : unlimitedTransfers(gw) && gw.chip
              ? `${CHIP_NAMES[gw.chip]} · ${next} next GW`
              : `${plural(gw.transfers_made, 'transfer')} · ${next} next GW`
        }
      />
      <StatCard
        label="Bank"
        value={money(gw.bank_after)}
        detail={gw.bank_before !== gw.bank_after ? `${money(gw.bank_before)} before transfers` : 'Left to spend'}
      />
      <StatCard label="Team value" value={money(teamValueOf(gw))} detail="Squad at current prices" />
    </div>
  );
}

function SeasonSummary({ view }: { view: MlManagerView }) {
  const { totals, history } = view;
  const league = totals.league;
  const best = history.reduce<MlManagerHistoryRow | null>((b, r) => (b == null || r.points > b.points ? r : b), null);
  const scoredAny = totals.gameweeks > 0;
  const top = topShare(league?.percentile);

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        label="Season points"
        value={scoredAny ? totals.points : '–'}
        detail={scoredAny ? `${plural(totals.gameweeks, 'gameweek')} since GW${view.start_gameweek}` : `First points after GW${view.start_gameweek}`}
        highlight
      />
      <StatCard
        label={`Rank among GW${view.start_gameweek} starters`}
        value={league ? rankText(league.rank) : '–'}
        detail={league ? `of ${rankText(league.size)}${top ? ` · ${top}` : ''}` : 'Not available yet'}
      />
      <StatCard
        label="vs average"
        value={totals.vs_average != null ? signed(totals.vs_average, 0) : '–'}
        detail={totals.vs_average != null ? 'Against the average manager, summed' : 'Not available yet'}
      />
      <StatCard label="Best gameweek" value={best ? best.points : '–'} detail={best ? `GW${best.gameweek}` : 'Not available yet'} />
    </div>
  );
}

function HistoryTable({ history }: { history: MlManagerHistoryRow[] }) {
  const rows = [...history].reverse(); // newest first

  const columns: Column<MlManagerHistoryRow>[] = [
    {
      key: 'gameweek',
      header: 'GW',
      description: 'Gameweek',
      sticky: true,
      render: (r) => (
        <span className="flex items-center gap-1.5">
          <span className="font-semibold">GW{r.gameweek}</span>
          {r.chip && <Chip tone="info">{CHIP_NAMES[r.chip]}</Chip>}
        </span>
      ),
    },
    {
      key: 'points',
      header: 'Pts',
      description: 'Points, after any hit',
      align: 'right',
      render: (r) => (
        <span className="font-bold">
          {r.points}
          {r.hit_cost > 0 && (
            <span className="ml-1 text-micro font-semibold text-danger">
              <span aria-hidden="true">−{r.hit_cost}</span>
              <span className="sr-only"> (after a {r.hit_cost}-point hit)</span>
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'average',
      header: 'Avg',
      description: 'Average manager’s points',
      align: 'right',
      hideBelow: 'sm',
      render: (r) => <span className="text-fg-muted">{r.gw_average ?? '–'}</span>,
    },
    {
      key: 'diff',
      header: '+/−',
      description: 'Points minus the average',
      align: 'right',
      render: (r) => {
        if (r.gw_average == null) return <span className="text-fg-subtle">–</span>;
        const diff = r.points - r.gw_average;
        return <span className={cn('font-semibold', diff > 0 ? 'text-accent' : diff < 0 ? 'text-danger' : 'text-fg-muted')}>{signed(diff, 0)}</span>;
      },
    },
    {
      key: 'captain',
      header: 'Captain',
      hideBelow: 'md',
      render: (r) => <span className="text-fg-muted">{r.captain ?? '–'}</span>,
    },
    {
      key: 'transfers',
      header: 'TR',
      description: 'Transfers made',
      align: 'right',
      hideBelow: 'sm',
      render: (r) => <span className="text-fg-muted">{r.transfers}</span>,
    },
    {
      key: 'total',
      header: 'Total',
      description: 'Season total',
      align: 'right',
      render: (r) => <span className="font-semibold">{r.total}</span>,
    },
    {
      key: 'rank',
      header: 'Rank',
      description: 'Rank among the managers who started in the same gameweek',
      align: 'right',
      hideBelow: 'md',
      render: (r) =>
        r.league_rank != null ? (
          <span>
            {rankText(r.league_rank)}
            {topShare(r.percentile) && <span className="block text-micro text-fg-subtle">{topShare(r.percentile)}</span>}
          </span>
        ) : (
          <span className="text-fg-subtle">–</span>
        ),
    },
    {
      key: 'value',
      header: 'Value',
      description: 'Team value at current prices, and the bank (£m)',
      align: 'right',
      hideBelow: 'lg',
      render: (r) => (
        <span className="text-fg-muted">
          {money(r.team_value)}
          <span className="block text-micro text-fg-subtle">bank {money(r.bank)}</span>
        </span>
      ),
    },
  ];

  return (
    <DataTable
      caption="The ML manager’s gameweeks, newest first: points after hits, the average manager’s points, captain, transfers, season total, rank and team value"
      columns={columns}
      rows={rows}
      rowKey={(r) => r.gameweek}
      dense
      empty="No scored gameweeks yet"
    />
  );
}

function LoadingState() {
  return (
    <div role="status" className="space-y-6">
      <span className="sr-only">Loading the ML manager…</span>
      <Skeleton className="h-20 w-full rounded-lg" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <Skeleton className="h-[30rem] w-full rounded-lg sm:h-[38rem]" />
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-48 w-full rounded-lg" />
          <Skeleton className="h-60 w-full rounded-lg" />
        </div>
      </div>
    </div>
  );
}

interface MlManagerProps {
  /** Undefined while the first load is in flight (skeletons show). */
  view?: MlManagerView;
  /** The query's error, if any: a 404 means the manager hasn't started. */
  error?: unknown;
  onRetry?: () => void;
  /** A background refresh is in flight. */
  updating?: boolean;
}

/** The ML manager's season: this gameweek's team and moves, chips, points and rank. */
export function MlManager({ view, error, onRetry, updating = false }: MlManagerProps) {
  const chipsHeadingId = useId();
  const seasonHeadingId = useId();
  const historyHeadingId = useId();
  // A plan whose deadline has passed is final even before the next data update locks it.
  const [now] = useState(() => Date.now());

  if (error) {
    return (
      <Card>
        {isNotFoundError(error) ? (
          <EmptyState
            icon={<CalendarClock className="h-8 w-8" aria-hidden="true" />}
            title="The ML manager hasn’t started yet"
            description="The ML manager starts in GW9; its first points appear after GW9 finishes."
          />
        ) : (
          <ErrorState title="Couldn’t load the ML manager" description={handleApiError(error)} onRetry={onRetry} />
        )}
      </Card>
    );
  }

  if (!view) return <LoadingState />;

  const gw = view.current;
  const deadlineMs = gw.deadline ? Date.parse(gw.deadline) : NaN;
  const phase: Phase =
    gw.status === 'scored' && gw.result
      ? 'scored'
      : gw.status !== 'scored' && gw.result?.live && deadlineMs <= now
        ? 'live'
      : gw.status === 'planned' && !(deadlineMs <= now)
        ? 'planned'
        : 'locked';
  // Lay the gameweek out in the phase on show (a plan whose deadline passed reads as locked).
  // A live gameweek is laid out like a scored one (points, auto-subs) from its provisional result.
  const shown: MlManagerGameweek =
    phase === 'live' ? { ...gw, status: 'scored' } : phase === gw.status ? gw : { ...gw, status: phase };
  const lineup = lineupOf(shown);
  const isStart = gw.gameweek === view.start_gameweek;

  return (
    <div aria-busy={updating} className="space-y-8">
      <h2 className="sr-only">
        ML manager, gameweek {gw.gameweek}: {phase === 'planned' ? 'planned team' : phase === 'locked' ? 'locked team' : phase === 'live' ? 'live points' : 'scored team'}
      </h2>

      <StatusBanner gw={gw} phase={phase} nextDeadline={view.next_deadline} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-data text-fg-muted">
            <span className="font-semibold text-fg">GW{gw.gameweek}</span>
            <span aria-hidden="true">·</span>
            <span>
              <span className="sr-only">Formation </span>
              {formationOf(lineup.xi)}
            </span>
            <div className="ml-auto flex items-center gap-2">
              {gw.chip && (
                <Chip tone="info">
                  <span className="sr-only">Chip: </span>
                  {CHIP_NAMES[gw.chip]}
                  {phase === 'planned' ? ' planned' : ' played'}
                </Chip>
              )}
              <ShareImageButton
                title={`ML manager, GW${gw.gameweek}`}
                eyebrow="Team of the Week · ML manager"
                subtitle={[
                  formationOf(lineup.xi),
                  shown.status === 'scored' && shown.result
                    ? `${shown.result.points} points${phase === 'live' ? ' so far' : ''}`
                    : shown.projected_points != null
                      ? `${shown.projected_points.toFixed(1)} projected points`
                      : null,
                  gw.chip ? CHIP_NAMES[gw.chip] : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                fileName={`pitch-me-ml-manager-gw${gw.gameweek}`}
                shareText={`The team our points model runs in the FPL game, gameweek ${gw.gameweek}.`}
                url="/team-of-the-week?mode=manager"
              >
                <ManagerPitch gw={shown} lineup={lineup} phase={phase} />
              </ShareImageButton>
            </div>
          </div>
          <ManagerPitch gw={shown} lineup={lineup} phase={phase} />
        </div>

        <aside className="space-y-4">
          <GameweekStats gw={shown} phase={phase} maxFreeTransfers={view.rules.max_free_transfers} isStart={isStart} />
          <MovesCard gw={shown} maxFreeTransfers={view.rules.max_free_transfers} isStart={isStart} />
          {lineup.bench.length > 0 && <BenchCard gw={shown} lineup={lineup} />}
        </aside>
      </div>

      {view.chips.length > 0 && (
        <section aria-labelledby={chipsHeadingId}>
          <SectionHeading
            as="h2"
            size="md"
            id={chipsHeadingId}
            title="Chips"
            subtitle="One of each per half of the season, one per gameweek; first-half chips expire after GW19"
          />
          <ChipsPanel chips={view.chips} gw={shown} />
        </section>
      )}

      <section aria-labelledby={seasonHeadingId} className="space-y-4">
        <SectionHeading
          as="h2"
          size="md"
          id={seasonHeadingId}
          title="Season so far"
          subtitle={`Every gameweek since GW${view.start_gameweek}, scored like any manager’s, hits included`}
          className="mb-0"
        />
        <SeasonSummary view={view} />
        <Card>
          <SectionHeading as="h3" title="Points per gameweek" subtitle="After hits, against the average manager’s score" />
          {view.history.length > 0 ? (
            <PointsChart history={view.history} />
          ) : (
            <EmptyState
              title="No scored gameweeks yet"
              description={`The first points appear after GW${view.start_gameweek} finishes.`}
              className="py-6"
            />
          )}
        </Card>
      </section>

      <section aria-labelledby={historyHeadingId}>
        <SectionHeading as="h2" size="md" id={historyHeadingId} title="Gameweek history" subtitle="Newest first" />
        <HistoryTable history={view.history} />
      </section>

      <p className="text-data text-fg-subtle">
        Picked and managed by the points model ({view.model}) under the game’s rules since GW{view.start_gameweek}: a{' '}
        {money(view.rules.budget)} budget, at most 3 players per club, up to {view.rules.max_free_transfers} free transfers
        banked, {view.rules.hit_cost} points for each extra transfer, and the usual chips. Its rank is among the managers who
        also started in GW{view.start_gameweek}.
      </p>
    </div>
  );
}
