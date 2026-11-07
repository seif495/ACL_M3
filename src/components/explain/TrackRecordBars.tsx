import { useId, useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { cn } from '../../utils/cn';
import { DataDisclosure } from './DataDisclosure';
import { useElementWidth } from './useElementWidth';
import { linearScale, niceTicks, points, tickLabel } from './format';

/*
 * Grouped vertical bars for the track record, drawn as SVG at the measured width: one group per
 * category (a gameweek, or a bin of predictions), one bar per series, and optionally a dash marker
 * for a ceiling such as "best possible". Hover (or tap) a group for its numbers; with the chart
 * focused, the arrow keys step through the groups. "Show the numbers" is the same data as a table.
 */

type Tone = 'accent' | 'muted' | 'mark';

const TONE_FILL: Record<Tone, string> = {
  accent: 'rgb(var(--accent))',
  muted: 'rgb(var(--text-subtle))',
  mark: 'rgb(var(--accent-2))',
};

export interface BarsCategory {
  key: string;
  label: string;
  /** Second, quieter line under the label (e.g. how many players). */
  sublabel?: string;
}

export interface BarsSeries {
  key: string;
  label: string;
  tone: Tone;
  /** 'bar' (default) or a 'dash' across the group; null values are left out. */
  kind?: 'bar' | 'dash';
  values: Array<number | null>;
}

interface TrackRecordBarsProps {
  categories: BarsCategory[];
  series: BarsSeries[];
  /** Accessible name and the sentence a screen reader gets for the whole chart. */
  summary: string;
  /** Spoken unit after a value: "points". */
  unit?: string;
  digits?: number;
  /** Write each bar's value above it (for a handful of groups). */
  showValues?: boolean;
  yCaption: string;
  /** Header of the table's first column, e.g. "Gameweek". */
  categoryHeader: string;
  tableCaption: string;
  /** Extra table columns, one cell per category. */
  extraColumns?: Array<{ header: string; cells: string[] }>;
  className?: string;
}

const MARGIN = { top: 18, right: 8, left: 40 };
const MAX_BAR = 24;

export function TrackRecordBars({
  categories,
  series,
  summary,
  unit = 'points',
  digits = 1,
  showValues = false,
  yCaption,
  categoryHeader,
  tableCaption,
  extraColumns,
  className,
}: TrackRecordBarsProps) {
  const [measureRef, width] = useElementWidth<HTMLDivElement>(640);
  const [active, setActive] = useState<number | null>(null);
  const [announce, setAnnounce] = useState('');
  const hintId = useId();
  const hasSub = categories.some((c) => c.sublabel);
  const height = (width < 480 ? 230 : 280) + (hasSub ? 14 : 0);
  const bottom = hasSub ? 42 : 28;
  const plotW = Math.max(40, width - MARGIN.left - MARGIN.right);
  const plotH = height - MARGIN.top - bottom;

  const bars = series.filter((s) => (s.kind ?? 'bar') === 'bar');
  const dashes = series.filter((s) => s.kind === 'dash');

  const geometry = useMemo(() => {
    const all = series.flatMap((s) => s.values).filter((v): v is number => v != null && Number.isFinite(v));
    const max = Math.max(0, ...all);
    const ticks = niceTicks(0, max || 1, 4);
    const top = Math.max(ticks[ticks.length - 1] ?? 1, max) || 1;
    const groupW = plotW / Math.max(1, categories.length);
    const barW = Math.max(2, Math.min(MAX_BAR, (groupW * 0.78) / Math.max(1, bars.length) - 1));
    const clusterW = barW * bars.length + (bars.length - 1);
    const labelEvery = Math.max(1, Math.ceil(26 / groupW));
    return { y: linearScale(0, top, plotH), ticks, groupW, barW, clusterW, labelEvery };
  }, [series, categories.length, bars.length, plotW, plotH]);

  const valueText = (v: number | null) => (v == null ? '–' : points(v, digits));
  const describe = (i: number) => {
    const parts = series.filter((s) => s.values[i] != null).map((s) => `${s.label} ${points(s.values[i] as number, digits)} ${unit}`);
    const c = categories[i];
    return `${c.label}${c.sublabel ? `, ${c.sublabel}` : ''}: ${parts.join(', ') || 'no data'}`;
  };

  const onPointer = (e: PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - rect.left - MARGIN.left) / geometry.groupW);
    setActive(i >= 0 && i < categories.length ? i : null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = active == null ? 0 : Math.min(categories.length - 1, active + 1);
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = active == null ? 0 : Math.max(0, active - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = categories.length - 1;
    else if (e.key === 'Escape') {
      setActive(null);
      return;
    } else return;
    e.preventDefault();
    if (!categories.length) return;
    setActive(next);
    setAnnounce(describe(next));
  };

  const groupX = (i: number) => i * geometry.groupW;
  const centre = (i: number) => groupX(i) + geometry.groupW / 2;

  return (
    <figure className={cn('min-w-0', className)}>
      <ul className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-micro font-semibold text-fg-muted" aria-label="Key">
        {series.map((s) => (
          <li key={s.key} className="inline-flex items-center gap-1.5">
            {s.kind === 'dash' ? (
              <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full" style={{ background: TONE_FILL[s.tone] }} />
            ) : (
              <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: TONE_FILL[s.tone] }} />
            )}
            {s.label}
          </li>
        ))}
      </ul>

      <div ref={measureRef} className="relative min-w-0">
        <div tabIndex={0} aria-describedby={hintId} onKeyDown={onKeyDown} onBlur={() => setActive(null)} className="rounded-md focus-visible:outline-offset-2">
          <svg
            role="img"
            aria-label={summary}
            width={width}
            height={height}
            className="block max-w-full"
            onPointerMove={onPointer}
            onPointerDown={onPointer}
            onPointerLeave={() => setActive(null)}
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {geometry.ticks.map((t) => (
                <g key={`y${t}`}>
                  <line x1={0} x2={plotW} y1={plotH - geometry.y(t)} y2={plotH - geometry.y(t)} stroke="rgb(var(--border))" strokeWidth={1} />
                  <text x={-8} y={plotH - geometry.y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="rgb(var(--text-subtle))" className="tabular-nums">
                    {tickLabel(t, geometry.ticks)}
                  </text>
                </g>
              ))}
              <text transform={`translate(${-32},${plotH / 2}) rotate(-90)`} textAnchor="middle" fontSize={11} fontWeight={600} fill="rgb(var(--text-muted))">
                {yCaption}
              </text>

              {active != null && (
                <rect x={groupX(active)} y={0} width={geometry.groupW} height={plotH} fill="rgb(var(--text) / 0.07)" rx={3} />
              )}

              {categories.map((c, i) => {
                const left = centre(i) - geometry.clusterW / 2;
                return (
                  <g key={c.key}>
                    {bars.map((s, j) => {
                      const v = s.values[i];
                      if (v == null || !Number.isFinite(v)) return null;
                      const h = Math.max(v > 0 ? 1 : 0, geometry.y(v));
                      const x = left + j * (geometry.barW + 1);
                      return (
                        <g key={s.key}>
                          <rect x={x} y={plotH - h} width={geometry.barW} height={h} rx={Math.min(3, geometry.barW / 2)} fill={TONE_FILL[s.tone]} fillOpacity={s.tone === 'muted' ? 0.8 : 1} />
                          {showValues && (
                            <text x={x + geometry.barW / 2} y={plotH - h - 4} textAnchor="middle" fontSize={11} fontWeight={600} fill="rgb(var(--text))" className="tabular-nums">
                              {points(v, digits)}
                            </text>
                          )}
                        </g>
                      );
                    })}
                    {dashes.map((s) => {
                      const v = s.values[i];
                      if (v == null || !Number.isFinite(v)) return null;
                      const w = Math.max(geometry.clusterW, Math.min(geometry.groupW * 0.9, MAX_BAR * 2));
                      return (
                        <line key={s.key} x1={centre(i) - w / 2} x2={centre(i) + w / 2} y1={plotH - geometry.y(v)} y2={plotH - geometry.y(v)} stroke={TONE_FILL[s.tone]} strokeWidth={2.5} strokeLinecap="round" />
                      );
                    })}
                    {i % geometry.labelEvery === 0 && (
                      <>
                        <text x={centre(i)} y={plotH + 16} textAnchor="middle" fontSize={11} fill={active === i ? 'rgb(var(--text))' : 'rgb(var(--text-subtle))'} className="tabular-nums">
                          {c.label}
                        </text>
                        {c.sublabel && (
                          <text x={centre(i)} y={plotH + 30} textAnchor="middle" fontSize={10} fill="rgb(var(--text-subtle))" className="tabular-nums">
                            {c.sublabel}
                          </text>
                        )}
                      </>
                    )}
                  </g>
                );
              })}
              <line x1={0} x2={plotW} y1={plotH} y2={plotH} stroke="rgb(var(--border-strong))" strokeWidth={1} />
            </g>
          </svg>
        </div>

        {active != null && categories[active] && (
          <div
            className="pointer-events-none absolute z-10 w-max max-w-[14rem] -translate-x-1/2 -translate-y-full rounded-md border border-line-strong bg-surface-2 px-2.5 py-1.5 text-micro text-fg shadow-pop"
            style={{ left: Math.min(Math.max(MARGIN.left + centre(active), 80), width - 80), top: 8 }}
          >
            <span className="block text-data font-semibold">
              {categories[active].label}
              {categories[active].sublabel && <span className="font-normal text-fg-muted"> · {categories[active].sublabel}</span>}
            </span>
            {series.map((s) => (
              <span key={s.key} className="flex items-center justify-between gap-3 text-fg-muted">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className={cn('inline-block', s.kind === 'dash' ? 'h-0.5 w-2.5' : 'h-2 w-2 rounded-sm')} style={{ background: TONE_FILL[s.tone] }} />
                  {s.label}
                </span>
                <span className="font-semibold tabular-nums text-fg">{valueText(s.values[active])}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <p id={hintId} className="mt-2 text-micro text-fg-subtle">
        <span className="hidden sm:inline">Hover a column for its numbers, or focus the chart and use the arrow keys.</span>
        <span className="sm:hidden">Tap a column for its numbers.</span>
      </p>
      <p aria-live="polite" className="sr-only">
        {announce}
      </p>

      <DataDisclosure summary="Show the numbers">
        <table className="w-full text-left text-data">
          <caption className="sr-only">{tableCaption}</caption>
          <thead className="text-micro uppercase text-fg-subtle">
            <tr>
              <th scope="col" className="py-1.5 pr-3 font-semibold">{categoryHeader}</th>
              {series.map((s) => (
                <th key={s.key} scope="col" className="py-1.5 pr-3 text-right font-semibold">{s.label}</th>
              ))}
              {extraColumns?.map((c) => (
                <th key={c.header} scope="col" className="py-1.5 pr-3 text-right font-semibold">{c.header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {categories.map((c, i) => (
              <tr key={c.key} className="border-t border-line">
                <th scope="row" className="whitespace-nowrap py-1 pr-3 font-normal text-fg">
                  {c.label}
                  {c.sublabel && <span className="text-fg-subtle"> · {c.sublabel}</span>}
                </th>
                {series.map((s) => (
                  <td key={s.key} className="whitespace-nowrap py-1 pr-3 text-right tabular-nums text-fg-muted">{valueText(s.values[i])}</td>
                ))}
                {extraColumns?.map((col) => (
                  <td key={col.header} className="whitespace-nowrap py-1 pr-3 text-right tabular-nums text-fg-muted">{col.cells[i] ?? '–'}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </DataDisclosure>
    </figure>
  );
}
