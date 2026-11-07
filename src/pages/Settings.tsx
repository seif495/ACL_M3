import type { ReactNode } from 'react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, CheckCircle2, ChevronDown, Database, Loader2, RefreshCw, Server, Star, XCircle } from 'lucide-react';
import { useAppStore } from '../store/appStore';
import { apiService, handleApiError } from '../services/api';
import { Button, Card, Container, ErrorState, PageHeader, SectionHeading, Select, SkeletonRows } from '../components/ui';
import { useFavouriteClub, useWatchlist } from '../lib/personal';
import { forgetTeamId, readSavedTeamId } from '../components/myteam/format';
import { useClubs } from '../hooks/usePlayerFilters';
import { OptionGroup } from '../components/settings/OptionGroup';
import { cn } from '../utils/cn';
import { usePageMeta } from '../hooks/usePageMeta';

// Keep in sync with RETIRED_MODELS / FALLBACK_ORDER in llm/llm_manager.py and the store migration.
const MODELS = [
  { key: 'gpt-oss-120b', label: 'GPT-OSS 120B', description: 'Highest quality model via Groq (recommended)' },
  { key: 'gpt-oss-20b', label: 'GPT-OSS 20B', description: 'Fast, lightweight responses' },
  { key: 'qwen3-27b', label: 'Qwen 3.8 27B', description: 'Balanced quality and speed' },
];

const RETRIEVAL_METHODS = [
  { key: 'Baseline', label: 'Baseline (Cypher)', description: 'Direct graph queries only' },
  { key: 'Embeddings', label: 'Embeddings', description: 'Semantic similarity search' },
  { key: 'Hybrid', label: 'Hybrid', description: 'Cypher + embeddings combined (recommended)' },
] as const;

const EMBEDDING_MODELS = [
  { key: 'minilm', label: 'MiniLM (Fast)', description: '384-dim · faster, lighter' },
  { key: 'mpnet', label: 'MPNet (Quality)', description: '768-dim · higher accuracy' },
] as const;

type Status = 'ok' | 'error' | 'pending';

const STATUS_META: Record<Status, { icon: ReactNode; className: string; word: string }> = {
  ok: { icon: <CheckCircle2 className="h-4 w-4" aria-hidden="true" />, className: 'text-success', word: 'OK' },
  error: { icon: <XCircle className="h-4 w-4" aria-hidden="true" />, className: 'text-danger', word: 'Problem' },
  pending: { icon: <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />, className: 'text-warning', word: 'In progress' },
};

function StatusRow({ label, value, status }: { label: string; value: string; status: Status }) {
  const meta = STATUS_META[status];
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5 sm:px-5">
      <dt className="text-data text-fg-muted">{label}</dt>
      <dd className={cn('flex min-w-0 items-center gap-1.5 text-right text-data font-semibold', meta.className)}>
        {meta.icon}
        <span className="sr-only">{meta.word}:</span>
        <span className="truncate">{value}</span>
      </dd>
    </div>
  );
}

function compact(n: number) {
  return n.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 });
}

function SystemStatus() {
  const { data: health, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: () => apiService.getHealth(),
    // Polling is owned by the app shell (always mounted) on the same ['health'] key; a second
    // interval here would double the /health traffic while this page is open.
  });

  return (
    <Card padded={false}>
      <div className="flex items-start justify-between gap-3 px-4 pt-4 sm:px-5 sm:pt-5">
        <SectionHeading
          as="h2"
          size="md"
          className="mb-3"
          title={
            <span className="flex items-center gap-2">
              <Server className="h-4 w-4 text-accent" aria-hidden="true" />
              System status
            </span>
          }
          subtitle="Checked every 30 seconds"
        />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh status"
          icon={<RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} aria-hidden="true" />}
        />
      </div>

      {isLoading ? (
        <SkeletonRows rows={5} className="px-4 pb-4 sm:px-5 sm:pb-5" />
      ) : isError || !health ? (
        <ErrorState
          title="API unreachable"
          description={error ? handleApiError(error) : 'The backend did not respond.'}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          <dl className="divide-y divide-line border-t border-line">
            <StatusRow
              label="API"
              value={health.status === 'healthy' ? 'Online' : 'Error'}
              status={health.status === 'healthy' ? 'ok' : 'error'}
            />
            <StatusRow
              label="Neo4j"
              value={
                health.neo4j === 'connected' && health.neo4j_stats
                  ? `Connected · ${compact(health.neo4j_stats.total_nodes)} nodes`
                  : health.neo4j === 'connected'
                    ? 'Connected'
                    : health.neo4j
              }
              status={health.neo4j === 'connected' ? 'ok' : 'error'}
            />
            <StatusRow
              label="LLM"
              value={health.llm_available ? 'Available' : 'Not configured'}
              status={health.llm_available ? 'ok' : 'error'}
            />
            <StatusRow
              label="Embeddings"
              value={
                health.embeddings_building
                  ? 'Building…'
                  : health.embeddings_built
                    ? `${health.embedding_count.toLocaleString()} vectors`
                    : 'Not built'
              }
              status={health.embeddings_building ? 'pending' : health.embeddings_built ? 'ok' : 'error'}
            />
            <StatusRow
              label="ML engine"
              value={health.ml_available ? `Online · ${health.ml_model_type || 'XGBoost'}` : 'Offline'}
              status={health.ml_available ? 'ok' : 'error'}
            />
          </dl>
          {health.embedding_build_error && (
            <p role="alert" className="border-t border-line px-4 py-3 text-data text-danger sm:px-5">
              Last build error: {health.embedding_build_error}
            </p>
          )}
        </>
      )}
    </Card>
  );
}

