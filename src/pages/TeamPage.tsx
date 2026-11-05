import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { apiService, handleApiError, isNotFoundError, type TeamLeader, type TeamSquadPlayer } from '../services/api';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import { getClubLogoPath } from '../utils/clubLogos';
import { clubColors } from '../utils/clubColors';
import { cn } from '../utils/cn';
import { PLAYER_SEASONS } from '../components/players/seasons';
import { ClubHero } from '../components/teams/ClubHero';
import { FormChips, MatchList } from '../components/teams/MatchBits';
import { PositionChart } from '../components/teams/PositionChart';
import { NewsSection } from '../components/news/NewsSection';
import {
  Card,
  ClubCrest,
  Container,
  EmptyState,
  ErrorState,
  PlayerAvatar,
  SectionHeading,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
  TabPanel,
  Tabs,
} from '../components/ui';

type Tab = 'overview' | 'squad' | 'matches' | 'news';
const TABS: { value: Tab; label: string }[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'squad', label: 'Squad' },
  { value: 'matches', label: 'Results & fixtures' },
  { value: 'news', label: 'News' },
];
const POSITION_TITLES: Record<string, string> = { GK: 'Goalkeepers', DEF: 'Defenders', MID: 'Midfielders', FWD: 'Forwards' };

function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

