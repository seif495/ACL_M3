import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { AlertCircle, ArrowLeftRight, Search, UserX } from 'lucide-react';
import { useAppStore } from '../store/appStore';
import { apiService, handleApiError } from '../services/api';
import { usePlayerAvatars } from '../hooks/usePlayerAvatars';
import {
  Button,
  Card,
  Container,
  EmptyState,
  ErrorState,
  Field,
  PageHeader,
  Select,
  Skeleton,
  SkeletonRows,
} from '../components/ui';
import { PlayerCombobox, type PlayerSuggestion } from '../components/compare/PlayerCombobox';
import { ComparisonResults, type PlayerStats } from '../components/compare/ComparisonResults';
import { usePageMeta } from '../hooks/usePageMeta';
import { useExamplePlayers } from '../hooks/useExamplePlayers';

const SEARCH_DEBOUNCE_MS = 200;

const SEASONS = ['', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26', '2026-27'];


type Suggestion = PlayerSuggestion;
type CompareRouteState = {
  player1?: string;
  player2?: string;
  season?: string;
};

function ResultsSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:gap-4">
        {[0, 1].map((i) => (
          <Card key={i} className="space-y-3">
            <Skeleton className="h-16 w-16 rounded-md" />
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </Card>
        ))}
      </div>
      <Card>
        <SkeletonRows rows={8} />
      </Card>
    </div>
  );
}

