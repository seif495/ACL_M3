import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { apiService, handleApiError, type SeasonFixture } from '../services/api';
import {
  Button,
  Card,
  Container,
  EmptyState,
  ErrorState,
  FixtureRow,
  PageHeader,
  Select,
  SkeletonRows,
  TabPanel,
  Tabs,
  type FixtureStatus,
} from '../components/ui';
import { usePageMeta } from '../hooks/usePageMeta';
import { matchPath } from '../utils/clubColors';
import { getGameweekScorers, type FixtureScorer } from '../services/scorersApi';
import { DEFAULT_TICKER_LENGTH, FixtureTicker } from '../components/fixtures/FixtureTicker';

type FixturesView = 'gameweek' | 'ticker';

const STATUS: Record<SeasonFixture['status'], FixtureStatus> = {
  scheduled: 'scheduled',
  in_progress: 'live',
  finished: 'finished',
};

const toRowScorers = (list: FixtureScorer[]) => list.map((s) => ({ name: s.name, webName: s.web_name, kind: s.kind, minute: s.minute, extra: s.extra, count: s.count }));

function dayKey(iso: string | null): string {
  if (!iso) return 'tbc';
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(iso: string | null): string {
  if (!iso) return 'Date to be confirmed';
  return new Date(iso).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
}

/** Fixtures in kickoff order, grouped by local calendar day. */
function groupByDay(fixtures: SeasonFixture[]) {
  const groups: { key: string; label: string; fixtures: SeasonFixture[] }[] = [];
  for (const f of fixtures) {
    const key = dayKey(f.kickoff_time);
    let group = groups.find((g) => g.key === key);
    if (!group) {
      group = { key, label: dayLabel(f.kickoff_time), fixtures: [] };
      groups.push(group);
    }
    group.fixtures.push(f);
  }
  return groups;
}

export function Fixtures() {
  usePageMeta({ title: "Fixtures & results", description: "Every Premier League fixture and result of the 2026-27 season by gameweek, with kick-off times in your time zone." });
  const [params, setParams] = useSearchParams();
  const requested = Number(params.get('gw')) || undefined;
  const view: FixturesView = params.get('view') === 'ticker' ? 'ticker' : 'gameweek';
  const [tickerLength, setTickerLength] = useState<number>(DEFAULT_TICKER_LENGTH);
  const changeView = (next: FixturesView) => {
    const p = new URLSearchParams(params);
    if (next === 'ticker') p.set('view', 'ticker');
    else p.delete('view');
    setParams(p, { replace: true });
  };

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['fixtures', requested ?? 'default'],
    queryFn: () => apiService.getFixtures(requested),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

  const gameweeks = data?.gameweeks ?? [];
  const gw = data?.gameweek;
  const anyFinished = (data?.fixtures ?? []).some((f) => f.status === 'finished');
  // Scorers are a nicety: a failure just leaves the scores bare
  const { data: scorers } = useQuery({
    queryKey: ['gameweek-scorers', gw],
    queryFn: ({ signal }) => getGameweekScorers(gw as number, signal),
    enabled: !!gw && anyFinished && view === 'gameweek',
    staleTime: 5 * 60_000,
    retry: false,
  });
  const index = gw ? gameweeks.indexOf(gw) : -1;
  const groups = useMemo(() => groupByDay(data?.fixtures ?? []), [data]);

  const go = (target: number | undefined) => {
    if (!target) return;
    setParams({ gw: String(target) }, { replace: false });
  };

  return (
    // A centred column: rows are centred on the score, so a full-width card left them stranded
    <Container className="max-w-4xl">
      <PageHeader
        eyebrow={data?.season ?? 'This season'}
        title="Fixtures & results"
        description="Every match of the season, with final scores once games finish. Kick-off times are shown in your local time zone."
      />

      <Tabs
        label="Fixture views"
        idPrefix="fixtures"
        value={view}
        onChange={changeView}
        items={[
          { value: 'gameweek', label: 'By gameweek' },
          { value: 'ticker', label: 'Difficulty ticker' },
        ]}
        className="mb-4"
      />

      <TabPanel idPrefix="fixtures" value={view}>
      {view === 'ticker' ? (
        <FixtureTicker length={tickerLength} onLengthChange={setTickerLength} />
      ) : (
      <>
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
          <Select
            value={gw ?? ''}
            onChange={(e) => go(Number(e.target.value))}
            disabled={!gameweeks.length}
            className="w-44"
          >
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
            <ErrorState title="Couldn't load fixtures" description={handleApiError(error)} onRetry={() => refetch()} />
          </Card>
        ) : groups.length === 0 ? (
          <Card>
            <EmptyState title="No fixtures" description="There are no matches scheduled for this gameweek." />
          </Card>
        ) : (
          groups.map((group) => (
            <Card key={group.key} padded={false}>
              <h2 className="border-b border-line bg-surface-2 px-4 py-2 font-display text-h3 font-semibold text-fg">
                {group.label}
              </h2>
              {group.fixtures.map((f) => (
                <FixtureRow
                  key={f.id}
                  home={f.home_team}
                  away={f.away_team}
                  homeScore={f.home_score}
                  awayScore={f.away_score}
                  kickoff={f.kickoff_time}
                  status={STATUS[f.status]}
              to={matchPath(f.id)}
                  scorers={scorers?.[f.id] ? { home: toRowScorers(scorers[f.id].home), away: toRowScorers(scorers[f.id].away) } : null}
                />
              ))}
            </Card>
          ))
        )}
      </div>
      </>
      )}
      </TabPanel>
    </Container>
  );
}
