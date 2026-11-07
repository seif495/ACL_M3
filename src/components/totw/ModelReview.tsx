import { useId } from 'react';
import { ArrowDown, ArrowUp, CalendarClock } from 'lucide-react';
import {
  handleApiError,
  isNotFoundError,
  type ModelReviewPlayer,
  type ModelReviewResponse,
} from '../../services/api';
import { cn } from '../../utils/cn';
import { displayName } from '../../utils/playerNames';
import {
  Button,
  Card,
  Chip,
  ClubCrest,
  DataTable,
  EmptyState,
  ErrorState,
  Pitch,
  PitchPlayer,
  PitchRow,
  PlayerAvatar,
  PositionBadge,
  SectionHeading,
  Select,
  Skeleton,
  StatCard,
  type Column,
} from '../ui';
import { PITCH_LINES } from './pitchLines';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { PlayerNameButton } from '../players/PlayerNameButton';

// "Model vs actual" view of the Team of the Week page: the squad the model picked before a past
// deadline and what it scored. The backend applies FPL's auto-sub and vice-captain rules; this
// only lays the result out (pitch of the final XI, bench, totals, and a player-by-player table).

/** FPL's web name when known, else the last word of the name (as on the pitch's name plates). */
const shortName = (p: ModelReviewPlayer) => p.web_name || p.name.split(' ').slice(-1)[0] || p.name;

/** Bench players (multiplier 0) are shown at face value; their points just don't count. */
const shownMultiplier = (p: ModelReviewPlayer) => Math.max(1, p.multiplier);

/** Signed, with a real minus sign: "+11", "−3.4", "0". */
function signed(value: number, digits = 0) {
  const rounded = Number(value.toFixed(digits)); // -0.04 rounds to -0, shown as "0"
  if (rounded === 0) return (0).toFixed(digits);
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded).toFixed(digits)}`;
}

function fmtDeadline(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function multiplierText(points: number, multiplier: number) {
  return multiplier === 2 ? `${points} doubled` : `${points} times ${multiplier}`;
}

/** A dash for a missing number, with its meaning for screen readers. */
export function Missing({ label }: { label: string }) {
  return (
    <>
      <span aria-hidden="true" className="text-fg-subtle">
        –
      </span>
      <span className="sr-only">{label}</span>
    </>
  );
}

interface SubMarkerProps {
  direction: 'in' | 'out';
  label: string;
  large?: boolean;
  /** Hidden from screen readers, when text next to it already says the same. */
  decorative?: boolean;
}

/** Round auto-sub arrow: mint up for the sub who came on, pink down for the starter he replaced. */
export function SubMarker({ direction, label, large = false, decorative = false }: SubMarkerProps) {
  const Icon = direction === 'in' ? ArrowUp : ArrowDown;
  return (
    <span
      title={label}
      aria-hidden={decorative || undefined}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full text-accent-on ring-1 ring-fg/70',
        large ? 'h-5 w-5' : 'h-4 w-4',
        direction === 'in' ? 'bg-accent' : 'bg-danger',
      )}
    >
      <Icon className={large ? 'h-3.5 w-3.5' : 'h-3 w-3'} strokeWidth={3} aria-hidden="true" />
      {!decorative && <span className="sr-only">{label}</span>}
    </span>
  );
}

/** C/V armband next to a name in the lists, as picked. */
export function ArmbandTag({ player }: { player: { captain: boolean; vice_captain: boolean } }) {
  if (!player.captain && !player.vice_captain) return null;
  const label = player.captain ? 'Captain' : 'Vice-captain';
  return (
    <span
      title={label}
      className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-fg text-micro font-extrabold leading-none text-brand"
    >
      <span aria-hidden="true">{player.captain ? 'C' : 'V'}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

function ReviewPitch({ review, cameOnFor }: { review: ModelReviewResponse; cameOnFor: Record<string, ModelReviewPlayer> }) {
  const dialog = usePlayerDialog();
  return (
    <Pitch label={`Model's team for gameweek ${review.gameweek}, after automatic substitutions`}>
      {PITCH_LINES.map((line) => (
        <PitchRow key={line.position} label={line.label}>
          {review.starters
            .filter((p) => p.position === line.position)
            .map((p) => {
              const m = shownMultiplier(p);
              const counted = p.points != null ? p.points * m : null;
              const predicted = p.predicted_points != null ? (p.predicted_points * m).toFixed(1) : null;
              const replaced = cameOnFor[p.name];
              return (
                // No availability tint: today's status says nothing about a past gameweek.
                <PitchPlayer
                  key={p.name}
                  name={p.name}
                  displayName={p.web_name}
                  team={p.team}
                  photo={p.photo}
                  opponents={p.opponents}
                  captain={p.captain}
                  viceCaptain={p.vice_captain}
                  onClick={
                    dialog
                      ? () => dialog.openPlayer({ name: p.name, team: p.team, position: p.position, photo: p.photo })
                      : undefined
                  }
                  actionLabel={`${p.name}: show stats`}
                  marker={
                    p.subbed_in ? (
                      <SubMarker
                        direction="in"
                        large
                        label={replaced ? `Came off the bench for ${replaced.name}` : 'Came off the bench'}
                      />
                    ) : undefined
                  }
                  value={
                    <>
                      <span aria-hidden="true" className="text-data font-extrabold tabular-nums">
                        {counted ?? '–'}
                      </span>
                      <span className="sr-only">
                        {counted == null || p.points == null
                          ? 'Yet to play'
                          : `${counted} points${m > 1 ? ` (${multiplierText(p.points, m)})` : ''}`}
                      </span>
                    </>
                  }
                  detail={
                    predicted != null ? (
                      <>
                        <span aria-hidden="true">pred {predicted}</span>
                        <span className="sr-only">, predicted {predicted}</span>
                      </>
                    ) : undefined
                  }
                />
              );
            })}
        </PitchRow>
      ))}
    </Pitch>
  );
}

