import { useState, type FormEvent } from 'react';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { Info, Lightbulb, RefreshCw, Sparkles } from 'lucide-react';
import { apiService, handleApiError, MLPrediction } from '../services/api';
import { useAppStore } from '../store/appStore';
import { cn } from '../utils/cn';
import { displayName } from '../utils/playerNames';
import { getClubAbbreviation } from '../utils/clubLogos';
import { PlayerWithClub } from '../components/PlayerWithClub';
import { usePlayerAvatars } from '../hooks/usePlayerAvatars';
import {
  Button,
  ButtonLink,
  Card,
  Chip,
  ClubCrest,
  Container,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageHeader,
  PositionBadge,
  SectionHeading,
  StatusBadge,
  TabPanel,
  Tabs,
  type Column,
} from '../components/ui';
import { SegmentedControl } from '../components/predictions/SegmentedControl';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import { PlayerFilters } from '../components/players/PlayerFilters';
import { EMPTY_PLAYER_FILTERS, hasPlayerFilters, useDebounced, type PlayerFilterValues } from '../hooks/usePlayerFilters';
import type { PlayerHint } from '../components/players/PlayerProfileView';

type PositionFilter = 'ALL' | 'GK' | 'DEF' | 'MID' | 'FWD';

// Rows per page, and the API's most (limit is capped at 50).
const PAGE_SIZE = 20;
const MAX_ROWS = 50;
type View = 'top' | 'value';

const POSITIONS: { value: PositionFilter; label: string; shortLabel: string }[] = [
  { value: 'ALL', label: 'All players', shortLabel: 'All' },
  { value: 'GK', label: 'Goalkeepers', shortLabel: 'GK' },
  { value: 'DEF', label: 'Defenders', shortLabel: 'DEF' },
  { value: 'MID', label: 'Midfielders', shortLabel: 'MID' },
  { value: 'FWD', label: 'Forwards', shortLabel: 'FWD' },
];

/** Prediction rows also carry fields the shared type doesn't declare. */
type PredictionRow = MLPrediction & {
  rank: number;
  position?: string;
  value?: number;
  cost?: number;
  value_score?: number;
};

// Format match date to readable local timezone format
function formatMatchDate(dateString: string) {
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return dateString;
  return date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

/** Best-value rows: the CSV endpoint sends price as `value` + `value_score`; the fallback sends `cost` + `value`. */
function priceOf(r: PredictionRow): number | undefined {
  if (typeof r.cost === 'number') return r.cost;
  if (typeof r.value_score === 'number') return r.value;
  return undefined;
}
function valueScoreOf(r: PredictionRow): number | undefined {
  if (typeof r.value_score === 'number') return r.value_score;
  if (typeof r.cost === 'number') return r.value;
  return undefined;
}

function Opponent({ fixture }: { fixture: MLPrediction['fixture_context'] }) {
  if (!fixture) return <span className="text-fg-subtle">No fixture</span>;
  const home = fixture.home_away === 'home';
  const abbr = getClubAbbreviation(fixture.opponent_team) || fixture.opponent_team;
  return (
    <span className="inline-flex items-center gap-1.5" title={`${fixture.opponent_team} (${home ? 'home' : 'away'})`}>
      <ClubCrest team={fixture.opponent_team} size={18} />
      <span className="font-semibold">{abbr}</span>
      <span className="text-fg-muted">
        <span aria-hidden="true">({home ? 'H' : 'A'})</span>
        <span className="sr-only">{home ? 'at home' : 'away'}</span>
      </span>
    </span>
  );
}

const hintOf = (row: PredictionRow, avatar: string | null | undefined): PlayerHint => ({
  name: row.player_name,
  team: row.team_name,
  position: row.position,
  photo: avatar,
  status: row.status,
  news: row.news,
  chance: row.chance_of_playing_next_round,
});

function PlayerCell({
  row,
  avatar,
  onOpen,
}: {
  row: PredictionRow;
  avatar: string | null | undefined;
  onOpen?: (row: PredictionRow) => void;
}) {
  const unavailable = row.status && row.status !== 'a';
  return (
    // Narrow on phones so rank + player + xPts fit 390px without hiding the points off-screen
    <span className="block max-w-[11.5rem] sm:max-w-[18rem]">
      {onOpen && (
        // The row is clickable with a mouse; this is the keyboard way in (and names the action).
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen(row);
          }}
          className="sr-only focus:not-sr-only focus:mb-1 focus:inline-block focus:rounded-sm focus:text-micro focus:text-accent"
        >
          Show {row.player_name}&apos;s stats
        </button>
      )}
      <PlayerWithClub
        playerName={row.player_name}
        displayName={displayName(row.player_name, row.web_name)}
        teamName={row.team_name}
        avatar={avatar}
        avatarSize={36}
        meta={
          <>
            <PositionBadge position={row.position} className="w-8" />
            <StatusBadge status={row.status} chance={row.chance_of_playing_next_round} news={row.news} />
          </>
        }
      />
      {unavailable && row.news && (
        <span className="mt-0.5 block truncate pl-[46px] text-micro text-warning" title={row.news} aria-hidden="true">
          {row.news}
        </span>
      )}
    </span>
  );
}

