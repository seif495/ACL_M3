import { useMemo, useState } from 'react';
import type { TrackRecordGameweek } from '../../services/trackRecordApi';
import { cn } from '../../utils/cn';
import { TrackRecordBars, type BarsSeries } from './TrackRecordBars';
import { points } from './format';

// The per-gameweek chart: one metric at a time, the model's bar next to FPL's own forecast.

type Metric = 'top10' | 'mae' | 'squad';

const METRICS: Array<{ key: Metric; label: string; blurb: string }> = [
  { key: 'top10', label: 'Top 10 picks', blurb: 'Real points scored by the ten players each forecast rated highest. Higher is better; the dash is the best ten players of the week.' },
  { key: 'mae', label: 'Forecast error', blurb: 'How far off the forecast was on average for players who played, in points. Lower is better.' },
  { key: 'squad', label: 'Model’s squad', blurb: 'Points of the 15-man squad the model picked before the deadline, counted like FPL does, against the average manager.' },
];

const mean = (values: Array<number | null>): number | null => {
  const v = values.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

function seriesFor(metric: Metric, gws: TrackRecordGameweek[]): BarsSeries[] {
  if (metric === 'top10') {
    return [
      { key: 'model', label: 'Model', tone: 'accent', values: gws.map((g) => g.top10?.model ?? null) },
      { key: 'fpl', label: 'FPL’s forecast', tone: 'muted', values: gws.map((g) => g.top10?.fpl ?? null) },
      { key: 'best', label: 'Best possible ten', tone: 'mark', kind: 'dash', values: gws.map((g) => g.top10?.best ?? null) },
    ];
  }
  if (metric === 'mae') {
    return [
      { key: 'model', label: 'Model', tone: 'accent', values: gws.map((g) => g.mae.model) },
      { key: 'fpl', label: 'FPL’s forecast', tone: 'muted', values: gws.map((g) => g.mae.fpl) },
    ];
  }
  return [
    { key: 'model', label: 'Model’s squad', tone: 'accent', values: gws.map((g) => g.squad?.points ?? null) },
    { key: 'average', label: 'Average manager', tone: 'muted', values: gws.map((g) => g.squad?.average ?? null) },
    { key: 'highest', label: 'Highest score', tone: 'mark', kind: 'dash', values: gws.map((g) => g.squad?.highest ?? null) },
  ];
}

export function TrackRecordChart({ gameweeks }: { gameweeks: TrackRecordGameweek[] }) {
  const [metric, setMetric] = useState<Metric>('top10');
  const gws = useMemo(() => [...gameweeks].sort((a, b) => a.gameweek - b.gameweek), [gameweeks]);
  const series = useMemo(() => seriesFor(metric, gws), [metric, gws]);
  const current = METRICS.find((m) => m.key === metric)!;
  const decimals = metric === 'mae' ? 2 : 0;

  const [model, other] = series;
  const avgModel = mean(model.values);
  const avgOther = mean(other.values);
  const summary =
    `${current.label} for ${gws.length} finished ${gws.length === 1 ? 'gameweek' : 'gameweeks'}, one group per gameweek. ` +
    (avgModel != null ? `The model averaged ${points(avgModel, metric === 'mae' ? 2 : 1)}` : 'No model values') +
    (avgOther != null ? `; ${other.label} averaged ${points(avgOther, metric === 'mae' ? 2 : 1)}.` : '.');

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Metric to chart">
        {METRICS.map((m) => {
          const on = m.key === metric;
          return (
            <button
              key={m.key}
              type="button"
              aria-pressed={on}
              onClick={() => setMetric(m.key)}
              className={cn(
                'inline-flex h-8 items-center rounded-full border px-3 text-data font-semibold transition-colors',
                on ? 'border-accent bg-surface-2 text-fg' : 'border-line bg-transparent text-fg-muted hover:text-fg',
              )}
            >
              {m.label}
            </button>
          );
        })}
      </div>
      <p className="mb-3 text-data text-fg-subtle">{current.blurb}</p>
      <TrackRecordBars
        key={metric}
        categories={gws.map((g) => ({ key: String(g.gameweek), label: gws.length > 12 ? String(g.gameweek) : `GW${g.gameweek}` }))}
        series={series}
        summary={summary}
        digits={decimals}
        yCaption={metric === 'mae' ? 'Average error, points' : 'Points'}
        categoryHeader="Gameweek"
        tableCaption={`${current.label} by gameweek`}
        extraColumns={[
          { header: 'Top pick', cells: gws.map((g) => (g.captain ? `${g.captain.name} (${g.captain.points})` : '–')) },
        ]}
      />
    </div>
  );
}