function Summary({ review }: { review?: ModelReviewResponse }) {
  const average = review?.fpl_average ?? null;
  const diff = review && average != null ? review.points - average : null;
  return (
    <div className="grid grid-cols-2 gap-3">
      <StatCard
        label="Model’s points"
        value={review?.points ?? '–'}
        detail={review ? `Predicted ${review.predicted_points.toFixed(1)}` : undefined}
        highlight
      />
      <StatCard
        label="vs average"
        value={diff != null ? signed(diff) : '–'}
        detail={diff == null ? undefined : diff > 0 ? 'Above the average' : diff < 0 ? 'Below the average' : 'Level with the average'}
      />
      <StatCard label="FPL average" value={average ?? '–'} detail="All managers" />
      <StatCard label="Highest" value={review?.fpl_highest ?? '–'} detail="Top FPL score" />
      <StatCard
        label="Formation"
        value={review?.formation ?? '–'}
        detail={review ? (review.picked_formation !== review.formation ? `Picked ${review.picked_formation}` : 'As picked') : undefined}
      />
      <StatCard
        label="Squad cost"
        value={review?.total_cost != null ? `£${review.total_cost.toFixed(1)}m` : '–'}
        detail={review?.budget != null ? `of £${review.budget.toFixed(1)}m` : '15 players'}
      />
    </div>
  );
}