function PlayerLookup() {
  const [name, setName] = useState('');
  const [missingName, setMissingName] = useState(false);
  const lookup = useMutation({
    mutationFn: (playerName: string) => apiService.predictPlayer(playerName),
  });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setMissingName(true); // explain instead of a silently disabled button
      return;
    }
    lookup.mutate(name.trim());
  };
  const result = lookup.data;
  const lookupAvatars = usePlayerAvatars(result ? [result.player_name] : []);

  return (
    <Card>
      <SectionHeading as="h2" size="md" title="Player lookup" subtitle="Predicted points for one player" />
      <form onSubmit={onSubmit} noValidate className="flex items-start gap-2">
        <Field label="Player name" className="min-w-0 flex-1" error={missingName ? 'Type a player name first.' : undefined}>
          {(id, a11y) => (
            <Input
              id={id}
              {...a11y}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setMissingName(false);
              }}
              placeholder="e.g. Haaland"
              autoComplete="off"
              maxLength={100}
            />
          )}
        </Field>
        {/* mt-5 lines the button up with the input, below the field label */}
        <Button type="submit" className="mt-5" loading={lookup.isPending} disabled={lookup.isPending}>
          Predict
        </Button>
      </form>

      <div aria-live="polite" className="mt-3">
        {lookup.isError && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-data text-danger">
            {handleApiError(lookup.error)}
          </p>
        )}
        {result && !lookup.isError && (
          <div className="rounded-md border border-line bg-surface-2 p-3">
            <div className="flex items-center justify-between gap-3">
              <PlayerWithClub
                playerName={result.player_name}
                teamName={result.team_name}
                avatar={lookupAvatars[result.player_name]}
                avatarSize={36}
                meta={
                  <StatusBadge status={result.status} chance={result.chance_of_playing_next_round} news={result.news} />
                }
                className="min-w-0"
              />
              <span className="shrink-0 text-right">
                <span className="block font-display text-h2 font-bold tabular-nums text-accent">
                  {result.predicted_points.toFixed(1)}
                </span>
                <span className="block text-micro uppercase text-fg-subtle">Pred. pts</span>
              </span>
            </div>
            {result.fixture_context && (
              <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line pt-2 text-data text-fg-muted">
                <span>GW{result.fixture_context.matchday} vs</span>
                <Opponent fixture={result.fixture_context} />
                <span className="text-fg-subtle">· {formatMatchDate(result.fixture_context.match_date)}</span>
              </p>
            )}
            {result.news && <p className="mt-2 text-data text-warning">{result.news}</p>}
          </div>
        )}
      </div>
    </Card>
  );
}