function Preferences() {
  const { club, set } = useFavouriteClub();
  const { data: clubs } = useClubs();
  const watchlist = useWatchlist();
  const [teamId, setTeamId] = useState<number | null>(() => readSavedTeamId());
  const options = clubs ?? [];
  const clubList = club && !options.includes(club) ? [club, ...options] : options;

  return (
    <Card>
      <SectionHeading
        as="h2"
        size="md"
        title={
          <span className="flex items-center gap-2">
            <Star className="h-4 w-4 text-accent" aria-hidden="true" />
            Your preferences
          </span>
        }
        subtitle="Kept only in this browser. There are no accounts."
      />
      <dl className="divide-y divide-line">
        <div className="grid gap-2 py-3 first:pt-0 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:items-center">
          <dt>
            <label htmlFor="favourite-club" className="text-data font-semibold text-fg">Favourite club</label>
          </dt>
          <dd>
            <Select
              id="favourite-club"
              value={club ?? ''}
              onChange={(e) => set(e.target.value || null)}
              className="max-w-sm"
            >
              <option value="">None</option>
              {clubList.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
          </dd>
        </div>
        <div className="grid gap-2 py-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:items-center">
          <dt className="text-data font-semibold text-fg">FPL team</dt>
          <dd className="flex flex-wrap items-center gap-3">
            <span className="text-data text-fg-muted">{teamId ? `Team ID ${teamId} is saved` : 'No team ID saved'}</span>
            {teamId && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  forgetTeamId();
                  setTeamId(null);
                }}
              >
                Forget
              </Button>
            )}
          </dd>
        </div>
        <div className="grid gap-2 py-3 last:pb-0 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:items-center">
          <dt className="text-data font-semibold text-fg">Watchlist</dt>
          <dd className="flex flex-wrap items-center gap-3">
            <span className="text-data text-fg-muted">
              {watchlist.names.length} {watchlist.names.length === 1 ? 'player' : 'players'}
            </span>
            {watchlist.names.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => watchlist.names.forEach((n) => watchlist.remove(n))}
              >
                Clear
              </Button>
            )}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

export function Settings() {
  usePageMeta({ title: "Settings", description: "Set your favourite club, forget your saved FPL team or watchlist, and adjust the assistant's technical options." });
  const {
    selectedModel,
    retrievalMethod,
    embeddingModel,
    setSelectedModel,
    setRetrievalMethod,
    setEmbeddingModel,
  } = useAppStore();

  return (
    <Container className="pb-12">
      <PageHeader
        eyebrow="Preferences"
        title="Settings"
        description="Your favourite club, saved team and watchlist. The technical options for the assistant are under Advanced."
      />

      <div className="space-y-4 lg:space-y-6">
        <Preferences />

        <details className="group rounded-lg border border-line bg-surface/60">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 sm:px-5 [&::-webkit-details-marker]:hidden">
            <span className="font-display text-h3 font-bold text-fg">Advanced</span>
            <ChevronDown className="h-4 w-4 text-fg-subtle transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>
          <div className="space-y-4 border-t border-line p-4 sm:p-5 lg:space-y-6">
            <p className="text-data text-fg-muted">
              These are technical options for the assistant: which language model answers and how it looks facts up. The defaults work well, and changes apply to your next question.
            </p>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] lg:gap-6">
        <div className="space-y-4 lg:space-y-6">
          <Card>
            <SectionHeading
              as="h2"
              size="md"
              title={
                <span className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-accent" aria-hidden="true" />
                  Model
                </span>
              }
              subtitle="All models are served by Groq. If one is over quota, the next one answers instead."
            />
            <OptionGroup
              name="model"
              legend="Language model"
              options={MODELS}
              value={selectedModel}
              onChange={setSelectedModel}
            />
          </Card>

          <Card>
            <SectionHeading
              as="h2"
              size="md"
              title={
                <span className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-accent" aria-hidden="true" />
                  Retrieval
                </span>
              }
              subtitle="How facts are pulled from the graph before the model writes an answer."
            />
            <div className="space-y-5">
              <OptionGroup
                name="retrieval"
                legend="Method"
                options={RETRIEVAL_METHODS}
                value={retrievalMethod}
                onChange={setRetrievalMethod}
                columns={3}
              />
              <OptionGroup
                name="embedding"
                legend="Embedding model"
                hint="Used by the Embeddings and Hybrid methods. Both are prebuilt; switching takes effect on the next query."
                options={EMBEDDING_MODELS}
                value={embeddingModel}
                onChange={setEmbeddingModel}
                columns={2}
              />
            </div>
          </Card>
        </div>

        <SystemStatus />
      </div>
          </div>
        </details>
      </div>
    </Container>
  );
}
