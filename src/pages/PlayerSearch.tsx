import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ChevronRight, ExternalLink, Search, Sparkles, UserX } from 'lucide-react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import { apiService, handleApiError, type PlayerLeader, type PlayerRow } from '../services/api';
import { PlayerWithClub } from '../components/PlayerWithClub';
import { SeasonLeaders, type LeadersFilters } from '../components/players/SeasonLeaders';
import { usePlayerAvatars } from '../hooks/usePlayerAvatars';
import {
  Button,
  ButtonLink,
  Card,
  Chip,
  Container,
  EmptyState,
  ErrorState,
  PageHeader,
  PositionBadge,
  SearchInput,
  SkeletonRows,
  StatusBadge,
} from '../components/ui';
import { PlayerProfileView } from '../components/players/PlayerProfileView';
import { PLAYER_SEASONS } from '../components/players/seasons';
import { usePageMeta } from '../hooks/usePageMeta';
import { useExamplePlayers } from '../hooks/useExamplePlayers';
import { playerPath } from '../utils/playerPath';


const DEFAULT_LEADERS_FILTERS: LeadersFilters = {
  season: PLAYER_SEASONS[0],
  position: null,
  sort: 'total_points',
  team: null,
  maxPrice: null,
};

type PlayerResult = PlayerRow;
type PlayerSearchRouteState = {
  player?: PlayerResult;
  season?: string;
  returnTo?: 'history' | 'results';
  compare?: {
    player1?: string;
    player2?: string;
    season?: string;
  };
};
/** Where the open profile came from, which decides what "Back" does. */
type DetailOrigin = 'history' | 'results' | 'leaders';

/** A season-leaders row in the shape the profile view reads from a search result. */
function leaderToPlayer(leader: PlayerLeader): PlayerResult {
  return {
    player_name: leader.name,
    team_name: leader.team ?? undefined,
    position: leader.position ?? undefined,
    avatar: leader.photo ?? undefined,
    status: leader.status ?? undefined,
    chance_of_playing_next_round: leader.chance_of_playing_next_round,
    news: leader.news ?? undefined,
  };
}

