import { useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, MessageCircle, Sparkles } from 'lucide-react';
import { apiService, type SeasonFixture } from '../services/api';
import { usePlayerAvatars } from '../hooks/usePlayerAvatars';
import {
  Button,
  ButtonLink,
  Card,
  ClubCrest,
  Container,
  EmptyState,
  ErrorState,
  FixtureRow,
  PlayerAvatar,
  SectionHeading,
  Skeleton,
  SkeletonRows,
  StatusBadge,
  type FixtureStatus,
} from '../components/ui';
import { getClubAbbreviation } from '../utils/clubLogos';
import { usePageMeta } from '../hooks/usePageMeta';
import { usePlayerDialog } from '../hooks/usePlayerDialog';
import type { PlayerHint } from '../components/players/PlayerProfileView';
import { cn } from '../utils/cn';

const STATUS: Record<SeasonFixture['status'], FixtureStatus> = {
  scheduled: 'scheduled',
  in_progress: 'live',
  finished: 'finished',
};

const SUGGESTIONS = [
  'Who should I captain this gameweek?',
  'Top 5 midfielders by points this season',
  'Compare Haaland and João Pedro this season',
];

function SeeAll({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-data font-semibold text-accent hover:underline">
      {children}
      <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
    </Link>
  );
}

function Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <Card padded={false} className="flex flex-col">
      <div className="px-4 pt-4 sm:px-5">
        <SectionHeading as="h2" size="md" title={title} action={action} className="mb-2" />
      </div>
      <div className="flex-1">{children}</div>
    </Card>
  );
}

