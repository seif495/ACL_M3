import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Radio } from 'lucide-react';
import { apiService, handleApiError, isNotFoundError, type MatchPageResponse, type MatchPlayer, type MatchSide } from '../services/api';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import { getClubAbbreviation, getClubLogoPath } from '../utils/clubLogos';
import { clubColors, matchPath, teamPath } from '../utils/clubColors';
import { cn } from '../utils/cn';
import { ClubHero } from '../components/teams/ClubHero';
import { FormChips } from '../components/teams/MatchBits';
import { Card, ClubCrest, Container, EmptyState, ErrorState, PlayerAvatar, PositionBadge, SectionHeading, Skeleton } from '../components/ui';

const LIVE_REFRESH_MS = 60_000;

const short = (name: string) => name.split(' ').slice(-1)[0] || name;
const times = (n: number) => (n > 1 ? ` ×${n}` : '');

function Events({ side, align }: { side: MatchSide; align: 'left' | 'right' }) {
  const lines: { key: string; icon: string; text: string; label: string }[] = [
    ...side.scorers.map((e) => ({ key: `g-${e.name}`, icon: '⚽', text: `${short(e.name)}${times(e.count)}`, label: `Goal: ${e.name}` })),
    ...side.own_goals_for.map((e) => ({ key: `og-${e.name}`, icon: '⚽', text: `${short(e.name)} (og)${times(e.count)}`, label: `Own goal: ${e.name}` })),
    ...side.red_cards.map((e) => ({ key: `r-${e.name}`, icon: '🟥', text: short(e.name), label: `Red card: ${e.name}` })),
    ...side.penalties_missed.map((e) => ({ key: `pm-${e.name}`, icon: '✕', text: `${short(e.name)} (pen missed)`, label: `Penalty missed: ${e.name}` })),
  ];
  if (!lines.length) return null;
  return (
    <ul className={cn('mt-3 space-y-1 text-data text-fg', align === 'right' ? 'text-right' : 'text-left')}>
      {lines.map((l) => (
        <li key={l.key} aria-label={l.label}>
          {align === 'left' ? (
            <>
              <span aria-hidden="true" className="mr-1.5">{l.icon}</span>
              {l.text}
            </>
          ) : (
            <>
              {l.text}
              <span aria-hidden="true" className="ml-1.5">{l.icon}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function StatBar({ label, home, away, format = (v: number) => String(v) }: { label: string; home: number; away: number; format?: (v: number) => string }) {
  const total = home + away || 1;
  return (
    <div>
      <div className="flex items-baseline justify-between text-data">
        <span className="font-display text-body font-bold tabular-nums text-fg">{format(home)}</span>
        <span className="text-micro font-semibold uppercase text-fg-subtle">{label}</span>
        <span className="font-display text-body font-bold tabular-nums text-fg">{format(away)}</span>
      </div>
      <div aria-hidden="true" className="mt-1 flex h-2 gap-1">
        <span className="rounded-l-full" style={{ width: `${(home / total) * 100}%`, background: 'rgb(var(--c1))' }} />
        <span className="rounded-r-full" style={{ width: `${(away / total) * 100}%`, background: 'rgb(var(--c2))' }} />
      </div>
    </div>
  );
}

function PlayerLine({ p, team }: { p: MatchPlayer; team: string }) {
  const dialog = usePlayerDialog();
  const tags = [
    ...Array(p.goals).fill('⚽'),
    ...Array(p.assists).fill('🅰️'),
    ...(p.yellow_cards ? ['🟨'] : []),
    ...(p.red_cards ? ['🟥'] : []),
  ];
  return (
    <li className="border-b border-line last:border-0">
      <button
        type="button"
        disabled={!dialog}
        onClick={() => dialog?.openPlayer({ name: p.name, team, photo: p.photo, position: p.position })}
        className="grid w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2 text-left transition-colors enabled:hover:bg-surface-2 sm:px-4"
      >
        <PlayerAvatar name={p.name} src={p.photo} size={32} />
        <span className="min-w-0">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold text-fg">{p.name}</span>
            {tags.length > 0 && <span aria-hidden="true" className="shrink-0 text-micro">{tags.join('')}</span>}
          </span>
          <span className="flex items-center gap-1.5 text-micro text-fg-subtle">
            <PositionBadge position={p.position ?? undefined} className="w-8" />
            {p.minutes}&prime;{p.bonus ? ` · ${p.bonus} bonus` : ''}
            {p.xg != null && p.xg > 0 ? ` · ${p.xg.toFixed(2)} xG` : ''}
          </span>
        </span>
        <span
          className={cn(
            'min-w-[2.25rem] rounded-md px-2 py-1 text-center font-display text-body font-bold tabular-nums',
            p.points >= 10 ? 'bg-accent text-accent-on' : p.points >= 6 ? 'bg-accent/20 text-accent' : 'bg-surface-2 text-fg',
          )}
        >
          {p.points}
          <span className="sr-only"> FPL points</span>
        </span>
      </button>
    </li>
  );
}

function Lineup({ side, team }: { side: MatchSide; team: string }) {
  return (
    <Card padded={false}>
      <h3 className="flex items-center gap-2 border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">
        <ClubCrest team={team} size={22} />
        {team}
        <span className="ml-auto text-micro font-semibold uppercase text-fg-subtle">{side.points} FPL pts</span>
      </h3>
      <p className="bg-surface-2/50 px-4 py-1.5 text-micro font-semibold uppercase text-fg-subtle">Started</p>
      <ul>
        {side.starters.map((p) => (
          <PlayerLine key={p.name} p={p} team={team} />
        ))}
      </ul>
      {side.substitutes.length > 0 && (
        <>
          <p className="bg-surface-2/50 px-4 py-1.5 text-micro font-semibold uppercase text-fg-subtle">Came on</p>
          <ul>
            {side.substitutes.map((p) => (
              <PlayerLine key={p.name} p={p} team={team} />
            ))}
          </ul>
        </>
      )}
      {side.unused.length > 0 && (
        <details className="border-t border-line">
          <summary className="cursor-pointer px-4 py-2 text-data text-fg-muted hover:text-fg">
            Didn&rsquo;t play ({side.unused.length})
          </summary>
          <p className="px-4 pb-3 text-data text-fg-subtle">{side.unused.map((p) => p.name).join(', ')}</p>
        </details>
      )}
    </Card>
  );
}

function Preview({ m }: { m: MatchPageResponse }) {
  const dialog = usePlayerDialog();
  const sides = [
    { key: 'home' as const, team: m.home_team },
    { key: 'away' as const, team: m.away_team },
  ];
  const hasPredicted = m.predicted.home.length + m.predicted.away.length > 0;
  return (
    <div className="space-y-6">
      {hasPredicted && (
        <section>
          <SectionHeading as="h2" size="md" title="Players to watch" subtitle="The model's top predicted players for this match" />
          <div className="grid gap-4 md:grid-cols-2">
            {sides.map(({ key, team }) => (
              <Card key={key} padded={false}>
                <h3 className="flex items-center gap-2 border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">
                  <ClubCrest team={team} size={22} /> {team}
                </h3>
                {m.predicted[key].length === 0 && <p className="px-4 py-6 text-data text-fg-subtle">No predictions for this club yet.</p>}
                <ol>
                  {m.predicted[key].map((p, i) => (
                    <li key={p.name} className="border-b border-line last:border-0">
                      <button
                        type="button"
                        disabled={!dialog}
                        onClick={() => dialog?.openPlayer({ name: p.name, team, position: p.position })}
                        className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors enabled:hover:bg-surface-2"
                      >
                        <span className="w-4 text-right text-data font-semibold text-fg-subtle">{i + 1}</span>
                        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{p.name}</span>
                        <PositionBadge position={p.position ?? undefined} />
                        <span className="font-display text-h3 font-bold tabular-nums text-accent">{p.predicted_points.toFixed(1)}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </Card>
            ))}
          </div>
        </section>
      )}
      <section className="grid gap-4 md:grid-cols-2">
        {sides.map(({ key, team }) => (
          <Card key={key}>
            <SectionHeading as="h3" size="md" title={`${team} form`} subtitle="Last five league matches before this one" />
            <FormChips
              items={m.form[key].map((f) => ({ fixture_id: f.fixture_id, result: f.result!, opponent: f.opponent, home: f.home, score: f.score }))}
            />
            <ul className="mt-3 space-y-1 text-data text-fg-muted">
              {[...m.form[key]].reverse().map((f) => (
                <li key={f.fixture_id}>
                  <Link to={matchPath(f.fixture_id)} className="hover:text-fg hover:underline">
                    {f.score} {f.home ? 'v' : 'at'} {f.opponent}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </section>
    </div>
  );
}

export function MatchPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: ['match', id],
    queryFn: () => apiService.getMatch(id),
    staleTime: 60_000,
    retry: (n, e) => !isNotFoundError(e) && n < 1,
    refetchInterval: (q) => (q.state.data?.status === 'in_progress' ? LIVE_REFRESH_MS : false),
  });
  const m = query.data;
  const played = m?.home_score != null && m?.away_score != null;
  usePageMeta({
    title: m ? `${m.home_team} ${played ? `${m.home_score}-${m.away_score}` : 'v'} ${m.away_team}` : 'Match',
    description: m ? `${m.home_team} v ${m.away_team}, ${m.season} gameweek ${m.gameweek}: score, scorers, line-ups and FPL points.` : 'A Premier League match.',
  });

  if (query.isError) {
    return (
      <Container className="py-10">
        <Card padded={false}>
          {isNotFoundError(query.error) ? (
            <EmptyState title="Match not found" description="This match isn't in the database." action={<Link to="/fixtures" className="text-accent underline">Fixtures</Link>} />
          ) : (
            <ErrorState description={handleApiError(query.error)} onRetry={() => query.refetch()} />
          )}
        </Card>
      </Container>
    );
  }

  const kickoff = m?.kickoff_time ? new Date(m.kickoff_time) : null;
  const live = m?.status === 'in_progress';
  const s = m?.summary;
  const homeColor = clubColors(m?.home_team);
  return (
    <div className="pb-12">
      <ClubHero home={m?.home_team ?? ''} away={m?.away_team ?? ''} crest={{ home: m ? getClubLogoPath(m.home_team) : null, away: m ? getClubLogoPath(m.away_team) : null }}>
        <Container className="relative py-8 sm:py-12">
          {m ? (
            <>
              <p className="text-center text-micro font-semibold uppercase tracking-wide text-fg-muted">
                {m.season} · Gameweek {m.gameweek ?? '–'}
                {kickoff && ` · ${kickoff.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}`}
              </p>
              <div className="mt-5 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-start gap-3 sm:gap-8">
                <div className="flex flex-col items-center text-center">
                  <Link to={teamPath(m.home_team)} className="group flex flex-col items-center gap-2">
                    <ClubCrest team={m.home_team} size={72} className="transition-transform group-hover:scale-105" />
                    <span className="font-display text-h3 font-bold text-fg group-hover:underline sm:text-h2">
                      <span className="hidden sm:inline">{m.home_team}</span>
                      <span className="sm:hidden">{getClubAbbreviation(m.home_team) || m.home_team}</span>
                    </span>
                  </Link>
                  {s && <Events side={s.home} align="right" />}
                </div>
                <div className="flex flex-col items-center pt-3">
                  {played ? (
                    <span className="rounded-xl bg-black/35 px-4 py-2 font-display text-display font-extrabold tabular-nums text-fg ring-1 ring-white/10 sm:px-6">
                      {m.home_score}
                      <span aria-hidden="true" className="text-fg-subtle"> – </span>
                      <span className="sr-only"> to </span>
                      {m.away_score}
                    </span>
                  ) : (
                    <span className="rounded-xl bg-black/35 px-4 py-3 text-center ring-1 ring-white/10">
                      <span className="block font-display text-h1 font-extrabold tabular-nums text-fg">
                        {kickoff ? kickoff.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : 'TBC'}
                      </span>
                      <span className="text-micro uppercase text-fg-subtle">Kickoff</span>
                    </span>
                  )}
                  <span className="mt-2 text-micro font-semibold uppercase">
                    {live ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-danger px-2 py-0.5 text-white">
                        <Radio className="h-3 w-3" aria-hidden="true" /> Live{m.live?.minutes ? ` · ${m.live.minutes}′` : ''}
                      </span>
                    ) : played ? (
                      <span className="text-fg-muted">Full time</span>
                    ) : null}
                  </span>
                </div>
                <div className="flex flex-col items-center text-center">
                  <Link to={teamPath(m.away_team)} className="group flex flex-col items-center gap-2">
                    <ClubCrest team={m.away_team} size={72} className="transition-transform group-hover:scale-105" />
                    <span className="font-display text-h3 font-bold text-fg group-hover:underline sm:text-h2">
                      <span className="hidden sm:inline">{m.away_team}</span>
                      <span className="sm:hidden">{getClubAbbreviation(m.away_team) || m.away_team}</span>
                    </span>
                  </Link>
                  {s && <Events side={s.away} align="left" />}
                </div>
              </div>
            </>
          ) : (
            <Skeleton className="mx-auto h-40 w-full max-w-2xl" />
          )}
        </Container>
      </ClubHero>

      <Container className="mt-6">
        {!m ? (
          <Skeleton className="h-96 w-full" />
        ) : s ? (
          <div
            className="space-y-6"
            style={{ '--c1': homeColor.primary === '230 230 230' ? '200 200 200' : homeColor.primary, '--c2': clubColors(m.away_team).primary } as React.CSSProperties}
          >
            {live && (
              <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-data text-fg">
                FPL&rsquo;s live points, updated every minute. Bonus points appear when FPL adds them.
              </p>
            )}
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Card>
                <SectionHeading as="h2" size="md" title="Match stats" subtitle="From FPL's match data" />
                <div className="space-y-4">
                  {s.home.xg != null && s.away.xg != null && (
                    <StatBar label="Expected goals" home={s.home.xg} away={s.away.xg} format={(v) => v.toFixed(2)} />
                  )}
                  <StatBar label="FPL points" home={s.home.points} away={s.away.points} />
                  <StatBar
                    label="Cards"
                    home={s.home.yellow_cards.reduce((a, e) => a + e.count, 0) + s.home.red_cards.reduce((a, e) => a + e.count, 0)}
                    away={s.away.yellow_cards.reduce((a, e) => a + e.count, 0) + s.away.red_cards.reduce((a, e) => a + e.count, 0)}
                  />
                  {(s.home.assists.length > 0 || s.away.assists.length > 0) && (
                    <div className="grid grid-cols-2 gap-4 border-t border-line pt-3 text-data">
                      <p className="text-fg-muted">
                        <span className="block text-micro font-semibold uppercase text-fg-subtle">Assists</span>
                        {s.home.assists.map((e) => `${short(e.name)}${times(e.count)}`).join(', ') || '–'}
                      </p>
                      <p className="text-right text-fg-muted">
                        <span className="block text-micro font-semibold uppercase text-fg-subtle">Assists</span>
                        {s.away.assists.map((e) => `${short(e.name)}${times(e.count)}`).join(', ') || '–'}
                      </p>
                    </div>
                  )}
                </div>
              </Card>
              <Card>
                <SectionHeading as="h2" size="md" title="Bonus points" subtitle={s.top_scorer ? `Top FPL score: ${s.top_scorer.name}, ${s.top_scorer.points} pts` : undefined} />
                {s.bonus.length ? (
                  <ol className="space-y-2">
                    {s.bonus.map((b) => (
                      <li key={b.name} className="flex items-center gap-3">
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent font-display text-body font-extrabold text-accent-on">{b.bonus}</span>
                        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{b.name}</span>
                        {b.team && <ClubCrest team={b.team} size={18} />}
                        <span className="w-16 text-right text-micro tabular-nums text-fg-subtle">{b.bps} BPS</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-data text-fg-subtle">No bonus points yet.</p>
                )}
              </Card>
            </div>
            <section>
              <SectionHeading as="h2" size="md" title="Line-ups and FPL points" subtitle="Click a player for their season" />
              <div className="grid gap-4 lg:grid-cols-2">
                <Lineup side={s.home} team={m.home_team} />
                <Lineup side={s.away} team={m.away_team} />
              </div>
            </section>
            {m.meetings.length > 0 && <Meetings m={m} />}
          </div>
        ) : (
          <div className="space-y-6">
            <Preview m={m} />
            {m.meetings.length > 0 && <Meetings m={m} />}
          </div>
        )}
      </Container>
    </div>
  );
}

function Meetings({ m }: { m: MatchPageResponse }) {
  return (
    <section>
      <SectionHeading as="h2" size="md" title="Last meetings" />
      <Card padded={false}>
        <ul>
          {m.meetings.map((x) => (
            <li key={x.fixture_id} className="border-b border-line last:border-0">
              <Link to={matchPath(x.fixture_id)} className="grid grid-cols-[5rem_minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 px-4 py-2.5 text-data transition-colors hover:bg-surface-2">
                <span className="text-micro text-fg-subtle">{x.season}</span>
                <span className="flex items-center justify-end gap-2 truncate text-right font-semibold text-fg">
                  {x.home_team} <ClubCrest team={x.home_team} size={18} />
                </span>
                <span className="rounded-sm bg-brand px-2 py-0.5 font-display font-bold tabular-nums text-fg">
                  {x.home_score}–{x.away_score}
                </span>
                <span className="flex items-center gap-2 truncate font-semibold text-fg">
                  <ClubCrest team={x.away_team} size={18} /> {x.away_team}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
