import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiService, type MLPrediction } from '../../services/api';
import { Card, ClubCrest, Container, FdrBadge, PlayerAvatar, SectionHeading, StatusBadge } from '../ui';
import { useFavouriteClub, useWatchlist } from '../../lib/personal';
import { readSavedTeamId } from '../myteam/format';
import { usePlayerAvatars } from '../../hooks/usePlayerAvatars';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { matchPath, teamPath, teamSlug } from '../../utils/clubColors';
import { shortName } from '../../utils/playerNames';

const ordinal = (n: number) => {
  const v = n % 100;
  return `${n}${['th', 'st', 'nd', 'rd'][(v - 20) % 10] || ['th', 'st', 'nd', 'rd'][v] || 'th'}`;
};

function WatchedPlayers({ names }: { names: string[] }) {
  const dialog = usePlayerDialog();
  // One request for the whole top 50, filtered here; prices come from the news feed's changes.
  const preds = useQuery({
    queryKey: ['top-performers', 'all', 50],
    queryFn: () => apiService.predictTopPerformers(undefined, 50),
    staleTime: 5 * 60_000,
  });
  const news = useQuery({ queryKey: ['news', 'all'], queryFn: () => apiService.getNews(), staleTime: 5 * 60_000 });
  const avatars = usePlayerAvatars(names);
  const byName = new Map<string, MLPrediction>((preds.data?.predictions ?? []).map((p) => [p.player_name, p]));
  const prices = new Map((news.data?.fpl.prices ?? []).map((p) => [p.name, p.change]));

  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-micro font-semibold uppercase text-fg-subtle">Watchlist</h3>
      <ul className="divide-y divide-line">
        {names.map((name) => {
          const p = byName.get(name);
          const change = prices.get(name);
          const row = (
            <>
              <PlayerAvatar name={name} src={avatars[name]} size={32} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-data font-semibold text-fg">{shortName(name, p?.web_name)}</span>
                <span className="flex flex-wrap items-center gap-x-2 text-micro text-fg-subtle">
                  {p?.status && <StatusBadge status={p.status} chance={p.chance_of_playing_next_round} />}
                  {change != null && (
                    <span className={change > 0 ? 'text-accent' : 'text-danger'}>
                      {change > 0 ? '+' : '−'}£{Math.abs(change).toFixed(1)}m this week
                    </span>
                  )}
                  {!p && preds.isSuccess && <span>Not among the top 50 picks</span>}
                </span>
              </span>
              <span className="shrink-0 text-right font-display text-h3 font-bold tabular-nums text-accent">
                {p ? p.predicted_points.toFixed(1) : '–'}
                <span className="block text-micro font-semibold uppercase text-fg-subtle">xPts</span>
              </span>
            </>
          );
          return (
            <li key={name}>
              {dialog ? (
                <button
                  type="button"
                  onClick={() => dialog.openPlayer({ name, team: p?.team_name, photo: avatars[name], status: p?.status, news: p?.news, chance: p?.chance_of_playing_next_round })}
                  className="flex w-full items-center gap-3 py-2 text-left hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {row}
                </button>
              ) : (
                <div className="flex items-center gap-3 py-2">{row}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function FavouriteClub({ club }: { club: string }) {
  const { data } = useQuery({ queryKey: ['team', teamSlug(club), 'current'], queryFn: () => apiService.getTeam(teamSlug(club)), staleTime: 5 * 60_000 });
  const next = data?.fixtures[0];
  const pos = data?.standing?.position;
  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-micro font-semibold uppercase text-fg-subtle">Your club</h3>
      <Link to={teamPath(club)} className="flex items-center gap-3 py-2 hover:text-accent">
        <ClubCrest team={club} size={32} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-data font-semibold text-fg">{club}</span>
          {data?.standing && pos != null && (
            <span className="text-micro text-fg-subtle">
              {ordinal(pos)} of {data.teams_in_table} · {data.standing.points} pts
            </span>
          )}
        </span>
      </Link>
      {next && (
        <Link
          to={matchPath(next.fixture_id)}
          className="mt-1 flex items-center gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-data hover:border-line-strong"
        >
          <span className="text-micro font-semibold uppercase text-fg-subtle">Next</span>
          <ClubCrest team={next.opponent} size={20} />
          <span className="min-w-0 flex-1 truncate font-semibold text-fg">
            {getClubAbbreviation(next.opponent) || next.opponent} ({next.home ? 'H' : 'A'})
          </span>
          {next.difficulty != null && <FdrBadge difficulty={next.difficulty} />}
        </Link>
      )}
    </div>
  );
}

/** "Your players & club" on the home page: only when this browser has a watchlist or a favourite club. */
export function YourPanel() {
  const { names } = useWatchlist();
  const { club } = useFavouriteClub();
  const teamId = readSavedTeamId();
  if (names.length === 0 && !club) return null;
  return (
    <Container className="pt-6">
      <Card>
        <SectionHeading
          as="h2"
          size="md"
          title="Your players & club"
          subtitle="Saved in this browser only"
          action={
            teamId ? (
              <Link to="/my-team" className="text-data font-semibold text-accent hover:underline">
                My team
              </Link>
            ) : undefined
          }
        />
        <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          {names.length > 0 && <WatchedPlayers names={names} />}
          {club && <FavouriteClub club={club} />}
        </div>
      </Card>
    </Container>
  );
}