function Leaders({ title, unit, items, team }: { title: string; unit: string; items: TeamLeader[]; team: string }) {
  const dialog = usePlayerDialog();
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <Card padded={false}>
      <h2 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">{title}</h2>
      {items.length === 0 ? (
        <p className="px-4 py-6 text-data text-fg-subtle">None yet.</p>
      ) : (
        <ol>
          {items.map((p, i) => (
            <li key={p.name} className="border-b border-line last:border-0">
              <button
                type="button"
                disabled={!dialog}
                onClick={() => dialog?.openPlayer({ name: p.name, team, photo: p.photo, position: p.position })}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors enabled:hover:bg-surface-2"
              >
                <span className="w-4 text-right text-data font-semibold tabular-nums text-fg-subtle">{i + 1}</span>
                <PlayerAvatar name={p.name} src={p.photo} size={36} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-fg">{p.web_name ?? p.name}</span>
                  <span aria-hidden="true" className="mt-1 block h-1.5 rounded-full bg-surface-2">
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${(p.value / max) * 100}%` }} />
                  </span>
                </span>
                <span className="font-display text-h3 font-bold tabular-nums text-fg">
                  {p.value}
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

function SquadTable({ squad, team, current }: { squad: TeamSquadPlayer[]; team: string; current: boolean }) {
  const dialog = usePlayerDialog();
  const groups = ['GK', 'DEF', 'MID', 'FWD'].map((pos) => ({ pos, players: squad.filter((p) => p.position === pos) }));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {groups.filter((g) => g.players.length).map((g) => (
        <Card key={g.pos} padded={false}>
          <h2 className="flex items-center justify-between border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">
            {POSITION_TITLES[g.pos]}
            <span className="text-micro font-semibold uppercase text-fg-subtle">
              Pts{current ? ' · xPts' : ''}
            </span>
          </h2>
          <ul>
            {g.players.map((p) => (
              <li key={p.name} className="border-b border-line last:border-0">
                <button
                  type="button"
                  disabled={!dialog}
                  onClick={() => dialog?.openPlayer({ name: p.name, team, photo: p.photo, position: p.position, status: p.status, news: p.news, chance: p.chance_of_playing_next_round })}
                  className="grid w-full grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 text-left transition-colors enabled:hover:bg-surface-2"
                >
                  <PlayerAvatar name={p.name} src={p.photo} size={40} />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-semibold text-fg">{p.name}</span>
                      <StatusBadge status={p.status} chance={p.chance_of_playing_next_round} news={p.news} />
                    </span>
                    <span className="block text-micro text-fg-subtle">
                      {p.appearances} apps · {p.goals} G · {p.assists} A{p.price != null ? ` · £${p.price.toFixed(1)}m` : ''}
                    </span>
                  </span>
                  <span className="text-right">
                    <span className="block font-display text-body font-bold tabular-nums text-fg">{p.total_points}</span>
                    {current && (
                      <span className="block text-micro tabular-nums text-accent">
                        {p.predicted_points != null ? p.predicted_points.toFixed(1) : '–'}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

export function TeamPage() {
  const { slug = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const season = params.get('season') ?? PLAYER_SEASONS[0];
  const tab = (TABS.find((t) => t.value === params.get('tab'))?.value ?? 'overview') as Tab;
  const current = season === PLAYER_SEASONS[0];

  const query = useQuery({
    queryKey: ['team', slug, season],
    queryFn: () => apiService.getTeam(slug, current ? undefined : season),
    staleTime: 5 * 60_000,
    retry: (n, e) => !isNotFoundError(e) && n < 1,
  });
  const t = query.data;
  usePageMeta({
    title: t ? `${t.team} ${t.season}` : 'Club',
    description: t ? `${t.team}'s ${t.season} season: position, form, results, fixtures, squad and FPL points.` : 'A Premier League club.',
  });

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    setParams(next, { replace: true });
  };

  if (query.isError) {
    return (
      <Container className="py-10">
        <Card padded={false}>
          {isNotFoundError(query.error) ? (
            <EmptyState title="Club not found" description={`No club "${slug}" in ${season}.`} action={<Link to="/teams" className="text-accent underline">All clubs</Link>} />
          ) : (
            <ErrorState description={handleApiError(query.error)} onRetry={() => query.refetch()} />
          )}
        </Card>
      </Container>
    );
  }

  const colors = clubColors(t?.team);
  const s = t?.standing;
  const next = t?.fixtures[0];
  return (
    <div className="pb-12">
      <ClubHero home={t?.team ?? ''} crest={{ home: t ? getClubLogoPath(t.team) : null }}>
        <Container className="relative py-8 sm:py-10">
          <Link to="/teams" className="inline-flex items-center gap-1 text-data text-fg-muted hover:text-fg">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> All clubs
          </Link>
          {t ? (
            <div className="mt-4 flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
              <div className="flex items-center gap-4 sm:gap-6">
                <span className="rounded-2xl bg-white/5 p-3 ring-1 ring-white/10 backdrop-blur-sm">
                  <ClubCrest team={t.team} size={88} />
                </span>
                <div>
                  <p className="text-micro font-semibold uppercase tracking-wide text-accent">{t.season} season</p>
                  <h1 className="font-display text-h1 font-extrabold leading-tight text-fg sm:text-display">{t.team}</h1>
                  {s && (
                    <p className="mt-1 text-body text-fg-muted">
                      <span className="font-semibold text-fg">{ordinal(s.position)}</span> of {t.teams_in_table} ·{' '}
                      <span className="font-semibold text-fg">{s.points} pts</span> · {s.won}W {s.drawn}D {s.lost}L ·{' '}
                      {s.goal_difference > 0 ? '+' : ''}
                      {s.goal_difference} GD
                    </p>
                  )}
                  <div className="mt-3 flex items-center gap-3">
                    <span className="text-micro font-semibold uppercase text-fg-subtle">Form</span>
                    <FormChips items={t.form.map((m) => ({ fixture_id: m.fixture_id, result: m.result!, opponent: m.opponent, home: m.home, score: m.score }))} />
                  </div>
                </div>
              </div>
              <div className="flex flex-col gap-3 md:items-end">
                <label className="flex items-center gap-2">
                  <span className="text-micro font-semibold uppercase text-fg-muted">Season</span>
                  <Select value={season} onChange={(e) => set({ season: e.target.value === PLAYER_SEASONS[0] ? null : e.target.value })} className="w-32">
                    {PLAYER_SEASONS.map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </Select>
                </label>
                {next && (
                  <Link
                    to={`/matches/${encodeURIComponent(next.fixture_id)}`}
                    className="flex items-center gap-3 rounded-lg border border-white/10 bg-black/25 px-4 py-3 transition hover:border-white/25"
                  >
                    <span className="text-micro font-semibold uppercase text-fg-subtle">Next</span>
                    <ClubCrest team={next.opponent} size={26} />
                    <span className="text-data font-semibold text-fg">
                      {next.opponent} ({next.home ? 'H' : 'A'})
                    </span>
                    <span className="text-micro text-fg-subtle">
                      {next.kickoff_time ? new Date(next.kickoff_time).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'TBC'}
                    </span>
                  </Link>
                )}
              </div>
            </div>
          ) : (
            <Skeleton className="mt-4 h-28 w-full max-w-xl" />
          )}
        </Container>
      </ClubHero>

      <Container className="mt-6">
        <Tabs items={TABS} value={tab} onChange={(v) => set({ tab: v === 'overview' ? null : v })} label="Club sections" idPrefix="team" />
        <TabPanel idPrefix="team" value={tab} className="mt-6">
          {!t ? (
            <Skeleton className="h-96 w-full" />
          ) : tab === 'overview' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatCard
                  label="Goals scored"
                  value={t.stats.goals_for}
                  detail={t.stats.xg_for != null ? `${t.stats.xg_for.toFixed(1)} xG` : `${t.stats.played} matches`}
                  highlight
                />
                <StatCard
                  label="Goals conceded"
                  value={t.stats.goals_against}
                  detail={t.stats.xg_against != null ? `${t.stats.xg_against.toFixed(1)} xG against` : undefined}
                />
                <StatCard label="Clean sheets" value={t.stats.clean_sheets} detail={`${t.stats.failed_to_score} without scoring`} />
                <StatCard
                  label="Home · away"
                  value={
                    <span className="text-h2">
                      {t.stats.home.won}-{t.stats.home.drawn}-{t.stats.home.lost}
                      <span className="text-fg-subtle"> · </span>
                      {t.stats.away.won}-{t.stats.away.drawn}-{t.stats.away.lost}
                    </span>
                  }
                  detail="Won-drawn-lost"
                />
              </div>
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
                <Card className="min-w-0">
                  <SectionHeading as="h2" size="md" title="League position" subtitle="After each gameweek" />
                  <PositionChart history={t.position_history} teams={t.teams_in_table} color={colors.primary === '230 230 230' ? '0 230 140' : colors.primary} />
                </Card>
                <Card padded={false}>
                  <h2 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">Recent results</h2>
                  <MatchList matches={t.results.slice(0, 5)} empty="No results yet." />
                </Card>
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                <Leaders title="Top scorers" unit="goals" items={t.top_scorers} team={t.team} />
                <Leaders title="Most assists" unit="assists" items={t.top_assists} team={t.team} />
                <Card padded={false}>
                  <h2 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">Coming up</h2>
                  <MatchList matches={t.fixtures.slice(0, 5)} empty="No fixtures left this season." />
                </Card>
              </div>
            </div>
          ) : tab === 'squad' ? (
            t.squad.length ? (
              <SquadTable squad={t.squad} team={t.team} current={current} />
            ) : (
              <EmptyState title="No squad listed" description="No players for this club and season." />
            )
          ) : tab === 'matches' ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card padded={false}>
                <h2 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">Results</h2>
                <MatchList matches={t.results} empty="No results yet." />
              </Card>
              <Card padded={false}>
                <h2 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">Fixtures</h2>
                <MatchList matches={t.fixtures} empty="No fixtures left this season." />
              </Card>
            </div>
          ) : (
            <NewsSection team={t.team} title={`${t.team} news`} articles={6} />
          )}
        </TabPanel>
        <p className={cn('mt-6 text-micro text-fg-subtle', tab === 'news' && 'hidden')}>
          Results and xG from FPL&rsquo;s match data. Click a player for their stats, a match for its page.
        </p>
      </Container>
    </div>
  );
}
