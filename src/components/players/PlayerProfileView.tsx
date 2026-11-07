import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react';
import { apiService, handleApiError, isNotFoundError, type PlayerProfile } from '../../services/api';
import { cn } from '../../utils/cn';
import {
  Card,
  Chip,
  ClubCrest,
  EmptyState,
  ErrorState,
  PlayerAvatar,
  PositionBadge,
  SectionHeading,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
} from '../ui';
import { PointsTimeline } from './PointsTimeline';
import { PlayerShare } from './PlayerShare';
import { ALL_SEASONS, PLAYER_SEASONS } from './seasons';


/** What the opener already knows, shown while the profile loads. */
export interface PlayerHint {
  name: string;
  team?: string | null;
  position?: string | null;
  photo?: string | null;
  status?: string | null;
  news?: string | null;
  chance?: number | null;
}

interface StatRow {
  key: string;
  label: string;
  /** Show the per-90 rate (from the profile's per90). */
  rate?: boolean;
  decimals?: number;
  hint?: string;
}

const GROUPS: { title: string; note?: string; rows: StatRow[]; positions?: string[] }[] = [
  {
    title: 'Attacking',
    note: "FPL doesn't publish shots. Threat is its measure of shots and attacking positions, and xG the quality of the chances.",
    rows: [
      { key: 'goals', label: 'Goals', rate: true },
      { key: 'xg', label: 'Expected goals (xG)', rate: true, decimals: 2 },
      { key: 'assists', label: 'Assists', rate: true },
      { key: 'xa', label: 'Expected assists (xA)', rate: true, decimals: 2 },
      { key: 'xgi', label: 'Expected goal involvements', rate: true, decimals: 2 },
      { key: 'threat', label: 'Threat', rate: true, decimals: 0 },
      { key: 'creativity', label: 'Creativity', rate: true, decimals: 0 },
      { key: 'penalties_missed', label: 'Penalties missed' },
    ],
  },
  {
    title: 'Defending',
    rows: [
      { key: 'clean_sheets', label: 'Clean sheets' },
      { key: 'goals_conceded', label: 'Goals conceded (on the pitch)', rate: true },
      { key: 'xgc', label: 'Expected goals conceded', rate: true, decimals: 2 },
      { key: 'defensive_contribution', label: 'Defensive contributions', rate: true },
      { key: 'tackles', label: 'Tackles', rate: true },
      { key: 'cbi', label: 'Clearances, blocks, interceptions', rate: true },
      { key: 'recoveries', label: 'Recoveries', rate: true },
      { key: 'own_goals', label: 'Own goals' },
    ],
  },
  {
    title: 'Goalkeeping',
    positions: ['GK'],
    rows: [
      { key: 'saves', label: 'Saves', rate: true },
      { key: 'penalties_saved', label: 'Penalties saved' },
    ],
  },
  {
    title: 'Bonus and discipline',
    rows: [
      { key: 'bonus', label: 'Bonus points' },
      { key: 'bps', label: 'Bonus points system (BPS)', rate: true, decimals: 0 },
      { key: 'influence', label: 'Influence', rate: true, decimals: 0 },
      { key: 'ict_index', label: 'ICT index', decimals: 1 },
      { key: 'yellow_cards', label: 'Yellow cards' },
      { key: 'red_cards', label: 'Red cards' },
    ],
  },
];

const fmt = (v: number | null | undefined, decimals = 0) =>
  v == null ? '–' : decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString();

function StatGroup({ title, note, rows, profile }: { title: string; note?: string; rows: StatRow[]; profile: PlayerProfile }) {
  const shown = rows.filter((r) => profile.totals[r.key] != null);
  if (!shown.length) return null;
  return (
    <Card padded={false}>
      <h4 className="border-b border-line px-4 py-2.5 font-display text-h3 font-bold text-fg">{title}</h4>
      <table className="w-full text-data">
        <caption className="sr-only">
          {title} stats, {profile.season}
        </caption>
        <thead className="sr-only">
          <tr>
            <th scope="col">Stat</th>
            <th scope="col">Total</th>
            <th scope="col">Per 90 minutes</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.key} className="border-b border-line last:border-0">
              <th scope="row" className="px-4 py-2 text-left font-normal text-fg-muted">
                {r.label}
              </th>
              <td className="px-2 py-2 text-right font-semibold tabular-nums text-fg">
                {fmt(profile.totals[r.key], r.decimals && r.decimals > 0 ? Math.min(r.decimals, 2) : 0)}
              </td>
              <td className="w-24 px-4 py-2 text-right tabular-nums text-fg-subtle">
                {r.rate && profile.per90[r.key] != null ? `${(profile.per90[r.key] as number).toFixed(2)} /90` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {note && <p className="border-t border-line px-4 py-2 text-micro text-fg-subtle">{note}</p>}
    </Card>
  );
}