function AskBox() {
  const navigate = useNavigate();
  const [question, setQuestion] = useState('');
  const ask = (q: string) => q.trim() && navigate('/assistant', { state: { question: q.trim() } });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    ask(question);
  };
  return (
    <div className="rounded-lg border border-white/10 bg-black/25 p-4 sm:p-5">
      <p className="flex items-center gap-2 font-display text-h3 font-bold text-fg">
        <MessageCircle className="h-5 w-5 text-accent" aria-hidden="true" />
        Ask the assistant
      </p>
      <p className="mt-1 text-data text-fg-muted">Answers come from a knowledge graph of every FPL season since 2020-21.</p>
      <form onSubmit={onSubmit} className="mt-3 flex gap-2">
        <label className="sr-only" htmlFor="home-ask">Your question</label>
        <input
          id="home-ask"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Best defenders under £5m"
          maxLength={1000}
          className="h-10 min-w-0 flex-1 rounded-md border border-line-strong bg-surface-2 px-3 text-sm text-fg placeholder:text-fg-subtle"
        />
        <Button type="submit" disabled={!question.trim()}>Ask</Button>
      </form>
      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Example questions">
        {SUGGESTIONS.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => ask(s)}
              className="rounded-full border border-white/15 px-3 py-1 text-data text-fg-muted transition-colors hover:border-accent hover:text-fg"
            >
              {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Shared with TopPicks below, so the hero adds no extra request.
const TOP_PICKS_QUERY = {
  queryKey: ['top-performers', 'all', 6],
  queryFn: () => apiService.predictTopPerformers(undefined, 6),
  staleTime: 5 * 60_000,
};

/** A row or card that opens the player's stats overlay (a plain block outside the app shell). */
function PlayerOpener({ player, className, children }: { player: PlayerHint; className?: string; children: ReactNode }) {
  const dialog = usePlayerDialog();
  if (!dialog) return <div className={className}>{children}</div>;
  return (
    <button
      type="button"
      onClick={() => dialog.openPlayer(player)}
      title={`${player.name}: show stats`}
      className={cn('w-full text-left transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent', className)}
    >
      {children}
    </button>
  );
}

function lastName(name: string) {
  return name.split(' ').slice(-1)[0] || name;
}

/** The model's top prediction for the gameweek, featured in the hero. */
function CaptainPick() {
  const { data, isLoading } = useQuery(TOP_PICKS_QUERY);
  const [top, ...rest] = data?.predictions ?? [];
  const avatars = usePlayerAvatars(top ? [top.player_name] : []);

  if (isLoading) return <Skeleton className="mt-6 h-[6.5rem] w-full max-w-xl rounded-lg" />;
  if (!top) return null;
  const opp = top.fixture_context;
  const oppLabel = opp ? `v ${getClubAbbreviation(opp.opponent_team) || opp.opponent_team} (${opp.home_away === 'home' ? 'H' : 'A'})` : null;

  return (
    <PlayerOpener
      player={{ name: top.player_name, team: top.team_name, photo: avatars[top.player_name], status: top.status, news: top.news, chance: top.chance_of_playing_next_round }}
      className="mt-6 flex max-w-xl items-center gap-4 rounded-lg border border-white/10 bg-black/25 p-4"
    >
      <PlayerAvatar name={top.player_name} src={avatars[top.player_name]} size={72} loading="eager" />
      <div className="min-w-0 flex-1">
        <p className="text-micro font-semibold uppercase text-accent">Model&rsquo;s captain pick</p>
        <p className="truncate font-display text-h3 font-bold text-fg sm:text-h2">{top.player_name}</p>
        <p className="flex items-center gap-1.5 text-data text-fg-muted">
          {top.team_name && <ClubCrest team={top.team_name} size={16} />}
          <span className="truncate">{[top.team_name, oppLabel].filter(Boolean).join(' · ')}</span>
        </p>
        {rest.length > 0 && (
          <p className="mt-1 truncate text-micro text-fg-subtle">
            Next best: {rest.slice(0, 2).map((p) => `${lastName(p.player_name)} ${p.predicted_points.toFixed(1)}`).join(' · ')}
          </p>
        )}
      </div>
      <div className="shrink-0 text-right">
        <span className="block font-display text-h1 font-extrabold leading-none tabular-nums text-accent sm:text-display">
          {top.predicted_points.toFixed(1)}
        </span>
        <span className="text-micro font-semibold uppercase text-fg-subtle">
          <abbr title="Predicted points" className="no-underline">xPts</abbr>
        </span>
      </div>
    </PlayerOpener>
  );
}

function Hero() {
  const { data: season } = useQuery({
    queryKey: ['season-current'],
    queryFn: () => apiService.getCurrentSeason(),
    staleTime: 5 * 60_000,
  });
  const gw = season?.next_gameweek;

  // The countdown lives in the deadline bar on every page; the hero leads with the model's pick.
  return (
    <section
      aria-labelledby="home-title"
      className="border-b border-line"
      style={{ backgroundImage: 'linear-gradient(135deg, rgb(var(--brand)) 0%, rgb(var(--bg)) 70%)' }}
    >
      <Container className="grid grid-cols-1 gap-6 py-8 sm:py-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-center">
        <div>
          <p className="text-micro font-semibold uppercase text-accent">{season?.season ?? 'Fantasy Premier League'}</p>
          <h1 id="home-title" className="mt-1 font-display text-h1 font-extrabold text-fg sm:text-display">
            {gw ? `Gameweek ${gw}` : 'Pitch Me'}
          </h1>
          <p className="mt-1 max-w-lg text-body text-fg-muted">
            Model picks, fixtures and team news for the next deadline.
          </p>
          <CaptainPick />
          <div className="mt-6 flex flex-wrap gap-2">
            <ButtonLink to="/team-of-the-week" icon={<Sparkles className="h-4 w-4" aria-hidden="true" />}>
              Predicted team
            </ButtonLink>
            <ButtonLink to="/predictions" variant="secondary">
              All predictions
            </ButtonLink>
          </div>
        </div>
        <AskBox />
      </Container>
    </section>
  );
}

function NextFixtures() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['fixtures', 'default'],
    queryFn: () => apiService.getFixtures(),
    staleTime: 60_000,
  });
  return (
    <Panel title={data ? `Gameweek ${data.gameweek} fixtures` : 'Fixtures'} action={<SeeAll to="/fixtures">All fixtures</SeeAll>}>
      {isLoading ? (
        <SkeletonRows rows={6} className="px-4 pb-4" />
      ) : isError ? (
        <ErrorState title="Couldn't load fixtures" onRetry={() => refetch()} />
      ) : !data?.fixtures.length ? (
        <EmptyState title="No fixtures" />
      ) : (
        <div className="border-t border-line">
          {data.fixtures.slice(0, 10).map((f) => (
            <FixtureRow
              key={f.id}
              home={f.home_team}
              away={f.away_team}
              homeScore={f.home_score}
              awayScore={f.away_score}
              kickoff={f.kickoff_time}
              status={STATUS[f.status]}
              showDay
            />
          ))}
        </div>
      )}
    </Panel>
  );
}

