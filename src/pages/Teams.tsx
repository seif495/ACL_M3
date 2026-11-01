import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiService, handleApiError } from '../services/api';
import { usePageMeta } from '../hooks/usePageMeta';
import { clubColors, teamPath } from '../utils/clubColors';
import { Card, ClubCrest, Container, ErrorState, PageHeader, Skeleton } from '../components/ui';
import { FormChips } from '../components/teams/MatchBits';

/** Every club of the season as a card lit by its colours, in table order. */
export function Teams() {
  usePageMeta({ title: 'Clubs', description: 'Every Premier League club this season: position, form, results, fixtures, squad and news.' });
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['teams'],
    queryFn: () => apiService.getTeams(),
    staleTime: 5 * 60_000,
  });
  return (
    <Container className="pb-12">
      <PageHeader eyebrow={data?.season ?? 'This season'} title="Clubs" description="Pick a club for its season: results, fixtures, squad, form and news." />
      {isError ? (
        <Card>
          <ErrorState description={handleApiError(error)} onRetry={() => refetch()} />
        </Card>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {isLoading
            ? Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-36 rounded-lg" />)
            : data?.teams.map((t) => {
                const c = clubColors(t.team);
                return (
                  <li key={t.team}>
                    <Link
                      to={teamPath(t.team)}
                      style={{ '--c1': c.primary } as CSSProperties}
                      className="group relative block overflow-hidden rounded-lg border border-line bg-surface p-4 transition hover:-translate-y-0.5 hover:border-line-strong hover:shadow-pop focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <span
                        aria-hidden="true"
                        className="absolute inset-0 opacity-60 transition-opacity group-hover:opacity-100"
                        style={{ background: 'radial-gradient(90% 110% at 0% 0%, rgb(var(--c1) / 0.35), transparent 60%)' }}
                      />
                      <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-0.5" style={{ background: 'rgb(var(--c1))' }} />
                      <span className="relative flex items-center gap-3">
                        <ClubCrest team={t.team} size={48} className="transition-transform group-hover:scale-110" />
                        <span className="min-w-0">
                          <span className="block truncate font-display text-h3 font-bold text-fg">{t.team}</span>
                          <span className="text-data text-fg-muted">
                            {t.played ? `${t.position}${t.position === 1 ? 'st' : t.position === 2 ? 'nd' : t.position === 3 ? 'rd' : 'th'} · ${t.points} pts` : 'No matches yet'}
                          </span>
                        </span>
                      </span>
                      <span className="relative mt-3 flex items-center justify-between">
                        <FormChips items={(t.recent ?? []).map((r) => ({ ...r }))} size="sm" />
                        <span className="text-micro tabular-nums text-fg-subtle">
                          {t.goal_difference > 0 ? '+' : ''}
                          {t.goal_difference} GD
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
        </ul>
      )}
    </Container>
  );
}
