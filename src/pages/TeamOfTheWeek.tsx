import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  apiService,
  handleApiError,
  isNotFoundError,
  type TeamOfWeekMode,
  type TeamOfWeekPlayer,
} from '../services/api';
import {
  Card,
  ClubCrest,
  Container,
  ErrorState,
  PageHeader,
  Pitch,
  PitchPlayer,
  PitchRow,
  PlayerAvatar,
  PositionBadge,
  SectionHeading,
  Select,
  Skeleton,
  StatCard,
  TabPanel,
  Tabs,
} from '../components/ui';
import { ModelReview } from '../components/totw/ModelReview';
import { MlManager } from '../components/totw/MlManager';
import { PITCH_LINES } from '../components/totw/pitchLines';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import { PlayerNameButton } from '../components/players/PlayerNameButton';

// The page's tabs: the two squads from /api/team-of-the-week, the model's past picks against what
// they scored (/api/model-review), and the season-long team the model manages (/api/ml-manager).
// `mode` in the URL; predicted is the default.
type View = TeamOfWeekMode | 'review' | 'manager';

/** How often a gameweek being played refreshes its live points. */
const LIVE_REFRESH_MS = 60_000;

const parseView = (value: string | null): View =>
  value === 'actual' || value === 'review' || value === 'manager' ? value : 'predicted';

function fmtPoints(points: number, mode: TeamOfWeekMode) {
  return mode === 'predicted' ? points.toFixed(1) : String(Math.round(points));
}

function TeamPitch({ starters, mode, gameweek }: { starters: TeamOfWeekPlayer[]; mode: TeamOfWeekMode; gameweek: number }) {
  const dialog = usePlayerDialog();
  return (
    <Pitch label={`${mode === 'predicted' ? 'Predicted' : 'Dream'} team, gameweek ${gameweek}`}>
      {PITCH_LINES.map((line) => (
        <PitchRow key={line.position} label={line.label}>
          {starters
            .filter((p) => p.position === line.position)
            .map((p) => (
              <PitchPlayer
                key={p.name}
                name={p.name}
                displayName={p.web_name ?? undefined}
                team={p.team}
                photo={p.photo}
                opponents={p.opponents}
                captain={p.captain}
                viceCaptain={p.vice_captain}
                // Availability is today's; only meaningful for the upcoming gameweek.
                status={mode === 'predicted' ? p.status : null}
                statusNote={mode === 'predicted' ? p.news : null}
                value={`${fmtPoints(p.points, mode)} pts`}
                onClick={
                  dialog
                    ? () =>
                        dialog.openPlayer({
                          name: p.name,
                          team: p.team,
                          position: p.position,
                          photo: p.photo,
                          status: p.status,
                          news: p.news,
                        })
                    : undefined
                }
                actionLabel={`${p.name}: show stats`}
              />
            ))}
        </PitchRow>
      ))}
    </Pitch>
  );
}

function PitchSkeleton() {
  return <Skeleton className="h-[30rem] w-full rounded-lg sm:h-[38rem]" />;
}