function MiniTable() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['league-table'],
    queryFn: () => apiService.getLeagueTable(),
    staleTime: 5 * 60_000,
  });
  return (
    <Panel title="Table" action={<SeeAll to="/table">Full table</SeeAll>}>
      {isLoading ? (
        <SkeletonRows rows={8} className="px-4 pb-4" />
      ) : isError ? (
        <ErrorState title="Couldn't load the table" onRetry={() => refetch()} />
      ) : (
        <table className="w-full text-data">
          <caption className="sr-only">Top of the league table</caption>
          <thead>
            <tr className="border-y border-line bg-surface-2 text-micro uppercase text-fg-subtle">
              <th scope="col" className="px-4 py-2 text-left font-semibold">Club</th>
              <th scope="col" className="px-2 py-2 text-right font-semibold"><abbr title="Played" className="no-underline">Pl</abbr></th>
              <th scope="col" className="px-2 py-2 text-right font-semibold"><abbr title="Goal difference" className="no-underline">GD</abbr></th>
              <th scope="col" className="px-4 py-2 text-right font-semibold"><abbr title="Points" className="no-underline">Pts</abbr></th>
            </tr>
          </thead>
          <tbody>
            {(data?.rows ?? []).slice(0, 8).map((r) => (
              <tr key={r.team} className="border-b border-line last:border-0">
                <td className="px-4 py-2">
                  <span className="flex items-center gap-2">
                    <span className="w-5 text-right tabular-nums text-fg-subtle">{r.position}</span>
                    <ClubCrest team={r.team} size={18} />
                    <span className="truncate font-semibold text-fg">{r.team}</span>
                  </span>
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-fg-muted">{r.played}</td>
                <td className="px-2 py-2 text-right tabular-nums text-fg-muted">{r.goal_difference > 0 ? `+${r.goal_difference}` : r.goal_difference}</td>
                <td className="px-4 py-2 text-right font-bold tabular-nums text-fg">{r.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function TopPicks() {
  const { data, isLoading, isError, refetch } = useQuery(TOP_PICKS_QUERY);
  const players = data?.predictions ?? [];
  const avatars = usePlayerAvatars(players.map((p) => p.player_name));

  return (
    <Panel title="Top predicted picks" action={<SeeAll to="/predictions">All predictions</SeeAll>}>
      {isLoading ? (
        <SkeletonRows rows={6} className="px-4 pb-4" />
      ) : isError ? (
        <ErrorState title="Predictions unavailable" onRetry={() => refetch()} />
      ) : (
        <ol className="grid border-t border-line sm:grid-cols-2">
          {players.map((p, i) => {
            const opp = p.fixture_context;
            return (
              <li key={p.player_name} className="border-b border-line sm:[&:nth-child(odd)]:border-r">
                <PlayerOpener
                  player={{ name: p.player_name, team: p.team_name, photo: avatars[p.player_name], status: p.status, news: p.news, chance: p.chance_of_playing_next_round }}
                  className="flex items-center gap-3 px-4 py-3"
                >
                <span className="w-4 text-right text-data font-semibold tabular-nums text-fg-subtle">{i + 1}</span>
                <PlayerAvatar name={p.player_name} src={avatars[p.player_name]} size={44} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-sm font-semibold text-fg">{p.player_name}</span>
                    <StatusBadge status={p.status} chance={p.chance_of_playing_next_round} news={p.news} />
                  </span>
                  <span className="flex items-center gap-1.5 text-data text-fg-subtle">
                    {p.team_name && <ClubCrest team={p.team_name} size={14} />}
                    {opp ? `v ${getClubAbbreviation(opp.opponent_team) || opp.opponent_team} (${opp.home_away === 'home' ? 'H' : 'A'})` : p.team_name}
                  </span>
                </span>
                <span className="text-right">
                  <span className="block font-display text-h3 font-bold tabular-nums text-accent">{p.predicted_points.toFixed(1)}</span>
                  <span className="text-micro text-fg-subtle">pts</span>
                </span>
                </PlayerOpener>
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

function TeamNews() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['injuries', 'all'],
    queryFn: () => apiService.getInjuries(),
    staleTime: 5 * 60_000,
  });
  const latest = [...(data?.players ?? [])]
    .filter((p) => p.news)
    .sort((a, b) => (b.news_added ?? '').localeCompare(a.news_added ?? ''))
    .slice(0, 6);

  return (
    <Panel title="Latest team news">
      {isLoading ? (
        <SkeletonRows rows={6} className="px-4 pb-4" />
      ) : isError ? (
        <ErrorState title="Team news unavailable" onRetry={() => refetch()} />
      ) : latest.length === 0 ? (
        <EmptyState title="No injury news" description="Every squad player is available." />
      ) : (
        <ul className="border-t border-line">
          {latest.map((p) => (
            <li key={p.name} className="border-b border-line last:border-0">
              <PlayerOpener
                player={{ name: p.name, team: p.team, photo: p.photo, status: p.status, news: p.news, chance: p.chance_of_playing_next_round }}
                className="flex gap-3 px-4 py-3"
              >
              <PlayerAvatar name={p.name} src={p.photo} size={36} />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold text-fg">{p.web_name ?? p.name}</span>
                  <ClubCrest team={p.team} size={14} />
                  <StatusBadge status={p.status} chance={p.chance_of_playing_next_round} />
                </p>
                <p className="mt-0.5 text-data text-fg-muted">{p.news}</p>
              </div>
              </PlayerOpener>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function Home() {
  usePageMeta({ description: "Gameweek captain pick, fixtures, the league table, team news and an AI assistant for Fantasy Premier League." });
  return (
    <>
      <Hero />
      {/* Two independent columns, so cards keep their natural height (rows of paired cards
          stretched the shorter one and left empty blocks). */}
      <Container className="grid items-start gap-5 py-6 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <NextFixtures />
          <TopPicks />
        </div>
        <div className="space-y-5">
          <MiniTable />
          <TeamNews />
        </div>
      </Container>
    </>
  );
}