export function PlayerSearch() {
  usePageMeta({ title: "Players", description: "Season leaders and stats for every FPL player since 2020-21: points, goals, assists, clean sheets and per-game rates." });
  const { neo4jConnected } = useAppStore();
  const examples = useExamplePlayers(neo4jConnected).top;
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<PlayerResult[]>([]);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerResult | null>(null);
  const [detailOrigin, setDetailOrigin] = useState<DetailOrigin | null>(null);
  const [detailSeason, setDetailSeason] = useState<string>('All seasons');
  const [detailCompareState, setDetailCompareState] = useState<PlayerSearchRouteState['compare'] | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  // Lives here (not in the table) so filters survive opening a profile and coming back.
  const [leadersFilters, setLeadersFilters] = useState<LeadersFilters>(DEFAULT_LEADERS_FILTERS);

  const queryClient = useQueryClient();
  // Only the most recently submitted search may write results (guards against out-of-order responses).
  const latestSearchRef = useRef('');

  const searchMutation = useMutation({
    // Go through the query cache so repeating a recent search is served without a new request.
    mutationFn: (q: string) =>
      queryClient.fetchQuery({
        queryKey: ['players', 'search', q.toLowerCase(), 20],
        queryFn: ({ signal }) => apiService.searchPlayers(q, { signal }),
        staleTime: 5 * 60 * 1000,
      }),
    onMutate: (q) => {
      latestSearchRef.current = q;
    },
    onSuccess: (data, q) => {
      if (q !== latestSearchRef.current) return;
      setResults(data.players);
      setSelectedPlayer(null);
      setHasSearched(true);
    },
  });

  // Results carry an avatar when the backend knows it instantly; the rest load per row
  const avatarByName = usePlayerAvatars(
    selectedPlayer ? [] : results.filter((p) => !p.avatar).map((p) => p.player_name ?? p.name)
  );

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !neo4jConnected) return;
    searchMutation.mutate(query.trim());
  };

  const handleExample = (name: string) => {
    setQuery(name);
    searchMutation.mutate(name);
  };

  const handleQueryChange = (value: string) => {
    setQuery(value);
    // An emptied search box goes back to the season leaders (ignoring any search in flight).
    if (!value.trim() && !selectedPlayer) {
      latestSearchRef.current = '';
      searchMutation.reset();
      setResults([]);
      setHasSearched(false);
    }
  };

  /** Opens a player's profile; `from` also sets where "Back" returns and the season shown. */
  const openPlayer = (player: PlayerResult, from?: { origin: DetailOrigin; season: string }) => {
    setSelectedPlayer(player);
    if (from) {
      setDetailOrigin(from.origin);
      setDetailSeason(from.season);
      setDetailCompareState(null);
    }
  };

  const openLeader = (leader: PlayerLeader, season: string) => {
    openPlayer(leaderToPlayer(leader), { origin: 'leaders', season });
    window.scrollTo({ top: 0 });
  };

  useEffect(() => {
    const state = location.state as PlayerSearchRouteState | null;
    if (!state?.player) {
      return;
    }

    setQuery(state.player.player_name ?? state.player.name ?? '');
    setSelectedPlayer(state.player);
    setDetailOrigin(state.returnTo ?? 'history');
    setDetailSeason(state.season || 'All seasons');
    setDetailCompareState(state.compare ?? null);
  }, [location.state]);

  const showSkeleton = searchMutation.isPending && results.length === 0 && !selectedPlayer;
  const showLeaders = !selectedPlayer && results.length === 0 && !searchMutation.isPending;

  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow="Player stats"
        title="Players"
        description="Browse each season's leaders, or search any FPL player since 2020-21 for season totals, per-game rates and next-gameweek predictions."
      />

      <form onSubmit={handleSearch} role="search" className="flex flex-col gap-2 sm:flex-row">
        <SearchInput
          value={query}
          onChange={handleQueryChange}
          label="Player name"
          placeholder={neo4jConnected ? 'Search by player name…' : 'Connecting to the database…'}
          disabled={!neo4jConnected}
          className="flex-1"
        />
        <Button
          type="submit"
          disabled={!neo4jConnected || searchMutation.isPending || !query.trim()}
          loading={searchMutation.isPending}
          icon={<Search className="h-4 w-4" aria-hidden="true" />}
          className="sm:w-32"
        >
          Search
        </Button>
      </form>

      <div className="mt-4 space-y-4">
        {!neo4jConnected && (
          <div role="status" className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-3 text-data text-warning">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>Connecting to Neo4j… Please wait.</p>
          </div>
        )}

        {!hasSearched && neo4jConnected && !selectedPlayer && examples.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-data text-fg-subtle">Try:</span>
            {examples.map((p) => (
              <button
                key={p.name}
                type="button"
                onClick={() => handleExample(p.name)}
                title={p.name}
                className="rounded-full border border-line-strong px-3 py-1 text-data text-fg-muted transition-colors hover:border-accent hover:text-fg"
              >
                {p.label}
              </button>
            ))}
          </div>
        )}

        {searchMutation.isError && (
          <Card padded={false}>
            <ErrorState
              title="Search failed"
              description={handleApiError(searchMutation.error)}
              onRetry={latestSearchRef.current ? () => searchMutation.mutate(latestSearchRef.current) : undefined}
            />
          </Card>
        )}

        {showSkeleton && <SkeletonRows rows={5} />}

        {hasSearched && results.length === 0 && !searchMutation.isPending && !selectedPlayer && (
          <Card padded={false}>
            <EmptyState
              icon={<UserX className="h-8 w-8" aria-hidden="true" />}
              title={`No players found for "${query}"`}
              description="Try a partial name or check the spelling."
            />
          </Card>
        )}

        {results.length > 0 && !selectedPlayer && (
          <ResultsList
            results={results}
            avatarByName={avatarByName}
            onSelect={(player) => openPlayer(player)}
          />
        )}

        {showLeaders && (
          <SeasonLeaders
            seasons={PLAYER_SEASONS}
            filters={leadersFilters}
            onFiltersChange={setLeadersFilters}
            onSelectPlayer={openLeader}
          />
        )}

        {selectedPlayer && (
          <PlayerProfileView
            player={{
              name: selectedPlayer.player_name ?? selectedPlayer.name ?? '',
              team: selectedPlayer.team_name,
              position: selectedPlayer.position,
              photo: selectedPlayer.avatar,
              status: selectedPlayer.status,
              news: selectedPlayer.news,
              chance: selectedPlayer.chance_of_playing_next_round,
            }}
            initialSeason={detailSeason}
            toolbar={<div className="flex flex-wrap items-center gap-2"><BackButton onBack={
              detailOrigin === 'history'
                ? () => {
                    if (detailCompareState?.player1 && detailCompareState?.player2) {
                      navigate('/compare', {
                        state: detailCompareState,
                      });
                      return;
                    }

                    navigate(-1);
                  }
                : () => {
                    setSelectedPlayer(null);
                    setDetailOrigin(null);
                    setDetailSeason('All seasons');
                    setDetailCompareState(null);
                  }
            }
            label={
              detailOrigin === 'history'
                ? detailCompareState?.player1 && detailCompareState?.player2
                  ? 'Back to compare players'
                  : 'Back'
                : detailOrigin === 'leaders'
                  ? 'Back to season leaders'
                  : 'Back to results'
            }
            />
            <ButtonLink
              to={`${playerPath(selectedPlayer.player_name ?? selectedPlayer.name ?? '')}${detailSeason && detailSeason !== 'All seasons' ? `?season=${encodeURIComponent(detailSeason)}` : ''}`}
              variant="ghost"
              size="sm"
              icon={<ExternalLink className="h-4 w-4" aria-hidden="true" />}
            >
              Open full page
            </ButtonLink></div>}
          />
        )}
      </div>
    </Container>
  );
}

