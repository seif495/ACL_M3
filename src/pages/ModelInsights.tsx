import { useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { ListOrdered, MousePointerClick } from 'lucide-react';
import {
  apiService,
  isNotFoundError,
  isServiceUnavailableError,
  type ExplainPlayerHit,
} from '../services/api';
import { ButtonLink, Card, Container, EmptyState, PageHeader, TabPanel, Tabs } from '../components/ui';
import { ExplainOverview } from '../components/explain/ExplainOverview';
import { ExplainError, ExplainSkeleton, ExplainUnavailable } from '../components/explain/ExplainStates';
import { FeatureEffects } from '../components/explain/FeatureEffects';
import { PlayerExplainer } from '../components/explain/PlayerExplainer';
import { PlayerChips, PlayerSearchBox } from '../components/explain/PlayerPicker';
import { usePageMeta } from '../hooks/usePageMeta';

// Model insights: SHAP and LIME explanations of the points model's next-gameweek predictions.
// URL: ?tab=overview|player|effects (overview is the default); ?player=<FPL element id> picks a
// player and implies the player tab; ?feature=<name> picks the feature on the effects tab.

type View = 'overview' | 'player' | 'effects';

function parseView(tab: string | null, hasPlayer: boolean): View {
  if (tab === 'overview' || tab === 'player' || tab === 'effects') return tab;
  return hasPlayer ? 'player' : 'overview';
}

const STALE_MS = 15 * 60 * 1000; // explanations change once a day, with the predictions

/** 404 and 503 mean "not there (yet)": show that at once instead of retrying. */
const retry = (failures: number, err: unknown) => !isNotFoundError(err) && !isServiceUnavailableError(err) && failures < 1;

export function ModelInsights() {
  usePageMeta({
    title: 'Model insights',
    description: 'How the points model makes its predictions, explained with SHAP and LIME: what drives this gameweek’s forecasts and why each player got his number.',
  });
  const [params, setParams] = useSearchParams();
  const tabsRef = useRef<HTMLDivElement>(null);
  const playerParam = Number(params.get('player'));
  const playerId = Number.isInteger(playerParam) && playerParam > 0 ? playerParam : undefined;
  const view = parseView(params.get('tab'), playerId != null);

  const summary = useQuery({
    queryKey: ['ml-explain-summary'],
    queryFn: ({ signal }) => apiService.getExplainSummary(signal),
    staleTime: STALE_MS,
    retry,
  });
  const features = summary.data?.features ?? [];
  const descriptions = Object.fromEntries(features.map((f) => [f.feature, f.description]));

  const player = useQuery({
    queryKey: ['ml-explain-player', playerId],
    queryFn: ({ signal }) => apiService.getPlayerExplanation({ element: playerId! }, signal),
    enabled: view === 'player' && playerId != null,
    placeholderData: keepPreviousData,
    staleTime: STALE_MS,
    retry,
  });

  const featureParam = params.get('feature');
  const topFeature = [...features].sort((a, b) => b.importance - a.importance)[0]?.feature;
  const feature = featureParam && features.some((f) => f.feature === featureParam) ? featureParam : topFeature;
  const dependence = useQuery({
    queryKey: ['ml-explain-dependence', feature],
    queryFn: ({ signal }) => apiService.getExplainDependence(feature!, signal),
    enabled: view === 'effects' && !!feature,
    placeholderData: keepPreviousData,
    staleTime: STALE_MS,
    retry,
  });

  const setView = (next: View) => {
    const p = new URLSearchParams();
    if (next !== 'overview') p.set('tab', next);
    if (next === 'player' && playerId) p.set('player', String(playerId));
    if (next === 'effects' && featureParam) p.set('feature', featureParam);
    setParams(p);
  };

  const pickPlayer = (hit: ExplainPlayerHit) => {
    const fromOverview = view !== 'player';
    setParams({ tab: 'player', player: String(hit.element) });
    // From the overview's chips (far down on a phone), bring the player view into sight.
    if (fromOverview && tabsRef.current && tabsRef.current.getBoundingClientRect().top < 0) {
      tabsRef.current.scrollIntoView({ block: 'start' });
    }
  };

  const setFeature = (next: string) => setParams({ tab: 'effects', feature: next }, { replace: true });

  /** The summary failed: a 404 (endpoint not deployed yet) counts as "not ready" too. */
  const summaryProblem = summary.isError ? (
    isNotFoundError(summary.error) ? (
      <ExplainUnavailable />
    ) : (
      <ExplainError error={summary.error} what="the model insights" onRetry={() => summary.refetch()} />
    )
  ) : null;

  const s = summary.data;

  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow={s ? `Gameweek ${s.gameweek} · ${s.season}` : 'Points model'}
        title="Model insights"
        description="Why the points model predicts what it does. SHAP splits every prediction into exact contributions from each feature; LIME checks it with a simple model fitted around one player."
        actions={
          <ButtonLink to="/predictions" variant="secondary" size="sm" icon={<ListOrdered className="h-4 w-4" aria-hidden="true" />}>
            See the predictions
          </ButtonLink>
        }
      />

      <div ref={tabsRef} className="scroll-mt-[calc(var(--header-h)+3rem)]">
        <Tabs
          label="Model insight views"
          idPrefix="model"
          value={view}
          onChange={setView}
          items={[
            { value: 'overview', label: 'Overview' },
            { value: 'player', label: 'Explain a player' },
            { value: 'effects', label: 'Feature effects' },
          ]}
        />
      </div>

      <TabPanel idPrefix="model" value={view} className="pt-5">
        {view === 'overview' &&
          (summaryProblem ?? (s ? <ExplainOverview summary={s} onPickPlayer={pickPlayer} /> : <ExplainSkeleton label="Loading the model insights…" blocks={3} slowTitle="Working out what drives the predictions" />))}

        {view === 'player' && (
          <div className="space-y-6">
            <Card>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
                <PlayerSearchBox onSelect={pickPlayer} />
                {s && s.top_players.length > 0 && (
                  <div className="min-w-0">
                    <p className="mb-1.5 text-micro font-semibold uppercase text-fg-muted">Top predicted</p>
                    <PlayerChips players={s.top_players} selected={playerId} onSelect={pickPlayer} />
                  </div>
                )}
              </div>
            </Card>

            {playerId == null ? (
              summary.isError && (isServiceUnavailableError(summary.error) || isNotFoundError(summary.error)) ? (
                <ExplainUnavailable />
              ) : (
                <Card padded={false}>
                  <EmptyState
                    icon={<MousePointerClick className="h-8 w-8" aria-hidden="true" />}
                    title="Pick a player"
                    description={`Search for anyone in this gameweek’s predictions${s?.top_players.length ? ', or pick one of the top scorers,' : ''} to see how the model reached his number.`}
                  />
                </Card>
              )
            ) : player.isError ? (
              <ExplainError
                error={player.error}
                what="this player’s explanation"
                onRetry={() => player.refetch()}
                notFound={{
                  title: 'Not in this gameweek’s predictions',
                  description: `There’s no prediction to explain for this player${s ? ` in gameweek ${s.gameweek}` : ''}, usually because he isn’t in the FPL game any more. Try another player.`,
                }}
              />
            ) : player.data ? (
              <PlayerExplainer
                explanation={player.data}
                descriptions={descriptions}
                updating={player.isFetching && player.isPlaceholderData}
              />
            ) : (
              <ExplainSkeleton label="Working out the explanation…" slowTitle="Working out this player’s explanation" />
            )}
          </div>
        )}

        {view === 'effects' &&
          (summaryProblem ??
            (!s ? (
              <ExplainSkeleton label="Loading the features…" slowTitle="Working out the feature effects" />
            ) : !feature ? (
              <Card padded={false}>
                <EmptyState title="No features to show" description="The explanation has no feature list for this gameweek." />
              </Card>
            ) : (
              <FeatureEffects
                features={features}
                feature={feature}
                onFeatureChange={setFeature}
                dependence={dependence.data}
                updating={dependence.isFetching && dependence.isPlaceholderData}
                problem={
                  dependence.isError ? (
                    <ExplainError
                      bare
                      error={dependence.error}
                      what="this feature’s effect"
                      onRetry={() => dependence.refetch()}
                      notFound={{ title: 'No data for this feature', description: 'This gameweek’s explanation has no effect data for it. Try another feature.' }}
                    />
                  ) : undefined
                }
              />
            )))}
      </TabPanel>
    </Container>
  );
}
