import { LineChart } from 'lucide-react';
import { handleApiError } from '../../services/api';
import type { TrackRecord as TrackRecordData, TrackRecordMissing, TrackRecordTotals } from '../../services/trackRecordApi';
import { Card, EmptyState, ErrorState, SectionHeading, Skeleton, StatCard } from '../ui';
import { TrackRecordCalibration } from './TrackRecordCalibration';
import { TrackRecordChart } from './TrackRecordChart';
import { points } from './format';
import { groupMissing } from './trackRecordFormat';

// The Track record tab: how the model's saved pre-deadline forecasts did, against what happened
// and against FPL's own forecast.

const TRACK_RECORD_EMPTY = 'The track record starts once a gameweek with saved predictions has finished.';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function Headline({ totals }: { totals: TrackRecordTotals }) {
  const { mae, top10, captain, beating_fpl: beating } = totals;
  const hasFpl = mae.fpl != null && top10.fpl != null;
  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Forecast error"
        value={mae.model != null ? points(mae.model, 2) : '–'}
        detail={mae.fpl != null ? `FPL’s forecast: ${points(mae.fpl, 2)} · lower is better` : 'Points off per player · lower is better'}
        highlight={mae.fpl != null && mae.model != null && mae.model < mae.fpl}
      />
      <StatCard
        label="Top 10 picks"
        value={top10.model != null ? points(top10.model, 1) : '–'}
        detail={
          top10.fpl != null
            ? `FPL’s top 10: ${points(top10.fpl, 1)} · best possible ${top10.best != null ? points(top10.best, 0) : '–'}`
            : `Points per gameweek · best possible ${top10.best != null ? points(top10.best, 0) : '–'}`
        }
        highlight={top10.fpl != null && top10.model != null && top10.model > top10.fpl}
      />
      <StatCard
        label={`Top pick scored ${captain.threshold}+`}
        value={captain.of ? `${captain.hits} of ${captain.of}` : '–'}
        detail={captain.mean_points != null ? `Averaged ${points(captain.mean_points, 1)} points` : 'Gameweeks'}
      />
      <StatCard
        label="Beat FPL’s forecast"
        value={hasFpl && beating.top10.of ? `${beating.top10.wins} of ${beating.top10.of}` : '–'}
        detail={
          hasFpl && beating.mae.of
            ? `Top 10 · lower error in ${beating.mae.wins} of ${beating.mae.of}`
            : 'FPL’s forecast wasn’t saved for these'
        }
      />
    </div>
  );
}

function HowToRead({ totals, missing }: { totals: TrackRecordTotals; missing: TrackRecordMissing[] }) {
  const groups = groupMissing(missing);
  const n = totals.gameweeks;
  return (
    <Card>
      <SectionHeading as="h2" size="md" title="How to read this" />
      <ul className="space-y-2 text-data text-fg-muted">
        <li>
          <span className="font-semibold text-fg">Forecast error</span> is how many points off the forecast was for a player who played, on
          average. <span className="font-semibold text-fg">Top 10</span> adds up the real points of the ten players the forecast rated highest.
        </li>
        <li>
          <span className="font-semibold text-fg">FPL’s forecast</span> is the expected points FPL itself showed for the gameweek just
          before the deadline, scored on the same players.
        </li>
        <li>
          Every forecast here was saved before its deadline and never changed afterwards, so nothing is judged with hindsight.
        </li>
      </ul>
      <p className="mt-3 rounded-md border border-line bg-surface-2 px-3 py-2 text-data text-fg-muted">
        <span className="font-semibold text-fg">Treat it as an early look.</span> {n < 10 ? `Only ${plural(n, 'gameweek')} so far.` : `${plural(n, 'gameweek')} so far, from one season.`}{' '}
        Points are noisy: one big haul moves a gameweek’s score a lot, and a few good or bad weeks say little about the next.
        {totals.compared_gameweeks < n && ` The comparison with FPL covers the ${totals.compared_gameweeks} that have both forecasts.`}
      </p>
      {groups.length > 0 && (
        <div className="mt-3">
          <p className="text-micro font-semibold uppercase text-fg-subtle">Not counted</p>
          <ul className="mt-1 space-y-1 text-micro text-fg-subtle">
            {groups.map((g) => (
              <li key={`${g.label}-${g.reason}`}>
                <span className="font-semibold text-fg-muted">{g.count === 1 ? 'Gameweek' : 'Gameweeks'} {g.label}:</span> {g.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

export function TrackRecordSkeleton() {
  return (
    <div role="status" className="space-y-4">
      <span className="sr-only">Loading the track record…</span>
      <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-80 w-full rounded-lg" />
    </div>
  );
}

export function TrackRecordEmpty({ note, missing = [] }: { note?: string | null; missing?: TrackRecordMissing[] }) {
  const groups = groupMissing(missing);
  return (
    <Card padded={false}>
      <EmptyState
        icon={<LineChart className="h-8 w-8" aria-hidden="true" />}
        title="No track record yet"
        description={note || TRACK_RECORD_EMPTY}
      />
      {groups.length > 0 && (
        <ul className="mx-auto max-w-md space-y-1 px-4 pb-6 text-center text-micro text-fg-subtle">
          {groups.map((g) => (
            <li key={`${g.label}-${g.reason}`}>
              {g.count === 1 ? 'Gameweek' : 'Gameweeks'} {g.label}: {g.reason}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function TrackRecordError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Card padded={false}>
      <ErrorState title="Couldn’t load the track record" description={handleApiError(error)} onRetry={onRetry} />
    </Card>
  );
}

export function TrackRecordView({ data }: { data: TrackRecordData }) {
  if (!data.gameweeks.length || !data.totals) return <TrackRecordEmpty note={data.note} missing={data.missing} />;
  const { totals } = data;
  const first = data.gameweeks[0]?.gameweek;
  return (
    <div className="space-y-6">
      <Headline totals={totals} />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <Card>
          <SectionHeading
            as="h2"
            title="Gameweek by gameweek"
            subtitle={`${plural(totals.gameweeks, 'finished gameweek')} of ${data.season}${first != null ? `, from gameweek ${first}` : ''}. Each forecast was saved before its deadline.`}
          />
          <TrackRecordChart gameweeks={data.gameweeks} />
        </Card>

        <div className="space-y-6">
          <Card>
            <SectionHeading
              as="h2"
              size="md"
              title="Predicted against actual"
              subtitle="Players grouped by the points the model forecast for them, all gameweeks together."
            />
            <TrackRecordCalibration bins={data.calibration} />
          </Card>
          <HowToRead totals={totals} missing={data.missing} />
        </div>
      </div>
    </div>
  );
}