export function PlayerComparison() {
  usePageMeta({ title: "Compare players", description: "Compare two FPL players head to head across any season: points, goals, assists, bonus, form and more." });
  const { neo4jConnected } = useAppStore();
  const examplePairs = useExamplePlayers(neo4jConnected).pairs;
  const location = useLocation();
  const navigate = useNavigate();

  const [player1, setPlayer1] = useState('');
  const [player2, setPlayer2] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ p1: string | null; p2: string | null }>({ p1: null, p2: null });
  const [p1Suggestions, setP1Suggestions] = useState<Suggestion[]>([]);
  const [p2Suggestions, setP2Suggestions] = useState<Suggestion[]>([]);
  const [p1Index, setP1Index] = useState(-1);
  const [p2Index, setP2Index] = useState(-1);
  const p1Timer = useRef<number | null>(null);
  const p2Timer = useRef<number | null>(null);
  const p1SuppressSearch = useRef(false);
  const p2SuppressSearch = useRef(false);
  const p1AutocompleteActive = useRef(false);
  const p2AutocompleteActive = useRef(false);
  // Bumped on every input change / commit so out-of-order search responses are dropped.
  const p1SearchSeq = useRef(0);
  const p2SearchSeq = useRef(0);
  const compareSeq = useRef(0);
  const queryClient = useQueryClient();
  const p1InputRef = useRef<HTMLInputElement | null>(null);
  const p2InputRef = useRef<HTMLInputElement | null>(null);
  const p1ListId = 'p1-suggestions-list';
  const p2ListId = 'p2-suggestions-list';
  const [season, setSeason] = useState('');
  const [comparisonData, setComparisonData] = useState<PlayerStats[]>([]);
  const restoreComparisonRef = useRef(false);

  const compareMutation = useMutation({
    // Served from the query cache when the same matchup was compared recently (e.g. returning from "View Details").
    mutationFn: ({ p1, p2, s }: { p1: string; p2: string; s?: string }) =>
      queryClient.fetchQuery({
        queryKey: ['players', 'compare', p1, p2, s || ''],
        queryFn: () => apiService.comparePlayers(p1, p2, s || undefined),
        staleTime: 5 * 60 * 1000,
      }),
    // Only the latest compare request may write results (season can change while one is in flight).
    onMutate: () => ({ seq: ++compareSeq.current }),
    onSuccess: (data, _vars, context) => {
      if (context?.seq !== compareSeq.current) return;
      setComparisonData(data.comparison);
    },
  });
  // TanStack Query keeps `mutate` stable across renders, so effects can depend on it.
  const runCompare = compareMutation.mutate;

  const handleCompare = (e: React.FormEvent) => {
    e.preventDefault();
    if (!neo4jConnected) return;
    // Say what's missing instead of silently ignoring the click
    const errors = {
      p1: player1.trim() ? null : 'Choose a player.',
      p2: !player2.trim()
        ? 'Choose a second player.'
        : player1.trim().toLowerCase() === player2.trim().toLowerCase()
          ? 'Pick a different player to compare with.'
          : null,
    };
    setFieldErrors(errors);
    if (errors.p1 || errors.p2) {
      (errors.p1 ? p1InputRef : p2InputRef).current?.focus();
      return;
    }

    // Deactivate autocomplete and clear suggestions to prevent them from reappearing
    p1AutocompleteActive.current = false;
    p2AutocompleteActive.current = false;
    p1SearchSeq.current++;
    p2SearchSeq.current++;
    setP1Suggestions([]);
    setP2Suggestions([]);

    // Blur the input fields to remove focus
    p1InputRef.current?.blur();
    p2InputRef.current?.blur();

    compareMutation.mutate({ p1: player1.trim(), p2: player2.trim(), s: season });
  };

  const handleExample = (p1: string, p2: string) => {
    setFieldErrors({ p1: null, p2: null });
    p1AutocompleteActive.current = false;
    p2AutocompleteActive.current = false;
    setPlayer1(p1);
    setPlayer2(p2);
    setP1Suggestions([]);
    setP2Suggestions([]);
    compareMutation.mutate({ p1, p2, s: season });
  };

  const changeSeason = (s: string) => {
    setSeason(s);
    if (player1.trim() && player2.trim() && neo4jConnected) {
      runCompare({ p1: player1.trim(), p2: player2.trim(), s });
    }
  };

  const runSearch = useCallback(async (q: string, setSuggestions: (s: Suggestion[]) => void, isCurrent: () => boolean) => {
    if (!q || q.trim().length < 2) {
      setSuggestions([]);
      return;
    }

    const term = q.trim();
    try {
      // fetchQuery dedupes identical in-flight requests and caches recent terms (e.g. after backspacing).
      const res = await queryClient.fetchQuery({
        queryKey: ['players', 'search', term.toLowerCase(), 10],
        queryFn: ({ signal }) => apiService.searchPlayers(term, { limit: 10, includeAvatars: false, signal }),
        staleTime: 5 * 60 * 1000,
      });
      if (!isCurrent()) return;
      const players = (res.players || [])
        .map((p) => ({
          name: p.player_name || p.name || '',
          position: p.position || p.pos || undefined,
          season: p.season || undefined,
          avatar: p.avatar || p.image || p.photo || undefined,
        }))
        .filter((x: Suggestion) => x.name);
      setSuggestions(players.slice(0, 10));
    } catch {
      if (isCurrent()) setSuggestions([]);
    }
  }, [queryClient]);

  // Avatars for suggestions that don't already carry one (cached per name, shared with Search)
  const avatarByName = usePlayerAvatars(
    [...p1Suggestions, ...p2Suggestions].filter((s) => s.name && !s.avatar).map((s) => s.name)
  );

  useEffect(() => {
    const seq = ++p1SearchSeq.current;
    if (p1Timer.current) window.clearTimeout(p1Timer.current);
    p1Timer.current = window.setTimeout(() => {
      if (!p1AutocompleteActive.current) {
        setP1Suggestions([]);
        setP1Index(-1);
        return;
      }
      if (p1SuppressSearch.current) {
        p1SuppressSearch.current = false;
        setP1Index(-1);
        return;
      }
      runSearch(player1, setP1Suggestions, () => seq === p1SearchSeq.current && p1AutocompleteActive.current);
      setP1Index(-1);
    }, SEARCH_DEBOUNCE_MS) as unknown as number;

    return () => {
      if (p1Timer.current) window.clearTimeout(p1Timer.current);
    };
  }, [player1, runSearch]);

  useEffect(() => {
    const seq = ++p2SearchSeq.current;
    if (p2Timer.current) window.clearTimeout(p2Timer.current);
    p2Timer.current = window.setTimeout(() => {
      if (!p2AutocompleteActive.current) {
        setP2Suggestions([]);
        setP2Index(-1);
        return;
      }
      if (p2SuppressSearch.current) {
        p2SuppressSearch.current = false;
        setP2Index(-1);
        return;
      }
      runSearch(player2, setP2Suggestions, () => seq === p2SearchSeq.current && p2AutocompleteActive.current);
      setP2Index(-1);
    }, SEARCH_DEBOUNCE_MS) as unknown as number;

    return () => {
      if (p2Timer.current) window.clearTimeout(p2Timer.current);
    };
  }, [player2, runSearch]);

  useEffect(() => {
    const q = player1.trim();
    if (q && !q.includes(' ') && p1Suggestions.length === 1) {
      p1SuppressSearch.current = true;
      setPlayer1(p1Suggestions[0].name);
      setP1Suggestions([]);
    }
  }, [p1Suggestions, player1]);

  useEffect(() => {
    const q = player2.trim();
    if (q && !q.includes(' ') && p2Suggestions.length === 1) {
      p2SuppressSearch.current = true;
      setPlayer2(p2Suggestions[0].name);
      setP2Suggestions([]);
    }
  }, [p2Suggestions, player2]);

  useEffect(() => {
    const state = location.state as CompareRouteState | null;
    if (!state?.player1 || !state?.player2) {
      return;
    }

    restoreComparisonRef.current = true;
    p1AutocompleteActive.current = false;
    p2AutocompleteActive.current = false;
    setPlayer1(state.player1);
    setPlayer2(state.player2);
    setSeason(state.season ?? '');
    setP1Suggestions([]);
    setP2Suggestions([]);

    if (neo4jConnected) {
      runCompare({ p1: state.player1, p2: state.player2, s: state.season });
    }
  }, [location.state, neo4jConnected, runCompare]);

  const onP1KeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (p1Suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setP1Index((i) => Math.min(i + 1, p1Suggestions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setP1Index((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      if (p1Index >= 0 && p1Index < p1Suggestions.length) {
        e.preventDefault();
        p1SuppressSearch.current = true;
        setPlayer1(p1Suggestions[p1Index].name);
        setP1Suggestions([]);
        setP1Index(-1);
        p1InputRef.current?.focus();
      }
    } else if (e.key === 'Escape') {
      setP1Suggestions([]);
      setP1Index(-1);
    }
  };

  const onP2KeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (p2Suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setP2Index((i) => Math.min(i + 1, p2Suggestions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setP2Index((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      if (p2Index >= 0 && p2Index < p2Suggestions.length) {
        e.preventDefault();
        p2SuppressSearch.current = true;
        setPlayer2(p2Suggestions[p2Index].name);
        setP2Suggestions([]);
        setP2Index(-1);
        p2InputRef.current?.focus();
      }
    } else if (e.key === 'Escape') {
      setP2Suggestions([]);
      setP2Index(-1);
    }
  };

  const pickP1 = (s: Suggestion) => {
    p1AutocompleteActive.current = false;
    p1SuppressSearch.current = true;
    setPlayer1(s.name);
    setFieldErrors((f) => ({ ...f, p1: null }));
    setP1Suggestions([]);
    p1InputRef.current?.focus();
  };

  const pickP2 = (s: Suggestion) => {
    p2AutocompleteActive.current = false;
    p2SuppressSearch.current = true;
    setPlayer2(s.name);
    setFieldErrors((f) => ({ ...f, p2: null }));
    setP2Suggestions([]);
    p2InputRef.current?.focus();
  };

  const p1Data = comparisonData[0];
  const p2Data = comparisonData[1];
  const showSkeleton = compareMutation.isPending && comparisonData.length === 0;

  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow="Head to head"
        title="Compare players"
        description="Pick two players and a season to see who comes out on top, stat by stat."
      />

      <div className="space-y-4">
        {!neo4jConnected && (
          <Card className="flex items-start gap-3 border-warning/40">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
            <p className="text-body text-fg-muted">
              <span className="font-semibold text-warning">Database offline.</span> Connect to Neo4j in Settings to compare players.
            </p>
          </Card>
        )}

        <Card>
          <form
            id="comparison-form"
            onSubmit={handleCompare}
            autoComplete="off"
            className="space-y-4"
            aria-label="Choose players to compare"
          >
            <input aria-hidden="true" tabIndex={-1} style={{ position: 'absolute', left: '-9999px', width: '1px', height: '1px', overflow: 'hidden' }} type="text" name="__fake_user" autoComplete="username" />
            <input aria-hidden="true" tabIndex={-1} style={{ position: 'absolute', left: '-9999px', width: '1px', height: '1px', overflow: 'hidden' }} type="password" name="__fake_pass" autoComplete="current-password" />

            <div className="grid grid-cols-1 items-end gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
              <PlayerCombobox
                id="p1"
                listId={p1ListId}
                label="Player 1"
                side={1}
                name="player1_search"
                error={fieldErrors.p1}
                value={player1}
                placeholder="e.g. Mohamed Salah"
                disabled={!neo4jConnected}
                suggestions={p1Suggestions}
                activeIndex={p1Index}
                avatarByName={avatarByName}
                inputRef={p1InputRef}
                onChange={(v) => {
                  p1AutocompleteActive.current = true;
                  setFieldErrors((f) => ({ ...f, p1: null }));
                  setPlayer1(v);
                }}
                onKeyDown={onP1KeyDown}
                onPick={pickP1}
              />
              <span
                aria-hidden="true"
                className="hidden h-11 items-center justify-center px-1 font-display text-micro font-bold uppercase text-fg-subtle md:flex"
              >
                vs
              </span>
              <PlayerCombobox
                id="p2"
                listId={p2ListId}
                label="Player 2"
                side={2}
                name="player2_search"
                error={fieldErrors.p2}
                value={player2}
                placeholder="e.g. Son Heung-min"
                disabled={!neo4jConnected}
                suggestions={p2Suggestions}
                activeIndex={p2Index}
                avatarByName={avatarByName}
                inputRef={p2InputRef}
                onChange={(v) => {
                  p2AutocompleteActive.current = true;
                  setFieldErrors((f) => ({ ...f, p2: null }));
                  setPlayer2(v);
                }}
                onKeyDown={onP2KeyDown}
                onPick={pickP2}
              />
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <Field label="Season" className="sm:w-48">
                {(id) => (
                  <Select id={id} value={season} onChange={(e) => changeSeason(e.target.value)} disabled={!neo4jConnected}>
                    {SEASONS.map((s) => (
                      <option key={s} value={s}>
                        {s || 'All seasons'}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Button
                type="submit"
                className="w-full sm:w-auto"
                loading={compareMutation.isPending}
                disabled={!neo4jConnected}
                icon={<ArrowLeftRight className="h-4 w-4" aria-hidden="true" />}
              >
                Compare
              </Button>
            </div>
          </form>

          {comparisonData.length === 0 && neo4jConnected && examplePairs.length > 0 && (
            <div className="mt-4 border-t border-line pt-4">
              <p className="text-micro font-semibold uppercase text-fg-subtle" id="example-matchups">
                Try a matchup
              </p>
              <ul className="mt-2 flex flex-wrap gap-2" aria-labelledby="example-matchups">
                {examplePairs.map(({ p1, p2 }) => (
                  <li key={p1.name}>
                    <button
                      type="button"
                      onClick={() => handleExample(p1.name, p2.name)}
                      aria-label={`Compare ${p1.name} vs ${p2.name}`}
                      className="rounded-full border border-line-strong px-3 py-1 text-data text-fg-muted transition-colors hover:border-accent hover:text-fg"
                    >
                      {p1.label} <span className="text-fg-subtle">vs</span> {p2.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <div aria-live="polite" aria-busy={compareMutation.isPending} className="space-y-4">
          {compareMutation.isError && (
            <Card>
              <ErrorState
                title="Couldn't compare these players"
                description={handleApiError(compareMutation.error)}
                onRetry={compareMutation.variables ? () => compareMutation.mutate(compareMutation.variables!) : undefined}
              />
            </Card>
          )}

          {showSkeleton && <ResultsSkeleton />}

          {compareMutation.isSuccess && comparisonData.length === 0 && (
            <Card>
              <EmptyState
                icon={<Search className="h-8 w-8" aria-hidden="true" />}
                title="No data found for these players"
                description="Check the spelling or try different names."
              />
            </Card>
          )}

          {comparisonData.length === 1 && (
            <Card className="flex items-start gap-3 border-warning/40">
              <UserX className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
              <p className="text-body text-fg-muted">
                Only found data for <strong className="text-fg">{comparisonData[0].player_name}</strong>. The second player may not
                exist or have no data for the selected season.
              </p>
            </Card>
          )}
        </div>

        {p1Data && p2Data && (
          <ComparisonResults
            p1={p1Data}
            p2={p2Data}
            onOpenDetails={(player) =>
              navigate('/players', {
                state: {
                  player,
                  season: season || 'All seasons',
                  returnTo: 'history',
                  compare: {
                    player1,
                    player2,
                    season,
                  },
                },
              })
            }
            onChangePlayers={() => p1InputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
          />
        )}
      </div>
    </Container>
  );
}