function Bench({ review, replacedBy }: { review: ModelReviewResponse; replacedBy: Record<string, ModelReviewPlayer> }) {
  const left = review.bench_points;
  return (
    <Card padded={false}>
      <div className="px-4 pt-4">
        <SectionHeading
          as="h3"
          title="Bench"
          subtitle={
            left > 0
              ? `In priority order; their ${left} ${left === 1 ? 'point doesn’t' : 'points don’t'} count`
              : 'In priority order; bench points don’t count'
          }
        />
      </div>
      <ul>
        {review.bench.map((p) => {
          const replacement = replacedBy[p.name];
          return (
            <li key={p.name} className="flex items-center gap-3 border-t border-line px-4 py-2.5">
              <PlayerAvatar name={p.name} src={p.photo} size={32} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <PlayerNameButton
                    player={{ name: p.name, team: p.team, position: p.position, photo: p.photo }}
                    label={displayName(p.name, p.web_name)}
                    className="text-sm font-semibold text-fg"
                  />
                  <ArmbandTag player={p} />
                  {p.subbed_out && <SubMarker direction="out" label="Didn’t play" decorative />}
                </span>
                {p.subbed_out ? (
                  <span className="flex items-center gap-1.5 text-micro font-semibold text-warning">
                    <ClubCrest team={p.team} size={14} />
                    <span>
                      Didn’t play{replacement ? ` · replaced by ${shortName(replacement)}` : ''}
                    </span>
                  </span>
                ) : (
                  <ClubCrest team={p.team} size={14} label="full" className="text-data text-fg-subtle" />
                )}
              </span>
              <PositionBadge position={p.position} />
              <span className="w-14 shrink-0 text-right tabular-nums">
                <span className="block text-sm font-bold text-fg-muted">
                  {p.points == null ? (
                    <Missing label="Yet to play" />
                  ) : (
                    <>
                      {p.points}
                      <span className="sr-only"> points, not counted</span>
                    </>
                  )}
                </span>
                {p.predicted_points != null && (
                  <span className="block text-micro text-fg-subtle">
                    <span aria-hidden="true">pred </span>
                    <span className="sr-only">predicted </span>
                    {p.predicted_points.toFixed(1)}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

interface ReviewRow {
  player: ModelReviewPlayer;
  bench: boolean;
}

function diffTone(diff: number, bench: boolean) {
  if (bench || Math.abs(diff) < 0.05) return 'text-fg-muted';
  return diff > 0 ? 'text-accent' : 'text-danger';
}

function ComparisonTable({
  review,
  replacedBy,
  cameOnFor,
  updating,
}: {
  review?: ModelReviewResponse;
  replacedBy: Record<string, ModelReviewPlayer>;
  cameOnFor: Record<string, ModelReviewPlayer>;
  updating: boolean;
}) {
  const starters = review?.starters ?? [];
  const rows: ReviewRow[] = [
    ...starters.map((player) => ({ player, bench: false })),
    ...(review?.bench ?? []).map((player) => ({ player, bench: true })),
  ];

  const columns: Column<ReviewRow>[] = [
    {
      key: 'player',
      header: 'Player',
      sticky: true,
      render: ({ player: p, bench }) => (
        <span className="flex items-center gap-2">
          <PlayerAvatar name={p.name} src={p.photo} size={28} />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5">
              <PlayerNameButton
                player={{ name: p.name, team: p.team, position: p.position, photo: p.photo }}
                label={displayName(p.name, p.web_name)}
                className={cn('max-w-[7.5rem] truncate font-semibold sm:max-w-[14rem]', bench && 'text-fg-muted')}
              />
              <ArmbandTag player={p} />
              {p.subbed_in && (
                <SubMarker direction="in" label={cameOnFor[p.name] ? `Came off the bench for ${cameOnFor[p.name].name}` : 'Came off the bench'} />
              )}
              {p.subbed_out && (
                <SubMarker direction="out" label={replacedBy[p.name] ? `Didn’t play, replaced by ${replacedBy[p.name].name}` : 'Didn’t play'} />
              )}
            </span>
            <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
              <ClubCrest team={p.team} size={14} label="short" />
              <PositionBadge position={p.position} className="w-auto px-1 sm:hidden" />
              {bench && <span>Bench</span>}
            </span>
          </span>
        </span>
      ),
    },
    {
      key: 'position',
      header: 'Pos',
      description: 'Position',
      hideBelow: 'sm',
      render: ({ player }) => <PositionBadge position={player.position} />,
    },
    {
      key: 'predicted',
      header: 'Pred',
      description: 'Predicted points (doubled for the captain)',
      align: 'right',
      render: ({ player: p, bench }) =>
        p.predicted_points == null ? (
          <Missing label="No prediction" />
        ) : (
          <span className={bench ? 'text-fg-subtle' : 'text-fg-muted'}>{(p.predicted_points * shownMultiplier(p)).toFixed(1)}</span>
        ),
    },
    {
      key: 'points',
      header: 'Pts',
      description: 'Actual points (doubled for the captain; bench points don’t count)',
      align: 'right',
      render: ({ player: p, bench }) => {
        if (p.points == null) return <Missing label="Yet to play" />;
        const m = shownMultiplier(p);
        return (
          <span className={cn('font-bold', bench ? 'text-fg-muted' : 'text-fg')}>
            {p.points * m}
            {m > 1 && (
              <span aria-hidden="true" className="ml-1 text-micro font-semibold text-fg-subtle">
                ×{m}
              </span>
            )}
            <span className="sr-only">{m > 1 ? ` (${multiplierText(p.points, m)})` : bench ? ' (bench, not counted)' : ''}</span>
          </span>
        );
      },
    },
    {
      key: 'diff',
      header: '+/−',
      description: 'Actual minus predicted points',
      align: 'right',
      render: ({ player: p, bench }) => {
        if (p.points == null || p.predicted_points == null) return <Missing label="Not known yet" />;
        const diff = (p.points - p.predicted_points) * shownMultiplier(p);
        return <span className={cn('font-semibold', diffTone(diff, bench))}>{signed(diff, 1)}</span>;
      },
    },
  ];

  return (
    <DataTable
      caption={`Gameweek ${review?.gameweek ?? ''}: predicted and actual points of the model’s 15 players, starting XI first, then the bench`}
      columns={columns}
      rows={rows}
      rowKey={(r) => r.player.name}
      // A heavier line where the bench starts.
      rowClassName={(r, i) => (r.bench && i === starters.length ? 'border-t-2 border-t-line-strong' : undefined)}
      loading={!review}
      skeletonRows={8}
      dense
      className={cn('transition-opacity', updating && 'opacity-60')}
    />
  );
}

interface ModelReviewProps {
  /** Undefined while the first load is in flight (skeletons show). */
  review?: ModelReviewResponse;
  /** The query's error, if any: a 404 means there's no saved pick for that gameweek. */
  error?: unknown;
  /** Gameweek asked for in the URL, so the picker shows it while it loads. */
  selectedGameweek?: number;
  /** An older gameweek is on screen while the selected one loads. */
  updating?: boolean;
  /** Shows the gameweek picker. */
  onGameweekChange?: (gameweek: number) => void;
  onRetry?: () => void;
  /** Back to the latest pick; offered when the requested gameweek has none. */
  onShowLatest?: () => void;
}

/** The model's saved pick for a past gameweek against what it actually scored. */
export function ModelReview({
  review,
  error,
  selectedGameweek,
  updating = false,
  onGameweekChange,
  onRetry,
  onShowLatest,
}: ModelReviewProps) {
  const tableHeadingId = useId();

  if (error) {
    return (
      <Card>
        {isNotFoundError(error) ? (
          <EmptyState
            icon={<CalendarClock className="h-8 w-8" aria-hidden="true" />}
            title={selectedGameweek ? `No saved pick for gameweek ${selectedGameweek}` : 'No saved pick yet'}
            description="The model’s picks are saved before each deadline from GW7 on, and shown here once the deadline has passed."
            action={
              onShowLatest && (
                <Button variant="secondary" size="sm" onClick={onShowLatest}>
                  Show the latest pick
                </Button>
              )
            }
          />
        ) : (
          <ErrorState title="Couldn’t load the review" description={handleApiError(error)} onRetry={onRetry} />
        )}
      </Card>
    );
  }

  const subs = review?.auto_subs ?? [];
  const byName = new Map([...(review?.starters ?? []), ...(review?.bench ?? [])].map((p) => [p.name, p]));
  const pairs = subs.flatMap((s) => {
    const out = byName.get(s.out);
    const on = byName.get(s.in);
    return out && on ? [{ out, on }] : [];
  });
  const replacedBy: Record<string, ModelReviewPlayer> = Object.fromEntries(pairs.map((s) => [s.out.name, s.on]));
  const cameOnFor: Record<string, ModelReviewPlayer> = Object.fromEntries(pairs.map((s) => [s.on.name, s.out]));
  const deadline = review?.deadline ? fmtDeadline(review.deadline) : null;
  const note = review?.note ?? (review?.source === 'manual' ? 'Recorded by hand from the FPL app.' : null);
  const pickerValue =
    selectedGameweek && review?.gameweeks.includes(selectedGameweek) ? selectedGameweek : review?.gameweek;

  return (
    <div aria-busy={updating || !review}>
      {review && (
        <h2 className="sr-only">
          Gameweek {review.gameweek}: the model’s pick against its actual points
        </h2>
      )}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <div className={cn('transition-opacity', updating && 'opacity-60')}>
          {review ? (
            <ReviewPitch review={review} cameOnFor={cameOnFor} />
          ) : (
            <Skeleton className="h-[30rem] w-full rounded-lg sm:h-[38rem]" />
          )}
        </div>

        {/* Spans both rows on wide screens, so the table sits under the pitch; on phones it comes after the aside. */}
        <aside className="space-y-4 lg:row-span-2">
          {review && onGameweekChange && review.gameweeks.length > 0 && (
            <label className="block">
              <span className="mb-1.5 block text-micro font-semibold uppercase text-fg-muted">Gameweek</span>
              <Select value={pickerValue} onChange={(e) => onGameweekChange(Number(e.target.value))}>
                {[...review.gameweeks].reverse().map((n) => (
                  <option key={n} value={n}>
                    Gameweek {n}
                  </option>
                ))}
              </Select>
            </label>
          )}

          {review?.status === 'live' && (
            <p className="flex items-start gap-2 text-data text-fg-muted">
              <Chip tone="danger">Live</Chip>
              <span>
                {review.live
                  ? `FPL's live points, updated ${new Date(review.live.fetched_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} and every minute. Bonus points appear once FPL adds them; auto-subs stay provisional until the gameweek is finished.`
                  : 'Points are still coming in, and auto-subs stay provisional until the gameweek is finished.'}
              </span>
            </p>
          )}

          <Summary review={review} />

          {review ? review.bench.length > 0 && <Bench review={review} replacedBy={replacedBy} /> : <Skeleton className="h-60 w-full rounded-lg" />}

          <p className="text-data text-fg-subtle">
            The squad the model picked before the deadline{deadline ? ` (${deadline})` : ''}, scored with FPL’s rules: a
            starter who doesn’t play is replaced by the first bench player who keeps the formation valid, and if the
            captain doesn’t play, the vice-captain’s points count double. Under each player’s points is what the model
            predicted.
          </p>
          {note && <p className="text-micro text-fg-subtle">{note}</p>}
        </aside>

        <section aria-labelledby={tableHeadingId} className="min-w-0">
          <SectionHeading
            as="h3"
            id={tableHeadingId}
            title="Predicted vs actual"
            subtitle="The final XI, then the bench. The captain’s points and prediction count double; bench points don’t count."
          />
          <ComparisonTable review={review} replacedBy={replacedBy} cameOnFor={cameOnFor} updating={updating} />
        </section>
      </div>
    </div>
  );
}