export function Predictions() {
  usePageMeta({ title: "Predictions", description: "Expected FPL points for every player next gameweek from a machine-learning model trained on every season since 2020-21, adjusted for availability." });
  const { mlAvailable, mlModelType } = useAppStore();
  const [selectedPosition, setSelectedPosition] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View>('top');
  const [filters, setFilters] = useState<PlayerFilterValues>(EMPTY_PLAYER_FILTERS);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const q = useDebounced(filters.q.trim());
  const apiFilters = { team: filters.team ?? undefined, max_price: filters.maxPrice ?? undefined, q: q || undefined };
  const dialog = usePlayerDialog();
  const changeFilters = (next: PlayerFilterValues) => {
    setFilters(next);
    setLimit(PAGE_SIZE);
  };

  const {
    data: topPerformers,
    isLoading: loadingTop,
    isError: errorTop,
    error: topError,
    isFetching: fetchingTop,
    refetch: refetchTop,
  } = useQuery({
    queryKey: ['ml', 'top-performers', selectedPosition, limit, apiFilters],
    queryFn: () => apiService.predictTopPerformers(selectedPosition, limit, apiFilters),
    placeholderData: keepPreviousData,
    enabled: mlAvailable,
    retry: 1,
    staleTime: 15 * 60 * 1000, // precomputed predictions; manual Refresh still refetches
  });

  const {
    data: bestValue,
    isLoading: loadingValue,
    isError: errorValue,
    error: valueError,
    isFetching: fetchingValue,
    refetch: refetchValue,
  } = useQuery({
    queryKey: ['ml', 'best-value', selectedPosition, limit, apiFilters],
    queryFn: () => apiService.predictBestValue(selectedPosition, limit, apiFilters),
    placeholderData: keepPreviousData,
    enabled: mlAvailable,
    retry: 1,
    staleTime: 15 * 60 * 1000,
  });

  const { data: season } = useQuery({
    queryKey: ['season-current'],
    queryFn: () => apiService.getCurrentSeason(),
    staleTime: 5 * 60_000,
    enabled: mlAvailable,
  });

  const handleRefresh = () => {
    refetchTop();
    refetchValue();
  };

  const topRows: PredictionRow[] = (topPerformers?.predictions ?? []).map((p, i) => ({ ...(p as PredictionRow), rank: i + 1 }));
  const valueRows: PredictionRow[] = (bestValue?.predictions ?? []).map((p, i) => ({ ...(p as PredictionRow), rank: i + 1 }));

  const avatarByName = usePlayerAvatars([...topRows, ...valueRows].map((r) => r.player_name));

  const metaGw = topPerformers?.metadata?.gameweek ?? bestValue?.metadata?.gameweek;
  const gameweek: number | undefined =
    (typeof metaGw === 'number' ? metaGw : undefined) ??
    topRows.find((r) => r.fixture_context)?.fixture_context?.matchday ??
    season?.next_gameweek ??
    undefined;
  const deadline = season?.next_deadline ? new Date(season.next_deadline) : null;

  if (!mlAvailable) {
    return (
      <Container>
        <PageHeader eyebrow="Model forecasts" title="Predictions" />
        <Card padded={false}>
          <EmptyState
            icon={<Info className="h-8 w-8" aria-hidden="true" />}
            title="Prediction engine offline"
            description="The machine learning prediction engine is currently unavailable. This might be because the models are still training or the backend is starting up. You can still ask the assistant about historical data and current statistics."
          />
        </Card>
      </Container>
    );
  }

  const rankColumn: Column<PredictionRow> = {
    key: 'rank',
    header: '#',
    description: 'Rank',
    align: 'center',
    className: 'w-10',
    sortValue: (r) => r.rank,
    render: (r) => <span className="font-semibold text-fg-subtle">{r.rank}</span>,
  };
  const playerColumn: Column<PredictionRow> = {
    key: 'player',
    header: 'Player',
    sticky: true,
    sortValue: (r) => displayName(r.player_name, r.web_name),
    render: (r) => (
      <PlayerCell
        row={r}
        avatar={avatarByName[r.player_name]}
        onOpen={dialog ? (row) => dialog.openPlayer(hintOf(row, avatarByName[row.player_name])) : undefined}
      />
    ),
  };
  const opponentColumn: Column<PredictionRow> = {
    key: 'opponent',
    header: 'Fixture',
    render: (r) => <Opponent fixture={r.fixture_context} />,
  };
  const kickoffColumn: Column<PredictionRow> = {
    key: 'kickoff',
    header: 'Kickoff',
    hideBelow: 'md',
    sortValue: (r) => (r.fixture_context ? new Date(r.fixture_context.match_date).getTime() : null),
    render: (r) => (
      <span className="text-fg-muted">{r.fixture_context ? formatMatchDate(r.fixture_context.match_date) : '–'}</span>
    ),
  };
  const pointsColumn = (highlight: boolean): Column<PredictionRow> => ({
    key: 'predicted_points',
    header: 'xPts',
    description: 'Predicted points next gameweek',
    align: 'right',
    sortValue: (r) => r.predicted_points,
    render: (r) => (
      <>
        <span className={cn('tabular-nums', highlight ? 'font-display text-h3 font-bold text-accent' : 'font-semibold text-fg')}>
          {r.predicted_points.toFixed(1)}
        </span>
        {highlight && r.fixture_context && (
          // The fixture column is hidden on phones; keep the opponent visible next to the points.
          <span className="block text-micro font-normal text-fg-subtle sm:hidden">
            {getClubAbbreviation(r.fixture_context.opponent_team) || r.fixture_context.opponent_team} (
            {r.fixture_context.home_away === 'home' ? 'H' : 'A'})
          </span>
        )}
      </>
    ),
  });

  const priceColumn = (hideBelow?: Column<PredictionRow>['hideBelow']): Column<PredictionRow> => ({
    key: 'price',
    header: 'Price',
    align: 'right',
    hideBelow,
    sortValue: (r) => priceOf(r),
    render: (r) => {
      const price = priceOf(r);
      return price != null ? `£${price.toFixed(1)}m` : '–';
    },
  });
  const topColumns: Column<PredictionRow>[] = [
    rankColumn,
    playerColumn,
    { ...opponentColumn, hideBelow: 'sm' },
    kickoffColumn,
    priceColumn('md'),
    pointsColumn(true),
  ];
  const valueColumns: Column<PredictionRow>[] = [
    rankColumn,
    playerColumn,
    { ...opponentColumn, hideBelow: 'sm' },
    kickoffColumn,
    priceColumn(),
    { ...pointsColumn(false), hideBelow: 'sm' },
    {
      key: 'value_score',
      header: 'Pts/£m',
      description: 'Predicted points per million',
      align: 'right',
      sortValue: (r) => valueScoreOf(r),
      render: (r) => {
        const score = valueScoreOf(r);
        return (
          <span className="font-display text-h3 font-bold tabular-nums text-accent">{score != null ? score.toFixed(2) : '–'}</span>
        );
      },
    },
  ];

  const positionLabel = POSITIONS.find((p) => p.value === (selectedPosition ?? 'ALL'))?.label.toLowerCase() ?? 'players';

  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow={
          <>
            {gameweek ? `Gameweek ${gameweek}` : 'Next gameweek'}
            {deadline && (
              <span className="ml-2 font-normal normal-case text-fg-muted">
                · Deadline {deadline.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
          </>
        }
        title="Predictions"
        description="Expected FPL points for the next gameweek from our machine-learning model, trained on every season since 2020-21 and adjusted for each player's chance of playing."
        actions={
          <>
            <Chip tone="brand" icon={<Sparkles className="h-3 w-3 text-accent" aria-hidden="true" />} className="h-8 px-2.5">
              Model: {mlModelType || 'Gradient-boosted trees'}
            </Chip>
            <ButtonLink to="/model" variant="secondary" size="sm" icon={<Lightbulb className="h-4 w-4" aria-hidden="true" />}>
              Why these numbers?
            </ButtonLink>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleRefresh}
              icon={<RefreshCw className={cn('h-4 w-4', (fetchingTop || fetchingValue) && 'animate-spin')} aria-hidden="true" />}
            >
              Refresh
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0">
          <Tabs
            label="Prediction views"
            idPrefix="predictions"
            value={view}
            onChange={setView}
            items={[
              { value: 'top', label: 'Top performers' },
              { value: 'value', label: 'Best value' },
            ]}
          />

          <div className="mt-4 flex flex-col gap-3">
            <SegmentedControl
              label="Filter by position"
              value={(selectedPosition ?? 'ALL') as PositionFilter}
              onChange={(v) => {
                setSelectedPosition(v === 'ALL' ? undefined : v);
                setLimit(PAGE_SIZE);
              }}
              options={POSITIONS}
              className="self-start"
            />
            <PlayerFilters value={filters} onChange={changeFilters} search />
          </div>

          <TabPanel idPrefix="predictions" value={view} className="mt-4">
            {view === 'top' ? (
              errorTop ? (
                <Card padded={false}>
                  <ErrorState
                    title="No predictions found for this selection"
                    description={handleApiError(topError)}
                    onRetry={() => refetchTop()}
                  />
                </Card>
              ) : (
                <DataTable
                  key="top"
                  caption={`Highest predicted scorers, ${positionLabel}${gameweek ? `, gameweek ${gameweek}` : ''}`}
                  columns={topColumns}
                  rows={topRows}
                  rowKey={(r) => r.player_name}
                  loading={loadingTop}
                  skeletonRows={12}
                  initialSort={{ key: 'predicted_points', direction: 'desc' }}
                  onRowClick={dialog ? (r) => dialog.openPlayer(hintOf(r, avatarByName[r.player_name])) : undefined}
                  className={cn('transition-opacity', fetchingTop && !loadingTop && 'opacity-60')}
                  empty={hasPlayerFilters(filters) ? 'No players match these filters.' : 'No predictions for this position.'}
                />
              )
            ) : errorValue ? (
              <Card padded={false}>
                <ErrorState title="No value picks found" description={handleApiError(valueError)} onRetry={() => refetchValue()} />
              </Card>
            ) : (
              <>
                <DataTable
                  key="value"
                  caption={`Best value picks by predicted points per million, ${positionLabel}`}
                  columns={valueColumns}
                  rows={valueRows}
                  rowKey={(r) => r.player_name}
                  loading={loadingValue}
                  skeletonRows={6}
                  initialSort={{ key: 'value_score', direction: 'desc' }}
                  onRowClick={dialog ? (r) => dialog.openPlayer(hintOf(r, avatarByName[r.player_name])) : undefined}
                  className={cn('transition-opacity', fetchingValue && !loadingValue && 'opacity-60')}
                  empty={hasPlayerFilters(filters) ? 'No players match these filters.' : 'No value picks found.'}
                />
                <p className="mt-3 text-data text-fg-subtle">
                  Ranked by predicted points per £1m of price: high predicted points at a low cost stretch your budget further.
                </p>
              </>
            )}
          </TabPanel>
          {(view === 'top' ? topRows : valueRows).length >= limit && limit < MAX_ROWS && (
            <div className="mt-3 flex justify-center">
              <Button
                variant="secondary"
                size="sm"
                loading={view === 'top' ? fetchingTop : fetchingValue}
                onClick={() => setLimit((n) => Math.min(n + PAGE_SIZE, MAX_ROWS))}
              >
                Show more
              </Button>
            </div>
          )}
        </div>

        <aside className="space-y-4">
          <PlayerLookup />
          <Card>
            <SectionHeading as="h2" size="md" title="How to read this" />
            <ul className="space-y-2 text-data text-fg-muted">
              <li>
                <span className="font-semibold text-fg">xPts</span> is the model's expected points for the gameweek. Double gameweeks are summed.
              </li>
              <li>Injured and suspended players are predicted 0; doubtful players are scaled by their chance of playing.</li>
              <li>Official FPL team news for doubtful or unavailable players is shown under their name.</li>
            </ul>
          </Card>
        </aside>
      </div>
    </Container>
  );
}
