import { useState, type MouseEvent } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Loader2, Trophy } from 'lucide-react';
import {
  apiService,
  handleApiError,
  type PlayerLeader,
  type PlayerLeaderSort,
  type PositionCode,
} from '../../services/api';
import { usePlayerAvatars } from '../../hooks/usePlayerAvatars';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { cn } from '../../utils/cn';
import { displayName } from '../../utils/playerNames';
import {
  Button,
  Card,
  ClubCrest,
  DataTable,
  EmptyState,
  ErrorState,
  PlayerAvatar,
  PositionBadge,
  SectionHeading,
  Select,
  StatusBadge,
  type Column,
} from '../ui';
import { SegmentedControl } from '../predictions/SegmentedControl';
import { PlayerFilters } from './PlayerFilters';

// 25 rows to start (50 made the page ~3,400px tall); "Show more" adds a page up to the API's max.
const PAGE_SIZE = 25;
const MAX_ROWS = 100;

export interface LeadersFilters {
  season: string;
  position: PositionCode | null;
  sort: PlayerLeaderSort;
  /** One club (graph name), or every club. */
  team: string | null;
  /** Highest average price that season, £m. */
  maxPrice: number | null;
}

type PositionFilter = 'ALL' | PositionCode;

const POSITION_OPTIONS: { value: PositionFilter; label: string; shortLabel: string }[] = [
  { value: 'ALL', label: 'All players', shortLabel: 'All' },
  { value: 'GK', label: 'Goalkeepers', shortLabel: 'GK' },
  { value: 'DEF', label: 'Defenders', shortLabel: 'DEF' },
  { value: 'MID', label: 'Midfielders', shortLabel: 'MID' },
  { value: 'FWD', label: 'Forwards', shortLabel: 'FWD' },
];

const SORT_LABELS: Record<PlayerLeaderSort, string> = {
  total_points: 'Total points',
  goals: 'Goals',
  assists: 'Assists',
  clean_sheets: 'Clean sheets',
  bonus: 'Bonus points',
  points_per_game: 'Points per game',
  minutes: 'Minutes',
};

interface SeasonLeadersProps {
  /** Seasons for the picker, newest first. */
  seasons: string[];
  filters: LeadersFilters;
  onFiltersChange: (filters: LeadersFilters) => void;
  /** Opens the player's profile for the season shown. */
  onSelectPlayer: (player: PlayerLeader, season: string) => void;
}

/**
 * Browsable "Season leaders" table for the Players page: the top players of a season
 * from SEASON_STATS, filtered by position and sorted on the server, so the ranking is
 * right across every player, not just the rows on screen.
 */
