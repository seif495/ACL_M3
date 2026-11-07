import { useQuery } from '@tanstack/react-query';
import { isNotFoundError } from '../../services/api';
import { forecastApi, type MatchForecastResponse } from '../../services/forecastApi';
import { getClubAbbreviation } from '../../utils/clubLogos';
import { cn } from '../../utils/cn';
import { Card, SectionHeading, Skeleton } from '../ui';
import { formatChance, spokenChance } from './format';

const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Home / draw / away as one bar. Segment labels show only when the segment is wide enough;
 * the full figures sit under the bar and in the label, so nothing depends on the colours. */
function OutcomeBar({ f }: { f: MatchForecastResponse }) {
  const parts = [
    { key: 'home', p: f.home_win, name: f.home_team, short: getClubAbbreviation(f.home_team) || f.home_team, tone: 'bg-accent text-accent-on' },
    { key: 'draw', p: f.draw, name: 'Draw', short: 'Draw', tone: 'bg-fg-subtle text-accent-on' },
    { key: 'away', p: f.away_win, name: f.away_team, short: getClubAbbreviation(f.away_team) || f.away_team, tone: 'bg-accent-2 text-accent-on' },
  ];
  const label = `${f.home_team} win ${spokenChance(f.home_win)}, draw ${spokenChance(f.draw)}, ${f.away_team} win ${spokenChance(f.away_win)}`;
  return (
    <div>
      <div role="img" aria-label={label} className="flex h-7 gap-0.5 overflow-hidden rounded-md">
        {parts.map((x) => (
          <span
            key={x.key}
            style={{ width: `${x.p * 100}%` }}
            className={cn('flex items-center justify-center text-micro font-bold tabular-nums', x.tone)}
          >
            {x.p >= 0.12 && <span aria-hidden="true">{pct(x.p)}</span>}
          </span>
        ))}
      </div>
      <dl className="mt-1.5 grid grid-cols-3 gap-2 text-data">
        {parts.map((x, i) => (
          <div key={x.key} className={cn('min-w-0', i === 1 && 'text-center', i === 2 && 'text-right')}>
            <dt className="truncate text-micro uppercase text-fg-subtle">
              <span className="hidden sm:inline">{x.name}</span>
              <span className="sm:hidden">{x.short}</span>
              {i !== 1 && ' win'}
            </dt>
            <dd className="font-display text-h3 font-bold tabular-nums text-fg">{formatChance(x.p)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Fact({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('min-w-0', className)}>
      <dt className="text-micro font-semibold uppercase text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 text-data text-fg">{children}</dd>
    </div>
  );
}

/**
 * "Model's view" for an unplayed match: win/draw/win chances, expected goals, likeliest scores
 * and clean-sheet chances from the team-strength model. Renders nothing when the match has no
 * forecast (played, or the backend doesn't have the endpoint yet).
 */
export function MatchForecastCard({ fixtureId }: { fixtureId: string }) {
  const { data: f, isLoading } = useQuery({
    queryKey: ['match-forecast', fixtureId],
    queryFn: ({ signal }) => forecastApi.getMatchForecast(fixtureId, signal),
    staleTime: 5 * 60_000,
    retry: (n, e) => !isNotFoundError(e) && n < 1,
  });
  if (isLoading) return <Skeleton className="h-44 w-full" />;
  if (!f) return null;
  const [likely, ...others] = f.most_likely_scores;
  return (
    <section aria-labelledby="model-view-heading">
      <SectionHeading
        as="h2"
        size="md"
        id="model-view-heading"
        title="Model’s view"
        subtitle="Chances from a team-strength model fitted to this season’s results"
      />
      <Card>
        <OutcomeBar f={f} />
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-3 sm:grid-cols-3">
          <Fact label="Expected goals">
            <span className="font-display text-h3 font-bold tabular-nums">
              {f.expected_goals_home.toFixed(2)}
              <span aria-hidden="true" className="text-fg-subtle"> – </span>
              <span className="sr-only"> to </span>
              {f.expected_goals_away.toFixed(2)}
            </span>
          </Fact>
          {likely && (
            <Fact label="Most likely score">
              <span className="font-display text-h3 font-bold tabular-nums">
                {likely.home}
                <span aria-hidden="true">–</span>
                <span className="sr-only"> to </span>
                {likely.away}
              </span>{' '}
              <span className="text-fg-muted">{formatChance(likely.probability)}</span>
              {others.length > 0 && (
                <span className="block text-micro text-fg-subtle">
                  Then {others.map((s) => `${s.home}–${s.away} (${formatChance(s.probability)})`).join(', ')}
                </span>
              )}
            </Fact>
          )}
          <Fact label="Clean sheet" className="col-span-2 sm:col-span-1">
            <span className="block tabular-nums">
              {getClubAbbreviation(f.home_team) || f.home_team} <span className="font-bold">{formatChance(f.clean_sheet_home)}</span>
              <span aria-hidden="true" className="text-fg-subtle"> · </span>
              {getClubAbbreviation(f.away_team) || f.away_team} <span className="font-bold">{formatChance(f.clean_sheet_away)}</span>
            </span>
          </Fact>
        </dl>
      </Card>
    </section>
  );
}
