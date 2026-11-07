import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertCircle, ArrowLeftRight, Plus, Search } from 'lucide-react';
import { useAppStore } from '../store/appStore';
import { handleApiError } from '../services/api';
import { fetchProfile, fetchProjection, fetchTicker } from '../services/compareApi';
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
import { PlayerSlot } from '../components/compare/PlayerSlot';
import { ComparisonResults } from '../components/compare/ComparisonResults';
import { MAX_PLAYERS, MIN_PLAYERS, playerColor, type ComparedPlayer } from '../components/compare/compareModel';
import { PLAYER_SEASONS } from '../components/players/seasons';
import { usePageMeta } from '../hooks/usePageMeta';
import { useExamplePlayers } from '../hooks/useExamplePlayers';

const CURRENT = PLAYER_SEASONS[0];
const ALL = 'all';

type CompareRouteState = {
  player1?: string;
  player2?: string;
  season?: string;
};

/** The URL's season: absent = this season, "all" (or "All seasons") = every season (''). */
function seasonFromParam(raw: string | null): string {
  if (raw == null || raw === '') return CURRENT;
  if (raw === ALL || raw === 'All seasons') return '';
  return PLAYER_SEASONS.includes(raw) ? raw : CURRENT;
}

const seasonToParam = (s: string) => s || ALL;

function parseNames(params: URLSearchParams): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of params.getAll('p')) {
    const name = raw.trim();
    const key = name.toLowerCase();
    if (name && !seen.has(key) && out.length < MAX_PLAYERS) {
      seen.add(key);
      out.push(name);
    }
  }
  return out;
}