export function SeasonLeaders({ seasons, filters, onFiltersChange, onSelectPlayer }: SeasonLeadersProps) {
  const { season, position, sort, team, maxPrice } = filters;
  const [limit, setLimit] = useState(PAGE_SIZE);

  const leadersQuery = useQuery({
    queryKey: ['player-leaders', season, position, sort, limit, team, maxPrice],
    queryFn: ({ signal }) =>
      apiService.getPlayerLeaders(
        {
          season,
          position: position ?? undefined,
          sort,
          limit,
          team: team ?? undefined,
          max_price: maxPrice ?? undefined,
        },
        signal,
      ),
    staleTime: 5 * 60 * 1000,
    // Keep the old rows on screen while a new filter or sort loads.
    placeholderData: keepPreviousData,
  });

  const rows = leadersQuery.data?.players ?? [];
  const missingPhotos = usePlayerAvatars(rows.filter((r) => !r.photo).map((r) => r.name));
  const updating = leadersQuery.isFetching && leadersQuery.isPlaceholderData;

  const setFilter = (patch: Partial<LeadersFilters>) => {
    setLimit(PAGE_SIZE); // a new ranking starts from the top page again
    onFiltersChange({ ...filters, ...patch });
  };
  const canShowMore = rows.length >= limit && limit < MAX_ROWS;

  const numberColumn = (
    key: PlayerLeaderSort,
    header: string,
    description: string,
    hideBelow?: Column<PlayerLeader>['hideBelow'],
    format: (r: PlayerLeader) => string = (r) => String(r[key]),
  ): Column<PlayerLeader> => ({
    key,
    header,
    description,
    align: 'right',
    // On phones the active sort column always shows, so the ranking is never hidden.
    hideBelow: sort === key ? undefined : hideBelow,
    sortValue: (r) => r[key],
    render: (r) => <span className={cn(sort === key ? 'font-bold text-fg' : 'text-fg-muted')}>{format(r)}</span>,
  });

  const columns: Column<PlayerLeader>[] = [
    {
      key: 'rank',
      header: '#',
      description: 'Rank',
      align: 'center',
      className: 'w-8',
      render: (_r, i) => <span className="font-semibold text-fg-subtle">{i + 1}</span>,
    },
    {
      key: 'player',
      header: 'Player',
      sticky: true,
      render: (r) => (
        <LeaderPlayerCell
          row={r}
          photo={r.photo ?? missingPhotos[r.name]}
          onSelect={(e) => {
            e.stopPropagation(); // the row's own click handler would open it a second time
            onSelectPlayer(r, season);
          }}
        />
      ),
    },
    {
      key: 'total_points',
      header: 'Pts',
      description: 'Total points',
      align: 'right',
      sortValue: (r) => r.total_points,
      render: (r) => (
        <span className="font-display text-h3 font-bold tabular-nums text-accent">{r.total_points}</span>
      ),
    },
    {
      key: 'games',
      header: 'Pld',
      description: 'Matches played (minutes on the pitch)',
      align: 'right',
      hideBelow: 'sm',
      render: (r) => <span className="text-fg-muted">{r.games}</span>,
    },
    numberColumn('minutes', 'Mins', 'Minutes played', 'md', (r) => r.minutes.toLocaleString()),
    numberColumn('goals', 'G', 'Goals', 'sm'),
    numberColumn('assists', 'A', 'Assists', 'sm'),
    numberColumn('clean_sheets', 'CS', 'Clean sheets', 'sm'),
    numberColumn('bonus', 'Bonus', 'Bonus points', 'lg'),
    numberColumn('points_per_game', 'Pts/G', 'Points per game', 'md', (r) => r.points_per_game.toFixed(2)),
    {
      key: 'price',
      header: 'Price',
      description: 'Average price that season',
      align: 'right',
      hideBelow: 'lg',
      render: (r) => <span className="text-fg-muted">{r.price != null ? `£${r.price.toFixed(1)}m` : '–'}</span>,
    },
  ];

  const positionLabel = POSITION_OPTIONS.find((p) => p.value === (position ?? 'ALL'))?.label ?? 'All players';
  const scope = [positionLabel, team, maxPrice != null ? `up to £${maxPrice.toFixed(1)}m` : null]
    .filter(Boolean)
    .join(' · ');

  return (
    <section aria-labelledby="season-leaders-heading">
      <SectionHeading
        id="season-leaders-heading"
        title="Season leaders"
        subtitle={`${season} · ${scope} · top ${limit} by ${SORT_LABELS[sort].toLowerCase()}${
          sort === 'points_per_game' ? ' (min. 270 minutes)' : ''
        }`}
        action={
          updating ? (
            <span role="status" className="inline-flex items-center gap-1.5 text-micro text-fg-subtle">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Updating
            </span>
          ) : undefined
        }
      />

      <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <SegmentedControl
          label="Filter by position"
          value={(position ?? 'ALL') as PositionFilter}
          onChange={(v) => setFilter({ position: v === 'ALL' ? null : v })}
          options={POSITION_OPTIONS}
          className="self-start"
        />
        <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center sm:gap-3">
          <label className="flex min-w-0 items-center gap-2">
            <span className="text-micro font-semibold uppercase text-fg-muted">Season</span>
            <Select
              value={season}
              onChange={(e) => setFilter({ season: e.target.value })}
              className="min-w-0 flex-1 sm:w-32 sm:flex-none"
            >
              {seasons.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </label>
          {/* Phones hide most stat columns, so the sort lives in a picker there. */}
          <label className="flex min-w-0 items-center gap-2 sm:hidden">
            <span className="text-micro font-semibold uppercase text-fg-muted">Sort</span>
            <Select
              value={sort}
              onChange={(e) => setFilter({ sort: e.target.value as PlayerLeaderSort })}
              className="min-w-0 flex-1"
            >
              {(Object.keys(SORT_LABELS) as PlayerLeaderSort[]).map((key) => (
                <option key={key} value={key}>
                  {SORT_LABELS[key]}
                </option>
              ))}
            </Select>
          </label>
        </div>
      </div>

      <PlayerFilters
        value={{ team, maxPrice, q: '' }}
        onChange={(v) => setFilter({ team: v.team, maxPrice: v.maxPrice })}
        season={season}
        priceLabel="Max avg price"
        className="mb-3"
      />

            {leadersQuery.isError && !leadersQuery.data ? (
        <Card padded={false}>
          <ErrorState
            title="Couldn't load season leaders"
            description={handleApiError(leadersQuery.error)}
            onRetry={() => leadersQuery.refetch()}
          />
        </Card>
      ) : (
        <DataTable
          caption={`${season} season leaders, ${scope.toLowerCase()}, sorted by ${SORT_LABELS[sort].toLowerCase()}`}
          columns={columns}
          rows={rows}
          rowKey={(r) => r.name}
          sort={{ key: sort, direction: 'desc' }}
          // The server only ranks biggest-first; a second click on the active column keeps it.
          onSortChange={(next) => setFilter({ sort: next.key as PlayerLeaderSort })}
          onRowClick={(r) => onSelectPlayer(r, season)}
          loading={leadersQuery.isLoading}
          skeletonRows={10}
          dense
          className={cn('transition-opacity', updating && 'opacity-60')}
          empty={
            <EmptyState
              icon={<Trophy className="h-8 w-8" aria-hidden="true" />}
              title={team || maxPrice != null ? 'No players match these filters' : `No ${season} stats yet`}
              description="Pick another season, position, club or price."
              className="py-4"
            />
          }
        />
      )}
      {canShowMore && (
        <div className="mt-3 flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            loading={leadersQuery.isFetching}
            onClick={() => setLimit((n) => Math.min(n + PAGE_SIZE, MAX_ROWS))}
          >
            Show more
          </Button>
        </div>
      )}
    </section>
  );
}

function LeaderPlayerCell({
  row,
  photo,
  onSelect,
}: {
  row: PlayerLeader;
  photo: string | null | undefined;
  onSelect: (e: MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      // Narrow on phones so rank + player + Pts fit 390px without scrolling sideways
      className="flex w-full min-w-0 max-w-[10.5rem] items-center gap-2 rounded-sm text-left sm:max-w-[16rem]"
    >
      <PlayerAvatar name={row.name} src={photo} size={32} />
      <span className="min-w-0">
        <span className="block truncate font-semibold text-fg group-hover:text-accent" title={row.name}>
          {displayName(row.name, row.web_name)}
        </span>
        <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-micro text-fg-subtle">
          {row.team && <ClubCrest team={row.team} size={14} />}
          {/* The crest (with alt text) names the club on phones, leaving room for the status chip */}
          {row.team && <span className="hidden shrink-0 sm:inline">{getClubAbbreviation(row.team)}</span>}
          <PositionBadge position={row.position} className="w-8 shrink-0" />
          <StatusBadge status={row.status} chance={row.chance_of_playing_next_round} news={row.news} className="shrink-0" />
        </span>
      </span>
    </button>
  );
}
