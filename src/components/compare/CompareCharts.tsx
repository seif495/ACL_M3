import { DataDisclosure } from '../explain/DataDisclosure';
import { useElementWidth } from '../explain/useElementWidth';
import { playerColor } from './compareModel';

/*
 * Hand-rolled SVG charts for the comparison, drawn at the measured width: grouped bars of the
 * totals (each stat on its own scale, every bar labelled) and a radar of the same stats scaled to
 * a common range. Each has a spoken summary and "Show the numbers".
 */

export interface ChartPlayer {
  name: string;
}

function Legend({ players }: { players: ChartPlayer[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-data text-fg-muted">
      {players.map((p, i) => (
        <li key={p.name + i} className="flex min-w-0 items-center gap-1.5">
          <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: playerColor(i) }} />
          <span className="truncate">{p.name}</span>
        </li>
      ))}
    </ul>
  );
}

function NumbersTable({
  caption,
  header,
  players,
  rows,
}: {
  caption: string;
  header: string;
  players: ChartPlayer[];
  rows: { label: string; cells: string[] }[];
}) {
  return (
    <table className="w-full border-collapse text-data">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-b border-line text-micro uppercase text-fg-subtle">
          <th scope="col" className="py-1.5 pr-3 text-left font-semibold">
            {header}
          </th>
          {players.map((p, i) => (
            <th key={p.name + i} scope="col" className="px-2 py-1.5 text-right font-semibold">
              {p.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-b border-line last:border-0">
            <th scope="row" className="py-1.5 pr-3 text-left font-normal text-fg-muted">
              {r.label}
            </th>
            {r.cells.map((c, i) => (
              <td key={i} className="px-2 py-1.5 text-right tabular-nums text-fg">
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export interface BarCategory {
  label: string;
  /** One value per player (null = no data). */
  values: (number | null)[];
}

const BAR_H = 10;
const BAR_GAP = 3;
const GROUP_GAP = 14;
const LABEL_H = 16;
const VALUE_W = 44;

export function GroupedBars({ categories, players }: { categories: BarCategory[]; players: ChartPlayer[] }) {
  const [ref, width] = useElementWidth<HTMLDivElement>(320);
  const n = players.length;
  const groupH = LABEL_H + n * BAR_H + (n - 1) * BAR_GAP;
  const height = categories.length * groupH + (categories.length - 1) * GROUP_GAP + 2;
  const trackW = Math.max(40, width - VALUE_W);

  const summary =
    'Totals side by side. ' +
    categories
      .map((c) => `${c.label}: ${players.map((p, i) => `${p.name} ${c.values[i] ?? 'no data'}`).join(', ')}`)
      .join('. ') +
    '.';

  return (
    <figure>
      <figcaption className="sr-only">Grouped bars of the totals; each stat has its own scale.</figcaption>
      <Legend players={players} />
      <div ref={ref}>
        <svg width={width} height={height} role="img" aria-label={summary} className="block">
          {categories.map((c, ci) => {
            const top = ci * (groupH + GROUP_GAP);
            const max = Math.max(1, ...c.values.map((v) => v ?? 0));
            return (
              <g key={c.label} transform={`translate(0 ${top})`}>
                <text x={0} y={11} fontSize={11} fontWeight={600} fill="rgb(var(--text-muted))">
                  {c.label}
                </text>
                {c.values.map((v, i) => {
                  const y = LABEL_H + i * (BAR_H + BAR_GAP);
                  const w = v == null ? 0 : Math.max(v > 0 ? 2 : 0, (v / max) * trackW);
                  return (
                    <g key={i}>
                      <rect x={0} y={y} width={trackW} height={BAR_H} rx={2} fill="rgb(var(--surface-2))" />
                      <rect x={0} y={y} width={w} height={BAR_H} rx={2} fill={playerColor(i)} />
                      <text x={trackW + 6} y={y + BAR_H - 1} fontSize={11} fontWeight={600} fill="rgb(var(--text))">
                        {v == null ? '–' : Math.round(v).toLocaleString()}
                      </text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <DataDisclosure summary="Show the numbers">
        <NumbersTable
          caption="Totals for each player"
          header="Stat"
          players={players}
          rows={categories.map((c) => ({
            label: c.label,
            cells: c.values.map((v) => (v == null ? '–' : Math.round(v).toLocaleString())),
          }))}
        />
      </DataDisclosure>
    </figure>
  );
}

export interface RadarAxis {
  label: string;
  /** Value that counts as 100. */
  max: number;
  values: (number | null)[];
}

export function Radar({ axes, players }: { axes: RadarAxis[]; players: ChartPlayer[] }) {
  const [ref, width] = useElementWidth<HTMLDivElement>(320);
  const size = Math.min(width, 340);
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 38;
  const k = axes.length;
  const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / k;
  const pt = (i: number, frac: number) => [cx + Math.cos(angle(i)) * r * frac, cy + Math.sin(angle(i)) * r * frac] as const;
  const polygon = (fracs: number[]) => fracs.map((f, i) => pt(i, f).join(',')).join(' ');
  const fracOf = (a: RadarAxis, v: number | null) => Math.min(1, Math.max(0, (v ?? 0) / a.max));

  const summary =
    'Profile on a common scale. ' +
    players
      .map((p, pi) => `${p.name}: ${axes.map((a) => `${a.label} ${Math.round(fracOf(a, a.values[pi]) * 100)}`).join(', ')}`)
      .join('. ') +
    '.';

  return (
    <figure>
      <figcaption className="sr-only">Radar of the same stats scaled so 100 is a strong season.</figcaption>
      <Legend players={players} />
      <div ref={ref} className="flex justify-center">
        <svg width={size} height={size} role="img" aria-label={summary} className="block">
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <polygon key={f} points={polygon(axes.map(() => f))} fill="none" stroke="rgb(var(--border))" strokeWidth={1} />
          ))}
          {axes.map((a, i) => {
            const [x, y] = pt(i, 1);
            const [lx, ly] = pt(i, 1.18);
            return (
              <g key={a.label}>
                <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgb(var(--border))" strokeWidth={1} />
                <text x={lx} y={ly} fontSize={11} fill="rgb(var(--text-subtle))" textAnchor="middle" dominantBaseline="middle">
                  {a.label}
                </text>
              </g>
            );
          })}
          {players.map((p, pi) => (
            <polygon
              key={p.name + pi}
              points={polygon(axes.map((a) => fracOf(a, a.values[pi])))}
              fill={playerColor(pi)}
              fillOpacity={0.14}
              stroke={playerColor(pi)}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          ))}
        </svg>
      </div>
      <DataDisclosure summary="Show the numbers">
        <NumbersTable
          caption="Each stat as a share of its scale, capped at 100"
          header="Stat (100 = scale)"
          players={players}
          rows={axes.map((a) => ({
            label: `${a.label} (${a.max})`,
            cells: a.values.map((v) => (v == null ? '–' : `${Math.round(fracOf(a, v) * 100)}`)),
          }))}
        />
      </DataDisclosure>
    </figure>
  );
}