function ResultsSkeleton({ count }: { count: number }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {Array.from({ length: count }, (_, i) => (
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
  usePageMeta({
    title: 'Compare players',
    description:
      'Compare up to four FPL players side by side: predicted points, price, points per million, underlying rates, fixtures and season stats.',
  });
  const { neo4jConnected } = useAppStore();
  const examplePairs = useExamplePlayers(neo4jConnected).pairs;
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const firstSlotRef = useRef<HTMLDivElement | null>(null);

  const urlNames = useMemo(() => parseNames(searchParams), [searchParams]);
  const urlKey = urlNames.join('|');
  const season = seasonFromParam(searchParams.get('season'));
  const current = season === CURRENT;

  const [drafts, setDrafts] = useState<string[]>(() => {
    const n = parseNames(searchParams);
    return n.length >= MIN_PLAYERS ? n : [...n, ...Array(MIN_PLAYERS - n.length).fill('')];
  });
  const [errors, setErrors] = useState<(string | null)[]>([]);
  const [seasonSel, setSeasonSel] = useState(season);

  // The URL is the source of truth: back/forward and links reset the form to it.
  useEffect(() => {
    if (urlNames.length > 0) {
      setDrafts(urlNames.length >= MIN_PLAYERS ? urlNames : [...urlNames, ''] );
      setErrors([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- urlKey stands for urlNames
  }, [urlKey]);
  useEffect(() => setSeasonSel(season), [season]);

  // Router-state contract from other pages: { player1, player2, season }
  useEffect(() => {
    const state = location.state as CompareRouteState | null;
    if (!state?.player1 || !state?.player2) return;
    const params = new URLSearchParams();
    params.append('p', state.player1);
    params.append('p', state.player2);
    params.set('season', seasonToParam(seasonFromParam(state.season ? state.season : ALL)));
    setSearchParams(params, { replace: true });
  }, [location.state, setSearchParams]);

  const writeUrl = (names: string[], s: string, replace = false) => {
    const params = new URLSearchParams();
    names.forEach((n) => params.append('p', n));
    params.set('season', seasonToParam(s));
    setSearchParams(params, { replace });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!neo4jConnected) return;
    const next: (string | null)[] = drafts.map(() => null);
    const seen = new Map<string, number>();
    const filled: string[] = [];
    drafts.forEach((d, i) => {
      const name = d.trim();
      if (!name) return;
      const key = name.toLowerCase();
      if (seen.has(key)) next[i] = 'Pick a different player.';
      else {
        seen.set(key, i);
        filled.push(name);
      }
    });
    if (filled.length < MIN_PLAYERS) {
      const blanks = drafts.map((d, i) => (d.trim() ? -1 : i)).filter((i) => i >= 0);
      blanks.slice(0, MIN_PLAYERS - filled.length).forEach((i) => (next[i] = 'Choose a player.'));
    }
    setErrors(next);
    if (next.some(Boolean)) return;
    writeUrl(filled, seasonSel);
  };

  const changeSeason = (s: string) => {
    setSeasonSel(s);
    if (urlNames.length >= MIN_PLAYERS) writeUrl(urlNames, s);
  };

  const setDraft = (i: number, v: string) => {
    setDrafts((d) => d.map((x, j) => (j === i ? v : x)));
    setErrors((e) => e.map((x, j) => (j === i ? null : x)));
  };
  const removeSlot = (i: number) => {
    setDrafts((d) => d.filter((_, j) => j !== i));
    setErrors([]);
  };

  const example = (a: string, b: string) => {
    setDrafts([a, b]);
    writeUrl([a, b], seasonSel);
  };

  // ---- data ----
  const active = neo4jConnected && urlNames.length >= MIN_PLAYERS;
  const profiles = useQueries({
    queries: urlNames.map((name) => ({
      queryKey: ['compare', 'profile', name, season],
      queryFn: ({ signal }: { signal?: AbortSignal }) => fetchProfile(name, season || undefined, signal),
      enabled: active,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const projections = useQueries({
    queries: urlNames.map((name) => ({
      queryKey: ['compare', 'projection', name],
      queryFn: ({ signal }: { signal?: AbortSignal }) => fetchProjection(name, signal),
      enabled: active && current,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const ticker = useQuery({
    queryKey: ['compare', 'ticker'],
    queryFn: fetchTicker,
    enabled: active && current,
    staleTime: 10 * 60_000,
    retry: false,
  });

  const loading = active && profiles.some((q) => q.isPending);
  const failed = profiles.map((q, i) => (q.isError ? urlNames[i] : null)).filter((n): n is string => !!n);
  const firstError = profiles.find((q) => q.isError)?.error;

  const players: ComparedPlayer[] = profiles.flatMap((q, i) => {
    if (!q.data) return [];
    const team = q.data.team;
    return [
      {
        profile: q.data,
        projection: projections[i]?.data ?? null,
        ticker: current && team ? (ticker.data ?? []).find((c) => c.team === team) ?? null : null,
      },
    ];
  });
  const hasMatches = players.some((p) => p.profile.appearances > 0);

  const openDetails = (p: ComparedPlayer) => {
    const pr = p.profile;
    navigate('/players', {
      state: {
        player: {
          player_name: pr.name,
          name: pr.name,
          position: pr.position,
          team: pr.team,
          avatar: pr.photo,
          season: season || undefined,
        },
        season: season || 'All seasons',
        returnTo: 'history',
        compare: { player1: urlNames[0], player2: urlNames[1], season },
      },
    });
  };

  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow="Head to head"
        title="Compare players"
        description="Pick two to four players to weigh up a transfer: predicted points, price, fixtures and underlying numbers side by side."
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
          <form onSubmit={submit} autoComplete="off" className="space-y-4" aria-label="Choose players to compare">
            <div ref={firstSlotRef} className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {drafts.map((d, i) => (
                <PlayerSlot
                  key={i}
                  id={`slot${i}`}
                  label={`Player ${i + 1}`}
                  color={playerColor(i)}
                  value={d}
                  placeholder={i === 0 ? 'e.g. Mohamed Salah' : 'Search a player'}
                  disabled={!neo4jConnected}
                  error={errors[i]}
                  onChange={(v) => setDraft(i, v)}
                  onRemove={drafts.length > MIN_PLAYERS ? () => removeSlot(i) : undefined}
                />
              ))}
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <Field label="Season" className="sm:w-48">
                {(id) => (
                  <Select id={id} value={seasonSel} onChange={(e) => changeSeason(e.target.value)} disabled={!neo4jConnected}>
                    {PLAYER_SEASONS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                    <option value="">All seasons</option>
                  </Select>
                )}
              </Field>
              {drafts.length < MAX_PLAYERS && (
                <Button
                  type="button"
                  variant="secondary"
                  className="w-full sm:w-auto"
                  disabled={!neo4jConnected}
                  onClick={() => setDrafts((d) => [...d, ''])}
                  icon={<Plus className="h-4 w-4" aria-hidden="true" />}
                >
                  Add player
                </Button>
              )}
              <Button
                type="submit"
                className="w-full sm:w-auto"
                loading={loading}
                disabled={!neo4jConnected}
                icon={<ArrowLeftRight className="h-4 w-4" aria-hidden="true" />}
              >
                Compare
              </Button>
            </div>
          </form>

          {urlNames.length < MIN_PLAYERS && neo4jConnected && examplePairs.length > 0 && (
            <div className="mt-4 border-t border-line pt-4">
              <p className="text-micro font-semibold uppercase text-fg-subtle" id="example-matchups">
                Try a matchup
              </p>
              <ul className="mt-2 flex flex-wrap gap-2" aria-labelledby="example-matchups">
                {examplePairs.map(({ p1, p2 }) => (
                  <li key={p1.name}>
                    <button
                      type="button"
                      onClick={() => example(p1.name, p2.name)}
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

        <div aria-live="polite" aria-busy={loading} className="space-y-4">
          {loading && <ResultsSkeleton count={urlNames.length} />}

          {!loading && failed.length > 0 && (
            <Card>
              <ErrorState
                title={players.length === 0 ? "Couldn't compare these players" : `No data for ${failed.join(', ')}`}
                description={
                  players.length === 0
                    ? handleApiError(firstError)
                    : 'Check the spelling or pick another player. The others are shown below.'
                }
                onRetry={() => profiles.forEach((q) => q.isError && q.refetch())}
              />
            </Card>
          )}

          {!loading && active && players.length === 1 && failed.length === 0 && (
            <Card>
              <EmptyState
                icon={<Search className="h-8 w-8" aria-hidden="true" />}
                title="No data found for these players"
                description="Check the spelling or try different names."
              />
            </Card>
          )}

          {!loading && players.length >= MIN_PLAYERS && !hasMatches && (
            <Card>
              <EmptyState
                icon={<Search className="h-8 w-8" aria-hidden="true" />}
                title={`No appearances${season ? ` in ${season}` : ''}`}
                description="Try another season."
              />
            </Card>
          )}
        </div>

        {!loading && players.length >= MIN_PLAYERS && hasMatches && (
          <ComparisonResults
            players={players}
            season={season}
            current={current}
            onOpenDetails={openDetails}
            onChangePlayers={() => firstSlotRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
          />
        )}
      </div>
    </Container>
  );
}