function ResultsList({
  results,
  avatarByName,
  onSelect,
}: {
  results: PlayerResult[];
  avatarByName: Record<string, string | null | undefined>;
  onSelect: (player: PlayerResult) => void;
}) {
  return (
    <Card padded={false}>
      <p className="border-b border-line px-4 py-2.5 text-micro font-semibold uppercase text-fg-subtle" aria-live="polite">
        {results.length} player{results.length !== 1 ? 's' : ''} found
      </p>
      <ul>
        {results.map((player, i) => {
          const name: string = player.player_name ?? player.name ?? 'Unknown';
          return (
            <li key={`${player.element_id ?? name}-${i}`} className="border-b border-line last:border-0">
              <Link
                to={playerPath(name)}
                onClick={(e) => {
                  // A new-tab click goes to the player's page; a plain click opens the profile here
                  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  onSelect(player);
                }}
                className="group flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
              >
                <PlayerWithClub
                  playerName={name}
                  teamName={player.team_name}
                  showAbbreviation={false}
                  avatar={player.avatar ?? avatarByName[name]}
                  avatarSize={40}
                  className="min-w-0 flex-1"
                  nameClassName="group-hover:text-accent"
                  meta={
                    <>
                      {player.season && <span className="whitespace-nowrap">· {player.season}</span>}
                      <StatusBadge status={player.status} chance={player.chance_of_playing_next_round} news={player.news} />
                    </>
                  }
                />
                <PositionBadge position={player.position} />
                <span className="hidden w-20 text-right sm:block">
                  {player.total_points != null && (
                    <span className="block text-data font-bold tabular-nums text-fg">{player.total_points} pts</span>
                  )}
                  {player.goals != null && (
                    <span className="block text-micro tabular-nums text-fg-subtle">
                      {player.goals}G {player.assists ?? 0}A
                    </span>
                  )}
                </span>
                {typeof player.predicted_points === 'number' && (
                  <Chip tone="accent" icon={<Sparkles className="h-3 w-3" aria-hidden="true" />}>
                    <span className="sr-only">Predicted next gameweek: </span>
                    {player.predicted_points.toFixed(1)}
                  </Chip>
                )}
                <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle group-hover:text-fg" aria-hidden="true" />
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function BackButton({ onBack, label }: { onBack: () => void; label: string }) {
  return (
    <Button variant="ghost" size="sm" onClick={onBack} icon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}>
      {label}
    </Button>
  );
}
