import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { Card, Skeleton } from '../../ui';
import { buildTeam, builderErrorMessage, BUILDER_LIMITS, type BuildResponse } from '../../../services/teamBuilderApi';
import { displayName } from '../../../utils/playerNames';
import { BuilderConstraintList } from './BuilderConstraintList';
import { BuilderControls } from './BuilderControls';
import { BuilderPitch } from './BuilderPitch';
import { BuilderPlayerActions } from './BuilderPlayerActions';
import { BuilderBench, BuilderSummary } from './BuilderSummary';
import {
  constraintsFromParams,
  constraintsKey,
  constraintsToParams,
  DEFAULT_CONSTRAINTS,
  isDefaultConstraints,
  toRequest,
  withExcluded,
  withLocked,
  withoutExcluded,
  withoutLocked,
  type Constraints,
} from './builderModel';

interface Props {
  /** The page's URL parameters: the applied constraints live there (`?mode=build&budget=995&lock=a,b`). */
  params: URLSearchParams;
  setParams: (next: Record<string, string>) => void;
  /** The default predicted team's points, for the comparison; null while loading. */
  defaultPoints: number | null;
}

function BuilderSkeleton() {
  return <Skeleton className="h-[30rem] w-full rounded-lg sm:h-[38rem]" />;
}

/**
 * The Build tab: the predicted squad re-run under your own limits. Edits change a draft; Rebuild
 * writes it to the URL, and the query for that URL posts it to /api/team-of-the-week/build.
 */
export function TeamBuilder({ params, setParams, defaultPoints }: Props) {
  const applied = useMemo(() => constraintsFromParams(params), [params]);
  const appliedKey = constraintsKey(applied);
  const [draft, setDraft] = useState<Constraints>(applied);
  const [selected, setSelected] = useState<string | null>(null);

  // The URL changed (Rebuild, Reset, the back button): the draft starts from it again.
  useEffect(() => {
    setDraft(applied);
  }, [applied]);

  const query = useQuery({
    queryKey: ['team-builder', appliedKey],
    queryFn: ({ signal }) => buildTeam(toRequest(applied), signal),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });

  // The last squad that fit stays on screen while a new request runs or after it fails.
  const [shown, setShown] = useState<BuildResponse | undefined>(undefined);
  useEffect(() => {
    if (query.data) setShown(query.data);
  }, [query.data]);

  const dirty = constraintsKey(draft) !== appliedKey;
  const canReset = !isDefaultConstraints(draft) || !isDefaultConstraints(applied);
  const loading = query.isFetching;

  const rebuild = () => {
    if (!dirty) return;
    setSelected(null);
    setParams(constraintsToParams(draft));
  };
  const reset = () => {
    setSelected(null);
    setDraft(DEFAULT_CONSTRAINTS);
    setParams(constraintsToParams(DEFAULT_CONSTRAINTS));
  };

  // Names to show on chips: FPL's short name for the players on screen, else what was typed or in the URL.
  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const p of [...(shown?.starters ?? []), ...(shown?.bench ?? [])]) out[p.name] = displayName(p.name, p.web_name);
    return out;
  }, [shown]);
  const labelFor = (name: string) => labels[name] ?? name;

  const players = useMemo(() => [...(shown?.starters ?? []), ...(shown?.bench ?? [])], [shown]);
  const selectedPlayer = selected ? players.find((p) => p.name === selected) : undefined;
  const error = query.isError ? builderErrorMessage(query.error) : null;

  return (
    <div className="space-y-6">
      <BuilderControls
        draft={draft}
        onChange={setDraft}
        dirty={dirty}
        loading={loading}
        canReset={canReset}
        onRebuild={rebuild}
        onReset={reset}
      />

      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-fg">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
          <div className="min-w-0">
            <p className="font-semibold">{error}</p>
            {shown && <p className="mt-0.5 text-data text-fg-muted">The squad below is the last one that fit.</p>}
          </div>
        </div>
      )}

      {shown && !shown.optimal && !error && (
        <p role="status" className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-muted">
          These limits took a while to solve, so this is the best squad found in the time allowed, and might not be the very best.
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <div className="min-w-0 space-y-4" aria-busy={loading}>
          {selectedPlayer && (
            <BuilderPlayerActions
              player={selectedPlayer}
              locked={draft.locked.includes(selectedPlayer.name)}
              lockDisabled={draft.locked.length >= BUILDER_LIMITS.maxLocked}
              onLock={() => setDraft((d) => withLocked(d, selectedPlayer.name))}
              onUnlock={() => setDraft((d) => withoutLocked(d, selectedPlayer.name))}
              onExclude={() => {
                setDraft((d) => withExcluded(d, selectedPlayer.name));
                setSelected(null);
              }}
              onClose={() => setSelected(null)}
              dirty={dirty}
              loading={loading}
              onRebuild={rebuild}
            />
          )}
          {shown ? (
            <BuilderPitch
              starters={shown.starters}
              gameweek={shown.gameweek}
              locked={draft.locked}
              selected={selected}
              onSelect={setSelected}
            />
          ) : query.isLoading ? (
            <BuilderSkeleton />
          ) : (
            <Card>
              <p className="text-sm text-fg-muted">No squad to show yet. Change the limits and press Rebuild.</p>
            </Card>
          )}
          <p className="text-data text-fg-subtle">
            Under each player: his predicted points, then the share of FPL managers who own him. Tap a player to lock him
            in or leave him out, then press Rebuild.
          </p>
        </div>

        <aside className="min-w-0 space-y-4">
          {shown && <BuilderSummary team={shown} defaultPoints={defaultPoints} />}
          <BuilderConstraintList
            draft={draft}
            labelFor={labelFor}
            onLock={(name) => setDraft((d) => withLocked(d, name))}
            onExclude={(name) => setDraft((d) => withExcluded(d, name))}
            onUnlock={(name) => setDraft((d) => withoutLocked(d, name))}
            onUnexclude={(name) => setDraft((d) => withoutExcluded(d, name))}
          />
          {shown && <BuilderBench bench={shown.bench} locked={draft.locked} onSelect={setSelected} />}
        </aside>
      </div>
    </div>
  );
}
