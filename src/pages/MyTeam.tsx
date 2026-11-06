import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiService, httpStatusOf } from '../services/api';
import {
  Button,
  Card,
  Container,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeading,
  Skeleton,
  StatCard,
} from '../components/ui';
import { usePageMeta } from '../hooks/usePageMeta';
import { formatDateTime } from '../components/explain/format';
import { money, plural } from '../components/totw/mlManagerFormat';
import { MyTeamBench, MyTeamPitch } from '../components/myteam/MyTeamPitch';
import { SquadTable } from '../components/myteam/SquadTable';
import { TransferPlanner } from '../components/myteam/TransferPlanner';
import { MyTeamChips } from '../components/myteam/MyTeamChips';
import { SeasonSoFar } from '../components/myteam/SeasonSoFar';
import { TeamIdForm } from '../components/myteam/TeamIdForm';
import { bigNumber, errorCopy, forgetTeamId, parseTeamId, readSavedTeamId, saveTeamId } from '../components/myteam/format';

// Your own FPL team, imported by its ID: the suggested XI for the next gameweek, a squad table,
// a transfer planner and your season so far. The server reads the team from FPL's public API
// (/api/my-team/{id}); the browser remembers the ID (pm-fpl-team-id) and keeps it in ?id=.

export function MyTeam() {
  usePageMeta({
    title: 'My team',
    description: 'Import your FPL team by its ID: see our model’s suggested XI, your squad’s predicted points and the best transfers to make.',
  });
  const [params, setParams] = useSearchParams();
  const id = parseTeamId(params.get('id'));
  const restored = useRef(false);

  // On the first visit with no ?id=, reopen the team this browser loaded last.
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    if (parseTeamId(params.get('id')) == null) {
      const saved = readSavedTeamId();
      if (saved != null) setParams({ id: String(saved) }, { replace: true });
    }
  }, [params, setParams]);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['my-team', id],
    queryFn: ({ signal }) => apiService.getMyTeam(id as number, signal),
    enabled: id != null,
    staleTime: 2 * 60_000,
    // The answers that retrying can't change are shown at once.
    retry: (failures, err) => ![404, 409, 429].includes(httpStatusOf(err) ?? 0) && failures < 1,
  });

  // Remember the ID once FPL has confirmed the team exists.
  useEffect(() => {
    if (data) saveTeamId(data.entry.id);
  }, [data]);

  const load = (next: number) => setParams({ id: String(next) });
  const switchTeam = () => setParams({});
  const forget = () => {
    forgetTeamId();
    setParams({});
  };

  const captain = data?.squad.find((p) => p.element === data.lineup.captain);
  const deadline = formatDateTime(data?.deadline);

  return (
    <Container>
      <PageHeader
        eyebrow={data ? `Gameweek ${data.gameweek}` : 'Your FPL team'}
        title="My team"
        description="Import your Fantasy Premier League team by its ID to see the XI our model suggests, how your squad is projected to score and the transfers it would make."
        actions={
          id != null ? (
            <Button variant="secondary" size="sm" onClick={switchTeam}>
              Switch team
            </Button>
          ) : undefined
        }
      />

      {id == null && (
        <div className="max-w-2xl space-y-4">
          <TeamIdForm onSubmit={load} />
          <p className="text-data text-fg-subtle">
            We read your team from FPL’s public data, the same squad anyone can see on the FPL site. Nothing is saved on our
            servers; your browser remembers the number so you don’t have to type it again.
          </p>
        </div>
      )}

      {id != null && isError && (
        <div className="space-y-4">
          <Card>
            <ErrorState {...errorCopy(error)} onRetry={httpStatusOf(error) === 404 || httpStatusOf(error) === 409 ? undefined : () => refetch()} />
          </Card>
          <div className="max-w-2xl">
            <TeamIdForm key={id} initial={id} loading={isFetching} onSubmit={load} />
          </div>
        </div>
      )}

      {id != null && isLoading && (
        <div role="status" aria-busy="true" className="space-y-6">
          <span className="sr-only">Loading your team…</span>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-[30rem] w-full rounded-lg sm:h-[38rem]" />
        </div>
      )}

      {data && (
        <div className="space-y-8" aria-busy={isFetching}>
          <section aria-labelledby="my-team-facts" className="space-y-3">
            <SectionHeading
              as="h2"
              size="md"
              id="my-team-facts"
              title={data.entry.name}
              subtitle={`Managed by ${data.entry.manager} · team ID ${data.entry.id}`}
              action={
                <Button variant="ghost" size="sm" onClick={forget}>
                  Forget this ID
                </Button>
              }
            />
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard
                label="Overall points"
                value={bigNumber(data.entry.overall_points)}
                detail={data.entry.event_points != null && data.entry.last_gameweek ? `${data.entry.event_points} in GW${data.entry.last_gameweek}` : undefined}
                highlight
              />
              <StatCard label="Overall rank" value={bigNumber(data.entry.overall_rank)} />
              <StatCard label="Bank" value={money(data.bank)} />
              <StatCard label="Team value" value={money(data.team_value)} detail="Squad at current prices" />
              <StatCard
                label="Free transfers"
                value={String(data.free_transfers)}
                detail="Worked out from your history"
              />
              <StatCard label="Next gameweek" value={`GW${data.gameweek}`} detail={deadline ? `Deadline ${deadline}` : undefined} />
            </div>
            <p className="text-data text-fg-subtle">
              {data.based_on_gameweek != null
                ? `Your squad at the GW${data.based_on_gameweek} deadline; transfers you’ve made since show after the GW${data.gameweek} deadline.`
                : `Your squad as FPL last published it; transfers you’ve made since show after the GW${data.gameweek} deadline.`}
              {data.pending_transfers.length > 0 &&
                ` You have ${plural(data.pending_transfers.length, 'transfer')} pending for GW${data.gameweek}.`}
              {data.predictions_gameweek != null && data.predictions_gameweek !== data.gameweek &&
                ` The predictions below are for GW${data.predictions_gameweek}.`}
            </p>
          </section>

          <section aria-labelledby="my-team-xi">
            <SectionHeading
              as="h2"
              size="md"
              id="my-team-xi"
              title="Suggested team"
              subtitle="The XI, captain and bench our model would pick from your 15 players"
            />
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
              <div className="min-w-0">
                <MyTeamPitch squad={data.squad} lineup={data.lineup} />
              </div>
              <aside className="min-w-0 space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <StatCard
                    label="Projected points"
                    value={data.lineup.projected_points.toFixed(1)}
                    detail="Captain counted twice"
                    highlight
                  />
                  <StatCard label="Formation" value={data.lineup.formation} />
                  <StatCard
                    label="Captain"
                    value={captain ? <span className="text-h3">{captain.web_name || captain.name}</span> : '–'}
                    detail={captain?.predicted_points != null ? `${captain.predicted_points.toFixed(1)} pts predicted` : undefined}
                    className="col-span-2"
                  />
                </div>
                <MyTeamBench squad={data.squad} lineup={data.lineup} />
              </aside>
            </div>
          </section>

          <section aria-labelledby="my-team-squad" className="space-y-3">
            <SectionHeading
              as="h2"
              size="md"
              id="my-team-squad"
              title="Your squad"
              subtitle="Prices, selling prices and projected points for the next gameweeks. Tap a heading to sort."
            />
            <SquadTable squad={data.squad} captain={data.lineup.captain} vice={data.lineup.vice_captain} />
          </section>

          <TransferPlanner
            key={data.entry.id}
            teamId={data.entry.id}
            squad={data.squad}
            chips={data.chips}
            freeTransfers={data.free_transfers}
          />

          <section aria-labelledby="my-team-chips" className="space-y-3">
            <SectionHeading as="h2" size="md" id="my-team-chips" title="Chips" subtitle="One of each per half of the season" />
            {data.chips.length > 0 ? (
              <MyTeamChips chips={data.chips} nextGameweek={data.gameweek} />
            ) : (
              <Card>
                <EmptyState title="No chip information" className="py-6" />
              </Card>
            )}
          </section>

          <SeasonSoFar history={data.history} />
        </div>
      )}
    </Container>
  );
}