export function TeamOfTheWeek() {
  usePageMeta({ title: "Team of the Week", description: "The highest-scoring FPL squad our model predicts for the next gameweek (£100m, 3 per club), how its past picks scored, each week's dream team, and a season-long team the model manages." });
  const [params, setParams] = useSearchParams();
  const view = parseView(params.get('mode'));
  const mode: TeamOfWeekMode = view === 'actual' ? 'actual' : 'predicted';
  const requestedGw = Number(params.get('gw')) || undefined;

  const { data: season } = useQuery({
    queryKey: ['season-current'],
    queryFn: () => apiService.getCurrentSeason(),
    staleTime: 5 * 60_000,
  });
  const finished = season?.finished_gameweeks ?? [];

  const gameweek = view === 'actual' ? requestedGw : undefined;
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['team-of-the-week', mode, gameweek ?? 'default'],
    queryFn: () => apiService.getTeamOfTheWeek(mode, gameweek),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    enabled: view === 'predicted' || view === 'actual',
  });

  const reviewGw = view === 'review' ? requestedGw : undefined;
  const review = useQuery({
    queryKey: ['model-review', reviewGw ?? 'default'],
    queryFn: () => apiService.getModelReview(reviewGw),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    enabled: view === 'review',
    // While the gameweek is played the backend merges FPL's live points (cached a minute).
    refetchInterval: (q) => (q.state.data?.status === 'live' ? LIVE_REFRESH_MS : false),
    // A 404 only means no saved pick for that gameweek: show it at once rather than retrying.
    retry: (failures, err) => !isNotFoundError(err) && failures < 1,
  });

  const manager = useQuery({
    queryKey: ['ml-manager'],
    queryFn: () => apiService.getMlManager(),
    staleTime: 5 * 60_000,
    enabled: view === 'manager',
    refetchInterval: (q) => {
      const gw = q.state.data?.current;
      const started = gw?.deadline ? Date.parse(gw.deadline) <= Date.now() : false;
      return gw && gw.status !== 'scored' && started ? LIVE_REFRESH_MS : false;
    },
    // A 404 only means the manager hasn't started: show it at once rather than retrying.
    retry: (failures, err) => !isNotFoundError(err) && failures < 1,
  });

  // Keep the gw param out of the URL in predicted and manager modes (it only applies to past gameweeks).
  useEffect(() => {
    if ((view === 'predicted' || view === 'manager') && params.has('gw')) {
      params.delete('gw');
      setParams(params, { replace: true });
    }
  }, [view, params, setParams]);

  // Switching tabs drops the gameweek: each tab has its own default.
  const setView = (next: View) => setParams(next === 'predicted' ? {} : { mode: next });
  const team = data && data.mode === mode ? data : undefined;
  const captain = team?.starters.find((p) => p.captain);

  return (
    <Container>
      <PageHeader
        eyebrow={season?.season ?? 'This season'}
        title="Team of the Week"
        description={
          view === 'predicted'
            ? 'The highest-scoring XI our model expects next gameweek, picked like a real FPL squad: £100m, 3 players per club at most.'
            : view === 'review'
              ? 'How the model’s pick for a past gameweek actually did: each player’s predicted and real FPL points, against the average FPL score.'
              : view === 'manager'
                ? 'A season-long team run by our points model since GW9, under the game’s rules: a £100m budget, free transfers, hits and chips, ranked against the managers who started that week.'
                : 'The best-performing XI of a finished gameweek, by actual FPL points.'
        }
      />

      <Tabs
        label="Team of the Week view"
        idPrefix="totw"
        value={view}
        onChange={setView}
        items={[
          { value: 'predicted', label: `Predicted${season?.next_gameweek ? ` · GW${season.next_gameweek}` : ''}` },
          { value: 'review', label: 'Model vs actual' },
          { value: 'actual', label: 'Dream team' },
          { value: 'manager', label: 'ML manager' },
        ]}
      />

      <TabPanel idPrefix="totw" value={view} className="pt-5">
        {view === 'manager' ? (
          <MlManager
            view={manager.data}
            error={manager.isError ? manager.error : undefined}
            updating={manager.isFetching && !manager.isLoading}
            onRetry={() => manager.refetch()}
          />
        ) : view === 'review' ? (
          <ModelReview
            review={review.data}
            error={review.isError ? review.error : undefined}
            selectedGameweek={reviewGw}
            updating={review.isFetching && review.isPlaceholderData}
            onGameweekChange={(gw) => setParams({ mode: 'review', gw: String(gw) })}
            onRetry={() => review.refetch()}
            onShowLatest={reviewGw ? () => setParams({ mode: 'review' }) : undefined}
          />
        ) : isError ? (
          <Card>
            <ErrorState title="Couldn't build the team" description={handleApiError(error)} onRetry={() => refetch()} />
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
            <div aria-busy={isFetching}>
              {isLoading || !team ? <PitchSkeleton /> : <TeamPitch starters={team.starters} mode={mode} gameweek={team.gameweek} />}
            </div>

            <aside className="space-y-4">
              {mode === 'actual' && finished.length > 0 && (
                <label className="block">
                  <span className="mb-1.5 block text-micro font-semibold uppercase text-fg-muted">Gameweek</span>
                  <Select
                    value={team?.gameweek ?? requestedGw ?? ''}
                    onChange={(e) => setParams({ mode: 'actual', gw: e.target.value })}
                  >
                    {[...finished].reverse().map((n) => (
                      <option key={n} value={n}>
                        Gameweek {n}
                      </option>
                    ))}
                  </Select>
                </label>
              )}

              <div className="grid grid-cols-2 gap-3">
                <StatCard
                  label={mode === 'predicted' ? 'Predicted points' : 'Points'}
                  value={team ? fmtPoints(team.total_points, mode) : '–'}
                  detail={mode === 'predicted' ? 'Captain counted twice' : `Gameweek ${team?.gameweek ?? ''}`}
                  highlight
                />
                <StatCard label="Formation" value={team?.formation ?? '–'} />
                {mode === 'predicted' && (
                  <StatCard
                    label="Squad cost"
                    value={team?.total_cost != null ? `£${team.total_cost.toFixed(1)}m` : '–'}
                    detail={team?.budget != null ? `of £${team.budget.toFixed(1)}m` : undefined}
                  />
                )}
                <StatCard
                  label="Captain"
                  value={captain ? <span className="text-h3">{captain.name.split(' ').slice(-1)[0]}</span> : '–'}
                  detail={captain ? `${fmtPoints(captain.points, mode)} pts${mode === 'predicted' ? ' predicted' : ''}` : undefined}
                />
              </div>

              {mode === 'predicted' && team && team.bench.length > 0 && (
                <Card padded={false}>
                  <div className="px-4 pt-4">
                    <SectionHeading as="h2" size="md" title="Bench" subtitle="Picked to come on when a starter doesn’t play" />
                  </div>
                  <ul>
                    {team.bench.map((p) => (
                      <li key={p.name} className="flex items-center gap-3 border-t border-line px-4 py-2.5">
                        <PlayerAvatar name={p.name} src={p.photo} size={32} />
                        <span className="min-w-0 flex-1">
                          <PlayerNameButton
                            player={{ name: p.name, team: p.team, position: p.position, photo: p.photo, status: p.status, news: p.news }}
                            className="block text-sm font-semibold text-fg"
                          />
                          <ClubCrest team={p.team} size={14} label="full" className="text-data text-fg-subtle" />
                        </span>
                        <PositionBadge position={p.position} />
                        <span className="w-14 text-right text-data tabular-nums text-fg-muted">
                          {p.cost != null ? `£${p.cost.toFixed(1)}m` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              <p className="text-data text-fg-subtle">
                {mode === 'predicted'
                  ? 'Picked by an optimiser from the points model’s next-gameweek predictions. Injured and suspended players are left out; doubtful players are marked in yellow.'
                  : 'Like FPL’s Dream Team: the best valid formation by points scored, with no budget or club limit.'}
              </p>
            </aside>
          </div>
        )}
      </TabPanel>
    </Container>
  );
}
