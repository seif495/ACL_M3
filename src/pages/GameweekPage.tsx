import { useNavigate, useParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { handleApiError, isNotFoundError } from '../services/api';
import { getGameweek, type GameweekFixture, type GameweekResponse } from '../services/gameweekApi';
import {
  Button,
  ButtonLink,
  Card,
  Chip,
  Container,
  Countdown,
  EmptyState,
  ErrorState,
  FixtureRow,
  PageHeader,
  SectionHeading,
  Select,
  SkeletonRows,
  type FixtureStatus,
} from '../components/ui';
import { PlayersCard } from '../components/gameweek/PlayersCard';
import { BonusTables } from '../components/gameweek/BonusTables';
import { DreamTeam } from '../components/gameweek/DreamTeam';
import { ManagerCard, ModelSquadCard } from '../components/gameweek/SquadScores';
import { usePageMeta } from '../hooks/usePageMeta';
import { matchPath } from '../utils/clubColors';

/** A gameweek being played refreshes its live points this often. */
const LIVE_REFRESH_MS = 60_000;

const STATUS: Record<GameweekFixture['status'], FixtureStatus> = {
  scheduled: 'scheduled',
  in_progress: 'live',
  finished: 'finished',
};

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function Fixtures({ fixtures, title, subtitle }: { fixtures: GameweekFixture[]; title: string; subtitle?: string }) {
  return (
    <Card padded={false}>
      <div className="px-4 pt-4 sm:px-5 sm:pt-5">
        <SectionHeading title={title} subtitle={subtitle} as="h3" />
      </div>
      {fixtures.length === 0 ? (
        <EmptyState title="No fixtures" className="py-6" />
      ) : (
        fixtures.map((f) => (
          <FixtureRow
            key={f.id}
            home={f.home_team}
            away={f.away_team}
            homeScore={f.home_score}
            awayScore={f.away_score}
            kickoff={f.kickoff_time}
            status={STATUS[f.status]}
            showDay
            to={matchPath(f.id)}
          />
        ))
      )}
    </Card>
  );
}

function StateChip({ page }: { page: GameweekResponse }) {
  if (page.state === 'live') return <Chip tone="warning">Live</Chip>;
  if (page.state === 'finished') return <Chip tone="accent">Finished</Chip>;
  return <Chip tone="info">Upcoming</Chip>;
}

function Upcoming({ page }: { page: GameweekResponse }) {
  return (
    <Card>
      <SectionHeading title="Not started" subtitle="Nothing to recap yet. The deadline is still ahead." as="h2" />
      {page.deadline && (
        <p className="mb-4 text-body text-fg-muted">
          Deadline {new Date(page.deadline).toLocaleString(undefined, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}
          {' '}(<Countdown target={page.deadline} className="text-fg" />)
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <ButtonLink to="/predictions" variant="primary">
          See the predictions
        </ButtonLink>
        <ButtonLink to="/fixtures?view=ticker" variant="secondary">
          Fixture difficulty ticker
        </ButtonLink>
      </div>
    </Card>
  );
}

function Live({ page }: { page: GameweekResponse }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <PlayersCard
          title="Top scorers so far"
          subtitle="FPL's live points. Bonus is added once it is confirmed."
          players={page.top_scorers}
          empty="No points yet. They appear once a match starts."
          valueLabel={(p) => (p.bps != null ? `${p.bps} BPS` : `${p.minutes ?? 0} min`)}
          detail={(p) => `${p.minutes ?? 0} min`}
        />
        <Fixtures fixtures={page.fixtures} title="Matches" subtitle="Live scores and results." />
      </div>
      <BonusTables tables={page.bonus} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {page.model_squad && <ModelSquadCard squad={page.model_squad} />}
        {page.ml_manager && <ManagerCard manager={page.ml_manager} />}
      </div>
    </div>
  );
}

function CallList({ title, subtitle, players }: { title: string; subtitle: string; players: NonNullable<GameweekResponse['calls']>['best'] }) {
  return (
    <PlayersCard
      title={title}
      subtitle={subtitle}
      players={players}
      empty="None this week."
      value={(p) => p.actual ?? 0}
      valueLabel={(p) => `predicted ${p.predicted?.toFixed(1)}`}
    />
  );
}

function Recap({ page }: { page: GameweekResponse }) {
  const positions = (['GK', 'DEF', 'MID', 'FWD'] as const).filter((p) => (page.hauls[p] ?? []).length > 0);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <PlayersCard
          title="Top 10 scorers"
          players={page.top_scorers}
          detail={(p) => `${p.minutes ?? 0} min`}
          empty="Player points aren't in yet."
        />
        {page.dream_team ? <DreamTeam team={page.dream_team} /> : <Card><EmptyState title="No Dream Team yet" description="It appears once the gameweek's points are loaded." /></Card>}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {page.model_squad && <ModelSquadCard squad={page.model_squad} />}
        {page.ml_manager && <ManagerCard manager={page.ml_manager} />}
      </div>
      {positions.length > 0 && (
        <section aria-labelledby="gw-hauls">
          <SectionHeading title="Biggest hauls by position" id="gw-hauls" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {positions.map((pos) => (
              <PlayersCard key={pos} title={{ GK: 'Goalkeepers', DEF: 'Defenders', MID: 'Midfielders', FWD: 'Forwards' }[pos]} players={page.hauls[pos] ?? []} detail={(p) => `${p.minutes ?? 0} min`} />
            ))}
          </div>
        </section>
      )}
      {page.calls && (
        <section aria-labelledby="gw-calls">
          <SectionHeading title="The model's calls" subtitle="Its predictions saved before the deadline, against what happened." id="gw-calls" />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <CallList title="Best calls" subtitle="Backed by the model, and they delivered." players={page.calls.best} />
            <CallList title="Worst calls" subtitle="Backed by the model, and they fell short." players={page.calls.worst} />
            <CallList title="Missed hauls" subtitle="The model had them low; they scored." players={page.calls.missed} />
          </div>
        </section>
      )}
      <Fixtures fixtures={page.fixtures} title="Results" subtitle="Open a match for scorers and line-ups." />
    </div>
  );
}

export function GameweekPage() {
  const params = useParams();
  const navigate = useNavigate();
  const requested = Number(params.gw) || undefined;
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['gameweek-page', requested ?? 'current'],
    queryFn: () => getGameweek(requested),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    // A gameweek being played updates every minute
    refetchInterval: (query) => (query.state.data?.state === 'live' ? LIVE_REFRESH_MS : false),
    retry: (count, err) => !isNotFoundError(err) && count < 2,
  });

  const gw = data?.gameweek ?? requested;
  usePageMeta({
    title: gw ? `Gameweek ${gw}` : 'Gameweek',
    description: gw
      ? `Gameweek ${gw} of the Premier League season: live points and bonus, the Dream Team, the biggest hauls, and how the model and the ML manager scored.`
      : 'The current Premier League gameweek: live points and bonus, or the recap.',
  });

  const gameweeks = data?.gameweeks ?? [];
  const index = data ? gameweeks.indexOf(data.gameweek) : -1;
  const go = (target: number | undefined) => target && navigate(`/gameweek/${target}`);

  return (
    <Container className="max-w-5xl">
      <PageHeader
        eyebrow={data?.season ?? 'This season'}
        title={gw ? `Gameweek ${gw}` : 'Gameweek'}
        description="Live points and bonus while the matches are on; the top scorers, the Dream Team and how the model did once they are over."
      />

      <div className="mb-4 flex items-center justify-between gap-2">
        <Button
          variant="secondary"
          size="sm"
          icon={<ChevronLeft className="h-4 w-4" aria-hidden="true" />}
          disabled={index <= 0}
          onClick={() => go(gameweeks[index - 1])}
        >
          <span className="hidden sm:inline">Previous</span>
          <span className="sr-only sm:hidden">Previous gameweek</span>
        </Button>
        <label className="flex items-center gap-2">
          <span className="sr-only">Gameweek</span>
          <Select value={data?.gameweek ?? ''} onChange={(e) => go(Number(e.target.value))} disabled={!gameweeks.length} className="w-44">
            {gameweeks.map((n) => (
              <option key={n} value={n}>
                Gameweek {n}
              </option>
            ))}
          </Select>
        </label>
        <Button
          variant="secondary"
          size="sm"
          disabled={index < 0 || index >= gameweeks.length - 1}
          onClick={() => go(gameweeks[index + 1])}
          className="flex-row-reverse"
          icon={<ChevronRight className="h-4 w-4" aria-hidden="true" />}
        >
          <span className="hidden sm:inline">Next</span>
          <span className="sr-only sm:hidden">Next gameweek</span>
        </Button>
      </div>

      <div aria-busy={isFetching} className="space-y-4">
        {isLoading ? (
          <Card>
            <SkeletonRows rows={10} />
          </Card>
        ) : isError ? (
          <Card>
            <ErrorState
              title={isNotFoundError(error) ? 'No such gameweek' : "Couldn't load the gameweek"}
              description={handleApiError(error)}
              onRetry={isNotFoundError(error) ? undefined : () => refetch()}
            />
          </Card>
        ) : data ? (
          <>
            <div className="flex flex-wrap items-center gap-2 text-data text-fg-muted">
              <StateChip page={data} />
              {data.live && <span>Updated {formatTime(data.live.fetched_at)}, refreshes every minute.</span>}
              {data.state === 'finished' && data.fpl_average != null && (
                <span>
                  FPL average {data.fpl_average}
                  {data.fpl_highest != null && `, highest ${data.fpl_highest}`}.
                </span>
              )}
            </div>
            {data.state === 'upcoming' && (
              <>
                <Upcoming page={data} />
                <Fixtures fixtures={data.fixtures} title="Fixtures" />
              </>
            )}
            {data.state === 'live' && <Live page={data} />}
            {data.state === 'finished' && <Recap page={data} />}
            {data.notes.length > 0 && (
              <ul className="space-y-1 text-data text-fg-subtle">
                {data.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
          </>
        ) : null}
      </div>
    </Container>
  );
}

export default GameweekPage;
