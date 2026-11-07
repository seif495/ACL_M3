import { useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { handleApiError } from '../services/api';
import { insightsApi, type InsightPlayer, type Position, type TransferMove } from '../services/insightsApi';
import { cn } from '../utils/cn';
import { displayName } from '../utils/playerNames';
import { getClubAbbreviation } from '../utils/clubLogos';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import { ClubSetPiecesCard } from '../components/insights/ClubSetPiecesCard';
import {
  Card,
  Chip,
  ClubCrest,
  Container,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  PlayerAvatar,
  PositionBadge,
  SectionHeading,
  Select,
  Skeleton,
  StatusBadge,
  TabPanel,
  Tabs,
  type Column,
} from '../components/ui';

type Tab = 'captains' | 'differentials' | 'transfers' | 'set-pieces';
const TABS: { value: Tab; label: string }[] = [
  { value: 'captains', label: 'Captains' },
  { value: 'differentials', label: 'Differentials & value' },
  { value: 'transfers', label: 'Transfers & prices' },
  { value: 'set-pieces', label: 'Set pieces' },
];
const OWNERSHIP_CHOICES = [5, 10, 20];
const POSITION_NAMES: Record<Position, string> = { GK: 'Goalkeepers', DEF: 'Defenders', MID: 'Midfielders', FWD: 'Forwards' };

const OWNERSHIP_LABEL = { differential: 'Differential', safe: 'Safe', template: 'Template' } as const;
const OWNERSHIP_TONE = { differential: 'info', safe: 'neutral', template: 'neutral' } as const;

const pct = (v: number | null | undefined) => (v == null ? '–' : `${v.toFixed(1)}%`);
const money = (v: number | null | undefined) => (v == null ? '–' : `£${v.toFixed(1)}m`);
const dec = (v: number | null | undefined) => (v == null ? '–' : v.toFixed(1));

interface Openable {
  name: string;
  team?: string | null;
  position?: string | null;
  photo?: string | null;
  status?: string;
  news?: string | null;
  chance_of_playing?: number;
}

/** Opens the stats overlay with the player's FULL name (what every lookup keys on). */
function useOpenPlayer() {
  const dialog = usePlayerDialog();
  return (p: Openable) =>
    dialog?.openPlayer({
      name: p.name,
      team: p.team,
      position: p.position,
      photo: p.photo,
      status: p.status,
      news: p.news,
      chance: p.chance_of_playing != null && p.chance_of_playing < 1 ? Math.round(p.chance_of_playing * 100) : null,
    });
}

interface BoundaryQuery {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

function QueryBoundary({ query, children }: { query: BoundaryQuery; children: ReactNode }) {
  if (query.isLoading) return <Skeleton className="h-96 w-full" />;
  if (query.isError) return <ErrorState description={handleApiError(query.error)} onRetry={() => void query.refetch()} />;
  return <>{children}</>;
}

function Note({ children }: { children: ReactNode }) {
  return <p className="mb-4 rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-muted">{children}</p>;
}

/** Opponents with crests, "ARS (H)". */
function Opponents({ fixtures }: { fixtures: InsightPlayer['fixtures'] }) {
  if (!fixtures.length) return <span className="text-fg-subtle">No fixture</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {fixtures.map((f, i) => (
        <span key={i} className="inline-flex items-center gap-1" title={`${f.opponent} (${f.home ? 'home' : 'away'})`}>
          <ClubCrest team={f.opponent} size={16} />
          <span className="font-semibold">{getClubAbbreviation(f.opponent) || f.opponent}</span>
          <span className="text-fg-muted">
            <span aria-hidden="true">({f.home ? 'H' : 'A'})</span>
            <span className="sr-only">{f.home ? 'at home' : 'away'}</span>
          </span>
        </span>
      ))}
    </span>
  );
}

function PlayerButton({ p, children }: { p: Openable; children: ReactNode }) {
  const open = useOpenPlayer();
  return (
    <button type="button" onClick={() => open(p)} className="min-w-0 text-left font-semibold text-fg hover:text-accent focus-visible:text-accent">
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ captains

function Captains() {
  const query = useQuery({ queryKey: ['insights', 'captains'], queryFn: () => insightsApi.captains(10), staleTime: 5 * 60_000 });
  const data = query.data;
  const captain = data?.options.find((o) => o.role === 'captain');
  const vice = data?.options.find((o) => o.role === 'vice');
  return (
    <QueryBoundary query={query}>
      {data?.note && <Note>{data.note}</Note>}
      {data && data.options.length > 0 && (
        <>
          {captain && (
            <Card tone="brand" className="mb-4">
              <p className="text-micro font-semibold uppercase text-fg-muted">The model&rsquo;s armband</p>
              <p className="mt-1 text-body text-fg">
                Captain <strong>{displayName(captain.name, captain.web_name)}</strong>
                {vice && (
                  <>
                    , vice-captain <strong>{displayName(vice.name, vice.web_name)}</strong>
                  </>
                )}
                {data.expected_extra_points != null && <> for about {data.expected_extra_points.toFixed(1)} extra points.</>}
              </p>
              <p className="mt-2 text-data text-fg-muted">
                Why the vice matters: FPL doubles the vice-captain&rsquo;s points when the captain doesn&rsquo;t play. The pick is the pair
                that maximises the captain&rsquo;s points plus the chance he misses out times the vice&rsquo;s, so a doubtful star can lose
                the armband to a safer player.
              </p>
            </Card>
          )}
          <ol className="space-y-2">
            {data.options.map((p, i) => (
              <li key={p.name}>
                <Card padded={false} className={cn('px-3 py-3 sm:px-4', p.role === 'captain' && 'border-accent', p.role === 'vice' && 'border-info')}>
                  <div className="flex items-center gap-3">
                    <span className="w-5 shrink-0 text-center font-display text-h3 font-bold tabular-nums text-fg-subtle">{i + 1}</span>
                    <PlayerAvatar name={p.name} src={p.photo} size={44} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <PlayerButton p={p}>
                          <span className="block truncate">{displayName(p.name, p.web_name)}</span>
                        </PlayerButton>
                        {p.role === 'captain' && <Chip tone="accent">Captain</Chip>}
                        {p.role === 'vice' && <Chip tone="info">Vice</Chip>}
                        <StatusBadge status={p.status} chance={Math.round(p.chance_of_playing * 100)} news={p.news} />
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-data text-fg-muted">
                        <span className="inline-flex items-center gap-1.5">
                          <ClubCrest team={p.team ?? ''} size={16} />
                          {p.team}
                        </span>
                        <span className="inline-flex items-center gap-1">
                          vs <Opponents fixtures={p.fixtures} />
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-fg-subtle">
                        <span>Plays: {Math.round(p.chance_of_playing * 100)}%</span>
                        <span>Owned: {pct(p.selected_by_percent)}</span>
                        {p.ownership && <Chip tone={OWNERSHIP_TONE[p.ownership]}>{OWNERSHIP_LABEL[p.ownership]}</Chip>}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <span className="block font-display text-h2 font-bold tabular-nums text-accent">{p.predicted_points.toFixed(1)}</span>
                      <span className="block text-micro uppercase text-fg-subtle">Pred. pts</span>
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-micro text-fg-subtle">
            Ranked by predicted points{data.gameweek ? ` for gameweek ${data.gameweek}` : ''}. Differential means under 10% owned; template
            means 25% or more.
          </p>
        </>
      )}
    </QueryBoundary>
  );
}

// ------------------------------------------------------------------ differentials & value

function playerColumns(open: ReturnType<typeof useOpenPlayer>, projections: boolean): Column<InsightPlayer>[] {
  const cols: Column<InsightPlayer>[] = [
    {
      key: 'name',
      header: 'Player',
      sticky: true,
      sortValue: (r) => displayName(r.name, r.web_name),
      render: (r) => (
        <span className="flex max-w-[11rem] items-center gap-2 sm:max-w-[16rem]">
          <PlayerAvatar name={r.name} src={r.photo} size={32} />
          <span className="min-w-0">
            <button type="button" onClick={() => open(r)} className="block max-w-full truncate text-left font-semibold text-fg hover:text-accent">
              {displayName(r.name, r.web_name)}
            </button>
            <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
              <PositionBadge position={r.position} className="w-8" />
              {getClubAbbreviation(r.team ?? '') || r.team}
            </span>
          </span>
        </span>
      ),
    },
    { key: 'opp', header: 'Opp', hideBelow: 'md', render: (r) => <Opponents fixtures={r.fixtures} /> },
    { key: 'price', header: 'Price', align: 'right', sortValue: (r) => r.price, render: (r) => money(r.price) },
    { key: 'owned', header: 'Own', description: 'Selected by', align: 'right', sortValue: (r) => r.selected_by_percent, render: (r) => pct(r.selected_by_percent) },
    {
      key: 'pts',
      header: 'xPts',
      description: 'Predicted points next gameweek',
      align: 'right',
      sortValue: (r) => r.predicted_points,
      render: (r) => <span className="font-bold tabular-nums text-accent">{r.predicted_points.toFixed(1)}</span>,
    },
  ];
  if (projections) {
    cols.push({
      key: 'pts4',
      header: '4 GW',
      description: 'Predicted points over the next four gameweeks',
      align: 'right',
      sortValue: (r) => r.points_4gw,
      render: (r) => dec(r.points_4gw),
    });
  }
  cols.push({ key: 'ppm', header: '/£m', description: 'Points per £m, next gameweek', align: 'right', sortValue: (r) => r.points_per_m, render: (r) => dec(r.points_per_m) });
  if (projections) {
    cols.push({
      key: 'ppm4',
      header: '/£m 4',
      description: 'Points per £m over four gameweeks',
      align: 'right',
      hideBelow: 'sm',
      sortValue: (r) => r.points_per_m_4gw,
      render: (r) => dec(r.points_per_m_4gw),
    });
  }
  return cols;
}

function Differentials() {
  const [params, setParams] = useSearchParams();
  const position = (['GK', 'DEF', 'MID', 'FWD'] as const).find((p) => p === params.get('pos'));
  const own = OWNERSHIP_CHOICES.find((o) => String(o) === params.get('own')) ?? 10;
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };
  const query = useQuery({
    queryKey: ['insights', 'differentials', position ?? 'all', own],
    queryFn: () => insightsApi.differentials({ position, max_ownership: own, limit: 30 }),
    staleTime: 5 * 60_000,
  });
  const open = useOpenPlayer();
  const data = query.data;
  const hasProjections = !!data?.has_projections;
  const cols = useMemo(() => playerColumns(open, hasProjections), [open, hasProjections]);
  const budgetPositions = (Object.keys(data?.budget ?? {}) as Position[]).filter((p) => data?.budget[p]?.length);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-micro font-semibold uppercase text-fg-muted">Position</span>
          <Select value={position ?? ''} onChange={(e) => set({ pos: e.target.value || null })} className="w-40">
            <option value="">All positions</option>
            {(Object.keys(POSITION_NAMES) as Position[]).map((p) => (
              <option key={p} value={p}>
                {POSITION_NAMES[p]}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-micro font-semibold uppercase text-fg-muted">Owned by under</span>
          <Select value={String(own)} onChange={(e) => set({ own: e.target.value === '10' ? null : e.target.value })} className="w-32">
            {OWNERSHIP_CHOICES.map((o) => (
              <option key={o} value={o}>
                {o}%
              </option>
            ))}
          </Select>
        </label>
      </div>
      <QueryBoundary query={query}>
        {data?.note && <Note>{data.note}</Note>}
        <section aria-labelledby="diff-heading">
          <SectionHeading id="diff-heading" title="Differentials" subtitle={`Highest predicted points among players owned by under ${own}%`} />
          <DataTable
            caption="Differentials by predicted points"
            columns={cols}
            rows={data?.differentials ?? []}
            rowKey={(r) => r.name}
            initialSort={{ key: 'pts', direction: 'desc' }}
            empty={<EmptyState title="No differentials" description="No players under that ownership have predicted points." />}
          />
        </section>
        {budgetPositions.length > 0 && (
          <section aria-labelledby="budget-heading" className="mt-6">
            <SectionHeading id="budget-heading" title="Budget picks" subtitle="The most predicted points at each position's cheapest price band" />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {budgetPositions.map((pos) => (
                <Card key={pos} padded={false} className="min-w-0">
                  <h3 className="flex items-center justify-between border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">
                    {POSITION_NAMES[pos]}
                    <span className="text-micro font-semibold uppercase text-fg-subtle">up to {money(data?.budget_caps[pos])}</span>
                  </h3>
                  <ul>
                    {data?.budget[pos]?.map((p) => (
                      <li key={p.name} className="flex items-center gap-3 border-b border-line px-4 py-2.5 last:border-0">
                        <PlayerAvatar name={p.name} src={p.photo} size={32} />
                        <span className="min-w-0 flex-1">
                          <PlayerButton p={p}>
                            <span className="block truncate">{displayName(p.name, p.web_name)}</span>
                          </PlayerButton>
                          <span className="block truncate text-micro text-fg-subtle">
                            {getClubAbbreviation(p.team ?? '') || p.team} · {money(p.price)} · {pct(p.selected_by_percent)} owned
                          </span>
                        </span>
                        <span className="text-right">
                          <span className="block font-bold tabular-nums text-accent">{p.predicted_points.toFixed(1)}</span>
                          <span className="block text-micro text-fg-subtle">{dec(p.points_per_m)}/£m</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              ))}
            </div>
          </section>
        )}
      </QueryBoundary>
    </div>
  );
}

// ------------------------------------------------------------------ transfers & prices

function MoveList({ title, items, unit, kind }: { title: string; items: TransferMove[]; unit: string; kind: 'transfers' | 'change' }) {
  const open = useOpenPlayer();
  return (
    <Card padded={false} className="min-w-0">
      <h3 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">{title}</h3>
      {items.length === 0 ? (
        <p className="px-4 py-6 text-data text-fg-subtle">None this gameweek.</p>
      ) : (
        <ol>
          {items.map((p) => (
            <li key={p.name} className="border-b border-line last:border-0">
              <button type="button" onClick={() => open(p)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2">
                <PlayerAvatar name={p.name} src={p.photo} size={32} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-data font-semibold text-fg">{displayName(p.name, p.web_name)}</span>
                  <span className="block truncate text-micro text-fg-subtle">
                    {getClubAbbreviation(p.team ?? '') || p.team} · {money(p.price)} · {pct(p.selected_by_percent)} owned
                  </span>
                </span>
                <span className={cn('shrink-0 font-display text-body font-bold tabular-nums', kind === 'change' ? ((p.change ?? 0) > 0 ? 'text-accent' : 'text-danger') : 'text-fg')}>
                  {kind === 'transfers' ? (p.transfers ?? 0).toLocaleString() : `${(p.change ?? 0) > 0 ? '+' : '−'}£${Math.abs(p.change ?? 0).toFixed(1)}m`}
                  <span className="sr-only"> {unit}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Transfers() {
  const query = useQuery({ queryKey: ['insights', 'transfers'], queryFn: () => insightsApi.transfers(15), staleTime: 5 * 60_000 });
  const d = query.data;
  return (
    <QueryBoundary query={query}>
      {d?.note && <Note>{d.note}</Note>}
      {d && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MoveList title="Most transferred in" items={d.transfers_in} unit="transfers in" kind="transfers" />
            <MoveList title="Most transferred out" items={d.transfers_out} unit="transfers out" kind="transfers" />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MoveList title="Price risers" items={d.risers} unit="price rise" kind="change" />
            <MoveList title="Price fallers" items={d.fallers} unit="price fall" kind="change" />
          </div>
          <p className="text-micro text-fg-subtle">Counts and price changes are for this gameweek so far, from FPL&rsquo;s player data.</p>
        </div>
      )}
    </QueryBoundary>
  );
}

// ------------------------------------------------------------------ set pieces

function SetPieces() {
  const [params, setParams] = useSearchParams();
  const team = params.get('club') ?? '';
  const query = useQuery({ queryKey: ['insights', 'set-pieces', 'all'], queryFn: () => insightsApi.setPieces(), staleTime: 15 * 60_000 });
  const clubs = query.data?.teams ?? [];
  const shown = team ? clubs.filter((c) => c.team === team) : clubs;
  const change = (value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set('club', value);
    else next.delete('club');
    setParams(next, { replace: true });
  };
  return (
    <div className="space-y-4">
      <label className="flex flex-col gap-1">
        <span className="text-micro font-semibold uppercase text-fg-muted">Club</span>
        <Select value={team} onChange={(e) => change(e.target.value)} className="w-56" disabled={!clubs.length}>
          <option value="">All clubs</option>
          {clubs.map((c) => (
            <option key={c.team} value={c.team}>
              {c.team}
            </option>
          ))}
        </Select>
      </label>
      <QueryBoundary query={query}>
        {query.data?.note && <Note>{query.data.note}</Note>}
        {shown.length > 0 && (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {shown.map((c) => (
                <ClubSetPiecesCard key={c.team} club={c} />
              ))}
            </div>
            <p className="text-micro text-fg-subtle">Takers in FPL&rsquo;s order of preference, 1 first.</p>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}

// ------------------------------------------------------------------ page

export function Insights() {
  usePageMeta({
    title: 'Insights',
    description: "FPL decision tools: the best captain options, differentials and budget picks, this gameweek's transfers and price changes, and set-piece takers.",
  });
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.value === params.get('tab'))?.value ?? 'captains') as Tab;
  const change = (value: Tab) => setParams(value === 'captains' ? {} : { tab: value }, { replace: true });
  return (
    <Container className="pb-10">
      <PageHeader
        eyebrow="Decision tools"
        title="Insights"
        description="Captain picks, differentials, transfer trends and set-piece takers, from the model's predictions and FPL's data."
      />
      <Tabs items={TABS} value={tab} onChange={change} label="Insight sections" idPrefix="insights" />
      <TabPanel idPrefix="insights" value={tab} className="mt-6">
        {tab === 'captains' ? <Captains /> : tab === 'differentials' ? <Differentials /> : tab === 'transfers' ? <Transfers /> : <SetPieces />}
      </TabPanel>
    </Container>
  );
}
