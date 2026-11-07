import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { handleApiError, isNotFoundError } from '../services/api';
import { getPlayerBySlug } from '../services/playerPagesApi';
import { usePageMeta } from '../hooks/usePageMeta';
import { Card, Container, EmptyState, ErrorState, SkeletonRows } from '../components/ui';
import { PlayerProfileView } from '../components/players/PlayerProfileView';
import { WatchButton } from '../components/players/WatchButton';
import { ALL_SEASONS, PLAYER_SEASONS } from '../components/players/seasons';

/** /players/:slug?season= : one player's full profile on its own, shareable URL. */
export function PlayerPage() {
  const { slug = '' } = useParams<{ slug: string }>();
  const [params, setParams] = useSearchParams();
  // The season in the link is where the page starts; the picker then writes it back to the URL
  const [initialSeason] = useState(() => {
    const s = params.get('season');
    return s && PLAYER_SEASONS.includes(s) ? s : undefined;
  });

  const query = useQuery({
    queryKey: ['players', 'by-slug', slug],
    queryFn: ({ signal }) => getPlayerBySlug(slug, signal),
    staleTime: 60 * 60 * 1000,
    retry: (count, err) => !isNotFoundError(err) && count < 1,
  });
  const name = query.data?.name;

  usePageMeta({
    title: name ?? 'Player',
    description: name
      ? `${name}'s FPL stats: points, goals, assists and per-90 rates by season, points by gameweek and the model's prediction for the next gameweek.`
      : 'FPL player stats: points, goals, assists and per-90 rates by season, points by gameweek and next-gameweek predictions.',
    noindex: isNotFoundError(query.error),
  });

  const back = (
    <Link to="/players" className="inline-flex items-center gap-1 text-data text-fg-muted hover:text-fg">
      <ArrowLeft className="h-4 w-4" aria-hidden="true" /> All players
    </Link>
  );

  if (!name) {
    return (
      <Container className="py-8 sm:py-10">
        {back}
        <Card padded={false} className="mt-4">
          {query.isError ? (
            isNotFoundError(query.error) ? (
              <EmptyState
                title="Player not found"
                description={`No player matches "${slug}".`}
                action={<Link to="/players" className="text-accent underline">Search players</Link>}
              />
            ) : (
              <ErrorState description={handleApiError(query.error)} onRetry={() => query.refetch()} />
            )
          ) : (
            <div className="p-4">
              <SkeletonRows rows={6} />
            </div>
          )}
        </Card>
      </Container>
    );
  }

  return (
    <Container className="py-8 sm:py-10">
      <h1 className="sr-only">{name}</h1>
      <PlayerProfileView
        key={slug}
        player={{ name }}
        initialSeason={initialSeason}
        toolbar={
          <div className="flex flex-wrap items-center gap-3">
            {back}
            <WatchButton name={name} />
          </div>
        }
        onSeasonChange={(season) =>
          setParams(
            (prev) => {
              const next = new URLSearchParams(prev);
              if (season === ALL_SEASONS) next.delete('season');
              else next.set('season', season);
              return next;
            },
            { replace: true },
          )
        }
      />
    </Container>
  );
}