/** Where the points came from: one bar per source, losses in red, against the largest source. */
function PointsBreakdown({ profile }: { profile: PlayerProfile }) {
  const items = [...profile.breakdown].sort((a, b) => b.points - a.points);
  if (!items.length) return <p className="text-data text-fg-subtle">No points yet.</p>;
  const max = Math.max(...items.map((i) => Math.abs(i.points)), 1);
  const total = profile.totals.total_points ?? 0;
  return (
    <ol className="flex flex-col gap-2">
      {items.map((i) => (
        <li key={i.key}>
          <div className="flex items-baseline justify-between gap-3 text-data">
            <span className="text-fg">{i.label}</span>
            <span className="font-semibold tabular-nums text-fg">
              {i.points > 0 ? '+' : ''}
              {i.points}
              {total > 0 && i.points > 0 && (
                <span className="ml-1.5 font-normal text-fg-subtle">{Math.round((i.points / total) * 100)}%</span>
              )}
            </span>
          </div>
          <div aria-hidden="true" className="mt-1 h-1.5 rounded-full bg-surface-2">
            <div
              className={cn('h-full rounded-full', i.points < 0 ? 'bg-danger' : 'bg-accent')}
              style={{ width: `${(Math.abs(i.points) / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}

interface PlayerProfileViewProps {
  player: PlayerHint;
  initialSeason?: string;
  /** Left of the season picker, e.g. a Back button. */
  toolbar?: ReactNode;
  /** Called when the person picks another season (the player page keeps it in the URL). */
  onSeasonChange?: (season: string) => void;
  /** Narrower layout for the overlay. */
  compact?: boolean;
  /** Heading level of the player's name (h2 on a page, h2 in the dialog too). */
  headingId?: string;
}

/** A player's season (or career): header, key numbers, points by gameweek, where the points came
 * from and every stat FPL has, with per-90 rates. Used by /players and the player overlay. */
export function PlayerProfileView({ player, initialSeason, toolbar, onSeasonChange, compact = false, headingId }: PlayerProfileViewProps) {
  const start = !initialSeason || initialSeason === ALL_SEASONS ? PLAYER_SEASONS[0] : initialSeason;
  const [season, setSeason] = useState(start);
  useEffect(() => setSeason(start), [player.name, start]);

  const query = useQuery({
    queryKey: ['players', 'profile', player.name, season],
    queryFn: ({ signal }) => apiService.getPlayerProfile(player.name, season, signal),
    staleTime: 10 * 60 * 1000,
    retry: (count, err) => !isNotFoundError(err) && count < 1,
    // Keep the previous season on screen (dimmed) while the next one loads, for the same player.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[2] === player.name ? prev : undefined),
  });
  const p = query.data;
  const loading = query.isFetching;
  const empty = p && p.matches === 0;

  const name = p?.name ?? player.name;
  const team = p?.team ?? player.team ?? null;
  const position = p?.position ?? player.position ?? null;
  const status = p ? p.status : player.status;
  const news = p ? p.news : player.news;
  const chance = p ? p.chance_of_playing_next_round : player.chance;
  const t = p?.totals ?? {};
  const isGk = position === 'GK';
  const isDefensive = position === 'GK' || position === 'DEF';

  return (
    <div className="space-y-4">
      {/* In the overlay, room on the right for its close button */}
      <div className={cn('flex flex-wrap items-center justify-between gap-3', compact && 'pr-11')}>
        <div className="min-w-0">{toolbar}</div>
        <label className="flex items-center gap-3">
          <span className="text-micro font-semibold uppercase text-fg-muted">Season</span>
          <Select value={season} onChange={(e) => {
              setSeason(e.target.value);
              onSeasonChange?.(e.target.value);
            }} className="w-40">
            {[...PLAYER_SEASONS, ALL_SEASONS].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>
      </div>

      <Card tone="brand" className="relative overflow-hidden" aria-busy={loading}>
        {loading && (
          <div role="status" className="absolute right-3 top-3 z-10 inline-flex items-center gap-2 rounded-full bg-bg/70 px-3 py-1 text-micro text-fg-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            Loading {season}
          </div>
        )}
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-4">
            <PlayerAvatar
              name={name}
              src={p?.photo ?? player.photo}
              size={compact ? 72 : 96}
              loading="eager"
              className="border border-brand-2 bg-surface"
            />
            <div className="min-w-0">
              <p className="text-micro font-semibold uppercase text-accent">{season}</p>
              <h2 id={headingId} className={cn('mt-0.5 font-display font-extrabold leading-tight text-fg', compact ? 'text-h2' : 'text-h1')}>
                {name}
              </h2>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-data text-fg-muted">
                {team && <ClubCrest team={team} size={20} label="full" className="font-semibold text-fg" />}
                <PositionBadge position={position ?? undefined} />
                <StatusBadge status={status} chance={chance} news={news} />
                {p?.predicted_points != null && (
                  <Chip tone="accent" icon={<Sparkles className="h-3 w-3" aria-hidden="true" />}>
                    Next GW: {p.predicted_points.toFixed(1)} pts
                  </Chip>
                )}
              </div>
              {(p?.price != null || p?.selected != null) && (
                <p className="mt-1.5 text-data text-fg-muted">
                  {p?.price != null && (
                    <>
                      <span className="font-semibold text-fg">£{p.price.toFixed(1)}m</span>
                      {season === ALL_SEASONS ? ' latest price' : ' price'}
                    </>
                  )}
                  {p?.price != null && p?.selected != null && ' · '}
                  {p?.selected != null && (
                    <>
                      selected by <span className="font-semibold text-fg">{p.selected.toLocaleString()}</span> managers
                    </>
                  )}
                </p>
              )}
              {news && (
                <p className="mt-2 flex items-start gap-1.5 text-data text-warning">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>{news}</span>
                </p>
              )}
            </div>
          </div>
          {p && !empty && (
            <div className="shrink-0 rounded-md border border-brand-2 bg-bg/30 px-4 py-3 sm:text-right">
              <p className="text-micro font-semibold uppercase text-fg-subtle">FPL points</p>
              <p className="font-display text-display font-extrabold leading-none tabular-nums text-accent">
                {fmt(t.total_points)}
              </p>
              <p className="mt-1 text-data text-fg-muted">
                {p.points_per_game != null ? `${p.points_per_game.toFixed(2)} per match played` : 'No matches played'}
              </p>
            </div>
          )}
        </div>
        {p && !empty && (
          <div className="mt-4 flex justify-end">
            <PlayerShare profile={p} photo={player.photo} />
          </div>
        )}
      </Card>

      {query.isError && !p && (
        <Card padded={false}>
          {isNotFoundError(query.error) ? (
            <EmptyState title={`No stats for ${player.name}`} description="This player isn't in the database." />
          ) : (
            <ErrorState title="Couldn't load stats" description={handleApiError(query.error)} onRetry={() => query.refetch()} />
          )}
        </Card>
      )}

      {!p && query.isLoading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}

      {empty && (
        <p role="status" className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-subtle">
          No {season} matches for {name}. Pick another season.
        </p>
      )}

      {p && !empty && (
        <div className={cn('space-y-4 transition-opacity', query.isPlaceholderData && 'opacity-60')}>
          <div className={cn('grid grid-cols-2 gap-3 sm:grid-cols-3', !compact && 'lg:grid-cols-6')}>
            <StatCard
              label="Minutes"
              value={fmt(t.minutes)}
              detail={`${p.appearances} apps${t.starts != null ? ` · ${fmt(t.starts)} starts` : ''}`}
            />
            <StatCard
              label="Goals"
              value={fmt(t.goals)}
              detail={t.xg != null ? `${t.xg.toFixed(1)} xG` : p.per90.goals != null ? `${p.per90.goals.toFixed(2)} per 90` : undefined}
            />
            <StatCard
              label="Assists"
              value={fmt(t.assists)}
              detail={t.xa != null ? `${t.xa.toFixed(1)} xA` : p.per90.assists != null ? `${p.per90.assists.toFixed(2)} per 90` : undefined}
            />
            <StatCard
              label="Clean sheets"
              value={fmt(t.clean_sheets)}
              detail={isDefensive ? `${fmt(t.goals_conceded)} conceded` : undefined}
            />
            {isGk ? (
              <StatCard label="Saves" value={fmt(t.saves)} detail={p.per90.saves != null ? `${p.per90.saves.toFixed(2)} per 90` : undefined} />
            ) : (
              <StatCard
                label="Def. contributions"
                value={fmt(t.defensive_contribution)}
                detail={t.defensive_contribution == null ? 'Counted from 2025-26' : p.per90.defensive_contribution != null ? `${p.per90.defensive_contribution.toFixed(1)} per 90` : undefined}
              />
            )}
            <StatCard label="Bonus" value={fmt(t.bonus)} detail={`${fmt(t.bps)} BPS`} />
          </div>

          <div className={cn('grid gap-4', !compact && 'lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]')}>
            <Card>
              <SectionHeading
                as="h3"
                size="md"
                title={season === ALL_SEASONS ? 'Points by season' : 'Points by gameweek'}
                subtitle={season === ALL_SEASONS ? `${p.seasons.length} seasons` : `${p.matches} matches, ${p.appearances} played`}
              />
              <PointsTimeline profile={p} />
            </Card>
            <Card>
              <SectionHeading as="h3" size="md" title="Where the points came from" subtitle="By FPL's scoring rules" />
              <PointsBreakdown profile={p} />
            </Card>
          </div>

          <div>
            <SectionHeading as="h3" size="md" title="All stats" subtitle="Totals, and per 90 minutes played (from 90 minutes on)" />
            <div className={cn('grid gap-4', !compact && 'md:grid-cols-2')}>
              {GROUPS.filter((g) => !g.positions || g.positions.includes(position ?? '')).map((g) => (
                <StatGroup key={g.title} title={g.title} note={g.note} rows={g.rows} profile={p} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
