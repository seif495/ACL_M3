import { Link } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { apiService, handleApiError, type TickerClub, type TickerFixture } from '../../services/api';
import { Card, ClubCrest, EmptyState, ErrorState, FdrBadge, Select, SkeletonRows } from '../ui';
import { cn } from '../../utils/cn';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { matchPath, teamPath } from '../../utils/clubColors';

export const TICKER_LENGTHS = [3, 6, 8, 10] as const;
export const DEFAULT_TICKER_LENGTH = 6;

const cellLabel = (gw: number, f: TickerFixture) =>
  `GW${gw}: ${f.opponent} ${f.home ? 'at home' : 'away'}, ${f.difficulty != null ? `difficulty ${f.difficulty}` : 'difficulty unknown'}`;

function TickerChip({ gw, fixture }: { gw: number; fixture: TickerFixture }) {
  const abbr = getClubAbbreviation(fixture.opponent) || fixture.opponent;
  const text = (
    <>
      <span aria-hidden="true">
        {abbr} ({fixture.home ? 'H' : 'A'})
      </span>
      <span className="sr-only">{cellLabel(gw, fixture)}</span>
    </>
  );
  const chip =
    fixture.difficulty != null ? (
      <FdrBadge difficulty={fixture.difficulty} className="w-full whitespace-nowrap">
        {text}
      </FdrBadge>
    ) : (
      <span className="inline-flex w-full min-w-[2rem] items-center justify-center whitespace-nowrap rounded-sm bg-surface-2 px-1.5 py-0.5 text-micro font-bold text-fg-muted">
        {text}
      </span>
    );
  return (
    <Link to={matchPath(fixture.fixture_id)} className="block rounded-sm hover:opacity-80 focus-visible:outline-offset-2">
      {chip}
    </Link>
  );
}

function TickerTable({ clubs, gameweeks }: { clubs: TickerClub[]; gameweeks: number[] }) {
  return (
    <div className="relative overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">
          Fixture difficulty for gameweeks {gameweeks[0]} to {gameweeks[gameweeks.length - 1]}, easiest run first
        </caption>
        <thead>
          <tr className="border-b border-line bg-surface-2">
            <th
              scope="col"
              className="sticky left-0 z-[1] whitespace-nowrap bg-surface-2 px-3 py-2.5 text-left text-micro font-semibold uppercase text-fg-subtle"
            >
              Club
            </th>
            {gameweeks.map((gw) => (
              <th key={gw} scope="col" className="whitespace-nowrap px-1.5 py-2.5 text-center text-micro font-semibold uppercase text-fg-subtle">
                GW{gw}
              </th>
            ))}
            <th
              scope="col"
              title="Average difficulty of the fixtures in this run"
              className="whitespace-nowrap px-3 py-2.5 text-right text-micro font-semibold uppercase text-fg-subtle"
            >
              Avg
            </th>
          </tr>
        </thead>
        <tbody>
          {clubs.map((club) => (
            <tr key={club.team} className="group border-b border-line last:border-b-0">
              <th
                scope="row"
                className="sticky left-0 z-[1] bg-surface px-3 py-2 text-left font-normal group-hover:bg-surface-2"
              >
                <Link to={teamPath(club.team)} className="flex items-center gap-2 whitespace-nowrap font-semibold text-fg hover:text-accent">
                  <ClubCrest team={club.team} size={20} />
                  {club.team}
                </Link>
              </th>
              {gameweeks.map((gw) => {
                const fixtures = club.gameweeks.find((g) => g.gameweek === gw)?.fixtures ?? [];
                return (
                  <td key={gw} className="min-w-[4.25rem] px-1 py-1.5 align-middle">
                    {fixtures.length === 0 ? (
                      <span className="block text-center text-fg-subtle">
                        <span aria-hidden="true">–</span>
                        <span className="sr-only">GW{gw}: no fixture</span>
                      </span>
                    ) : (
                      <div className="flex flex-col gap-1">
                        {fixtures.map((f) => (
                          <TickerChip key={f.fixture_id} gw={gw} fixture={f} />
                        ))}
                      </div>
                    )}
                  </td>
                );
              })}
              <td className="whitespace-nowrap px-3 py-2 text-right font-semibold tabular-nums text-fg">
                {club.average_difficulty != null ? club.average_difficulty.toFixed(2) : '–'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Each club's next run of fixtures coloured by FPL's difficulty, easiest run first. */
export function FixtureTicker({ length, onLengthChange }: { length: number; onLengthChange: (n: number) => void }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['fixtures', 'ticker', length],
    queryFn: () => apiService.getFixtureTicker(length),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="min-w-0 text-data text-fg-subtle">
          Difficulty is FPL&apos;s rating from 1 (easy) to 5 (hard). Clubs are ranked by their run, easiest first.
        </p>
        <label className="flex items-center gap-2 text-data text-fg-muted">
          <span>Show</span>
          <Select value={length} onChange={(e) => onLengthChange(Number(e.target.value))} className="w-36">
            {TICKER_LENGTHS.map((n) => (
              <option key={n} value={n}>
                {n} gameweeks
              </option>
            ))}
          </Select>
        </label>
      </div>

      <div aria-busy={isFetching}>
        {isLoading ? (
          <Card>
            <SkeletonRows rows={10} />
          </Card>
        ) : isError ? (
          <Card>
            <ErrorState title="Couldn't load the difficulty ticker" description={handleApiError(error)} onRetry={() => refetch()} />
          </Card>
        ) : !data || data.clubs.length === 0 || data.gameweeks.length === 0 ? (
          <Card>
            <EmptyState title="No upcoming fixtures" description="There are no fixtures to rate yet." />
          </Card>
        ) : (
          <div className={cn('transition-opacity', isFetching && 'opacity-60')}>
            <TickerTable clubs={data.clubs} gameweeks={data.gameweeks} />
          </div>
        )}
      </div>
    </div>
  );
}
