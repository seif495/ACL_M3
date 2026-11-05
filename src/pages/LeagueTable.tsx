import { useQuery } from '@tanstack/react-query';
import { apiService, handleApiError, type TableRow } from '../services/api';
import { Card, ClubCrest, Container, DataTable, ErrorState, PageHeader, type Column } from '../components/ui';
import { getClubAbbreviation } from '../utils/clubLogos';
import { FormChips, TeamLink } from '../components/teams/MatchBits';
import { cn } from '../utils/cn';
import { usePageMeta } from '../hooks/usePageMeta';

// Season-end zones. Shown as a coloured bar beside the position, always with a legend.
function zone(position: number, total: number): 'ucl' | 'europe' | 'relegation' | null {
  if (position <= 4) return 'ucl';
  if (position <= 6) return 'europe';
  if (position > total - 3) return 'relegation';
  return null;
}

const ZONE_BAR = {
  ucl: 'bg-accent',
  europe: 'bg-accent-2',
  relegation: 'bg-danger',
} as const;

const ZONE_LABEL = {
  ucl: 'Champions League place',
  europe: 'European place',
  relegation: 'Relegation zone',
} as const;

const RESULT_STYLE = {
  W: 'bg-accent text-accent-on',
  D: 'bg-fg-subtle text-accent-on',
  L: 'bg-danger text-accent-on',
} as const;
const RESULT_WORD = { W: 'Win', D: 'Draw', L: 'Loss' } as const;

function Form({ results }: { results: TableRow['form'] }) {
  if (!results.length) return <span className="text-fg-subtle">–</span>;
  return (
    <span className="inline-flex gap-1">
      <span className="sr-only">Last {results.length}, oldest first: </span>
      {results.map((r, i) => (
        <span
          key={i}
          title={RESULT_WORD[r]}
          className={cn('inline-flex h-5 w-5 items-center justify-center rounded-full text-micro font-bold', RESULT_STYLE[r])}
        >
          <span aria-hidden="true">{r}</span>
          <span className="sr-only">{RESULT_WORD[r]}{i < results.length - 1 ? ', ' : ''}</span>
        </span>
      ))}
    </span>
  );
}

export function LeagueTable() {
  usePageMeta({ title: "Premier League table", description: "The 2026-27 Premier League table calculated from every finished match: points, goal difference, form and next fixture." });
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['league-table'],
    queryFn: () => apiService.getLeagueTable(),
    staleTime: 5 * 60_000,
  });
  const total = data?.rows.length ?? 20;

  const columns: Column<TableRow>[] = [
    {
      key: 'position',
      header: 'Pos',
      description: 'Position',
      align: 'center',
      className: 'w-12',
      render: (r) => {
        const z = zone(r.position, total);
        return (
          <span className="relative inline-flex w-6 justify-center font-semibold">
            {z && <span aria-hidden="true" className={cn('absolute -left-3 top-1/2 h-6 w-1 -translate-y-1/2 rounded-full', ZONE_BAR[z])} />}
            {r.position}
            {z && <span className="sr-only">, {ZONE_LABEL[z]}</span>}
          </span>
        );
      },
    },
    {
      key: 'team',
      header: 'Club',
      sticky: true,
      render: (r) => <TeamLink team={r.team} short />,
    },
    { key: 'played', header: 'Pl', description: 'Played', align: 'right' },
    { key: 'won', header: 'W', description: 'Won', align: 'right', hideBelow: 'sm' },
    { key: 'drawn', header: 'D', description: 'Drawn', align: 'right', hideBelow: 'sm' },
    { key: 'lost', header: 'L', description: 'Lost', align: 'right', hideBelow: 'sm' },
    { key: 'goals_for', header: 'GF', description: 'Goals for', align: 'right', hideBelow: 'md' },
    { key: 'goals_against', header: 'GA', description: 'Goals against', align: 'right', hideBelow: 'md' },
    {
      key: 'goal_difference',
      header: 'GD',
      description: 'Goal difference',
      align: 'right',
      render: (r) => (r.goal_difference > 0 ? `+${r.goal_difference}` : r.goal_difference),
    },
    {
      key: 'points',
      header: 'Pts',
      description: 'Points',
      align: 'right',
      render: (r) => <span className="font-bold text-fg">{r.points}</span>,
    },
    {
      key: 'form',
      header: 'Form',
      hideBelow: 'lg',
      // Each chip opens its match page; older backends send only the letters.
      render: (r) => (r.recent?.length ? <FormChips items={r.recent} size="sm" /> : <Form results={r.form} />),
    },
    {
      key: 'next',
      header: 'Next',
      description: 'Next fixture',
      hideBelow: 'lg',
      render: (r) =>
        r.next_fixture ? (
          <span className="inline-flex items-center gap-1.5" title={`${r.next_fixture.opponent} (${r.next_fixture.home ? 'home' : 'away'})`}>
            <ClubCrest team={r.next_fixture.opponent} size={18} />
            <span className="text-data text-fg-muted">
              {getClubAbbreviation(r.next_fixture.opponent) || r.next_fixture.opponent} ({r.next_fixture.home ? 'H' : 'A'})
            </span>
          </span>
        ) : (
          <span className="text-fg-subtle">–</span>
        ),
    },
  ];

  return (
    <Container>
      <PageHeader
        eyebrow={data?.season ?? 'This season'}
        title="Premier League table"
        description="Calculated from every finished match this season: 3 points for a win, 1 for a draw."
      />

      {isError ? (
        <Card>
          <ErrorState title="Couldn't load the table" description={handleApiError(error)} onRetry={() => refetch()} />
        </Card>
      ) : (
        <>
          <DataTable
            caption="League table"
            columns={columns}
            rows={data?.rows ?? []}
            rowKey={(r) => r.team}
            loading={isLoading}
            skeletonRows={20}
            empty="No matches have been played yet."
          />
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-data text-fg-subtle" aria-label="Table zones">
            <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-accent" />Champions League places</li>
            <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-accent-2" />Europa / Conference League places</li>
            <li className="flex items-center gap-2"><span aria-hidden="true" className="h-3 w-1 rounded-full bg-danger" />Relegation</li>
          </ul>
        </>
      )}
    </Container>
  );
}
